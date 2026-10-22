//! 桌面壳错误类型：面向用户的启动异常在此归类，便于给出友好提示。
use thiserror::Error;

#[derive(Error, Debug)]
pub enum DesktopError {
    /// 端口被其它进程占用（非本项目残留），启动前置检查会拦下。
    #[error("端口 {0} 已被占用，请关闭占用该端口的程序后重试。")]
    PortOccupied(u16),

    /// App 资源目录内缺少内置二进制（打包不完整 / 架构不匹配 / 被杀软删除）。
    #[error("缺少内置可执行文件：{0}。可能是安装包损坏或架构不匹配，请重新安装。")]
    MissingBinary(String),

    /// 依赖服务在超时窗口内未就绪。
    #[error("服务 {service}（端口 {port}）在 {secs}s 内未就绪。{detail}")]
    StartupTimeout {
        service: String,
        port: u16,
        secs: u64,
        detail: String,
    },

    /// 子进程在端口就绪前退出（端口冲突 / 配置错误 / 安全软件拦截）。
    /// 必须立刻报错：等满超时只会让用户对着空进度条卡几分钟。
    #[error("服务 {service} 启动后立即退出（{code}）。{detail}")]
    ServiceExited {
        service: String,
        code: String,
        detail: String,
    },

    /// 权限不足：无法在 app-data 写文件，或无法执行某二进制。
    #[error("权限不足：{0}")]
    Permission(String),

    #[error(transparent)]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    Json(#[from] serde_json::Error),

    #[error("模型下载失败：{0}")]
    Download(String),
}

pub type Result<T> = std::result::Result<T, DesktopError>;

impl DesktopError {
    /// 返回给前端的结构化错误：kind 供 UI 分图标/按钮，message 为人话。
    pub fn to_ui(&self) -> (String, String) {
        let kind = match self {
            DesktopError::PortOccupied(_) => "port-occupied",
            DesktopError::MissingBinary(_) => "missing-binary",
            DesktopError::StartupTimeout { .. } => "startup-timeout",
            DesktopError::ServiceExited { .. } => "service-exited",
            DesktopError::Permission(_) => "permission",
            DesktopError::Download(_) => "download",
            DesktopError::Io(_) => "io",
            DesktopError::Json(_) => "json",
        };
        (kind.to_string(), self.to_string())
    }
}
