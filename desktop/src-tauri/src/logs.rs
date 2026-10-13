//! 子进程输出采集：把 stdout/stderr 逐行同时落盘（app-data/logs/<service>.log）
//! 并推送到内存通道，由编排器转发给状态面板实时展示。
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::Child;
use std::sync::mpsc::Sender;
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::models::LogLine;

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// 接管 child 的 stdout/stderr，各起一个读线程做 tee。
/// `tx` 为 `mpsc::Sender`（可 Clone）。线程为分离线程，随管道 EOF 自然退出。
pub fn pipe_output(
    service: &'static str,
    child: &mut Child,
    log_path: &Path,
    tx: &Sender<LogLine>,
) {
    if let Some(out) = child.stdout.take() {
        spawn_reader(service, "stdout", out, log_path, tx.clone());
    }
    if let Some(err) = child.stderr.take() {
        spawn_reader(service, "stderr", err, log_path, tx.clone());
    }
}

fn spawn_reader<R: Read + Send + 'static>(
    service: &'static str,
    stream: &'static str,
    reader: R,
    log_path: &Path,
    tx: Sender<LogLine>,
) {
    let log_path = log_path.to_path_buf();
    thread::spawn(move || {
        // 每线程各自以 append 方式持有文件句柄，避免共享锁；交错可接受。
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .ok();
        let mut buf = BufReader::new(reader);
        let mut raw: Vec<u8> = Vec::with_capacity(512);
        loop {
            raw.clear();
            // 按字节读一行，再决定编码：直接 read_line into String 会在遇到非 UTF-8
            // 字节时返回 Err，读线程随之退出——日志断流且管道写满后子进程会卡死。
            match buf.read_until(b'\n', &mut raw) {
                Ok(0) | Err(_) => break, // EOF 或读错误：进程已结束
                Ok(_) => {}
            }
            let decoded = decode_line(&raw);
            let trimmed = decoded.trim_end_matches(['\n', '\r']);
            if trimmed.is_empty() {
                continue;
            }
            if let Some(f) = file.as_mut() {
                let _ = writeln!(f, "[{stream}] {trimmed}");
                let _ = f.flush();
            }
            let _ = tx.send(LogLine {
                service: service.to_string(),
                stream: stream.to_string(),
                line: trimmed.to_string(),
                ts: now_ms(),
            });
        }
    });
}

/// 一行原始字节 → 可展示文本。
///
/// Python 侧已由编排器注入 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`，正常走 UTF-8 分支；
/// 其余原生程序（nginx.exe 等）在中文 Windows 上按系统 OEM 代码页（cp936）输出，
/// 需要回落解码，否则面板与 .log 里是乱码。
fn decode_line(raw: &[u8]) -> String {
    match std::str::from_utf8(raw) {
        Ok(s) => s.to_string(),
        Err(_) => fallback_decode(raw),
    }
}

#[cfg(not(windows))]
fn fallback_decode(raw: &[u8]) -> String {
    String::from_utf8_lossy(raw).into_owned()
}

#[cfg(windows)]
fn fallback_decode(raw: &[u8]) -> String {
    use std::sync::OnceLock;
    use windows::Win32::Globalization::{
        GetOEMCP, MultiByteToWideChar, MULTI_BYTE_TO_WIDE_CHAR_FLAGS,
    };

    static OEM_CP: OnceLock<u32> = OnceLock::new();
    let cp = *OEM_CP.get_or_init(|| unsafe { GetOEMCP() });

    // 窄字节 → UTF-16（Windows 代码页 API 的正解），再交给 Rust 转 String。
    let converted = unsafe {
        let need = MultiByteToWideChar(cp, MULTI_BYTE_TO_WIDE_CHAR_FLAGS(0), raw, None);
        if need > 0 {
            let mut wide: Vec<u16> = vec![0; need as usize];
            let got = MultiByteToWideChar(
                cp,
                MULTI_BYTE_TO_WIDE_CHAR_FLAGS(0),
                raw,
                Some(&mut wide),
            );
            if got > 0 {
                wide.truncate(got as usize);
                Some(String::from_utf16_lossy(&wide))
            } else {
                None
            }
        } else {
            None
        }
    };
    converted.unwrap_or_else(|| String::from_utf8_lossy(raw).into_owned())
}
