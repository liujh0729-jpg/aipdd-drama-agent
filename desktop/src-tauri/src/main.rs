//! AIPDD DramaClaw 桌面壳入口（Tauri v2）。
//!
//! 生命周期：
//!   setup → 计算 app-data / resource 目录 → 构建 RuntimePaths → 造 Orchestrator（含监控线程）
//!         → 打开启动卡（splash，无边框，只显示百分比）→ 后台线程串行启动全套服务
//!         → 就绪后打开主窗口并关掉启动卡。
//!   启动失败时启动卡转为错误态，其「查看详细信息」才按需打开诊断窗口（服务 + 日志）。
//!   退出（所有窗口关闭）→ RunEvent::Exit → Orchestrator::shutdown 逆序回收全部子进程。
//!
//! 安全：进程编排全在 Rust 侧用 std::process 完成，Web 侧无任何 shell 能力。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod datadir;
mod downloader;
mod env;
mod error;
mod logs;
mod models;
mod orchestrator;
mod portadmin;
mod probe;
mod proc;

use std::sync::Arc;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use crate::env::RuntimePaths;
use crate::models::{BootEvent, LogLine, ServiceId, ServiceState};
use crate::orchestrator::Orchestrator;

/// 主窗口加载地址：内置 nginx 托管的前端站点。端口在启动自检里解析
/// （8080 被占则往后避让并定格本次会话），从编排状态取真实值，不写死。
fn app_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// 就绪后启动卡的收尾上限：等前端把百分比补满后由 `splash_finished` 通知，再开主窗口、关卡；
/// 前端没起来/桥不通时最多兜底等这么久，不能让主窗口后面挂着一个已经跑完的启动卡。
const SPLASH_FINISH_WAIT: std::time::Duration = std::time::Duration::from_millis(1500);

/// 启动卡自报「百分比已跑满并停留完毕」的旗标（前端 → Rust）。
static SPLASH_DONE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// DESIGN.md 暗色 background token（#0a0c12），窗口底色与页面底色取齐避免首帧白闪。
const DARK_BG: tauri::window::Color = tauri::window::Color(10, 12, 18, 255);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let handle = app.handle().clone();

            // 目录：home 是平台 app-data（固定，只放配置指针）；实际数据根可由用户
            // 改到其他磁盘，runtime 资源仍是只读安装目录。
            let home = handle.path().app_data_dir()?;
            let resource = handle.path().resource_dir()?;
            let install_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|p| p.to_path_buf()))
                .unwrap_or_default();
            let (store, root) = datadir::resolve(home, install_dir);
            let store = Arc::new(store);
            let paths = Arc::new(RuntimePaths::new(root, resource)?);
            handle.manage(store);

            let orc = Orchestrator::new(handle.clone(), paths.clone());
            handle.manage(orc);

            // 启动卡（Photoshop 式 splash）：无边框、不可缩放、居中，只画品牌与进度。
            // 诊断用的服务/日志面板不在启动流程里露出，改由 open_console_window 按需开。
            WebviewWindowBuilder::new(&handle, "panel", WebviewUrl::App("index.html".into()))
                .title("AIPDD DramaClaw")
                .inner_size(660.0, 380.0)
                // 与页面底色一致，否则 WebView2 首帧是白的，启动卡会先闪一下白屏。
                .background_color(DARK_BG)
                .resizable(false)
                .maximizable(false)
                .minimizable(false)
                .decorations(false)
                .center()
                .build()?;

            // 后台启动全栈；就绪后打开主窗口。
            let boot_handle = handle.clone();
            std::thread::spawn(move || boot_sequence(boot_handle));

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status,
            restart_service,
            recent_logs,
            get_boot_state,
            open_app_window,
            open_console_window,
            quit_app,
            splash_finished,
            logs_dir_path,
            app_version,
            port_health,
            release_ports,
            restart_to_default,
            data_root_info,
            choose_data_root,
            apply_data_root,
            reset_data_root,
            restart_app,
            uninstall_app
        ])
        .on_menu_event(|app, event| match event.id().as_ref() {
            // 主窗口加载的是 nginx 托管的 Web 站点（远程来源拿不到 Tauri IPC），
            // 所以管理入口放在原生菜单里，由 Rust 直接开诊断窗口。
            "menu:console" => {
                let _ = open_console_window(app.clone());
            }
            "menu:quit" => app.exit(0),
            _ => {}
        })
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

/// 后台启动序列：先处理待办的数据迁移，再并行拉起模型下载，最后串行启动服务。
fn boot_sequence(handle: AppHandle) {
    let orc = handle.state::<Arc<Orchestrator>>().inner().clone();
    let store = handle.state::<Arc<datadir::DataRootState>>().inner().clone();

    let info = store.info();
    if info.blocked {
        // 配置的数据位置不可用时不能偷偷在默认位置新建一套数据，宁可不启动并提示救援入口。
        orc.report_boot_error(
            info.last_error
                .as_deref()
                .unwrap_or("存储位置不可用，已暂停启动；请在诊断窗口更改存储位置"),
        );
        return;
    }
    if store.has_pending() {
        // 成功会重启进程，不返回；失败已回退旧配置，继续用旧位置启动。
        datadir::run_pending_migration(&handle, &store, &orc);
    }

    // 模型下载（清单缺失则跳过）。放在独立 tokio 任务，与本地服务启动并行。
    if let Ok(entries) = downloader::load_manifest(&orc.paths().model_manifest()) {
        if !entries.is_empty() {
            let models_dir = orc.paths().models_dir();
            let app_for_dl = handle.clone();
            tauri::async_runtime::spawn(async move {
                downloader::ensure_models(app_for_dl, entries, models_dir).await;
            });
        }
    }

    match orc.start_all() {
        Ok(()) => {
            // 先等启动卡把百分比补满并自报收尾（最多 SPLASH_FINISH_WAIT），再开主窗口：
            // 顺序反过来会让主窗口直接盖在还没跑满的进度条上，用户看不到「跑完」。
            boot_trace("start_all ok -> 等启动卡收尾");
            let t = std::time::Instant::now();
            wait_splash_finished();
            boot_trace(&format!("收尾等待 {}ms", t.elapsed().as_millis()));
            if let Err(e) = open_main_window(&handle) {
                orc.report_boot_error(&format!("打开主窗口失败：{e}"));
                return;
            }
            // 主界面已经起来，启动卡就该消失——不是留在旁边当第二个窗口。
            close_splash(&handle);
        }
        Err(e) => {
            let (_kind, msg) = e.to_ui();
            orc.report_boot_error(&msg);
        }
    }
}

/// 关掉启动卡。此时主窗口已在建，故 close 触发的「所有窗口关闭即退出」不会误杀进程。
fn close_splash(handle: &AppHandle) {
    if let Some(w) = handle.get_webview_window("panel") {
        let _ = w.close();
    }
}

/// 等启动卡自报收尾完成（百分比补满 100% 并停留 0.35s），最多等 `SPLASH_FINISH_WAIT`。
fn wait_splash_finished() {
    let started = std::time::Instant::now();
    while started.elapsed() < SPLASH_FINISH_WAIT {
        if SPLASH_DONE.load(std::sync::atomic::Ordering::SeqCst) {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
}

/// 主窗口菜单：常驻的「服务与存储」入口（数据位置迁移、端口健康、日志、卸载）。
fn build_main_menu(handle: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let console = MenuItem::with_id(
        handle,
        "menu:console",
        "服务与存储…",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(handle, "menu:quit", "退出", true, Some("CmdOrCtrl+Q"))?;
    let separator = PredefinedMenuItem::separator(handle)?;
    let app_menu = Submenu::with_items(
        handle,
        "应用",
        true,
        &[&console, &separator, &quit],
    )?;
    Menu::with_items(handle, &[&app_menu])
}

/// 创建（或聚焦）加载前端的主窗口。
fn open_main_window(handle: &AppHandle) -> Result<(), String> {
    if let Some(w) = handle.get_webview_window("main") {
        let _ = w.set_focus();
        return Ok(());
    }
    let addr = app_url(handle.state::<Arc<Orchestrator>>().web_port());
    let url: url::Url = addr.parse().map_err(|_| format!("无效地址：{addr}"))?;
    let window = WebviewWindowBuilder::new(handle, "main", WebviewUrl::External(url))
        .title("AIPDD DramaClaw")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1024.0, 680.0)
        .resizable(true)
        .focused(true)
        .build()
        .map_err(|e| e.to_string())?;
    // 菜单只是管理入口，挂载失败不能挡住主界面。
    if let Ok(menu) = build_main_menu(handle) {
        let _ = window.set_menu(menu);
    }
    Ok(())
}

// ---------- Tauri 命令（面板 / 主窗口可调用）----------

#[tauri::command]
fn get_status(orc: State<'_, Arc<Orchestrator>>) -> Vec<ServiceState> {
    orc.snapshot()
}

/// 诊断窗口用：取最近 limit 行日志。窗口是在启动失败之后才创建的，此前的日志事件已经
/// 错过，靠这份内存尾巴才能看到失败现场（磁盘上 app-data/logs/*.log 是同批内容）。
#[tauri::command]
fn recent_logs(orc: State<'_, Arc<Orchestrator>>, limit: Option<u32>) -> Vec<LogLine> {
    orc.recent_logs(limit.unwrap_or(500) as usize)
}

/// 诊断窗口用：回看最后一条启动阶段事件（当前阶段/进度，或失败原因）。
#[tauri::command]
fn get_boot_state(orc: State<'_, Arc<Orchestrator>>) -> Option<BootEvent> {
    orc.last_boot()
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

/// 打开（或聚焦）运行期诊断窗口：服务卡片 + 端口健康 + 存储位置 + 日志 + 卸载。
/// 两个入口：主窗口菜单「服务与存储…」；启动失败时启动卡的「查看详细信息」。
#[tauri::command]
fn open_console_window(handle: AppHandle) -> Result<(), String> {
    if let Some(w) = handle.get_webview_window("console") {
        let _ = w.set_focus();
        return Ok(());
    }
    WebviewWindowBuilder::new(&handle, "console", WebviewUrl::App("console.html".into()))
        .title("AIPDD DramaClaw · 服务状态")
        .inner_size(760.0, 860.0)
        .background_color(DARK_BG)
        .min_inner_size(520.0, 560.0)
        .resizable(true)
        .focused(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 启动卡右上角的「×」：无边框窗口没有系统标题栏可关，失败或卡住时得留一条退出路，
/// 否则进程会挂在后台无法结束。走 app.exit 才能触发 RunEvent::Exit 的子进程回收。
#[tauri::command]
fn quit_app(handle: AppHandle) {
    handle.exit(0);
}

/// 启动卡在百分比补满并停留后通知壳：可以换屏了。主窗口的出现时机由这个信号决定，
/// 而不是固定 sleep——就绪那一刻进度条落后多少取决于真实进度走到了哪一段。
#[tauri::command]
fn splash_finished() {
    SPLASH_DONE.store(true, std::sync::atomic::Ordering::SeqCst);
    boot_trace("收到 splash_finished");
}

/// 只有 debug 构建才打印：定位启动卡换屏时机用，release 里整条链路零开销。
fn boot_trace(msg: &str) {
    #[cfg(debug_assertions)]
    eprintln!("[boot] {msg}");
    #[cfg(not(debug_assertions))]
    let _ = msg;
}

#[tauri::command]
fn logs_dir_path(orc: State<'_, Arc<Orchestrator>>) -> String {
    orc.paths().logs_dir().display().to_string()
}

#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// 诊断窗口：受管端口的健康状况（空闲 / 我方 / 外部占用 + 占用者）。
#[tauri::command]
fn port_health(orc: State<'_, Arc<Orchestrator>>) -> Vec<portadmin::PortStatus> {
    let orc = orc.inner();
    let managed = orc.managed_pids();
    portadmin::WATCH_PORTS
        .iter()
        .map(|(name, port)| {
            let ls = portadmin::find_listeners(*port);
            let mut ours = false;
            let mut ext: Vec<String> = Vec::new();
            for l in &ls {
                if managed.contains(&l.pid) {
                    ours = true;
                } else {
                    ext.push(format!("{}({})", l.pid, if l.name.is_empty() { "?" } else { &l.name }));
                }
            }
            let state = if !ext.is_empty() {
                "external"
            } else if ours {
                "ours"
            } else {
                "free"
            };
            portadmin::PortStatus {
                service: (*name).to_string(),
                port: *port,
                state: state.to_string(),
                owner: if ext.is_empty() { None } else { Some(ext.join(", ")) },
            }
        })
        .collect()
}

/// 诊断窗口：一键释放受管端口上的**外部**占用者（我方受管 pid 不动，留给 restart_all 处理）。
#[tauri::command]
fn release_ports(orc: State<'_, Arc<Orchestrator>>) -> Vec<portadmin::ReleaseResult> {
    let orc = orc.inner();
    let managed = orc.managed_pids();
    portadmin::WATCH_PORTS
        .iter()
        .map(|(_n, port)| {
            let mut killed = Vec::new();
            let mut failed = Vec::new();
            for l in portadmin::find_listeners(*port)
                .iter()
                .filter(|l| !managed.contains(&l.pid))
            {
                if portadmin::kill_pid(l.pid) {
                    killed.push(l.pid);
                } else {
                    failed.push(l.pid);
                }
            }
            portadmin::ReleaseResult {
                port: *port,
                killed,
                failed,
            }
        })
        .collect()
}

/// 诊断窗口：释放被占端口后整套重启回默认（web 端口回到 8080），并把主窗口指向新地址。
#[tauri::command]
fn restart_to_default(handle: AppHandle, orc: State<'_, Arc<Orchestrator>>) -> Result<(), String> {
    let orc = orc.inner().clone();
    // 1) 先释放外部占用者（我方受管 pid 交给 restart_all 逐个重启）。
    let managed = orc.managed_pids();
    for (_n, port) in portadmin::WATCH_PORTS.iter() {
        for l in portadmin::find_listeners(*port)
            .iter()
            .filter(|l| !managed.contains(&l.pid))
        {
            portadmin::kill_pid(l.pid);
        }
    }
    // 2) 重置 web 端口回默认并整套重启受管服务（Nginx 重新以 8080 渲染）。
    orc.restart_all().map_err(|e| e.to_string())?;
    // 3) 主窗口指向新地址（无主窗口则开一个）。
    let addr = app_url(orc.web_port());
    let url: url::Url = addr.parse().map_err(|_| format!("无效地址：{addr}"))?;
    if let Some(w) = handle.get_webview_window("main") {
        w.navigate(url).map_err(|e| e.to_string())?;
        let _ = w.set_focus();
    } else {
        open_main_window(&handle)?;
    }
    Ok(())
}

// ---------- 存储位置 / 卸载 ----------

#[tauri::command]
fn data_root_info(store: State<'_, Arc<datadir::DataRootState>>) -> datadir::DataRootInfo {
    store.info()
}

/// 原生文件夹选择器。回调在主线程完成，这里用轮询等待，避免阻塞命令线程。
#[tauri::command]
async fn choose_data_root(handle: AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    handle
        .dialog()
        .file()
        .set_title("选择数据存放位置（建议选空间充足的磁盘）")
        .pick_folder(move |picked| {
            let _ = tx.send(picked);
        });

    let deadline = std::time::Instant::now() + Duration::from_secs(600);
    let picked = loop {
        match rx.try_recv() {
            Ok(p) => break p,
            Err(std::sync::mpsc::TryRecvError::Disconnected) => break None,
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                if std::time::Instant::now() >= deadline {
                    break None;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
    };
    match picked {
        Some(fp) => fp
            .into_path()
            .map(|p| Some(p.display().to_string()))
            .map_err(|e| format!("无法解析所选文件夹：{e}")),
        None => Ok(None),
    }
}

/// 写入切换配置 → 关闭服务 → 重启。迁移模式下本次仍从旧位置启动，
/// 重启后的启动早期完成复制与切换（见 datadir::run_pending_migration）。
#[tauri::command]
fn apply_data_root(
    handle: AppHandle,
    store: State<'_, Arc<datadir::DataRootState>>,
    orc: State<'_, Arc<Orchestrator>>,
    path: String,
    migrate: Option<bool>,
    delete_source: Option<bool>,
) -> Result<(), String> {
    let target = std::path::PathBuf::from(path.trim());
    if path.trim().is_empty() {
        return Err("请选择目标文件夹".into());
    }
    let current = orc.paths().app_data.clone();
    datadir::prepare_switch(
        &store,
        &current,
        &target,
        migrate.unwrap_or(true),
        delete_source.unwrap_or(false),
    )?;
    restart_after_shutdown(handle, orc.inner().clone());
    Ok(())
}

/// 救援入口：自定义位置所在磁盘长期不可用时，先恢复到默认位置让应用能启动。
/// 不迁移也不删除旧数据，旧位置的数据仍在原磁盘。
#[tauri::command]
fn reset_data_root(
    handle: AppHandle,
    store: State<'_, Arc<datadir::DataRootState>>,
    orc: State<'_, Arc<Orchestrator>>,
) -> Result<(), String> {
    datadir::reset_to_default(&store)?;
    restart_after_shutdown(handle, orc.inner().clone());
    Ok(())
}

/// 仅重启应用（例如插回磁盘后重新检测存储位置）。
#[tauri::command]
fn restart_app(handle: AppHandle, orc: State<'_, Arc<Orchestrator>>) {
    restart_after_shutdown(handle, orc.inner().clone());
}

/// Windows 安装版：退出应用并拉起 NSIS 卸载程序（应用数据由卸载器勾选项决定）。
#[tauri::command]
fn uninstall_app(
    handle: AppHandle,
    store: State<'_, Arc<datadir::DataRootState>>,
) -> Result<(), String> {
    #[cfg(windows)]
    {
        let exe = store.info().uninstaller.ok_or_else(|| {
            "未找到卸载程序：开发运行或绿色版请直接删除安装目录。".to_string()
        })?;
        // 先退再卸：用批处理延迟 2 秒启动卸载器，避免它看到本进程仍在运行。
        let script = std::env::temp_dir().join("dramaclaw-uninstall.cmd");
        let body = format!("@echo off\r\ntimeout /t 2 /nobreak >nul\r\nstart \"\" \"{exe}\"\r\n");
        std::fs::write(&script, body).map_err(|e| format!("无法创建卸载脚本：{e}"))?;
        std::process::Command::new("cmd")
            .arg("/C")
            .arg(&script)
            .spawn()
            .map_err(|e| format!("无法启动卸载程序：{e}"))?;
        handle.exit(0);
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = (handle, store);
        Err("仅 Windows 安装版支持应用内卸载；macOS 请将 App 拖入废纸篓。".into())
    }
}

fn restart_after_shutdown(handle: AppHandle, orc: Arc<Orchestrator>) {
    std::thread::spawn(move || {
        orc.shutdown();
        // 留一点时间让前端收到命令返回值并渲染「正在重启」。
        std::thread::sleep(Duration::from_millis(600));
        handle.restart();
    });
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
