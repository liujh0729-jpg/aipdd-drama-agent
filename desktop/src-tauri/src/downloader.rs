//! 模型下载器：不打包进安装包，首启后台拉取到 app-data/models。
//! - 断点续传（HTTP Range + `.part` 临时文件）
//! - sha256 完整性校验（续传时先对已下分片做哈希，避免错位）
//! - 进度事件（下发 `model://progress` 给面板）
//! - 失败重试
use std::path::{Path, PathBuf};

use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::error::{DesktopError, Result};
use crate::models::DownloadProgress;

pub const EVT_MODEL_PROGRESS: &str = "model://progress";

const MAX_ATTEMPTS: u32 = 3;
/// 无 Content-Range 的整文件下载时的重试粒度；单文件失败最多重试 MAX_ATTEMPTS 次。

#[derive(Debug, Deserialize)]
pub struct Manifest {
    #[serde(default)]
    pub models: Vec<ModelEntry>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModelEntry {
    pub name: String,
    pub url: String,
    /// 相对 models 目录的落盘子路径（含子目录）。
    pub dest: String,
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size: Option<u64>,
}

/// 读取清单；文件不存在视为“无需下载”，返回空列表。
pub fn load_manifest(path: &Path) -> Result<Vec<ModelEntry>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let raw = std::fs::read_to_string(path)?;
    let m: Manifest = serde_json::from_str(&raw)?;
    Ok(m.models)
}

/// 幂等地补齐缺失模型。已存在且哈希匹配的跳过。
pub async fn ensure_models(app: AppHandle, entries: Vec<ModelEntry>, models_dir: PathBuf) {
    for e in entries {
        let target = models_dir.join(&e.dest);
        if file_ok(&target, e.sha256.as_deref()).await {
            emit(&app, &e.name, e.size.unwrap_or(0), e.size, 0.0, true, None);
            continue;
        }
        let _ = std::fs::create_dir_all(target.parent().unwrap_or(&models_dir));

        let mut last_err: Option<String> = None;
        for attempt in 1..=MAX_ATTEMPTS {
            match download_one(&app, &e, &target).await {
                Ok(()) => {
                    last_err = None;
                    break;
                }
                Err(err) => {
                    last_err = Some(err.to_string());
                    emit(
                        &app,
                        &e.name,
                        0,
                        e.size,
                        0.0,
                        false,
                        Some(format!("第 {attempt}/{MAX_ATTEMPTS} 次尝试失败：{err}")),
                    );
                    tokio::time::sleep(std::time::Duration::from_secs(2 * attempt as u64)).await;
                }
            }
        }
        if let Some(msg) = last_err {
            emit(&app, &e.name, 0, e.size, 0.0, true, Some(msg));
        }
    }
}

/// 目标文件存在且（若给定）sha256 匹配。
async fn file_ok(target: &Path, expect_sha: Option<&str>) -> bool {
    if !target.exists() {
        return false;
    }
    match expect_sha {
        None => true,
        Some(want) => match sha256_of(target).await {
            Ok(got) => got.eq_ignore_ascii_case(want),
            Err(_) => false,
        },
    }
}

async fn download_one(app: &AppHandle, e: &ModelEntry, target: &Path) -> Result<()> {
    let part = part_path(target);
    let client = reqwest::Client::builder()
        .user_agent("dramaclaw-desktop/2.1")
        .build()
        .map_err(|err| DesktopError::Download(err.to_string()))?;

    // 断点续传：以已存在的 .part 长度为偏移。
    let mut offset: u64 = tokio::fs::metadata(&part).await.map(|m| m.len()).unwrap_or(0);

    let mut req = client.get(&e.url);
    if offset > 0 {
        req = req.header("Range", format!("bytes={offset}-"));
    }
    let resp = req
        .send()
        .await
        .map_err(|err| DesktopError::Download(err.to_string()))?;

    let status = resp.status();
    // 206=续传成功；200=对方不支持 Range，从头覆盖。
    if status == reqwest::StatusCode::RANGE_NOT_SATISFIABLE {
        // 已下满：直接进入校验/落盘。
        offset = tokio::fs::metadata(&part).await.map(|m| m.len()).unwrap_or(offset);
    } else if !status.is_success() {
        return Err(DesktopError::Download(format!("HTTP {status}")));
    } else if status == reqwest::StatusCode::OK {
        // 服务端返回全量：丢弃旧 .part，重头下载。
        offset = 0;
        let _ = tokio::fs::remove_file(&part).await;
    }

    let total = e.size.or(resp.content_length().map(|c| c + offset));

    // 续传需先把已有分片喂进哈希，保证与最终整文件一致。
    let mut hasher = Sha256::new();
    if offset > 0 {
        let mut pf = tokio::fs::File::open(&part)
            .await
            .map_err(|err| DesktopError::Download(err.to_string()))?;
        let mut buf = [0u8; 64 * 1024];
        loop {
            let n = pf.read(&mut buf).await.unwrap_or(0);
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
    }

    let mut file = if offset > 0 {
        tokio::fs::OpenOptions::new()
            .append(true)
            .open(&part)
            .await
            .map_err(|err| DesktopError::Download(err.to_string()))?
    } else {
        tokio::fs::File::create(&part)
            .await
            .map_err(|err| DesktopError::Download(err.to_string()))?
    };

    let mut downloaded = offset;
    let started = std::time::Instant::now();
    let mut last_emit = std::time::Instant::now();
    let mut stream = resp.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let bytes = chunk.map_err(|err| DesktopError::Download(err.to_string()))?;
        hasher.update(&bytes);
        file.write_all(&bytes)
            .await
            .map_err(|err| DesktopError::Download(err.to_string()))?;
        downloaded += bytes.len() as u64;

        // 限频：每 ~200ms 回一次进度。
        if last_emit.elapsed() >= std::time::Duration::from_millis(200) {
            let elapsed = started.elapsed().as_secs_f64().max(0.001);
            let rate = (downloaded - offset) as f64 / elapsed;
            emit(app, &e.name, downloaded, total, rate, false, None);
            last_emit = std::time::Instant::now();
        }
    }
    file.flush().await.ok();
    drop(file);

    // 校验并原子落盘。
    let got = format!("{:x}", hasher.finalize());
    if let Some(want) = e.sha256.as_deref() {
        if !got.eq_ignore_ascii_case(want) {
            let _ = tokio::fs::remove_file(&part).await;
            return Err(DesktopError::Download(format!(
                "sha256 校验失败（期望 {want}，实际 {got}）"
            )));
        }
    }
    tokio::fs::rename(&part, target)
        .await
        .map_err(|err| DesktopError::Download(err.to_string()))?;
    emit(app, &e.name, downloaded, total, 0.0, true, None);
    Ok(())
}

fn part_path(target: &Path) -> PathBuf {
    let mut p = target.as_os_str().to_os_string();
    p.push(".part");
    PathBuf::from(p)
}

async fn sha256_of(path: &Path) -> Result<String> {
    let mut f = tokio::fs::File::open(path).await?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 64 * 1024];
    loop {
        let n = f.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn emit(
    app: &AppHandle,
    file: &str,
    downloaded: u64,
    total: Option<u64>,
    rate: f64,
    done: bool,
    error: Option<String>,
) {
    let _ = app.emit(
        EVT_MODEL_PROGRESS,
        DownloadProgress {
            file: file.to_string(),
            downloaded,
            total,
            rate,
            done,
            error,
        },
    );
}

