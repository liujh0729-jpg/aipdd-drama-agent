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
        let mut line = String::new();
        loop {
            line.clear();
            match buf.read_line(&mut line) {
                Ok(0) | Err(_) => break, // EOF 或读错误：进程已结束
                Ok(_) => {}
            }
            let trimmed = line.trim_end_matches(['\n', '\r']);
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
