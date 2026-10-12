//! Unix（macOS/Linux）进程树控制。
//!
//! 策略：spawn 前用 `process_group(0)` 让子进程自成一个进程组（pgid == 子 pid），
//! 终止时对整个进程组 `killpg`，从而连带回收它 fork/exec 的所有后代
//! （uvicorn worker、python 起的 ffmpeg/chromium 等），杜绝端口残留。
use std::os::unix::process::CommandExt;
use std::process::Command;

use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;

/// 子进程归入独立进程组。
pub fn configure(cmd: &mut Command) {
    // SAFETY: 仅设置进程组，无内存不安全。
    cmd.process_group(0);
}

/// Windows Job Object 语义在此无对应，Unix 无需指派（killpg 已覆盖进程树）。
pub fn assign(_child: &std::process::Child) {}

/// 优雅终止整个进程组（SIGTERM）。
pub fn terminate_tree(pid: u32) {
    let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGTERM);
}

/// 强制杀死整个进程组（SIGKILL）。
pub fn force_kill_tree(pid: u32) {
    let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGKILL);
}

/// 进程退出前的兜底：Unix 依赖父进程死亡时子进程随组处理，无全局句柄需关闭。
pub fn shutdown_all() {}
