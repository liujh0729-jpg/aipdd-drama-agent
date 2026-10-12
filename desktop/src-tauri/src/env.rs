//! 运行时路径与环境变量注入。
//!
//! 两类目录：
//! - `APP_RESOURCE_PATH`：App 内置只读资源（便携 Python、二进制、前端 dist、后端源码）。
//! - `APP_DATA_PATH`：可写业务数据（模型、缓存、SQLite、日志、产物）。
//!
//! 后端本身已高度 env 化（`NOVELVIDEO_*`、`ST_*`、`NEWAPI_BASE_URL`），桌面侧
//! 只需把这些指向 app-data 下对应子目录，无需改动业务代码。
use std::path::{Path, PathBuf};

pub struct RuntimePaths {
    pub app_data: PathBuf,
    /// Tauri `resource_dir()` 返回的 Resources 根。
    pub resource: PathBuf,
}

impl RuntimePaths {
    pub fn new(app_data: PathBuf, resource: PathBuf) -> std::io::Result<Self> {
        let p = Self { app_data, resource };
        // 预建可写目录，避免各服务首次写文件时因目录缺失失败。
        // gateway 必须预建：new-api 以 app-data/gateway 为 cwd 启动（oneapi.db 落此），
        // 目录不存在会让 Command::current_dir 的 spawn 直接失败。
        for sub in ["state", "output", "runtime", "logs", "models", "gateway"] {
            std::fs::create_dir_all(p.app_data.join(sub))?;
        }
        Ok(p)
    }

    /// 解析资源内相对路径。Tauri 打包后资源可能在 `Resources/resources/...`
    /// 或 `Resources/...`（取决于版本对 glob 基路径的处理），两处都探一次，
    /// 谁存在用谁；都不存在时返回首选，用于错误信息展示。
    fn rel(&self, sub: &str) -> PathBuf {
        let nested = self.resource.join("resources").join(sub);
        if nested.exists() {
            return nested;
        }
        let direct = self.resource.join(sub);
        if direct.exists() {
            return direct;
        }
        nested
    }

    /// 内置二进制（nginx、new-api、ffmpeg、chromium、hermes）。
    pub fn bin(&self, name: &str) -> PathBuf {
        self.rel(&format!("bin/{name}"))
    }

    /// 便携 Python 解释器。
    pub fn python(&self) -> PathBuf {
        self.rel("python/bin/python3")
    }

    /// python 的 bin 目录（console script 也在此，但见 orchestrator 注释——
    /// 我们用 `python -c` 方式启动，绕开 shim 里写死的绝对解释器路径）。
    pub fn python_bin_dir(&self) -> PathBuf {
        self.rel("python/bin")
    }

    /// 后端 FastAPI 源码根（打包期拷贝 `src/` 进来），作为 PYTHONPATH。
    pub fn backend_src(&self) -> PathBuf {
        self.rel("app/src")
    }

    /// VectCut 服务源码目录。
    pub fn vectcut_src(&self) -> PathBuf {
        self.rel("app/services/vectcut")
    }

    /// 前端静态站点根目录（vite 产出的 dist）。
    pub fn web_root(&self) -> PathBuf {
        self.rel("app/dist")
    }

    /// 桌面 nginx 主配置源文件。
    pub fn nginx_conf_src(&self) -> PathBuf {
        self.rel("nginx.conf")
    }

    /// 资源内 nginx 目录（含 mime.types 等随附文件）。
    pub fn rel_nginx_dir(&self) -> PathBuf {
        self.rel("nginx")
    }

    /// 网关 SQLite 落盘目录（把网关工作目录设为此处，one-api.db 即落在 app-data）。
    pub fn gateway_dir(&self) -> PathBuf {
        self.app_data.join("gateway")
    }

    pub fn logs_dir(&self) -> PathBuf {
        self.app_data.join("logs")
    }

    pub fn models_dir(&self) -> PathBuf {
        self.app_data.join("models")
    }

    pub fn model_manifest(&self) -> PathBuf {
        self.rel("model-manifest.json")
    }

    /// 供各子进程注入的环境变量（除各自专属项外的公共集合）。
    pub fn base_env(&self) -> Vec<(String, String)> {
        let path_sep = if cfg!(windows) { ';' } else { ':' };
        let extra_path = format!(
            "{}{}{}",
            self.python_bin_dir().display(),
            path_sep,
            self.rel("bin").display()
        );
        let system_path = std::env::var("PATH").unwrap_or_default();

        vec![
            ("APP_DATA_PATH".into(), self.app_data.display().to_string()),
            ("APP_RESOURCE_PATH".into(), self.resource.display().to_string()),
            // 后端数据根：CE inline 模式全部落在 app-data。
            ("NOVELVIDEO_DATA_ROOT".into(), self.app_data.display().to_string()),
            ("NOVELVIDEO_OUTPUT_DIR".into(), self.app_data.join("output").display().to_string()),
            ("NOVELVIDEO_STATE_DIR".into(), self.app_data.join("state").display().to_string()),
            ("NOVELVIDEO_RUNTIME_DIR".into(), self.app_data.join("runtime").display().to_string()),
            // CE 最小多用户，本地 http，不强制 Secure cookie。
            ("ST_EDITION".into(), "ce".into()),
            ("ST_CONTROL_PLANE_DSN".into(), String::new()),
            ("ST_CE_MULTI_USER".into(), "true".into()),
            ("ST_COOKIE_SECURE".into(), "false".into()),
            ("ST_REDIS_URL".into(), String::new()),
            ("ST_CELERY_BROKER_URL".into(), String::new()),
            ("ST_CELERY_RESULT_BACKEND".into(), String::new()),
            // 网关在本机，FastAPI 通过内部端口访问。
            ("NEWAPI_BASE_URL".into(), "http://127.0.0.1:3000".into()),
            // Python 行为。
            ("PYTHONUNBUFFERED".into(), "1".into()),
            ("PYTHONPATH".into(), self.backend_src().display().to_string()),
            ("PATH".into(), format!("{extra_path}{path_sep}{system_path}")),
        ]
    }

    /// 把 base_env 应用到 Command，并保留调用方后续追加的专属变量。
    pub fn apply_env(&self, cmd: &mut std::process::Command) {
        for (k, v) in self.base_env() {
            cmd.env(k, v);
        }
    }
}

/// 存在性检查：确认关键内置二进制齐全（环境自检用）。
pub fn missing_required_bins(paths: &RuntimePaths) -> Vec<String> {
    let mut missing = Vec::new();
    let checks: [(&str, PathBuf); 5] = [
        ("python", paths.python()),
        ("nginx", paths.bin(if cfg!(windows) { "nginx.exe" } else { "nginx" })),
        ("new-api", paths.bin(if cfg!(windows) { "new-api.exe" } else { "new-api" })),
        ("ffmpeg", paths.bin(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" })),
        ("frontend-dist", paths.web_root()),
    ];
    for (name, p) in checks {
        if !p.exists() {
            missing.push(format!("{name} -> {}", strip(&p)));
        }
    }
    missing
}

fn strip(p: &Path) -> String {
    p.display().to_string()
}
