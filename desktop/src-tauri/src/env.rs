//! 运行时路径与环境变量注入。
//!
//! 两类目录：
//! - `APP_RESOURCE_PATH`：App 内置只读资源（便携 Python、二进制、前端 dist、后端源码）。
//! - `APP_DATA_PATH`：可写业务数据（模型、缓存、SQLite、日志、产物）。
//!
//! 后端本身已高度 env 化（`NOVELVIDEO_*`、`ST_*`、`LOCALAPI_BASE_URL`），桌面侧
//! 只需把这些指向 app-data 下对应子目录，无需改动业务代码。
use std::path::{Path, PathBuf};

pub struct RuntimePaths {
    pub app_data: PathBuf,
    /// Tauri `resource_dir()` 返回的 Resources 根。
    pub resource: PathBuf,
}

impl RuntimePaths {
    pub fn new(app_data: PathBuf, resource: PathBuf) -> std::io::Result<Self> {
        let p = Self {
            app_data: native_path(app_data),
            resource: native_path(resource),
        };
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

    /// 便携 Python 解释器。两种发布布局不同（prepare-resources.sh 同源约定）：
    /// - Unix（python-build-standalone）：`python/bin/python3` + `python/lib/python3.12/site-packages`
    /// - Windows：`python\python.exe` + `python\Lib\site-packages`（没有 `bin/` 这一层）
    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.rel("python/python.exe")
        } else {
            self.rel("python/bin/python3")
        }
    }

    /// Python 可执行文件与 console script 所在目录，按序供 PATH 前置。
    /// Windows 需要 `python\`（放 python.exe / python312.dll / vcruntime DLL）和
    /// `python\Scripts\`（pip 等入口点）；Unix 两者同为 `python/bin`。
    pub fn python_path_dirs(&self) -> Vec<PathBuf> {
        if cfg!(windows) {
            vec![self.rel("python"), self.rel("python/Scripts")]
        } else {
            vec![self.rel("python/bin")]
        }
    }

    /// 后端 FastAPI 源码根（打包期拷贝 `src/` 进来），作为 PYTHONPATH。
    pub fn backend_src(&self) -> PathBuf {
        self.rel("app/src")
    }

    /// 随包 Hermes 运行时依赖目录（`uv pip install --target` 的产物）。
    /// 不是 venv：`pyvenv.cfg` 里的 base 解释器是绝对路径，整包挪位置会失效。
    pub fn hermes_site_packages(&self) -> PathBuf {
        self.rel("hermes/site-packages")
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
        let path_sep = if cfg!(windows) { ";" } else { ":" };
        // PATH 前置：便携 Python（解释器目录 + Scripts）、随附二进制目录（nginx/ffmpeg/chrome）。
        let mut path_dirs = self.python_path_dirs();
        path_dirs.push(self.rel("bin"));
        let extra_path = path_dirs
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(path_sep);
        let system_path = std::env::var("PATH").unwrap_or_default();

        let mut env = vec![
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
            ("LOCALAPI_BASE_URL".into(), "http://127.0.0.1:3000".into()),
            ("LOCALAPI_ADMIN_BASE_URL".into(), "http://127.0.0.1:3000".into()),
            // Python 行为。
            ("PYTHONUNBUFFERED".into(), "1".into()),
            // .pyc 一律写进 app-data：安装包内的 resources/ 视为只读。默认行为会在
            // 首次运行时往 site-packages 里生成约 4000 个 __pycache__ 文件，Windows 上
            // 卸载器不认识这些文件（安装目录留残骸），macOS 上更会破坏 .app 签名。
            ("PYTHONPYCACHEPREFIX".into(), self.app_data.join("pycache").display().to_string()),
            ("PYTHONPATH".into(), self.backend_src().display().to_string()),
            // 不让解释器回落到用户目录的 site-packages（Windows AppData、macOS/Linux
            // 的 user site）。user site 在 sys.path 上排在随包 site-packages **之前**，
            // 真机事故：Intel 版 App（x86_64）在 M 系列机上导入了用户 pip --user 装的
            // arm64 tiktoken，架构不匹配 dlopen 失败，FastAPI 直接退出码 1。
            ("PYTHONNOUSERSITE".into(), "1".into()),
            ("PATH".into(), format!("{extra_path}{path_sep}{system_path}")),
        ];
        if cfg!(windows) {
            // 关键：Windows 上 Python 默认按系统代码页（zh-CN 为 cp936）写管道，
            // 编排器侧按 UTF-8 解码会失败并中断读线程（见 logs.rs）。强制 UTF-8 模式，
            // 让便携 Python 的 stdio / 文件名编码统一走 UTF-8。
            env.push(("PYTHONUTF8".into(), "1".into()));
            env.push(("PYTHONIOENCODING".into(), "utf-8".into()));
        }
        // 随包 Hermes：console script 的 shim 写着构建机绝对解释器路径，便携
        // Python 换位置后失效；与 API 服务同法，用 `-c` 直接调 hermes_cli。
        // 依赖目录只用 HERMES_SITE_PACKAGES 交给子进程的 PYTHONPATH，
        // 绝不并进 API 进程的 PYTHONPATH（两边 pin 的 pydantic/fastapi 不同）。
        let hermes_site = self.hermes_site_packages();
        if hermes_site.is_dir() {
            env.push(("HERMES_CLI_PATH".into(), self.python().display().to_string()));
            env.push((
                "HERMES_CLI_ARGS".into(),
                r#"-c "from hermes_cli.main import main; main()""#.into(),
            ));
            env.push(("HERMES_SITE_PACKAGES".into(), hermes_site.display().to_string()));
        }
        env
    }

    /// 把 base_env 应用到 Command，并保留调用方后续追加的专属变量。
    pub fn apply_env(&self, cmd: &mut std::process::Command) {
        for (k, v) in self.base_env() {
            cmd.env(k, v);
        }
    }
}

/// 去掉 Windows 的 `\\?\` 扩展前缀，得到常规原生路径。
///
/// 真机踩点：Rust `current_exe()`（Tauri 未打包运行时的 `resource_dir()` 走它）返回
/// `\\?\E:\…` 形式的**verbatim 路径**。它会被 `display()` 原样渗进 nginx 配置的
/// `include`/`root`、以及注入给后端的 `PYTHONPATH`/`PATH`——verbatim 语义关闭了系统的
/// 路径规范化，Python 的 site 初始化与各第三方程序对它的支持并不一致，是出包后的隐性故障源。
/// 这里只做前缀剥离（路径本身已是绝对规范路径，无需再 canonicalize）。
#[cfg(windows)]
pub(crate) fn native_path(path: PathBuf) -> PathBuf {
    let s = path.display().to_string();
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    path
}

#[cfg(not(windows))]
pub(crate) fn native_path(path: PathBuf) -> PathBuf {
    path
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

/// 写进 nginx 配置文件的路径值。
///
/// nginx 在 Windows 上把反斜杠当转义符（`C:\Users\x` 会被解析成 `C:Usersx`），
/// 官方 Windows 版要求配置内使用正斜杠；argv（`-p` 前缀）仍用原生反斜杠路径。
pub fn nginx_path_str(p: &Path) -> String {
    let s = p.display().to_string();
    if cfg!(windows) {
        s.replace('\\', "/")
    } else {
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_root(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("dramaclaw-env-{tag}-{nanos}"));
        std::fs::create_dir_all(root.join("resources")).unwrap();
        std::fs::create_dir_all(root.join("app-data")).unwrap();
        root
    }

    #[test]
    fn base_env_wires_bundled_hermes_runtime() {
        let root = temp_root("hermes");
        let site = root.join("resources/hermes/site-packages");
        std::fs::create_dir_all(&site).unwrap();

        let paths = RuntimePaths::new(root.join("app-data"), root.join("resources")).unwrap();
        let env: std::collections::HashMap<String, String> =
            paths.base_env().into_iter().collect();

        assert!(env["HERMES_CLI_PATH"].ends_with(if cfg!(windows) {
            "python.exe"
        } else {
            "python3"
        }));
        assert_eq!(
            env["HERMES_CLI_ARGS"],
            r#"-c "from hermes_cli.main import main; main()""#
        );
        assert_eq!(PathBuf::from(&env["HERMES_SITE_PACKAGES"]), site);
        // 依赖目录绝不能混进 API 进程自己的 PYTHONPATH。
        assert_eq!(env["PYTHONPATH"], paths.backend_src().display().to_string());
        // 用户目录 site-packages 遮蔽随包依赖是跨平台风险（macOS 真机踩过架构不匹配），
        // 所有平台都必须禁用 user site。
        assert_eq!(env["PYTHONNOUSERSITE"], "1");
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn base_env_omits_hermes_when_runtime_absent() {
        let root = temp_root("no-hermes");
        let paths = RuntimePaths::new(root.join("app-data"), root.join("resources")).unwrap();
        let env: std::collections::HashMap<String, String> =
            paths.base_env().into_iter().collect();
        assert!(!env.contains_key("HERMES_CLI_PATH"));
        assert!(!env.contains_key("HERMES_SITE_PACKAGES"));
        std::fs::remove_dir_all(&root).ok();
    }
}
