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
use std::sync::atomic::{AtomicU16, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter};

use crate::env::RuntimePaths;
use crate::error::{DesktopError, Result};
use crate::logs;
use crate::models::*;
use crate::{probe, proc};

/// FastAPI 冷启动（含便携 Python 首次 import torch）保留较宽松的就绪超时。
const API_START_TIMEOUT: Duration = Duration::from_secs(180);
/// nginx 是原生二进制，正常 1s 内即可监听。25s 只用于覆盖首次运行时安全软件
/// 扫描未签名程序；超时立刻报错，不再让用户对着 3 分钟的空白进度条。
const NGINX_START_TIMEOUT: Duration = Duration::from_secs(25);
/// Go / Python 辅助服务的就绪预算：比 nginx 宽，但远小于 API 的冷启动预算。
const SERVICE_START_TIMEOUT: Duration = Duration::from_secs(90);
/// nginx 启动失败（端口竞态 / 被安全软件拦截）时最多尝试的端口数。
const NGINX_START_ATTEMPTS: u8 = 2;
/// 失败信息里附带的服务日志行数与单行长度上限，避免错误弹窗变成日志墙。
const LOG_TAIL_LINES: usize = 6;
const LOG_LINE_MAX_CHARS: usize = 240;
/// 崩溃自愈的最大连续重启次数，超过则放弃并置 Crashed，等待人工在面板点“重启”。
const MAX_RESTARTS: u32 = 5;
/// 内存日志尾巴的容量上限，与面板侧缓冲同量级（约 2000 行 ≈ 几百 KB）。
const LOG_TAIL_MAX: usize = 2000;
/// web（nginx）端口被占时往后避让的扫描宽度：8080→…→8089，全无空闲才报错。
const WEB_PORT_SPAN: u16 = 10;

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
    /// start_all / restart_all 期间为 true：此时由编排主流程负责起停与判错，
    /// 监控线程暂停自动重启，避免与「换端口重试 / 全量重试」互相抢进程。
    starting: bool,
    booted: bool,
    /// 最近若干行日志的内存尾巴。诊断窗口是按需才创建的，事件在那之前就流过去了，
    /// 没有这份缓冲的话「查看详细信息」打开时会是空白。
    log_tail: Vec<LogLine>,
    /// 最后一条启动阶段事件，同样供后创建的诊断窗口回看（进度/失败原因 + 排错提示）。
    last_boot: Option<BootEvent>,
}

/// Tauri 托管状态。命令（面板/主窗口）通过它驱动编排。
pub struct Orchestrator {
    app: AppHandle,
    paths: Arc<RuntimePaths>,
    shared: Arc<Mutex<Supervisor>>,
    log_tx: Sender<LogLine>,
    /// 本次会话定格的 web 监听端口：start_all 解析一次（8080 被占则往后避让），
    /// 之后 nginx 渲染/探活/重启/诊断展示全部跟随这个值，主窗口地址同源——
    /// 运行期绝不重新扫描，否则重启后窗口会失联。
    web_port: Arc<AtomicU16>,
    /// 本次 nginx 启动前 `nginx/logs/error.log` 的字节长度。nginx 的 bind / 配置
    /// 错误只写这份文件（不在 stdout 捕获的 nginx.log 里），从该偏移往后读才能
    /// 只取本轮启动新增的报错，不把上次运行的旧错误混进错误详情。
    nginx_error_offset: AtomicU64,
}

impl Orchestrator {
    pub fn new(app: AppHandle, paths: Arc<RuntimePaths>) -> Arc<Self> {
        let (log_tx, log_rx): (Sender<LogLine>, Receiver<LogLine>) = mpsc::channel();
        let shared = Arc::new(Mutex::new(Supervisor {
            services: HashMap::new(),
            shutting_down: false,
            starting: false,
            booted: false,
            log_tail: Vec::new(),
            last_boot: None,
        }));
        let initial_web_port = default_web_port();
        let orc = Arc::new(Self {
            app: app.clone(),
            paths,
            shared: shared.clone(),
            log_tx: log_tx.clone(),
            web_port: Arc::new(AtomicU16::new(initial_web_port)),
            nginx_error_offset: AtomicU64::new(0),
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

    /// 本次会话的 web 监听端口（启动自检后定格）。
    pub fn web_port(&self) -> u16 {
        self.web_port.load(Ordering::SeqCst)
    }

    /// 当前受管子进程的 pid 集合（端口健康判定「我方 vs 外部」用）。
    pub fn managed_pids(&self) -> Vec<u32> {
        self.shared
            .lock()
            .unwrap()
            .services
            .values()
            .map(|m| m.pid)
            .collect()
    }

    /// 把 web 端口重置回默认（释放外部占用后「重启回默认」用：8080 一空就能重新 binds）。
    fn reset_web_port(&self) {
        self.web_port.store(default_web_port(), Ordering::SeqCst);
    }

    /// 释放完默认端口后整套重启：按启动顺序逐个 restart_one（Nginx 会用重置后的
    /// web_port 重新渲染 8080）。复用 restart_one，不走 shutdown，监控线程不受影响。
    /// 非可选服务任一重启失败即中断；可选服务（VectCut）失败忽略。
    pub fn restart_all(&self) -> Result<()> {
        self.reset_web_port();
        self.shared.lock().unwrap().starting = true;
        let mut result = Ok(());
        for spec in ordered_specs() {
            if spec.optional {
                let _ = self.restart_one(spec.id);
                continue;
            }
            if let Err(e) = self.restart_one(spec.id) {
                result = Err(e);
                break;
            }
        }
        self.shared.lock().unwrap().starting = false;
        result
    }

    /// Nginx 的端口是启动时动态解析的，其余服务照抄静态规格；
    /// 探活/渲染/展示等所有消费方统一走这里拿「真实端口」。
    fn resolve_spec(&self, spec: &ServiceSpec) -> ServiceSpec {
        if spec.id == ServiceId::Nginx {
            let mut s = spec.clone();
            s.port = self.web_port();
            s
        } else {
            spec.clone()
        }
    }

    /// 串行启动全套服务。全部非可选服务就绪返回 Ok；否则带结构化错误返回。
    pub fn start_all(&self) -> Result<()> {
        {
            let mut sup = self.shared.lock().unwrap();
            sup.starting = true;
            sup.booted = false;
        }
        let result = self.start_all_inner();
        self.shared.lock().unwrap().starting = false;
        result
    }

    fn start_all_inner(&self) -> Result<()> {
        // 0) 给启动卡一个起点，别让窗口空着像卡死。
        self.emit_boot("check", "正在检查运行环境与端口…", 0.02);

        // 1) 环境自检：内置二进制齐全。
        let missing = crate::env::missing_required_bins(&self.paths);
        if !missing.is_empty() {
            return Err(DesktopError::MissingBinary(missing.join("; ")));
        }

        // 2) 端口冲突自检（启动前）。web（nginx）允许自动避让：默认端口被占就
        //    往后找第一个可绑定端口并定格本次会话；其余非可选服务仍「占用即拦停」
        //    （它们的端口牵动 nginx upstream / 子进程 env，不在本轮改动范围）。
        for spec in SERVICE_SPECS
            .iter()
            .filter(|s| !s.optional && s.id != ServiceId::Nginx)
        {
            if probe::port_in_use(spec.port) {
                return Err(DesktopError::PortOccupied(spec.port));
            }
        }
        let base_web_port = default_web_port();
        let port = probe::find_free_port(base_web_port, WEB_PORT_SPAN)
            .ok_or(DesktopError::PortOccupied(base_web_port))?;
        self.web_port.store(port, Ordering::SeqCst);
        if port != base_web_port {
            self.emit_boot(
                "check",
                format!("端口 {base_web_port} 被占用，站点本次改用 {port}…"),
                0.04,
            );
        }
        self.emit_boot("check", "环境自检通过，正在启动本地服务…", 0.08);

        let ordered = ordered_specs();
        for spec in ordered.iter() {
            let spec = self.resolve_spec(spec);
            self.emit_boot(
                &format!("start:{}", spec.id.as_str()),
                format!("正在启动 {}…", spec.id.display()),
                boot_window(spec.id).0,
            );
            let outcome = if spec.id == ServiceId::Nginx {
                self.start_nginx(&spec)
            } else {
                self.start_one(&spec)
            };
            match outcome {
                Ok(()) => {}
                Err(e) => {
                    if spec.optional {
                        // 可选服务失败：记录但不阻断，诊断窗口标红可手动重试。
                        self.note_optional_failure(spec.id, &e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        self.shared.lock().unwrap().booted = true;
        self.emit_boot("ready", "全部服务已就绪", 1.0);
        Ok(())
    }

    /// nginx 专用启动：失败即暴露；若是端口竞态 / 首启拦截，换下一个空闲端口再试一次。
    /// 只允许在 `start_all` 阶段换端口——那时主窗口还没加载，换端口不会让窗口失联。
    fn start_nginx(&self, spec: &ServiceSpec) -> Result<()> {
        let base = default_web_port();
        let upper = base.saturating_add(WEB_PORT_SPAN);
        let mut last_err: Option<DesktopError> = None;
        self.capture_nginx_error_offset();

        for attempt in 0..NGINX_START_ATTEMPTS {
            let current = self.resolve_spec(spec);
            match self.start_one(&current) {
                Ok(()) => return Ok(()),
                Err(e) => {
                    let retryable = matches!(
                        e,
                        DesktopError::ServiceExited { .. } | DesktopError::StartupTimeout { .. }
                    );
                    last_err = Some(e);
                    if !retryable || attempt + 1 >= NGINX_START_ATTEMPTS {
                        break;
                    }
                    // 超时场景子进程可能还活着，先回收再换端口，避免两套 nginx 抢站点目录。
                    self.discard_service(ServiceId::Nginx);
                    let Some(next) =
                        probe::find_free_port_between(current.port.saturating_add(1), upper)
                    else {
                        break;
                    };
                    self.web_port.store(next, Ordering::SeqCst);
                    self.emit_boot(
                        "check",
                        format!("端口 {} 启动失败，改用 {next} 重试…", current.port),
                        boot_window(ServiceId::Nginx).0,
                    );
                }
            }
        }

        Err(last_err.unwrap_or_else(|| DesktopError::PortOccupied(base)))
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
        self.wait_ready(spec)
    }

    /// 等待端口就绪，同时盯着子进程是否已经退出。
    /// 旧实现只等端口，nginx 秒退也要等满超时；现在秒退立刻带退出码 + 日志尾巴报错。
    fn wait_ready(&self, spec: &ServiceSpec) -> Result<()> {
        let timeout = startup_timeout(spec.id);
        let started = Instant::now();
        loop {
            // 先判进程：若它已退出，哪怕端口恰好被别的进程接走也不能算本服务就绪。
            let exited = {
                let mut sup = self.shared.lock().unwrap();
                match sup.services.get_mut(&spec.id) {
                    Some(m) => match m.child.try_wait() {
                        Ok(Some(status)) => Some(exit_label(status)),
                        _ => None,
                    },
                    None => Some("进程已回收".to_string()),
                }
            };
            if let Some(code) = exited {
                // 给日志读线程一点时间把管道里最后几行落盘。
                std::thread::sleep(Duration::from_millis(120));
                let err = DesktopError::ServiceExited {
                    service: spec.id.display().to_string(),
                    code,
                    detail: log_detail(self.log_tail(spec.id)),
                };
                self.mark_failed(spec.id, &err);
                return Err(err);
            }

            if probe::port_open(spec.port) {
                if let Some(m) = self.shared.lock().unwrap().services.get_mut(&spec.id) {
                    m.status = ServiceStatus::Ready;
                }
                self.emit_state_of(spec.id);
                return Ok(());
            }

            let elapsed = started.elapsed();
            if elapsed >= timeout {
                let err = DesktopError::StartupTimeout {
                    service: spec.id.display().to_string(),
                    port: spec.port,
                    secs: timeout.as_secs(),
                    detail: log_detail(self.log_tail(spec.id)),
                };
                self.mark_failed(spec.id, &err);
                return Err(err);
            }

            // 探活期间按「已耗时 / 该服务期望耗时」渐近推进：端口要等几秒才开的服务
            // （api 冷启动要 import 整套 Python 依赖）不能让百分比整段僵住。
            self.emit_boot(
                &format!("wait:{}", spec.id.as_str()),
                format!("正在启动 {}…", spec.id.display()),
                boot_progress(spec.id, elapsed.as_secs_f32()),
            );
            std::thread::sleep(Duration::from_millis(300));
        }
    }

    fn mark_failed(&self, id: ServiceId, err: &DesktopError) {
        if let Some(m) = self.shared.lock().unwrap().services.get_mut(&id) {
            m.status = ServiceStatus::Crashed;
            m.last_error = Some(err.to_string());
        }
        self.emit_state_of(id);
    }

    /// 丢掉一个受管服务：从状态表移除并回收进程树（重试换端口前用）。
    fn discard_service(&self, id: ServiceId) {
        let m = self.shared.lock().unwrap().services.remove(&id);
        if let Some(m) = m {
            terminate_managed(m);
        }
    }

    /// 读该服务落盘日志的最后若干行，拼进错误信息；读不到就返回空串。
    fn log_tail(&self, id: ServiceId) -> String {
        let path = self.paths.logs_dir().join(format!("{}.log", id.as_str()));
        let raw = std::fs::read(path).unwrap_or_default();
        let mut tail = tail_lines(&raw, LOG_TAIL_LINES);
        if id == ServiceId::Nginx {
            let err_path = self.paths.app_data.join("nginx").join("logs").join("error.log");
            if let Ok(all) = std::fs::read(&err_path) {
                let offset = self.nginx_error_offset.load(Ordering::SeqCst);
                let err_tail = tail_from(&all, offset, LOG_TAIL_LINES);
                if !err_tail.is_empty() {
                    if !tail.is_empty() {
                        tail.push_str(" | ");
                    }
                    tail.push_str("nginx 错误日志：");
                    tail.push_str(&err_tail);
                }
            }
        }
        tail
    }

    /// 记下 nginx error.log 当前长度；启动失败时只展示这之后新增的报错。
    fn capture_nginx_error_offset(&self) {
        let path = self.paths.app_data.join("nginx").join("logs").join("error.log");
        let len = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
        self.nginx_error_offset.store(len, Ordering::SeqCst);
    }

    /// 全部服务停掉但保持编排器可再次启动（启动卡的「重试启动」用）。
    /// 与 shutdown 的区别：不置 shutting_down、不关监控线程，随后还能 start_all。
    pub fn prepare_retry(&self) {
        {
            let mut sup = self.shared.lock().unwrap();
            sup.starting = true;
            sup.booted = false;
        }
        let mut ordered = ordered_specs();
        ordered.sort_by_key(|s| std::cmp::Reverse(s.order));
        for spec in ordered {
            self.discard_service(spec.id);
        }
        self.reset_web_port();
    }

    /// 重试线程异常退出时复位启动闸门；否则监控线程会永久停止崩溃自愈。
    pub fn clear_starting(&self) {
        self.shared.lock().unwrap().starting = false;
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
                let conf = prepare_nginx_conf(p, self.web_port());
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
                let mut c = Command::new(p.bin(&exe("localapi")));
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
                // 与 --host 同值落一份环境变量：后端按 NOVELVIDEO_API_HOST 判定
                // 是否「确认仅回环」，单用户 CE 只在这时才放弃暴露告警。
                c.env("NOVELVIDEO_API_HOST", "127.0.0.1");
                c
            }
        };
        p.apply_env(&mut cmd);
        cmd
    }

    /// 单独重启某个服务（面板按钮 / 崩溃自愈都走这里）。Nginx 复用已定格的
    /// web_port，绝不重新扫描——主窗口已经加载在旧解析值上，换端口会让窗口失联。
    pub fn restart_one(&self, id: ServiceId) -> Result<()> {
        let spec = ordered_specs()
            .into_iter()
            .find(|s| s.id == id)
            .ok_or_else(|| DesktopError::MissingBinary(format!("unknown service {id:?}")))?;
        let spec = self.resolve_spec(&spec);

        if id == ServiceId::Nginx {
            self.capture_nginx_error_offset();
        }

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

        // 运行期重启绝不换端口（主窗口已加载在 web_port 上），但同样要
        // 失败即暴露：进程秒退带日志，最长等待按服务类型收敛。
        self.wait_ready(&spec)
    }

    /// 全部服务的当前状态快照（按启动顺序，端口展示真实值）。
    pub fn snapshot(&self) -> Vec<ServiceState> {
        let sup = self.shared.lock().unwrap();
        ordered_specs()
            .into_iter()
            .map(|s| self.resolve_spec(&s))
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

    /// 最近 `limit` 行日志（时间升序）。诊断窗口打开时用它补齐此前错过的实时事件。
    pub fn recent_logs(&self, limit: usize) -> Vec<LogLine> {
        let sup = self.shared.lock().unwrap();
        let n = sup.log_tail.len().min(limit);
        sup.log_tail[sup.log_tail.len() - n..].to_vec()
    }

    /// 最后一条启动阶段事件（进度或失败原因）；还没发过事件时为 None。
    pub fn last_boot(&self) -> Option<BootEvent> {
        self.shared.lock().unwrap().last_boot.clone()
    }

    /// 启动失败：以 boot 事件的形式报给界面（启动卡转错误态），同时留档给诊断窗口。
    pub fn report_boot_error(&self, message: &str) {
        self.emit_boot("error", message, 0.0);
    }

    /// 启动前阶段（如数据迁移）推进启动卡：与 start_all 同一事件通道，进度同样留档。
    pub fn report_boot(&self, stage: &str, message: impl Into<String>, progress: f32) {
        self.emit_boot(stage, message, progress);
    }

    /// 系统级日志：走独立日志管道，诊断窗口打开时能补看到（不依赖控制台输出）。
    pub fn log_system(&self, line: impl Into<String>) {
        let _ = self.log_tx.send(LogLine {
            service: "system".into(),
            stream: "system".into(),
            line: line.into(),
            ts: logs::now_ms(),
        });
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

    fn emit_boot(&self, stage: &str, message: impl Into<String>, progress: f32) {
        let ev = BootEvent { stage: stage.into(), message: message.into(), progress };
        // 留一份：诊断窗口是失败后才创建的，实时事件那一刻它还不存在。
        self.shared.lock().unwrap().last_boot = Some(ev.clone());
        let _ = self.app.emit(EVT_BOOT, ev);
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

fn default_web_port() -> u16 {
    SERVICE_SPECS
        .iter()
        .find(|s| s.id == ServiceId::Nginx)
        .map(|s| s.port)
        .unwrap_or(8080)
}

/// 各服务的就绪预算：nginx 原生二进制必须短，Python 主服务冷启动才需要长预算。
fn startup_timeout(id: ServiceId) -> Duration {
    match id {
        ServiceId::Nginx => NGINX_START_TIMEOUT,
        ServiceId::Api => API_START_TIMEOUT,
        ServiceId::Gateway | ServiceId::Vectcut => SERVICE_START_TIMEOUT,
    }
}

fn exit_label(status: std::process::ExitStatus) -> String {
    match status.code() {
        Some(code) => format!("退出码 {code}"),
        None => "被系统终止".to_string(),
    }
}

fn log_detail(tail: String) -> String {
    if tail.is_empty() {
        String::new()
    } else {
        format!("最近日志：{tail}")
    }
}

/// 从日志原文提取最后 `max` 行非空内容，单行截断，避免错误信息过长。
fn tail_lines(raw: &[u8], max: usize) -> String {
    let text = String::from_utf8_lossy(raw);
    let mut lines: Vec<String> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(|l| l.chars().take(LOG_LINE_MAX_CHARS).collect())
        .collect();
    if lines.len() > max {
        lines.drain(0..lines.len() - max);
    }
    lines.join(" | ")
}

/// 只取 `offset` 之后新写入的内容再抽尾巴；offset 超过文件长度时视为无新增。
fn tail_from(raw: &[u8], offset: u64, max: usize) -> String {
    let start = usize::try_from(offset).unwrap_or(usize::MAX).min(raw.len());
    tail_lines(&raw[start..], max)
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

/// 自检阶段的进度上限（还没开始起服务时也该让条动一下）。
const BOOT_CHECK_TOP: f32 = 0.08;

/// 启动卡进度分段：`(服务, 区间上限, 暖启动实测期望耗时秒)`。
/// 权重按「到这里应该完成什么」给；段内进度按 `已耗时 / 期望耗时` 渐近推进——
/// 服务提前就绪时百分比已经爬到接近上限，而不是整段卡在 45% 等超时再跳 100%。
const BOOT_STAGES: [(ServiceId, f32, f32); 4] = [
    (ServiceId::Nginx, 0.14, 0.4),
    (ServiceId::Gateway, 0.30, 1.5),
    (ServiceId::Vectcut, 0.40, 2.5),
    (ServiceId::Api, 0.97, 10.0),
];

/// 段内渐近系数：耗时=期望值时爬到该区间的 ~70%，2 倍期望时 ~91%，永远不越界。
const BOOT_EASE_K: f32 = 2.2;

/// 某个服务的进度区间 `(起点, 终点)`。
fn boot_window(id: ServiceId) -> (f32, f32) {
    let i = BOOT_STAGES
        .iter()
        .position(|(s, _, _)| *s == id)
        .unwrap_or(0);
    let prev_top = if i == 0 { BOOT_CHECK_TOP } else { BOOT_STAGES[i - 1].1 };
    (prev_top, BOOT_STAGES[i].1)
}

/// 某个服务的期望耗时；未登记的服务给一个中庸值。
fn boot_expectation(id: ServiceId) -> f32 {
    BOOT_STAGES
        .iter()
        .find(|(s, _, _)| *s == id)
        .map(|(_, _, e)| *e)
        .unwrap_or(5.0)
}

/// 已等待 `elapsed` 秒后的绝对进度，钉在区间内。
fn boot_progress(id: ServiceId, elapsed: f32) -> f32 {
    let win = boot_window(id);
    let ratio = (elapsed / boot_expectation(id)).max(0.0);
    let eased = (1.0 - (-BOOT_EASE_K * ratio).exp()).clamp(0.0, 1.0);
    (win.0 + (win.1 - win.0) * eased).clamp(0.0, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn boot_windows_are_ordered_and_bounded() {
        let nginx = boot_window(ServiceId::Nginx);
        let gateway = boot_window(ServiceId::Gateway);
        let vectcut = boot_window(ServiceId::Vectcut);
        let api = boot_window(ServiceId::Api);
        // 区间首尾相接、单调递增、终点给 ready 留位置。
        assert_eq!(nginx.0, BOOT_CHECK_TOP);
        assert_eq!(nginx.1, gateway.0);
        assert_eq!(gateway.1, vectcut.0);
        assert_eq!(vectcut.1, api.0);
        assert!(api.1 < 1.0);
        // 最慢的那段（api 冷启动）应占最大权重。
        assert!(api.1 - api.0 > gateway.1 - gateway.0);
    }

    #[test]
    fn boot_progress_rather_than_stalls_before_the_timeout() {
        // 旧模型按「离统一超时多久」算，服务 2s 就就绪时百分比只爬到区间底。
        // 新模型按各服务期望耗时爬：耗时=1×期望就该走完区间的六成以上。
        for sid in [
            ServiceId::Nginx,
            ServiceId::Gateway,
            ServiceId::Vectcut,
            ServiceId::Api,
        ] {
            let win = boot_window(sid);
            let exp = boot_expectation(sid);
            assert_eq!(boot_progress(sid, 0.0), win.0);
            let at_expect = boot_progress(sid, exp);
            assert!(
                at_expect > win.0 + (win.1 - win.0) * 0.6,
                "{:?} 在期望耗时时进度过慢：{}（区间 {:?}）",
                sid,
                at_expect,
                win
            );
            // 单调、不越界、负数不往回拉。
            assert!(boot_progress(sid, exp * 3.0) < win.1);
            assert!(boot_progress(sid, exp * 3.0) > boot_progress(sid, exp));
            assert!(boot_progress(sid, -5.0) >= win.0);
        }
    }

    #[test]
    fn nginx_gets_a_short_budget_while_api_keeps_the_cold_start_budget() {
        // nginx 秒级可起：超过 30s 仍未监听就该报错，而不是拖满 3 分钟。
        assert!(startup_timeout(ServiceId::Nginx) <= Duration::from_secs(30));
        assert!(startup_timeout(ServiceId::Nginx) < startup_timeout(ServiceId::Gateway));
        // Python 主服务冷启动仍保留长预算。
        assert_eq!(startup_timeout(ServiceId::Api), Duration::from_secs(180));
        assert!(startup_timeout(ServiceId::Vectcut) >= Duration::from_secs(60));
    }

    #[test]
    fn tail_lines_keeps_the_newest_lines_and_caps_width() {
        let mut raw = String::new();
        for i in 0..10 {
            raw.push_str(&format!("line-{i}\n"));
        }
        assert_eq!(tail_lines(raw.as_bytes(), 3), "line-7 | line-8 | line-9");

        let long = "x".repeat(500);
        let out = tail_lines(long.as_bytes(), 2);
        assert_eq!(out.chars().count(), LOG_LINE_MAX_CHARS);

        assert_eq!(tail_lines(b"", 6), "");
        assert_eq!(tail_lines(b"\n\n  \n", 6), "");
    }

    #[test]
    fn tail_from_skips_bytes_written_before_the_attempt() {
        let raw = b"old error\nnew error-1\nnew error-2\n";
        let offset = b"old error\n".len() as u64;
        assert_eq!(tail_from(raw, offset, 2), "new error-1 | new error-2");
        // offset 落在文件末尾之后（例如日志被轮转清空）不 panic，按无新增处理。
        assert_eq!(tail_from(raw, raw.len() as u64 + 64, 2), "");
        assert_eq!(tail_from(raw, 0, 1), "new error-2");
    }
}

fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

/// 渲染桌面版 nginx 配置：把模板占位替换为 app-data 实际路径后落到 prefix。
/// `listen_port` 用启动自检解析并定格的 web 端口（模板里 CSP 的 connect-src
/// 共用同一占位符，端口变更后自动跟随，不存在第二处写死）。
fn prepare_nginx_conf(p: &RuntimePaths, listen_port: u16) -> PathBuf {
    let prefix = p.app_data.join("nginx");
    // nginx 需要的所有临时目录：缺一则启动报 [alert] cannot create temp dir
    let _ = std::fs::create_dir_all(prefix.join("logs"));
    let _ = std::fs::create_dir_all(prefix.join("client_body"));
    let _ = std::fs::create_dir_all(prefix.join("temp").join("proxy"));
    let _ = std::fs::create_dir_all(prefix.join("temp").join("fastcgi"));
    let _ = std::fs::create_dir_all(prefix.join("temp").join("uwsgi"));
    let _ = std::fs::create_dir_all(prefix.join("temp").join("scgi"));

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
        .replace("__LISTEN_PORT__", &listen_port.to_string());
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
            {
                // 先入内存尾巴（诊断窗口按需创建，事件不会回放），再广播。
                let mut sup = orc.shared.lock().unwrap();
                let over = sup.log_tail.len() + batch.len();
                sup.log_tail.extend(batch.iter().cloned());
                if over > LOG_TAIL_MAX {
                    sup.log_tail.drain(0..over - LOG_TAIL_MAX);
                }
            }
            let _ = orc.app.emit(EVT_LOG, LogBatch { lines: batch });
        }

        // 2) 判活：锁内仅标记崩溃并挑出需重启者。
        //    启动 / 全量重启期间跳过：主流程正在自己起停并判错，避免两边抢进程。
        let mut to_restart: Vec<ServiceId> = Vec::new();
        {
            let mut sup = orc.shared.lock().unwrap();
            if sup.shutting_down {
                break;
            }
            if !sup.starting {
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
