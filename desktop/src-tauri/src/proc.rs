//! 进程树控制的平台门面：按编译目标转发到 proc_unix / proc_win。
#[cfg(unix)]
#[path = "proc_unix.rs"]
mod imp;

#[cfg(windows)]
#[path = "proc_win.rs"]
mod imp;

pub use imp::{assign, configure, force_kill_tree, shutdown_all, terminate_tree};
