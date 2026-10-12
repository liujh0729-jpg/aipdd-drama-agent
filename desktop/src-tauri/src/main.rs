//! AIPDD DramaClaw 桌面壳入口（Tauri v2）。
//!
//! 生命周期：
//!   setup → 计算 app-data / resource 目录 → 构建 RuntimePaths → 造 Orchestrator（含监控线程）
//!         → 打开「服务状态面板」窗口 → 后台线程串行启动全套服务 → 就绪后打开主窗口加载前端。
//!   退出（所有窗口关闭）→ RunEvent::Exit → Orchestrator::shutdown 逆序回收全部子进程。
//!
//! 安全：进程编排全在 Rust 侧用 std::process 完成，Web 侧无任何 shell 能力。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod downloader;
mod env;
mod error;
mod logs;
mod models;
mod orchestrator;
mod probe;
mod proc;

use std::sync::Arc;

use tauri::{
    AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder,
};

use crate::env::RuntimePaths;
use crate::models::{ServiceId, ServiceState};
use crate::orchestrator::{Orchestrator, EVT_BOOT};

/// 主窗口加载地址：内置 nginx 托管的前端站点。
const APP_URL: &str = "http://127.0.0.1:8080";

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();

            // 目录：app-data（可写）与 resource（只读内置资源）。
            let app_data = handle.path().app_data_dir()?;
            let resource = handle.path().resource_dir()?;
            let paths = Arc::new(RuntimePaths::new(app_data, resource)?);

            let orc = Orchestrator::new(handle.clone(), paths.clone());
            handle.manage(orc);

            // 服务状态面板（booting / 运行期诊断入口）。
            WebviewWindowBuilder::new(&handle, "panel", WebviewUrl::App("index.html".into()))
                .title("AIPDD DramaClaw · 服务状态")
                .inner_size(560.0, 680.0)
                .min_inner_size(440.0, 480.0)
                .resizable(true)
                .build()?;

            // 后台启动全栈；就绪后打开主窗口。
            let boot_handle = handle.clone();
            let boot_paths = paths.clone();
            std::thread::spawn(move || boot_sequence(boot_handle, boot_paths));

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status,
            restart_service,
            open_app_window,
            logs_dir_path,
            app_version
        ])
        .build(tauri::generate_context!())
        .expect("初始化 AIPDD DramaClaw 桌面壳失败")
        .run(|app_handle, event| {
            // 进程真正退出时逆序终止所有子进程，杜绝端口残留。
            if let tauri::RunEvent::Exit = event {
                if let Some(orc) = app_handle.try_state::<Arc<Orchestrator>>() {
                    orc.shutdown();
                }
                proc::shutdown_all();
            }
        });
}

/// 后台启动序列：先并行拉起模型下载，再串行启动服务，全绿则开主窗口。
fn boot_sequence(handle: AppHandle, _paths: Arc<RuntimePaths>) {
    // 模型下载（清单缺失则跳过）。放在独立 tokio 任务，与本地服务启动并行。
    {
        let orc_paths = handle
            .state::<Arc<Orchestrator>>()
            .paths()
            .clone();
        if let Ok(entries) = downloader::load_manifest(&orc_paths.model_manifest()) {
            if !entries.is_empty() {
                let models_dir = orc_paths.models_dir();
                let app_for_dl = handle.clone();
                tauri::async_runtime::spawn(async move {
                    downloader::ensure_models(app_for_dl, entries, models_dir).await;
                });
            }
        }
    }

    let orc = handle.state::<Arc<Orchestrator>>();
    match orc.start_all() {
        Ok(()) => {
            if let Err(e) = open_main_window(&handle) {
                eprint_boot_error(&handle, &format!("打开主窗口失败：{e}"));
            }
        }
        Err(e) => {
            let (_kind, msg) = e.to_ui();
            eprint_boot_error(&handle, &msg);
        }
    }
}

/// 创建（或聚焦）加载前端的主窗口。
fn open_main_window(handle: &AppHandle) -> Result<(), String> {
    if let Some(w) = handle.get_webview_window("main") {
        let _ = w.set_focus();
        return Ok(());
    }
    let url: url::Url = APP_URL.parse().map_err(|_| format!("无效地址：{APP_URL}"))?;
    WebviewWindowBuilder::new(handle, "main", WebviewUrl::External(url))
        .title("AIPDD DramaClaw")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1024.0, 680.0)
        .resizable(true)
        .focused(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn eprint_boot_error(handle: &AppHandle, message: &str) {
    // 以启动进度事件回给面板，面板据此展示红条 + 排障建议。
    let _ = handle.emit(
        EVT_BOOT,
        crate::models::BootEvent {
            stage: "error".into(),
            message: message.to_string(),
            progress: 0.0,
        },
    );
}

// ---------- Tauri 命令（面板 / 主窗口可调用）----------

#[tauri::command]
fn get_status(orc: State<'_, Arc<Orchestrator>>) -> Vec<ServiceState> {
    orc.snapshot()
}

#[tauri::command]
fn restart_service(orc: State<'_, Arc<Orchestrator>>, id: String) -> Result<(), String> {
    let sid = parse_id(&id).ok_or_else(|| format!("未知服务：{id}"))?;
    let orc = orc.inner().clone();
    orc.restart_one(sid).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_app_window(handle: AppHandle) -> Result<(), String> {
    open_main_window(&handle)
}

#[tauri::command]
fn logs_dir_path(orc: State<'_, Arc<Orchestrator>>) -> String {
    orc.paths().logs_dir().display().to_string()
}

#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

fn parse_id(s: &str) -> Option<ServiceId> {
    match s {
        "nginx" => Some(ServiceId::Nginx),
        "gateway" => Some(ServiceId::Gateway),
        "vectcut" => Some(ServiceId::Vectcut),
        "api" => Some(ServiceId::Api),
        _ => None,
    }
}
