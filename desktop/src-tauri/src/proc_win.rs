//! Windows 进程树控制：Job Object。
//!
//! 创建带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的作业对象，每启动一个服务子进程
//! 就把它指派进作业；作业内的进程再 spawn 的后代默认继续留在作业中。App 退出时
//! 关闭作业句柄（或显式 TerminateJobObject），系统会连带结束作业内**所有**进程，
//! 彻底避免 python/uvicorn/ffmpeg/chromium 树残留占用端口。
//!
//! 注意：Windows API 细节多、需在真实 Windows 上编译验证（见 desktop/README「构建机」）。
use std::os::windows::io::AsRawHandle;
use std::process::Child;
use std::sync::OnceLock;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject, TerminateJobObject,
    JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

static JOB: OnceLock<Option<HANDLE>> = OnceLock::new();

fn job_handle() -> Option<&'static HANDLE> {
    JOB.get_or_init(|| {
        unsafe {
            // 创建作业对象；失败返回 None，后续 assign/terminate 会被跳过。
            let h = CreateJobObjectW(None, PCWSTR::null()).ok()?;
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let _ = SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            Some(h)
        }
    })
    .as_ref()
}

/// 确保作业对象存在。
pub fn configure(_cmd: &mut std::process::Command) {
    let _ = job_handle();
}

/// 把子进程指派进作业（其后续后代自动继承）。
pub fn assign(child: &Child) {
    let Some(job) = job_handle() else { return };
    unsafe {
        let ph = HANDLE(child.as_raw_handle() as *mut _);
        let _ = AssignProcessToJobObject(*job, ph);
    }
}

/// 结束单个进程树（作业语义下优先整体终止；这里保留接口对称，交给作业句柄处理）。
pub fn terminate_tree(_pid: u32) {
    // 单服务重启由编排器先 kill 顶层 pid（见 orchestrator 的 std Child::kill），
    // 再重新指派进作业；作业级清理在 App 退出时统一执行。
}

pub fn force_kill_tree(pid: u32) {
    // 兜底：对顶层进程调用 TerminateProcess 由 std Child::kill 覆盖，
    // 其余后代随作业回收。为保持接口对称保留该函数。
    let _ = pid;
}

/// App 退出：终止并关闭作业，连带杀死作业内全部进程。
pub fn shutdown_all() {
    if let Some(Some(job)) = JOB.get() {
        unsafe {
            let _ = TerminateJobObject(*job, 0);
            let _ = CloseHandle(*job);
        }
    }
}
