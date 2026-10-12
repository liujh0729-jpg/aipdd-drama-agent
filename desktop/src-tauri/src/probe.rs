//! 端口探活。就绪判定以 TCP 连接成功为主，另提供 HTTP 健康检查辅助。
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

/// 单次 TCP 连接探活（短超时）。
pub fn port_open(port: u16) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// 轮询直到端口可连或超时。
pub fn wait_port_open(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if port_open(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}

/// 端口是否已被“别的进程”占用（用于启动前的冲突自检）。
/// 若本应无人监听却能连上，说明外部占用。
pub fn port_in_use(port: u16) -> bool {
    port_open(port)
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
