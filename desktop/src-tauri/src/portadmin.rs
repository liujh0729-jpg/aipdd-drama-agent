//! 端口健康与释放（桌面诊断窗口用）：探测受管端口上的监听进程（pid + 程序名），
//! 并按需释放外部占用者。
//!
//! 与 `probe` 的分工：`probe` 判「我们的服务是否就绪」（connect 探活）；本模块判
//! 「这个端口此刻被谁 LISTEN 着」——用 `lsof`（Unix）/ `netstat`（Windows）取真实占用者，
//! 据此区分「我们自己起的（ours）/ 外部占用（external）/ 空闲（free）」。
//!
//! 安全边界：`kill_pid` 只对外部占用者调用（受管 pid 由调用方剔除后再传进来），
//! 且仅针对 WATCH_PORTS 这 4 个端口上 LISTEN 的进程——绝不泛杀。命令执行产物解析
//! 全部走下面的纯函数（parse_lsof / parse_netstat），跨平台可单测；exec 层保持极薄。
use serde::Serialize;
use std::process::Command;
#[cfg(windows)]
use std::process::Stdio;

/// 受管端口（启动顺序）：`(服务显示名, 默认端口)`。端口健康检测与释放都以这份清单为准。
pub const WATCH_PORTS: &[(&str, u16)] = &[
    ("Nginx 反代 / 前端", 8080),
    ("newapi 网关 (Go)", 3000),
    ("FastAPI 主服务 (Python)", 8780),
    ("VectCut 剪辑 (Python)", 9001),
];

/// 一个 LISTEN 在该端口上的进程。
#[derive(Clone, Debug)]
pub struct Listener {
    pub pid: u32,
    pub name: String,
}

/// 单个端口的健康状态，直接序列化给诊断窗口。
#[derive(Serialize, Clone)]
pub struct PortStatus {
    pub service: String,
    pub port: u16,
    /// `free`（空闲）| `ours`（我方服务在监听）| `external`（被外部进程占用）
    pub state: String,
    /// 外部占用者描述：`pid(程序名)`，多个逗号连接；无外部占用时 None。
    pub owner: Option<String>,
}

/// 一次释放操作对单个端口的结果。
#[derive(Serialize, Clone)]
pub struct ReleaseResult {
    pub port: u16,
    pub killed: Vec<u32>,
    pub failed: Vec<u32>,
}

/// 找出 LISTEN 在 `port` 上的所有进程（按 pid 去重）。
pub fn find_listeners(port: u16) -> Vec<Listener> {
    #[cfg(unix)]
    {
        find_listeners_unix(port)
    }
    #[cfg(windows)]
    {
        find_listeners_win(port)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = port;
        Vec::new()
    }
}

#[cfg(unix)]
fn find_listeners_unix(port: u16) -> Vec<Listener> {
    let out = Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN"])
        .output();
    match out {
        Ok(o) => parse_lsof(&String::from_utf8_lossy(&o.stdout)),
        Err(_) => Vec::new(),
    }
}

#[cfg(windows)]
fn find_listeners_win(port: u16) -> Vec<Listener> {
    let out = Command::new("netstat")
        .args(["-ano", "-p", "tcp"])
        .output();
    let o = match out {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };
    let mut seen = std::collections::HashSet::new();
    let mut v = Vec::new();
    for pid in parse_netstat(&String::from_utf8_lossy(&o.stdout), port) {
        if seen.insert(pid) {
            v.push(Listener {
                pid,
                name: process_name_win(pid),
            });
        }
    }
    v
}

/// 杀死一个**外部**占用进程（调用方须已剔除我方受管 pid）。尽力而为：
/// Unix 先 SIGTERM 再 SIGKILL；进程已消失（ESRCH）也算成功。返回是否发出成功/已不在。
pub fn kill_pid(pid: u32) -> bool {
    #[cfg(unix)]
    {
        use nix::errno::Errno;
        use nix::sys::signal::{kill, Signal};
        use nix::unistd::Pid;
        let p = Pid::from_raw(pid as i32);
        let _ = kill(p, Signal::SIGTERM);
        std::thread::sleep(std::time::Duration::from_millis(250));
        match kill(p, Signal::SIGKILL) {
            Ok(()) => true,
            Err(Errno::ESRCH) => true, // 已经没了 = 目标达成
            Err(_) => false,           // EPERM 等无权限
        }
    }
    #[cfg(windows)]
    {
        Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = pid;
        false
    }
}

/// 解析 `lsof -nP -iTCP:<port> -sTCP:LISTEN` 的表格输出：跳过表头，取 COMMAND / PID 列并按 pid 去重。
/// 生产路径只在 Unix 调用；非 Unix 下仅被单测覆盖，故对非 Unix 豁免 dead_code。
#[cfg_attr(not(unix), allow(dead_code))]
fn parse_lsof(text: &str) -> Vec<Listener> {
    let mut seen = std::collections::HashSet::new();
    let mut v = Vec::new();
    for line in text.lines().skip(1) {
        let mut it = line.split_whitespace();
        let name = match it.next() {
            Some(n) => n.to_string(),
            None => continue,
        };
        let pid: u32 = match it.next().and_then(|p| p.parse().ok()) {
            Some(p) => p,
            None => continue,
        };
        if seen.insert(pid) {
            v.push(Listener { pid, name });
        }
    }
    v
}

/// 解析 `netstat -ano -p tcp` 输出，返回 LISTENING 且本地地址以 `:port` 结束的 pid 列表（可含重复）。
/// 生产路径只在 Windows 调用；作为纯解析函数在非 Windows 下仅被单测覆盖，故对非 Windows 豁免 dead_code。
#[cfg_attr(not(windows), allow(dead_code))]
fn parse_netstat(text: &str, port: u16) -> Vec<u32> {
    let suffix = format!(":{port}");
    let mut pids = Vec::new();
    for line in text.lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        // 形如： TCP  127.0.0.1:8080  0.0.0.0:0  LISTENING  4321
        if f.len() < 5 || f[0] != "TCP" || f[3] != "LISTENING" {
            continue;
        }
        if f[1].ends_with(&suffix) {
            if let Ok(pid) = f[4].parse::<u32>() {
                pids.push(pid);
            }
        }
    }
    pids
}

/// Windows：用 tasklist 查某 pid 的映像名（best-effort，失败返回空串）。
#[cfg(windows)]
fn process_name_win(pid: u32) -> String {
    let filter = format!("PID eq {pid}");
    let out = Command::new("tasklist")
        .args(["/FI", &filter, "/NH", "/FO", "CSV"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output();
    match out {
        Ok(o) => {
            let text = String::from_utf8_lossy(&o.stdout).to_string();
            // CSV 首列是 "映像名"，形如： "nginx.exe","1234",...
            text.lines()
                .next()
                .and_then(|l| l.split(',').next())
                .map(|s| s.trim_matches('"').to_string())
                .unwrap_or_default()
        }
        Err(_) => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_lsof_skips_header_and_dedupes() {
        let text = "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n\
                    nginx   12345 user    6u  IPv4 123456      0t0  TCP *:8080 (LISTEN)\n\
                    nginx   12345 user    8u  IPv6 123457      0t0  TCP *:8080 (LISTEN)\n\
                    Python  777   user   12u  IPv4 999999      0t0  TCP 127.0.0.1:8080 (LISTEN)\n";
        let ls = parse_lsof(text);
        assert_eq!(ls.len(), 2, "同一 pid 的 IPv4/IPv6 应去重");
        assert_eq!(ls[0].pid, 12345);
        assert_eq!(ls[0].name, "nginx");
        assert_eq!(ls[1].pid, 777);
        assert_eq!(ls[1].name, "Python");
    }

    #[test]
    fn parse_lsof_empty_is_zero() {
        // 无匹配时 lsof 只回表头。
        assert!(parse_lsof("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n").is_empty());
        assert!(parse_lsof("").is_empty());
    }

    #[test]
    fn parse_netstat_matches_only_listening_on_port() {
        let text = "  Proto  Local Address          Foreign Address        State           PID\r\n\
                    \x20 TCP    127.0.0.1:8080          0.0.0.0:0              LISTENING       4321\r\n\
                    \x20 TCP    0.0.0.0:3000            0.0.0.0:0              LISTENING       5555\r\n\
                    \x20 TCP    127.0.0.1:8080          127.0.0.1:52000        ESTABLISHED     4321\r\n";
        assert_eq!(parse_netstat(text, 8080), vec![4321]);
        assert_eq!(parse_netstat(text, 3000), vec![5555]);
        assert!(parse_netstat(text, 9999).is_empty());
    }
}
