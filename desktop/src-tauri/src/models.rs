//! 桌面壳内部的数据模型：服务规格、运行态、日志行、下载进度。
//! 这些结构同时是发给状态面板（Webview）的事件负载，需 `Serialize`。
use serde::{Deserialize, Serialize};

/// 受管服务标识。启动顺序即 `order` 升序。
#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Hash, Debug)]
#[serde(rename_all = "kebab-case")]
pub enum ServiceId {
    Nginx,
    Gateway,
    Vectcut,
    Api,
}

impl ServiceId {
    pub fn as_str(self) -> &'static str {
        match self {
            ServiceId::Nginx => "nginx",
            ServiceId::Gateway => "gateway",
            ServiceId::Vectcut => "vectcut",
            ServiceId::Api => "api",
        }
    }

    pub fn display(self) -> &'static str {
        match self {
            ServiceId::Nginx => "Nginx 反代 / 前端",
            ServiceId::Gateway => "newapi 网关 (Go)",
            ServiceId::Vectcut => "VectCut 剪辑 (Python)",
            ServiceId::Api => "FastAPI 主服务 (Python)",
        }
    }
}

/// 服务的静态规格（不随运行变化）。
#[derive(Clone, Debug)]
pub struct ServiceSpec {
    pub id: ServiceId,
    /// 启动顺序，值小者先起。
    pub order: u8,
    /// 探活端口。
    pub port: u16,
    /// 可选服务：起失败不阻断整体就绪（如 VectCut，compose 里默认不启动）。
    pub optional: bool,
}

pub const SERVICE_SPECS: [ServiceSpec; 4] = [
    ServiceSpec { id: ServiceId::Nginx, order: 10, port: 8080, optional: false },
    ServiceSpec { id: ServiceId::Gateway, order: 20, port: 3000, optional: false },
    // VectCut 为 Python 服务且默认关，纳入 optional，失败不影响就绪。
    ServiceSpec { id: ServiceId::Vectcut, order: 30, port: 9001, optional: true },
    ServiceSpec { id: ServiceId::Api, order: 40, port: 8780, optional: false },
];

/// 服务运行状态机。
#[derive(Serialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "kebab-case")]
pub enum ServiceStatus {
    Stopped,
    Starting,
    Ready,
    Crashed,
    Restarting,
}

/// 单个服务的实时运行态快照，发给面板。
#[derive(Serialize, Clone, Debug)]
pub struct ServiceState {
    pub id: String,
    pub display: String,
    pub port: u16,
    pub status: ServiceStatus,
    pub pid: Option<u32>,
    pub restarts: u32,
    pub optional: bool,
    pub last_error: Option<String>,
}

/// 一行子进程输出（stdout/stderr）。
#[derive(Serialize, Clone, Debug)]
pub struct LogLine {
    pub service: String,
    /// "stdout" | "stderr"
    pub stream: String,
    pub line: String,
    /// unix 毫秒时间戳
    pub ts: u64,
}

/// 整体启动阶段事件（进度提示用）。
#[derive(Serialize, Clone, Debug)]
pub struct BootEvent {
    pub stage: String,
    pub message: String,
    /// 0.0 ~ 1.0
    pub progress: f32,
}

/// 模型下载进度事件。
#[derive(Serialize, Clone, Debug)]
pub struct DownloadProgress {
    pub file: String,
    pub downloaded: u64,
    pub total: Option<u64>,
    /// 字节/秒
    pub rate: f64,
    pub done: bool,
    pub error: Option<String>,
}
