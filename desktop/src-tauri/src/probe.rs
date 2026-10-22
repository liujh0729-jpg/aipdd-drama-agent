//! 端口探活。就绪判定以 TCP 连接成功为主，另提供 HTTP 健康检查辅助。
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::time::Duration;

/// 单次 TCP 连接探活（短超时）。
pub fn port_open(port: u16) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// 端口是否已被“别的进程”占用（用于启动前的冲突自检）。
/// 若本应无人监听却能连上，说明外部占用。
pub fn port_in_use(port: u16) -> bool {
    port_open(port)
}

/// 端口能否被本机绑定（用于「选端口」而不是「等服务就绪」：bind 比 connect 探活可靠，
/// 不会把「有人监听但拒绝连接」误判为可用）。监听器借出即释放，不占用端口。
///
/// 必须按 `0.0.0.0` 通配地址试绑：nginx 监听的是通配地址，只测 `127.0.0.1`
/// 会把「仅绑定了某个具体网卡 / IPv6 双栈」的占用误判为空闲。
pub fn port_bindable(port: u16) -> bool {
    TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], port))).is_ok()
}

/// 从 `start` 起连续探测 `span` 个端口，返回第一个可绑定的。
/// 全被占时返回 None（调用方自行决定报错文案）。
pub fn find_free_port(start: u16, span: u16) -> Option<u16> {
    find_free_port_between(start, start.saturating_add(span))
}

/// 在 `[start, end)` 内找第一个可绑定端口（nginx 自动换端口重试用）。
pub fn find_free_port_between(start: u16, end: u16) -> Option<u16> {
    (start..end).find(|&p| port_bindable(p))
}

/// 轻量 HTTP GET 健康检查（不引入额外依赖，手工拼请求，仅判断 2xx）。
/// `path` 如 `/api/v1/config`、`/api/status`。
#[allow(dead_code)]
pub fn http_ok(port: u16, path: &str, timeout: Duration) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let req = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    match TcpStream::connect_timeout(&addr, timeout)
        .and_then(|mut s| {
            s.set_read_timeout(Some(timeout))?;
            s.set_write_timeout(Some(timeout))?;
            s.write_all(req.as_bytes())?;
            let mut buf = [0u8; 64];
            let n = s.read(&mut buf)?;
            Ok(String::from_utf8_lossy(&buf[..n]).to_string())
        }) {
        Ok(head) => head.starts_with("HTTP/1.1 2") || head.starts_with("HTTP/1.0 2"),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn port_bindable_flips_with_a_real_listener() {
        // 与 nginx 一样占通配地址：绑上即可见 false，释放后回到 true。
        let l = TcpListener::bind("0.0.0.0:0").unwrap();
        let port = l.local_addr().unwrap().port();
        assert!(!port_bindable(port));
        drop(l);
        assert!(port_bindable(port));
    }

    #[test]
    fn find_free_port_skips_the_occupied_start() {
        // 起点被自己占住，探测必须往后让；用系统分配的临时端口避免撞开发环境真服务。
        let occupied = TcpListener::bind("0.0.0.0:0").unwrap();
        let start = occupied.local_addr().unwrap().port();
        let found = find_free_port(start, 16).expect("临时端口段内应有可用端口");
        assert_ne!(found, start, "被占用的起点不应被选中");
        assert!(port_bindable(found));
    }

    #[test]
    fn find_free_port_between_respects_upper_bound() {
        let occupied = TcpListener::bind("0.0.0.0:0").unwrap();
        let start = occupied.local_addr().unwrap().port();
        // 区间只包含被占端口时不能越界去选后面的空闲端口。
        assert_eq!(find_free_port_between(start, start + 1), None);
    }
}
