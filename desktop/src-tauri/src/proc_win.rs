//! Windows 进程树控制：每个服务一个 Job Object。
//!
//! 为什么不是「一个全局作业」：单服务重启（面板按钮 / 崩溃自愈）只结束顶层 pid，
//! 若全部服务共用一个作业，它的后代（nginx worker、uvicorn 子进程、ffmpeg、chrome）
//! 会留在作业里继续占着端口，重启必然探活超时。所以**每个受管子进程建一个作业**，
//! 作业带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`：
//!   - 重启/退出：`TerminateJobObject` 连带结束作业内整棵树，端口立刻释放；
//!   - 桌面壳被强杀/崩溃：进程句柄表随进程销毁，作业句柄关闭 → 系统照样回收全树，
//!     这是需求「杜绝后台残留进程占用端口」的兜底。
//!
//! Windows 无 SIGTERM：作业级终止即硬杀，靠 SQLite WAL / 服务幂等启动承受 abrupt stop。
use std::collections::HashMap;
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::process::{Child, Command};
use std::sync::{Mutex, OnceLock};

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// 不给子进程分配控制台窗口（桌面壳是 GUI 子系统，否则每个服务都弹一个黑框）。
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// pid → 该服务专属作业句柄（以 isize 存，HANDLE 本身不是 Send/Sync）。
static JOBS: OnceLock<Mutex<HashMap<u32, isize>>> = OnceLock::new();

fn jobs() -> &'static Mutex<HashMap<u32, isize>> {
    JOBS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// spawn 前的平台设置。
pub fn configure(cmd: &mut Command) {
    cmd.creation_flags(CREATE_NO_WINDOW);
}

/// 为该子进程新建专属作业并指派进去。
///
/// 已知窗口期：`std::process` 无法「创建即挂起、指派后再恢复」，故 spawn 与本函数之间
/// 若子进程已抢先 fork 后代，那部分后代不在作业内（实际服务在此毫秒级窗口内不会派生子进程）。
pub fn assign(child: &Child) {
    let pid = child.id();
    let job = match unsafe { create_job() } {
        Some(h) => h,
        None => return, // 作业创建失败：退化为只结束顶层进程（std Child::kill）
    };
    let assigned = unsafe { AssignProcessToJobObject(job, HANDLE(child.as_raw_handle() as *mut _)) };
    match assigned {
        Ok(()) => {
            jobs().lock().unwrap().insert(pid, job.0 as isize);
        }
        Err(_) => unsafe {
            let _ = CloseHandle(job);
        },
    }
}

/// 结束某个服务的整棵进程树。
pub fn terminate_tree(pid: u32) {
    kill_job(pid);
}

/// 硬杀兜底：与 terminate_tree 同义（作业级终止已是硬杀）。
pub fn force_kill_tree(pid: u32) {
    kill_job(pid);
}

/// App 退出：终止并关闭所有仍存活的作业，连带回收其内全部进程。
pub fn shutdown_all() {
    let pending: Vec<(u32, isize)> = {
        let mut map = jobs().lock().unwrap();
        let all = map.keys().copied().collect::<Vec<_>>();
        all.iter().filter_map(|p| map.remove(p).map(|h| (*p, h))).collect()
    };
    for (_, handle) in pending {
        unsafe {
            let h = HANDLE(handle as *mut _);
            let _ = TerminateJobObject(h, 1);
            let _ = CloseHandle(h);
        }
    }
}

fn kill_job(pid: u32) {
    let handle = jobs().lock().unwrap().remove(&pid);
    if let Some(raw) = handle {
        unsafe {
            let h = HANDLE(raw as *mut _);
            let _ = TerminateJobObject(h, 1);
            let _ = CloseHandle(h);
        }
    }
}

/// 创建带 KILL_ON_JOB_CLOSE 的作业对象；失败返回 None（不阻断服务启动）。
unsafe fn create_job() -> Option<HANDLE> {
    let h = CreateJobObjectW(None, PCWSTR::null()).ok()?;
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if SetInformationJobObject(
        h,
        JobObjectExtendedLimitInformation,
        &limits as *const _ as *const core::ffi::c_void,
        std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
    )
    .is_err()
    {
        let _ = CloseHandle(h);
        return None;
    }
    Some(h)
}

/// 真实 Windows 上的进程树回收回归测试。
///
/// 覆盖本轮修复的核心语义：单服务重启只给顶层 pid，必须连带杀掉它的后代
/// （对应 nginx master→worker、uvicorn→ffmpeg/chrome），否则端口残留、重启必然超时。
///
/// 需要交互式/真实 Windows 会话（会拉起 cmd + ping 子进程），故默认 `#[ignore]`：
///   cargo test -- --ignored proc_win
#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    /// 按进程名取 pid 列表（用 PowerShell，避免依赖已被移除的 wmic）。
    fn pids_of(name: &str) -> Vec<u32> {
        let ps = format!("Get-Process -Name '{name}' -ErrorAction SilentlyContinue | ForEach-Object {{ $_.Id }}");
        let out = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .ok();
        out.map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter_map(|l| l.trim().parse::<u32>().ok())
                .collect()
        })
        .unwrap_or_default()
    }

    fn alive(pid: u32) -> bool {
        pids_of("ping").contains(&pid)
    }

    /// cmd.exe 派生一个 ping 孙进程；terminate_tree(cmd_pid) 后两者都必须消失。
    #[test]
    #[ignore = "会拉起真实子进程，需专机跑：cargo test -- --ignored"]
    fn terminate_tree_kills_grandchildren() {
        let before: Vec<u32> = pids_of("ping").into_iter().collect();

        let mut cmd = Command::new("cmd.exe");
        configure(&mut cmd);
        // /c start /b 让 cmd 派生一个独立 ping 子进程，自己再挂一个 ping 后长驻。
        cmd.args(["/C", "start /b ping -n 120 127.0.0.1 & ping -n 120 127.0.0.1"]);
        let mut child = cmd.spawn().expect("spawn cmd.exe");
        assign(&child);
        let top = child.id();

        // 等孙进程出现（最多 10s），拿到它的 pid。
        let deadline = Instant::now() + Duration::from_secs(10);
        let grand: Vec<u32> = loop {
            let now = pids_of("ping");
            let fresh: Vec<u32> = now.iter().copied().filter(|p| !before.contains(p)).collect();
            if !fresh.is_empty() || Instant::now() > deadline {
                break fresh;
            }
            std::thread::sleep(Duration::from_millis(200));
        };
        assert!(!grand.is_empty(), "10s 内没等到 cmd 派生的 ping 孙进程，测试环境不适");

        terminate_tree(top);

        // 顶层与孙进程都应在作业级终止下消失（给 5s 收敛窗口）。
        let deadline = Instant::now() + Duration::from_secs(5);
        let leaked: Vec<u32> = loop {
            let leaked: Vec<u32> = grand.iter().copied().filter(|p| alive(*p)).collect();
            let top_alive = pids_of("cmd").contains(&top);
            if (!leaked.is_empty() || top_alive) && Instant::now() > deadline {
                break leaked;
            }
            if leaked.is_empty() && !top_alive {
                break leaked;
            }
            std::thread::sleep(Duration::from_millis(200));
        };
        let _ = child.wait();
        assert!(leaked.is_empty(), "孙进程残留在作业外：{leaked:?}");
    }

    /// shutdown_all 兜底：未被逐个 terminate 的服务树也要全清（对应 App 被强杀/崩溃）。
    #[test]
    #[ignore = "会拉起真实子进程，需专机跑：cargo test -- --ignored"]
    fn shutdown_all_terminates_remaining_jobs() {
        let before: Vec<u32> = pids_of("ping").into_iter().collect();
        let mut child = Command::new("cmd.exe")
            .args(["/C", "ping -n 120 127.0.0.1"])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn cmd.exe");
        assign(&child);
        let top = child.id();
        std::thread::sleep(Duration::from_millis(800));

        shutdown_all();
        std::thread::sleep(Duration::from_millis(500));

        let leaked: Vec<u32> = pids_of("ping").into_iter().filter(|p| !before.contains(p)).collect();
        assert!(leaked.is_empty(), "shutdown_all 后仍有新起 ping 残留：{leaked:?}");
        assert!(!pids_of("cmd").contains(&top), "顶层 cmd 未随作业结束");
        let _ = child.wait();
    }
}

