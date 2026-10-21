//! 用户可选的存储位置（用于把模型 / 数据库 / 成片 / 日志迁出 C 盘）。
//!
//! 固定留在平台 app-data 的只有一个小配置指针 `data-root.json`；其余可写数据
//! 全部跟随 `data_root`。切换分两步，避免半途丢数据：
//!   1) 写入「待迁移」配置 → 关闭服务 → 重启进程；
//!   2) 重启后的启动早期，先把旧位置完整复制到新位置并校验，再提交配置、按需
//!      删除旧数据，最后再重启一次绑定新位置。
//! 复制/校验任一步失败都回退旧配置并继续用旧位置启动，旧数据始终保留。
//!
//! 环境变量 `DRAMACLAW_DATA_ROOT` 可强制指定（优先级最高，不写配置、不迁移）。
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use crate::orchestrator::Orchestrator;

pub const CONFIG_NAME: &str = "data-root.json";
pub const ENV_VAR: &str = "DRAMACLAW_DATA_ROOT";
const MARKER_NAME: &str = ".dramaclaw-data";
/// 迁移时跳过的根级目录：纯字节码缓存，删掉会自动重建，不值得复制数 GB。
const SKIP_ROOT_DIRS: [&str; 1] = ["pycache"];

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    /// 自定义数据根；None = 平台默认 app-data。
    pub data_root: Option<String>,
    /// 非空表示有待完成的迁移：本次仍从该位置启动，迁移成功后切换到 data_root。
    pub migrate_from: Option<String>,
    pub delete_source: bool,
    /// 最近一次迁移失败原因（诊断窗口展示；下次切换成功会清空）。
    pub last_error: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
pub struct DataRootInfo {
    /// 本次实际生效的数据根（被阻塞时为可安全启动的默认位置）。
    pub current: String,
    pub default_root: String,
    /// 配置或环境变量里写的目标（可能不可用）。
    pub configured: Option<String>,
    /// default | custom | env | pending | fallback
    pub source: String,
    pub env_override: bool,
    /// 配置的位置不可用：服务不启动，避免把新数据写进错误的地方。
    pub blocked: bool,
    pub last_error: Option<String>,
    pub config_path: String,
    pub install_dir: String,
    /// Windows 安装版才有；开发运行 / macOS 为 None。
    pub uninstaller: Option<String>,
}

pub struct DataRootState {
    pub home: PathBuf,
    pub install_dir: PathBuf,
    info: Mutex<DataRootInfo>,
}

impl DataRootState {
    pub fn info(&self) -> DataRootInfo {
        self.info.lock().unwrap().clone()
    }

    pub fn config_path(&self) -> PathBuf {
        self.home.join(CONFIG_NAME)
    }

    pub fn has_pending(&self) -> bool {
        self.info.lock().unwrap().pending_target().is_some()
    }
}

impl DataRootInfo {
    fn pending_target(&self) -> Option<String> {
        if self.source == "pending" {
            self.configured.clone()
        } else {
            None
        }
    }
}

/// 解析本次会话的数据根。返回值第二个元素给 `RuntimePaths` 用。
pub fn resolve(home: PathBuf, install_dir: PathBuf) -> (DataRootState, PathBuf) {
    let home = crate::env::native_path(home);
    let install_dir = crate::env::native_path(install_dir);
    let config_path = home.join(CONFIG_NAME);

    let mut read_error = None;
    let config = match fs::read(&config_path) {
        Ok(bytes) => match serde_json::from_slice::<Config>(&bytes) {
            Ok(c) => c,
            Err(e) => {
                read_error = Some(format!("存储位置配置无法解析（已按默认位置启动）：{e}"));
                Config::default()
            }
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Config::default(),
        Err(e) => {
            read_error = Some(format!("存储位置配置读取失败（已按默认位置启动）：{e}"));
            Config::default()
        }
    };

    let env_root = std::env::var(ENV_VAR)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    let mut info = DataRootInfo {
        current: String::new(),
        default_root: home.display().to_string(),
        configured: None,
        source: "default".into(),
        env_override: env_root.is_some(),
        blocked: false,
        last_error: read_error,
        config_path: config_path.display().to_string(),
        install_dir: install_dir.display().to_string(),
        uninstaller: uninstaller_path(&install_dir).map(|p| p.display().to_string()),
    };

    let root = if let Some(raw) = env_root {
        let p = PathBuf::from(raw.trim());
        info.configured = Some(p.display().to_string());
        if !sane_absolute(&p) {
            info.source = "fallback".into();
            info.blocked = true;
            info.last_error = Some(format!(
                "环境变量 {ENV_VAR} 不是有效的绝对路径：{}",
                p.display()
            ));
            home.clone()
        } else if usable_root(&p) {
            info.source = "env".into();
            p
        } else {
            info.source = "fallback".into();
            info.blocked = true;
            info.last_error = Some(format!(
                "环境变量 {ENV_VAR} 指定的目录不可用：{}；插回磁盘或移除该变量后重启。",
                p.display()
            ));
            home.clone()
        }
    } else if let (Some(target), Some(from)) = (config.data_root.as_deref(), config.migrate_from.as_deref()) {
        // 有未完成的迁移：本次仍从旧位置启动，迁移在启动早期执行。
        let from_p = PathBuf::from(from);
        info.configured = Some(target.to_string());
        if from_p.is_absolute() && from_p.is_dir() {
            info.source = "pending".into();
            from_p
        } else {
            info.source = "fallback".into();
            info.blocked = true;
            info.last_error = Some(format!(
                "待迁移的旧数据目录不可用：{}；为避免写入错误位置已暂停启动。",
                from_p.display()
            ));
            home.clone()
        }
    } else if let Some(custom) = config.data_root.as_deref() {
        let p = PathBuf::from(custom);
        info.configured = Some(p.display().to_string());
        if sane_absolute(&p) && usable_root(&p) {
            info.source = "custom".into();
            p
        } else {
            info.source = "fallback".into();
            info.blocked = true;
            info.last_error = Some(format!(
                "已设置的存储位置不可用：{}；插回磁盘，或在诊断窗口改回默认后重启。",
                p.display()
            ));
            home.clone()
        }
    } else {
        info.source = "default".into();
        home.clone()
    };

    if info.last_error.is_none() {
        info.last_error = config.last_error.clone();
    }
    info.current = root.display().to_string();
    (
        DataRootState {
            home,
            install_dir,
            info: Mutex::new(info),
        },
        root,
    )
}

/// 切换前的目标校验（不写任何配置）。
pub fn validate_target(
    _home: &Path,
    install_dir: &Path,
    current: &Path,
    target: &Path,
) -> Result<(), String> {
    if target.as_os_str().is_empty() {
        return Err("请选择目标文件夹".into());
    }
    if !sane_absolute(target) {
        return Err(format!("目标必须是绝对路径：{}", target.display()));
    }
    if target.file_name().is_none() {
        return Err("不能直接选择磁盘根目录，请在其下新建一个专用文件夹".into());
    }

    let t = norm_key(target);
    let c = norm_key(current);
    let i = norm_key(install_dir);

    if same_key(&t, &c) {
        return Err("目标位置与当前数据位置相同".into());
    }
    if within(&t, &c) {
        return Err("不能把数据放进当前数据目录的子目录".into());
    }
    if within(&c, &t) {
        return Err("不能选择当前数据目录的上级目录".into());
    }
    if within(&t, &i) || within(&i, &t) {
        return Err("不能选择程序安装目录本身、其上级或其子目录".into());
    }

    if target.exists() {
        if !target.is_dir() {
            return Err(format!("目标不是文件夹：{}", target.display()));
        }
        let state = marker_state(target);
        let has_entries = fs::read_dir(target)
            .map_err(|e| format!("无法读取目标文件夹 {}：{e}", target.display()))?
            .filter_map(|e| e.ok())
            .any(|e| e.file_name() != OsStr::new(MARKER_NAME));
        if has_entries && state.as_deref() != Some("copying") {
            return Err("目标文件夹不是空目录。请新建一个专用空文件夹，避免覆盖已有文件。".into());
        }
    }

    fs::create_dir_all(target)
        .map_err(|e| format!("无法创建目标文件夹 {}：{e}", target.display()))?;
    probe_writable(target)
}

/// 写配置准备切换；返回写入的配置。调用方随后关闭服务并重启。
pub fn prepare_switch(
    state: &DataRootState,
    current: &Path,
    target: &Path,
    migrate: bool,
    delete_source: bool,
) -> Result<Config, String> {
    if state.info().env_override {
        return Err(format!(
            "当前由环境变量 {ENV_VAR} 指定数据位置，配置不会生效；请先移除该变量再切换。"
        ));
    }
    let target = crate::env::native_path(target.to_path_buf());
    let current = crate::env::native_path(current.to_path_buf());
    validate_target(&state.home, &state.install_dir, &current, &target)?;

    // 上一次中断留下的半成品（带 copying 标记）可以安全清空；其他非空目录已被
    // validate_target 拒绝，这里不会误删用户数据。
    clear_partial(&target)?;
    if migrate {
        write_marker(&target, "copying").map_err(|e| format!("无法写入目标目录标记：{e}"))?;
    } else {
        write_marker(&target, "ready").map_err(|e| format!("无法写入目标目录标记：{e}"))?;
    }

    let root_value = if same_key(&norm_key(&target), &norm_key(&state.home)) {
        None
    } else {
        Some(target.display().to_string())
    };
    let cfg = if migrate {
        Config {
            data_root: Some(target.display().to_string()),
            migrate_from: Some(current.display().to_string()),
            delete_source,
            last_error: None,
        }
    } else {
        Config {
            data_root: root_value,
            migrate_from: None,
            delete_source: false,
            last_error: None,
        }
    };
    write_config(&state.home, &cfg)
        .map_err(|e| format!("写入存储位置配置失败：{e}"))?;
    Ok(cfg)
}

/// 恢复默认位置（用于自定义磁盘不可用时的救援）。不迁移、不删旧数据。
pub fn reset_to_default(state: &DataRootState) -> Result<(), String> {
    if state.info().env_override {
        return Err(format!(
            "当前由环境变量 {ENV_VAR} 指定数据位置；请先移除该变量。"
        ));
    }
    let cfg = Config::default();
    write_config(&state.home, &cfg).map_err(|e| format!("写入存储位置配置失败：{e}"))
}

pub fn read_config(home: &Path) -> Config {
    fs::read(home.join(CONFIG_NAME))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn write_config(home: &Path, cfg: &Config) -> std::io::Result<()> {
    fs::create_dir_all(home)?;
    let path = home.join(CONFIG_NAME);
    let tmp = home.join(format!("{CONFIG_NAME}.tmp"));
    let bytes = serde_json::to_vec_pretty(cfg)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    fs::write(&tmp, bytes)?;
    // Windows 的 rename 不覆盖已存在文件；配置很小，短暂窗口可接受。
    #[cfg(windows)]
    let _ = fs::remove_file(&path);
    fs::rename(tmp, path)
}

// ---------------------------------------------------------------- 迁移执行

#[derive(Clone, Copy, Debug)]
pub struct Progress {
    pub copied_files: u64,
    pub total_files: u64,
    pub copied_bytes: u64,
    pub total_bytes: u64,
}

#[derive(Clone, Copy, Debug)]
pub struct Outcome {
    pub files: u64,
    pub bytes: u64,
    pub skipped: u64,
}

#[derive(Clone, Debug)]
struct Entry {
    rel: PathBuf,
    len: u64,
}

/// 启动早期执行待办迁移。成功时 `app.restart()` 不返回；失败时回退配置并返回，
/// 调用方继续用旧位置启动。
pub fn run_pending_migration(
    app: &AppHandle,
    state: &DataRootState,
    orc: &std::sync::Arc<Orchestrator>,
) {
    let cfg = read_config(&state.home);
    let (Some(target_s), Some(from_s)) = (cfg.data_root.as_deref(), cfg.migrate_from.as_deref())
    else {
        return;
    };
    let target = PathBuf::from(target_s);
    let from = PathBuf::from(from_s);
    let current = orc.paths().app_data.clone();

    if !same_key(&norm_key(&from), &norm_key(&current)) {
        let msg = format!(
            "待迁移配置的旧位置（{}）与本次启动的数据位置（{}）不一致，已取消迁移",
            from.display(),
            current.display()
        );
        let _ = revert_config(state, &from, &msg);
        orc.log_system(format!("数据迁移未执行：{msg}"));
        orc.report_boot("datamove", msg, 0.02);
        return;
    }

    orc.report_boot(
        "datamove",
        format!("正在把数据迁移到新位置：{}（旧位置 {}）…", target.display(), from.display()),
        0.0,
    );
    orc.log_system(format!(
        "数据迁移开始：{} → {}（迁移后删除旧数据：{}）",
        from.display(),
        target.display(),
        if cfg.delete_source { "是" } else { "否" }
    ));

    let mut last_emit = Instant::now()
        .checked_sub(Duration::from_secs(5))
        .unwrap_or_else(Instant::now);
    let mut last_pct = 255i32;
    let result = migrate_to(&from, &target, |p| {
        let pct = if p.total_bytes == 0 {
            100
        } else {
            ((p.copied_bytes.saturating_mul(100)) / p.total_bytes) as i32
        };
        let now = Instant::now();
        if now.duration_since(last_emit) >= Duration::from_millis(300) && pct != last_pct {
            last_emit = now;
            last_pct = pct;
            orc.report_boot(
                "datamove",
                format!(
                    "正在迁移数据到新位置… {} / {} 个文件，{:.0} / {:.0} MB（{}%）",
                    p.copied_files,
                    p.total_files,
                    p.copied_bytes as f64 / 1048576.0,
                    p.total_bytes as f64 / 1048576.0,
                    pct
                ),
                0.10 * (pct as f32 / 100.0),
            );
        }
    });

    match result {
        Ok(outcome) => {
            let committed = Config {
                data_root: if same_key(&norm_key(&target), &norm_key(&state.home)) {
                    None
                } else {
                    Some(target.display().to_string())
                },
                migrate_from: None,
                delete_source: false,
                last_error: None,
            };
            if let Err(e) = write_config(&state.home, &committed) {
                let msg = format!("迁移已完成但写入配置失败，继续使用原位置：{e}");
                let _ = clear_partial(&target);
                let _ = revert_config(state, &from, &msg);
                orc.log_system(msg.clone());
                orc.report_boot("datamove", msg, 0.02);
                return;
            }

            let mut removed = 0u64;
            let mut failures = Vec::new();
            if cfg.delete_source {
                let keep = if same_key(&norm_key(&from), &norm_key(&state.home)) {
                    vec![state.config_path()]
                } else {
                    Vec::new()
                };
                let (n, errs) = remove_source(&from, &keep);
                removed = n;
                failures = errs;
                if !failures.is_empty() {
                    let msg = format!(
                        "旧位置已清理 {} 个文件，另有 {} 项未能删除（可手动处理）：{}",
                        removed,
                        failures.len(),
                        failures.iter().take(3).cloned().collect::<Vec<_>>().join("；")
                    );
                    let mut cfg = committed.clone();
                    cfg.last_error = Some(msg.clone());
                    let _ = write_config(&state.home, &cfg);
                    orc.log_system(msg);
                }
            }

            update_info_after_commit(state, &target, failures.first().cloned());

            orc.log_system(format!(
                "数据迁移完成：{} 个文件 / {:.1} MB{}；正在重启以绑定新位置",
                outcome.files,
                outcome.bytes as f64 / 1048576.0,
                format!(
                    "{}{}",
                    if outcome.skipped > 0 {
                        format!("，跳过 {} 个符号链接/特殊文件", outcome.skipped)
                    } else {
                        String::new()
                    },
                    if removed > 0 {
                        format!("，旧位置已清理 {removed} 个文件")
                    } else {
                        String::new()
                    }
                )
            ));
            orc.report_boot("datamove", "数据迁移完成，正在重启…", 1.0);
            std::thread::sleep(Duration::from_millis(500));
            app.restart();
        }
        Err(e) => {
            let _ = clear_partial(&target);
            let msg = format!("数据迁移未完成，已继续使用原位置（{}）：{e}", from.display());
            let _ = revert_config(state, &from, &msg);
            update_info_after_revert(state, &from, &msg);
            orc.log_system(msg.clone());
            orc.report_boot("datamove", msg, 0.02);
        }
    }
}

fn revert_config(state: &DataRootState, source: &Path, message: &str) -> std::io::Result<()> {
    let cfg = Config {
        data_root: if same_key(&norm_key(source), &norm_key(&state.home)) {
            None
        } else {
            Some(source.display().to_string())
        },
        migrate_from: None,
        delete_source: false,
        last_error: Some(message.to_string()),
    };
    write_config(&state.home, &cfg)
}

fn update_info_after_commit(state: &DataRootState, target: &Path, warning: Option<String>) {
    let mut info = state.info.lock().unwrap();
    info.current = target.display().to_string();
    info.configured = if same_key(&norm_key(target), &norm_key(&state.home)) {
        None
    } else {
        Some(target.display().to_string())
    };
    info.source = if info.configured.is_some() {
        "custom".into()
    } else {
        "default".into()
    };
    info.blocked = false;
    info.last_error = warning;
}

fn update_info_after_revert(state: &DataRootState, source: &Path, message: &str) {
    let mut info = state.info.lock().unwrap();
    info.current = source.display().to_string();
    info.configured = if same_key(&norm_key(source), &norm_key(&state.home)) {
        None
    } else {
        Some(source.display().to_string())
    };
    info.source = if info.configured.is_some() {
        "custom".into()
    } else {
        "default".into()
    };
    info.blocked = false;
    info.last_error = Some(message.to_string());
}

/// 复制整棵树到目标（目标必须为空或只带 copying 标记），完成后逐项校验。
fn migrate_to(
    src: &Path,
    dst: &Path,
    mut on_progress: impl FnMut(&Progress),
) -> Result<Outcome, String> {
    if !src.is_dir() {
        return Err(format!("旧数据目录不存在：{}", src.display()));
    }
    fs::create_dir_all(dst).map_err(|e| format!("无法创建目标目录 {}：{e}", dst.display()))?;
    clear_partial(dst)?;
    write_marker(dst, "copying").map_err(|e| format!("无法写入目标目录标记：{e}"))?;

    let mut entries = Vec::new();
    let mut skipped = 0u64;
    scan_dir(src, Path::new(""), true, &mut entries, &mut skipped)?;
    let total_files = entries.len() as u64;
    let total_bytes: u64 = entries.iter().map(|e| e.len).sum();

    let mut copied_files = 0u64;
    let mut copied_bytes = 0u64;
    for entry in &entries {
        let from = src.join(&entry.rel);
        let to = dst.join(&entry.rel);
        if let Some(parent) = to.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("创建目录 {} 失败：{e}", parent.display()))?;
        }
        fs::copy(&from, &to).map_err(|e| {
            format!(
                "复制文件失败：{} → {}：{e}",
                from.display(),
                to.display()
            )
        })?;
        copied_files += 1;
        copied_bytes += entry.len;
        on_progress(&Progress {
            copied_files,
            total_files,
            copied_bytes,
            total_bytes,
        });
    }

    let (dst_files, dst_bytes) = tree_stats(dst)?;
    if dst_files != total_files || dst_bytes != total_bytes {
        return Err(format!(
            "复制校验未通过：源 {} 个文件 / {} 字节，目标 {} 个文件 / {} 字节",
            total_files, total_bytes, dst_files, dst_bytes
        ));
    }

    write_marker(dst, "ready").map_err(|e| format!("无法写入目标目录标记：{e}"))?;
    Ok(Outcome {
        files: copied_files,
        bytes: copied_bytes,
        skipped,
    })
}

fn scan_dir(
    root: &Path,
    rel: &Path,
    at_root: bool,
    out: &mut Vec<Entry>,
    skipped: &mut u64,
) -> Result<(), String> {
    let dir = root.join(rel);
    let rd = fs::read_dir(&dir).map_err(|e| format!("读取目录 {} 失败：{e}", dir.display()))?;
    for item in rd {
        let item = item.map_err(|e| format!("读取目录 {} 失败：{e}", dir.display()))?;
        let name = item.file_name();
        if at_root
            && (name == OsStr::new(CONFIG_NAME)
                || name == OsStr::new(MARKER_NAME)
                || SKIP_ROOT_DIRS.iter().any(|d| name == OsStr::new(d)))
        {
            continue;
        }
        let path = item.path();
        let child_rel = rel.join(&name);
        let md = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(e) => return Err(format!("读取 {} 失败：{e}", path.display())),
        };
        if md.file_type().is_symlink() {
            // 不跟随符号链接：既避免复制越界，也避免清理时误删链接目标。
            *skipped += 1;
        } else if md.is_dir() {
            scan_dir(root, &child_rel, false, out, skipped)?;
        } else if md.is_file() {
            out.push(Entry { rel: child_rel, len: md.len() });
        } else {
            *skipped += 1;
        }
    }
    Ok(())
}

/// 目标树统计，排除根级标记文件。
fn tree_stats(root: &Path) -> Result<(u64, u64), String> {
    let mut files = 0u64;
    let mut bytes = 0u64;
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let rd = fs::read_dir(&dir).map_err(|e| format!("读取 {} 失败：{e}", dir.display()))?;
        for item in rd {
            let item = item.map_err(|e| format!("读取 {} 失败：{e}", dir.display()))?;
            let path = item.path();
            let md = fs::symlink_metadata(&path)
                .map_err(|e| format!("读取 {} 失败：{e}", path.display()))?;
            if md.is_dir() {
                stack.push(path);
            } else if md.is_file() {
                if path.parent() == Some(root) && item.file_name() == OsStr::new(MARKER_NAME) {
                    continue;
                }
                files += 1;
                bytes += md.len();
            }
        }
    }
    Ok((files, bytes))
}

/// 删除旧位置的内容（保留调用方指定的文件，例如默认位置里的配置指针）。
/// 返回（删除文件数, 失败项）。失败不抛出：迁移已提交，旧数据残留不影响新位置。
pub fn remove_source(src: &Path, keep: &[PathBuf]) -> (u64, Vec<String>) {
    let mut removed = 0u64;
    let mut errors = Vec::new();
    let keep_keys: Vec<String> = keep.iter().map(|p| norm_key(p)).collect();
    remove_children(src, &keep_keys, &mut removed, &mut errors);
    (removed, errors)
}

fn remove_children(dir: &Path, keep: &[String], removed: &mut u64, errors: &mut Vec<String>) {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) => {
            errors.push(format!("{}：{e}", dir.display()));
            return;
        }
    };
    for item in rd.flatten() {
        let path = item.path();
        if keep.iter().any(|k| same_key(&norm_key(&path), k)) {
            continue;
        }
        let md = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(e) => {
                errors.push(format!("{}：{e}", path.display()));
                continue;
            }
        };
        if md.is_dir() && !md.file_type().is_symlink() {
            remove_children(&path, keep, removed, errors);
            if let Err(e) = fs::remove_dir(&path) {
                if e.kind() != std::io::ErrorKind::NotFound {
                    errors.push(format!("{}：{e}", path.display()));
                }
            }
        } else if remove_file_force(&path) {
            *removed += 1;
        } else {
            errors.push(format!("{}：无法删除", path.display()));
        }
    }
}

fn remove_file_force(path: &Path) -> bool {
    if fs::remove_file(path).is_ok() {
        return true;
    }
    // Windows 只读属性会挡住删除；去掉后重试一次。
    if let Ok(md) = fs::metadata(path) {
        let mut perms = md.permissions();
        if perms.readonly() {
            #[allow(clippy::permissions_set_readonly_false)]
            perms.set_readonly(false);
            let _ = fs::set_permissions(path, perms);
            return fs::remove_file(path).is_ok();
        }
    }
    false
}

/// 清空「半成品」目标：只允许空目录，或带 copying 标记的目标。
fn clear_partial(dst: &Path) -> Result<(), String> {
    if !dst.exists() {
        return Ok(());
    }
    if !dst.is_dir() {
        return Err(format!("目标不是文件夹：{}", dst.display()));
    }
    let state = marker_state(dst);
    let entries: Vec<PathBuf> = fs::read_dir(dst)
        .map_err(|e| format!("无法读取目标文件夹 {}：{e}", dst.display()))?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .collect();
    let non_marker: Vec<&PathBuf> = entries
        .iter()
        .filter(|p| p.file_name() != Some(OsStr::new(MARKER_NAME)))
        .collect();
    if !non_marker.is_empty() && state.as_deref() != Some("copying") {
        return Err(format!(
            "目标文件夹不是空目录（{}）。请新建一个专用空文件夹。",
            dst.display()
        ));
    }
    for p in &entries {
        if p.is_dir() && fs::symlink_metadata(p).map(|m| !m.file_type().is_symlink()).unwrap_or(false) {
            fs::remove_dir_all(p).map_err(|e| format!("清理 {} 失败：{e}", p.display()))?;
        } else {
            fs::remove_file(p).map_err(|e| format!("清理 {} 失败：{e}", p.display()))?;
        }
    }
    Ok(())
}

fn write_marker(dst: &Path, state: &str) -> std::io::Result<()> {
    fs::write(
        dst.join(MARKER_NAME),
        format!("{{\"app\":\"dramaclaw\",\"state\":\"{state}\"}}\n"),
    )
}

fn marker_state(dst: &Path) -> Option<String> {
    let bytes = fs::read(dst.join(MARKER_NAME)).ok()?;
    let value: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    value.get("state")?.as_str().map(|s| s.to_string())
}

// ---------------------------------------------------------------- 路径工具

fn sane_absolute(p: &Path) -> bool {
    p.is_absolute() && p.file_name().is_some()
}

fn usable_root(p: &Path) -> bool {
    if !sane_absolute(p) {
        return false;
    }
    if p.exists() {
        if !p.is_dir() {
            return false;
        }
    } else if fs::create_dir_all(p).is_err() {
        return false;
    }
    probe_writable(p).is_ok()
}

fn probe_writable(dir: &Path) -> Result<(), String> {
    let probe = dir.join(format!(".dramaclaw-write-test-{}", std::process::id()));
    match fs::write(&probe, b"ok") {
        Ok(()) => {
            let _ = fs::remove_file(&probe);
            Ok(())
        }
        Err(e) => Err(format!(
            "目标文件夹不可写：{}（{e}）。请换一个当前用户有写权限的磁盘/文件夹。",
            dir.display()
        )),
    }
}

fn uninstaller_path(install_dir: &Path) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    let p = install_dir.join("uninstall.exe");
    p.is_file().then_some(p)
}

/// 规范化后的小写字符串，用于 Windows 的大小写不敏感比较。
fn norm_key(p: &Path) -> String {
    let abs = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|c| c.join(p))
            .unwrap_or_else(|_| p.to_path_buf())
    };
    let mut head = abs.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while !head.exists() {
        match head.file_name() {
            Some(name) => {
                tail.push(name.to_os_string());
                if !head.pop() {
                    break;
                }
            }
            None => break,
        }
    }
    let mut base = fs::canonicalize(&head).unwrap_or(head);
    for name in tail.iter().rev() {
        base.push(name);
    }
    let base = crate::env::native_path(base);
    let s = base.to_string_lossy().to_string();
    if cfg!(windows) {
        s.replace('/', "\\").to_lowercase()
    } else {
        s
    }
}

fn same_key(a: &str, b: &str) -> bool {
    a == b
}

fn within(child: &str, parent: &str) -> bool {
    if child == parent {
        return true;
    }
    #[cfg(windows)]
    {
        child
            .strip_prefix(parent)
            .map(|rest| rest.starts_with('\\'))
            .unwrap_or(false)
    }
    #[cfg(not(windows))]
    {
        child
            .strip_prefix(parent)
            .map(|rest| rest.starts_with('/'))
            .unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(tag: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("dramaclaw-datadir-{tag}-{nanos}"));
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn resolve_defaults_to_home() {
        let home = temp_root("home");
        let (state, root) = resolve(home.join("app-data"), home.join("app"));
        assert_eq!(root, home.join("app-data"));
        assert_eq!(state.info().source, "default");
        assert!(!state.info().blocked);
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resolve_reads_custom_root_and_pending() {
        let home = temp_root("resolve");
        let app_data = home.join("app-data");
        let custom = home.join("D-drive");
        let source = home.join("old");
        fs::create_dir_all(&app_data).unwrap();
        fs::create_dir_all(&custom).unwrap();
        fs::create_dir_all(&source).unwrap();

        write_config(
            &app_data,
            &Config {
                data_root: Some(custom.display().to_string()),
                ..Config::default()
            },
        )
        .unwrap();
        let (state, root) = resolve(app_data.clone(), home.join("app"));
        assert_eq!(root, custom);
        assert_eq!(state.info().source, "custom");

        write_config(
            &app_data,
            &Config {
                data_root: Some(custom.display().to_string()),
                migrate_from: Some(source.display().to_string()),
                delete_source: true,
                last_error: None,
            },
        )
        .unwrap();
        let (state, root) = resolve(app_data.clone(), home.join("app"));
        assert_eq!(root, source);
        assert_eq!(state.info().source, "pending");
        assert!(state.has_pending());
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn validate_rejects_bad_targets() {
        let home = temp_root("validate");
        let app_data = home.join("app-data");
        let install = home.join("install");
        let current = app_data.clone();
        fs::create_dir_all(&app_data).unwrap();
        fs::create_dir_all(&install).unwrap();

        assert!(validate_target(&app_data, &install, &current, &current).is_err());
        assert!(validate_target(&app_data, &install, &current, &app_data.join("sub")).is_err());
        assert!(validate_target(&app_data, &install, &current, &install).is_err());
        assert!(validate_target(&app_data, &install, &current, Path::new("D:")).is_err());

        let full = home.join("full");
        fs::create_dir_all(&full).unwrap();
        fs::write(full.join("keep.txt"), b"x").unwrap();
        assert!(validate_target(&app_data, &install, &current, &full).is_err());

        let empty = home.join("empty");
        fs::create_dir_all(&empty).unwrap();
        assert!(validate_target(&app_data, &install, &current, &empty).is_ok());
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn partial_target_only_cleared_with_marker() {
        let root = temp_root("partial");
        let target = root.join("target");
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("a.bin"), b"x").unwrap();
        assert!(clear_partial(&target).is_err());

        write_marker(&target, "copying").unwrap();
        assert!(clear_partial(&target).is_ok());
        assert_eq!(fs::read_dir(&target).unwrap().count(), 0);

        let completed = root.join("completed");
        fs::create_dir_all(&completed).unwrap();
        fs::write(completed.join("a.bin"), b"x").unwrap();
        write_marker(&completed, "ready").unwrap();
        assert!(clear_partial(&completed).is_err());
        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn migrate_copies_verifies_and_skips_cache_and_pointer() {
        let root = temp_root("migrate");
        let src = root.join("src");
        let dst = root.join("dst");
        fs::create_dir_all(src.join("models")).unwrap();
        fs::create_dir_all(src.join("pycache")).unwrap();
        fs::create_dir_all(src.join("state/nested")).unwrap();
        fs::write(src.join("models/a.bin"), vec![7u8; 2048]).unwrap();
        fs::write(src.join("state/nested/b.txt"), b"hello").unwrap();
        fs::write(src.join("pycache/c.pyc"), b"ignored").unwrap();
        fs::write(src.join(CONFIG_NAME), b"{}").unwrap();
        write_marker(&src, "ready").unwrap();

        let mut events = 0;
        let outcome = migrate_to(&src, &dst, |_| events += 1).unwrap();
        assert_eq!(outcome.files, 2);
        assert_eq!(outcome.bytes, 2053);
        assert!(events >= 2);
        assert!(dst.join("models/a.bin").is_file());
        assert!(dst.join("state/nested/b.txt").is_file());
        assert!(!dst.join("pycache").exists());
        assert!(!dst.join(CONFIG_NAME).exists());
        assert_eq!(marker_state(&dst).as_deref(), Some("ready"));
        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn remove_source_keeps_pointer_and_removes_rest() {
        let root = temp_root("cleanup");
        let src = root.join("src");
        fs::create_dir_all(src.join("models")).unwrap();
        fs::write(src.join("models/a.bin"), b"x").unwrap();
        fs::write(src.join("state.db"), b"x").unwrap();
        fs::write(src.join(CONFIG_NAME), b"{}").unwrap();

        let keep = vec![src.join(CONFIG_NAME)];
        let (removed, errors) = remove_source(&src, &keep);
        assert_eq!(removed, 2);
        assert!(errors.is_empty(), "{errors:?}");
        assert!(src.join(CONFIG_NAME).is_file());
        assert!(!src.join("models").exists());
        assert!(!src.join("state.db").exists());
        fs::remove_dir_all(&root).ok();
    }
}
