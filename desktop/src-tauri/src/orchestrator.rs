//! 进程编排核心：按序启动、探活、崩溃自愈、日志转发、逆序优雅退出。
//!
//! 设计要点
//! - 子进程一律用 `std::process`（Web 侧无从触发，天然安全）。
//! - `Child` 存于共享 `Supervisor`；监控线程持 `Arc<Orchestrator>` 轮询 `try_wait` 判活并自愈。
//! - 状态/日志/启动进度通过 Tauri 事件广播给状态面板。
//! - 每个子进程独立进程组（Unix）/ 指派进 Job Object（Windows），退出连带回收全树。
use std::collections::HashMap;
use std::io::Write as _;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter};

use crate::env::RuntimePaths;
use crate::error::{DesktopError, Result};
use crate::logs;
use crate::models::*;
use crate::{probe, proc};

/// 冷启动（含便携 Python 首次 import torch）预留较宽松的就绪超时。
const START_TIMEOUT: Duration = Duration::from_secs(180);
/// 崩溃自愈的最大连续重启次数，超过则放弃并置 Crashed，等待人工在面板点“重启”。
const MAX_RESTARTS: u32 = 5;

// 事件名（状态面板监听这些）。
pub const EVT_STATE: &str = "svc://state";
pub const EVT_LOG: &str = "log://line";
pub const EVT_BOOT: &str = "boot://progress";

struct Managed {
    child: Child,
    pid: u32,
    status: ServiceStatus,
    restarts: u32,
    last_error: Option<String>,
}

struct Supervisor {
    services: HashMap<ServiceId, Managed>,
    shutting_down: bool,
    booted: bool,
}

/// Tauri 托管状态。命令（面板/主窗口）通过它驱动编排。
pub struct Orchestrator {
    app: AppHandle,
    paths: Arc<RuntimePaths>,
    shared: Arc<Mutex<Supervisor>>,
    log_tx: Sender<LogLine>,
}

impl Orchestrator {
    pub fn new(app: AppHandle, paths: Arc<RuntimePaths>) -> Arc<Self> {
        let (log_tx, log_rx): (Sender<LogLine>, Receiver<LogLine>) = mpsc::channel();
        let shared = Arc::new(Mutex::new(Supervisor {
            services: HashMap::new(),
            shutting_down: false,
            booted: false,
        }));
        let orc = Arc::new(Self {
            app: app.clone(),
            paths,
            shared: shared.clone(),
            log_tx: log_tx.clone(),
        });
        // 监控线程持 Arc<Self>，复用完整 restart/shutdown 逻辑；随 shutting_down 退出循环释放。
        {
            let o = orc.clone();
            std::thread::spawn(move || monitor_loop(o, log_rx));
        }
        orc
    }

    pub fn paths(&self) -> &Arc<RuntimePaths> {
        &self.paths
    }

    /// 串行启动全套服务。全部非可选服务就绪返回 Ok；否则带结构化错误返回。
    pub fn start_all(&self) -> Result<()> {
        // 1) 环境自检：内置二进制齐全。
        let missing = crate::env::missing_required_bins(&self.paths);
        if !missing.is_empty() {
            return Err(DesktopError::MissingBinary(missing.join("; ")));
        }

        // 2) 端口冲突自检（启动前，任何非可选端口被外部占用即拦下）。
        for spec in SERVICE_SPECS.iter().filter(|s| !s.optional) {
            if probe::port_in_use(spec.port) {
                return Err(DesktopError::PortOccupied(spec.port));
            }
        }

        let ordered = ordered_specs();
        for (idx, spec) in ordered.iter().enumerate() {
            self.emit_boot(
                &format!("start:{}", spec.id.as_str()),
                format!("正在启动 {}…", spec.id.display()),
                idx as f32 / ordered.len() as f32,
            );
            match self.start_one(spec) {
                Ok(()) => {}
                Err(e) => {
                    if spec.optional {
                        // 可选服务失败：记录但不阻断，面板标红可手动重试。
                        self.note_optional_failure(spec.id, &e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        self.shared.lock().unwrap().booted = true;
        self.emit_boot("ready", "全部服务已就绪".into(), 1.0);
        Ok(())
    }

    fn start_one(&self, spec: &ServiceSpec) -> Result<()> {
        let child = self
            .spawn(spec)
            .map_err(|e| map_spawn_err(spec, e))?;
        let pid = child.id();
        proc::assign(&child);

        {
            let mut sup = self.shared.lock().unwrap();
            sup.services.insert(
                spec.id,
                Managed {
                    child,
                    pid,
                    status: ServiceStatus::Starting,
                    restarts: 0,
                    last_error: None,
                },
            );
        }
        self.emit_state_of(spec.id);

        if probe::wait_port_open(spec.port, START_TIMEOUT) {
            if let Some(m) = self.shared.lock().unwrap().services.get_mut(&spec.id) {
                m.status = ServiceStatus::Ready;
            }
            self.emit_state_of(spec.id);
            Ok(())
        } else {
            Err(DesktopError::StartupTimeout {
                service: spec.id.display().to_string(),
                port: spec.port,
                secs: START_TIMEOUT.as_secs(),
            })
        }
    }

    /// 构造并 spawn 一个服务子进程（管道接管输出）。
    fn spawn(&self, spec: &ServiceSpec) -> std::io::Result<Child> {
        let mut cmd = self.build_command(spec.id);
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        proc::configure(&mut cmd);

        let mut child = cmd.spawn()?;
        let log_path = self.paths.logs_dir().join(format!("{}.log", spec.id.as_str()));
        logs::pipe_output(spec.id.as_str(), &mut child, &log_path, &self.log_tx);
        Ok(child)
    }

    fn build_command(&self, id: ServiceId) -> Command {
        let p = &self.paths;
        let mut cmd = match id {
            ServiceId::Nginx => {
                let conf = prepare_nginx_conf(p);
                let prefix = p.app_data.join("nginx");
                let mut c = Command::new(p.bin(&exe("nginx")));
                c.current_dir(&prefix)
                    .arg("-p")
                    .arg(&prefix)
                    .arg("-c")
                    .arg(&conf)
                    .arg("-g")
                    .arg("daemon off;");
                c
            }
            ServiceId::Gateway => {
                let mut c = Command::new(p.bin(&exe("new-api")));
                c.current_dir(p.gateway_dir())
                    .env("PORT", "3000")
                    .env("SQL_DSN", "");
                c
            }
            ServiceId::Vectcut => {
                let mut c = Command::new(p.python());
                c.current_dir(p.vectcut_src())
                    .arg("aipdd_server.py")
                    .env("VECTCUT_PORT", "9001");
                c
            }
            ServiceId::Api => {
                // 关键：用 `python -c` 调 typer app()，绕开 console-script shim
                // 里写死的构建机绝对解释器路径（便携 Python 移动后会失效）。
                // click/typer 读 sys.argv[1:]，`-c` 之后的参数正好被当作子命令。
                let mut c = Command::new(p.python());
                c.current_dir(p.backend_src()).args([
                    "-c",
                    "from novelvideo.cli import app; app()",
                    "api",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8780",
                ]);
                c
            }
        };
        p.apply_env(&mut cmd);
        cmd
    }

    /// 单独重启某个服务（面板按钮 / 崩溃自愈都走这里）。
    pub fn restart_one(&self, id: ServiceId) -> Result<()> {
        let spec = ordered_specs()
            .into_iter()
            .find(|s| s.id == id)
            .ok_or_else(|| DesktopError::MissingBinary(format!("unknown service {id:?}")))?;

        // 先杀旧进程树；提前记下重启次数（old 随后被 terminate 移走）。
        let old = {
            let mut sup = self.shared.lock().unwrap();
            sup.services.remove(&id)
        };
        let prev_restarts = old.as_ref().map(|m| m.restarts).unwrap_or(0);
        if let Some(m) = old {
            terminate_managed(m);
        }

        // 等端口释放（重启竞态保护）。
        let deadline = Instant::now() + Duration::from_secs(10);
        while probe::port_in_use(spec.port) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(200));
        }

        let restarts = prev_restarts + 1;
        let child = self
            .spawn(&spec)
            .map_err(|e| map_spawn_err(&spec, e))?;
        let pid = child.id();
        proc::assign(&child);
        {
            let mut sup = self.shared.lock().unwrap();
            sup.services.insert(
                id,
                Managed {
                    child,
                    pid,
                    status: ServiceStatus::Restarting,
                    restarts,
                    last_error: None,
                },
            );
        }
        self.emit_state_of(id);

        if probe::wait_port_open(spec.port, START_TIMEOUT) {
            if let Some(m) = self.shared.lock().unwrap().services.get_mut(&id) {
                m.status = ServiceStatus::Ready;
            }
            self.emit_state_of(id);
            Ok(())
        } else {
            if let Some(m) = self.shared.lock().unwrap().services.get_mut(&id) {
                m.status = ServiceStatus::Crashed;
                m.last_error = Some("重启后未在超时内就绪".into());
            }
            self.emit_state_of(id);
            Err(DesktopError::StartupTimeout {
                service: spec.id.display().to_string(),
                port: spec.port,
                secs: START_TIMEOUT.as_secs(),
            })
        }
    }

    /// 全部服务的当前状态快照（按启动顺序）。
    pub fn snapshot(&self) -> Vec<ServiceState> {
        let sup = self.shared.lock().unwrap();
        ordered_specs()
            .into_iter()
            .map(|spec| match sup.services.get(&spec.id) {
                Some(m) => ServiceState {
                    id: spec.id.as_str().to_string(),
                    display: spec.id.display().to_string(),
                    port: spec.port,
                    status: m.status,
                    pid: Some(m.pid),
                    restarts: m.restarts,
                    optional: spec.optional,
                    last_error: m.last_error.clone(),
                },
                None => ServiceState {
                    id: spec.id.as_str().to_string(),
                    display: spec.id.display().to_string(),
                    port: spec.port,
                    status: ServiceStatus::Stopped,
                    pid: None,
                    restarts: 0,
                    optional: spec.optional,
                    last_error: None,
                },
            })
            .collect()
    }

    /// 是否已完成整套启动（预留：面板/主窗口可据此 gating；当前无调用方）。
    #[allow(dead_code)]
    pub fn is_booted(&self) -> bool {
        self.shared.lock().unwrap().booted
    }

    /// 逆序优雅终止全部子进程；父进程退出前必调用，杜绝端口残留。
    pub fn shutdown(&self) {
        {
            let mut sup = self.shared.lock().unwrap();
            sup.shutting_down = true;
        }
        let mut ordered = ordered_specs();
        ordered.sort_by_key(|s| std::cmp::Reverse(s.order));
        for spec in ordered {
            let m = {
                let mut sup = self.shared.lock().unwrap();
                sup.services.remove(&spec.id)
            };
            if let Some(m) = m {
                terminate_managed(m);
            }
        }
        proc::shutdown_all();
    }

    // ---- 事件广播 ----
    fn emit_state_of(&self, id: ServiceId) {
        if let Some(st) = self.snapshot().into_iter().find(|s| s.id == id.as_str()) {
            let _ = self.app.emit(EVT_STATE, st);
        }
    }

    fn emit_boot(&self, stage: &str, message: String, progress: f32) {
        let _ = self.app.emit(
            EVT_BOOT,
            BootEvent { stage: stage.into(), message, progress },
        );
    }

    fn note_optional_failure(&self, id: ServiceId, err: &DesktopError) {
        if let Some(m) = self.shared.lock().unwrap().services.get_mut(&id) {
            m.status = ServiceStatus::Crashed;
            m.last_error = Some(err.to_string());
        }
        let _ = self.app.emit(
            EVT_LOG,
            LogLine {
                service: id.as_str().to_string(),
                stream: "system".into(),
                line: format!("可选服务启动失败：{err}"),
                ts: logs::now_ms(),
            },
        );
        self.emit_state_of(id);
    }
}

fn map_spawn_err(spec: &ServiceSpec, e: std::io::Error) -> DesktopError {
    match e.kind() {
        std::io::ErrorKind::PermissionDenied => {
            DesktopError::Permission(format!("无法执行 {}: {e}", spec.id.display()))
        }
        std::io::ErrorKind::NotFound => DesktopError::MissingBinary(spec.id.as_str().to_string()),
        _ => DesktopError::Io(e),
    }
}

/// 优雅停一棵进程树：SIGTERM → 最多等 2s → SIGKILL + kill()。
fn terminate_managed(mut m: Managed) {
    proc::terminate_tree(m.pid);
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if let Ok(Some(_)) = m.child.try_wait() {
            break;
        }
        if Instant::now() >= deadline {
            proc::force_kill_tree(m.pid);
            let _ = m.child.kill();
            let _ = m.child.wait();
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn ordered_specs() -> Vec<ServiceSpec> {
    let mut v: Vec<ServiceSpec> = SERVICE_SPECS.to_vec();
    v.sort_by_key(|s| s.order);
    v
}

fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

/// 渲染桌面版 nginx 配置：把模板占位替换为 app-data 实际路径后落到 prefix。
fn prepare_nginx_conf(p: &RuntimePaths) -> PathBuf {
    let prefix = p.app_data.join("nginx");
    let _ = std::fs::create_dir_all(prefix.join("logs"));
    let _ = std::fs::create_dir_all(prefix.join("temp"));
    let _ = std::fs::create_dir_all(prefix.join("client_body"));

    let template = std::fs::read_to_string(p.nginx_conf_src()).unwrap_or_default();
    // 配置内的路径值统一转正斜杠：Windows 下反斜杠会被 nginx 当转义符（见 env::nginx_path_str）。
    let rendered = template
        .replace("__PREFIX__", &crate::env::nginx_path_str(&prefix))
        .replace("__WEB_ROOT__", &crate::env::nginx_path_str(&p.web_root()))
        .replace(
            "__RESOURCE_NGINX__",
            &crate::env::nginx_path_str(&p.rel_nginx_dir()),
        )
        .replace("__BACKEND_PORT__", "8780")
        .replace("__LISTEN_PORT__", "8080");
    let out = prefix.join("nginx.conf");
    if let Ok(mut f) = std::fs::File::create(&out) {
        let _ = f.write_all(rendered.as_bytes());
    }
    out
}

/// 监控线程：转发日志 + 判活 + 自愈重启。
fn monitor_loop(orc: Arc<Orchestrator>, log_rx: Receiver<LogLine>) {
    loop {
        // 1) 批量转发日志（每轮最多 256 行，降低 IPC 频次）。
        let mut batch: Vec<LogLine> = Vec::with_capacity(256);
        while let Ok(line) = log_rx.try_recv() {
            batch.push(line);
            if batch.len() >= 256 {
                break;
            }
        }
        if !batch.is_empty() {
            let _ = orc.app.emit(EVT_LOG, LogBatch { lines: batch });
        }

        // 2) 判活：锁内仅标记崩溃并挑出需重启者。
        let mut to_restart: Vec<ServiceId> = Vec::new();
        {
            let mut sup = orc.shared.lock().unwrap();
            if sup.shutting_down {
                break;
            }
            for (id, m) in sup.services.iter_mut() {
                if matches!(m.status, ServiceStatus::Ready | ServiceStatus::Starting) {
                    if let Ok(Some(_)) = m.child.try_wait() {
                        m.status = ServiceStatus::Crashed;
                        m.last_error = Some("进程意外退出".into());
                        if m.restarts < MAX_RESTARTS {
                            to_restart.push(*id);
                        }
                    }
                }
            }
        }
        for id in &to_restart {
            orc.emit_state_of_pub(*id);
        }

        // 3) 锁外重启，带线性退避。
        for id in to_restart {
            let backoff = Duration::from_millis(500 * (1 + orc.restarts_of(id) as u64));
            std::thread::sleep(backoff);
            let _ = orc.restart_one(id);
        }

        std::thread::sleep(Duration::from_millis(500));
    }
}

// 监控线程需要访问私有方法的少量桥接。
impl Orchestrator {
    fn emit_state_of_pub(&self, id: ServiceId) {
        self.emit_state_of(id)
    }
    fn restarts_of(&self, id: ServiceId) -> u32 {
        self.shared
            .lock()
            .unwrap()
            .services
            .get(&id)
            .map(|m| m.restarts)
            .unwrap_or(0)
    }
}

/// 批量日志事件负载（一次 emit 多行）。
#[derive(serde::Serialize, Clone)]
struct LogBatch {
    lines: Vec<LogLine>,
}
