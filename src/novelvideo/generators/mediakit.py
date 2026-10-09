"""[aipdd fork delta] 火山引擎 AI MediaKit 生成式视频超分客户端。

用于画布视频组件的「高清」：把本地视频交给 MediaKit 的画质增强（大模型版）
`enhance-video-generative` 异步工具做生成式超分，替代原本地 ffmpeg 缩放。

官方接口契约（cn-beijing）：

1. 申请上传地址（本地上传第一步）
   POST {base}/tools-sync/request-media-upload-url   body {}   ->
   {success, result: {file_id, method, upload_headers[], upload_url}}
   再 PUT upload_url（原始二进制 + upload_headers）上传本地文件。
   之后用 `mediakit://{file_id}` 引用该文件（示例里 file_id 已含 `mediakit://` 前缀）。

2. 提交增强任务
   POST {base}/tools/enhance-video-generative
   {video_url, resolution: 720p|1080p|2k|4k, fps?, bitrate?}  ->  {success, task_id}

3. 轮询任务
   GET {base}/tasks/{task_id}  ->  {status: running|completed|failed,
                                     result: {video_url,...} | error: {code,message}}
   完成后 result.video_url 为增强视频下载地址（默认 24h 过期，需尽快下载）。

鉴权：`Authorization: Bearer <MEDIAKIT_API_KEY>`。

源视频投递择优：
- 配置了云端媒体中转（Aliyun OSS / Cloudinary）时，复用 relay 上传拿公网/签名 URL；
- 本地存储档则走 MediaKit 原生 `mediakit://` 本地上传。
两者都让火山服务器可 fetch。
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable

import httpx

__all__ = [
    "MediaKitError",
    "mediakit_api_key",
    "mediakit_base_url",
    "mediakit_backend_mode",
    "mediakit_configured",
    "enhance_video_file",
    "erase_video_file",
]

_DEFAULT_BASE_URL = "https://mediakit.cn-beijing.volces.com/api/v1"
# 画质增强（大模型版）平均 RTF≈15-20：1 分钟视频约需 15-20 分钟。给足轮询预算。
_DEFAULT_POLL_INTERVAL_SECONDS = 10.0
_DEFAULT_MAX_WAIT_SECONDS = 60 * 60  # 60 分钟

# MediaKit 支持的输出分辨率档位（大写 T 之外的 2k/4k 亦为其原生取值）。
_SUPPORTED_RESOLUTIONS = {"720p", "1080p", "2k", "4k"}

# [aipdd fork delta] 字幕擦除（精细化版，OCR + AIGC 图像修复）。与高清同 base/同鉴权，
# 只是提交端点不同；mode 取 Subtitle（智能检测下半屏白字）或 Text（配 erase_ratio_location
# 框选擦除，可擦任意位置的文本/水印/人名条）。
_ERASE_ENDPOINT = "/tools/erase-video-subtitle-pro"
_ERASE_MODES = {"Subtitle", "Text"}

_LogFn = Callable[[str], None]


class MediaKitError(RuntimeError):
    """MediaKit 调用链任一环节失败（上传/提交/轮询/下载）。"""


def _effective_mediakit_config() -> Any:
    from novelvideo.model_gateway_settings import get_effective_mediakit_config

    return get_effective_mediakit_config(
        env_backend=os.environ.get("VIDEO_ENHANCE_BACKEND", ""),
    )


def mediakit_api_key() -> str:
    return str(_effective_mediakit_config().api_key or "").strip()


def mediakit_base_url() -> str:
    return (
        str(_effective_mediakit_config().base_url or "").strip().rstrip("/")
        or _DEFAULT_BASE_URL
    )


def mediakit_backend_mode() -> str:
    """`auto`（默认，配了就走 MediaKit、失败回退 ffmpeg）| `mediakit` | `ffmpeg`。"""
    return str(_effective_mediakit_config().backend or "auto").strip().lower()


def mediakit_configured() -> bool:
    """[aipdd fork delta] 高清/去字幕引擎策略固定为「有 API Key 就走火山 MediaKit，
    否则本地 ffmpeg」。设置面板已移除可切换的处理模式，这里不再读取用户可选的
    backend 模式；仅保留运维级环境变量 ``VIDEO_ENHANCE_BACKEND=ffmpeg`` 作为强制
    走本地、不下第三方主机调用的总开关。
    """
    env_backend = str(os.environ.get("VIDEO_ENHANCE_BACKEND", "")).strip().lower()
    if env_backend == "ffmpeg":
        return False
    return bool(mediakit_api_key())


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _normalize_media_ref(file_id: str) -> str:
    """MediaKit 返回的 file_id 示例已含 `mediakit://` 前缀；两种形态都归一。"""
    clean = str(file_id or "").strip()
    if not clean:
        raise MediaKitError("MediaKit upload returned empty file_id")
    if clean.startswith("mediakit://"):
        return clean
    return f"mediakit://{clean}"


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "video/mp4"


async def _request_upload_ref(
    *,
    source_path: Path,
    api_key: str,
    base_url: str,
    log: _LogFn,
) -> str:
    """申请上传地址并把本地文件 PUT 上去，返回 `mediakit://` 引用。"""
    data = await asyncio.to_thread(source_path.read_bytes)
    if not data:
        raise MediaKitError(f"source video is empty: {source_path}")
    mime = _guess_mime(source_path)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/tools-sync/request-media-upload-url",
            json={},
            headers=_auth_headers(api_key),
        )
        if resp.status_code != 200:
            raise MediaKitError(
                f"request-media-upload-url failed: HTTP {resp.status_code} "
                f"- {resp.text[:500]}"
            )
        payload = resp.json()
        if not payload.get("success", True):
            raise MediaKitError(f"request-media-upload-url rejected: {payload}")
        result = payload.get("result") or {}
        upload_url = str(result.get("upload_url") or "").strip()
        file_id = str(result.get("file_id") or "").strip()
        raw_headers = result.get("upload_headers") or []
        if not upload_url or not file_id:
            raise MediaKitError(f"upload credentials missing in response: {payload}")

    # upload_headers 为 [{key, value}, ...]；PUT 必须带上其中所有头部。
    put_headers: dict[str, str] = {}
    for item in raw_headers:
        if isinstance(item, dict) and item.get("key"):
            put_headers[str(item["key"])] = str(item.get("value", ""))
    put_headers.setdefault("Content-Type", mime)

    log("上传本地视频到火山 MediaKit...")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        put = await client.put(upload_url, content=data, headers=put_headers)
        if put.status_code not in (200, 201, 204, 206):
            raise MediaKitError(
                f"media PUT upload failed: HTTP {put.status_code} - {put.text[:500]}"
            )
    return _normalize_media_ref(file_id)


async def _resolve_source_ref(
    *,
    source_path: Path,
    api_key: str,
    base_url: str,
    log: _LogFn,
) -> str:
    """择优拿到火山可 fetch 的 video_url。

    云端中转（OSS/Cloudinary）→ 上传拿公网/签名 URL；本地存储档 → MediaKit
    原生 `mediakit://` 上传。
    """
    from novelvideo.storage.media_relay import get_media_relay, media_relay_is_local

    if not media_relay_is_local():
        relay = get_media_relay()
        ext = source_path.suffix.lstrip(".") or "mp4"
        data = await asyncio.to_thread(source_path.read_bytes)
        log("检测到云端媒体中转，上传拿公网 URL 供 MediaKit 抓取...")
        public_url = await asyncio.to_thread(
            relay.upload_bytes,
            data,
            ext=ext,
            ttl=3600,
            resource_type="video",
        )
        public_url = str(public_url or "").strip()
        if not public_url:
            raise MediaKitError("cloud media relay returned no URL")
        return public_url
    return await _request_upload_ref(
        source_path=source_path, api_key=api_key, base_url=base_url, log=log
    )


async def _submit_enhance(
    *,
    video_url: str,
    resolution: str,
    fps: int | None,
    api_key: str,
    base_url: str,
) -> str:
    body: dict[str, Any] = {"video_url": video_url, "resolution": resolution}
    if fps:
        body["fps"] = int(fps)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/tools/enhance-video-generative",
            json=body,
            headers=_auth_headers(api_key),
        )
        if resp.status_code != 200:
            raise MediaKitError(
                f"enhance-video-generative submit failed: HTTP {resp.status_code} "
                f"- {resp.text[:500]}"
            )
        payload = resp.json()
        task_id = str(payload.get("task_id") or "").strip()
        if not payload.get("success", True) or not task_id:
            raise MediaKitError(f"enhance submit rejected: {payload}")
        return task_id


async def _poll_task(
    *,
    task_id: str,
    api_key: str,
    base_url: str,
    poll_interval: float,
    max_wait: float,
    log: _LogFn,
) -> dict[str, Any]:
    """轮询直到 completed 返回 result；failed 抛错；超预算抛错。"""
    elapsed = 0.0
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        while elapsed < max_wait:
            resp = await client.get(f"{base_url}/tasks/{task_id}", headers=headers)
            if resp.status_code != 200:
                log(f"查询任务状态失败: HTTP {resp.status_code}，稍后重试")
            else:
                payload = resp.json()
                status = str(payload.get("status") or "").strip().lower()
                if status == "completed":
                    result = payload.get("result") or {}
                    if not isinstance(result, dict):
                        raise MediaKitError(f"completed but result not object: {payload}")
                    return result
                if status == "failed":
                    err = payload.get("error") or {}
                    raise MediaKitError(
                        "MediaKit task failed: "
                        f"{err.get('code') or 'Unknown'} - {err.get('message') or payload}"
                    )
                # running / 其它非终态：继续等待
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
    raise MediaKitError(f"MediaKit task timed out after {max_wait:.0f}s: {task_id}")


async def _download_to(url: str, output_path: Path, *, log: _LogFn) -> None:
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    raise MediaKitError(f"download HTTP {resp.status_code}")
                await asyncio.to_thread(
                    output_path.parent.mkdir, parents=True, exist_ok=True
                )
                await asyncio.to_thread(output_path.write_bytes, resp.content)
                return
        except Exception as exc:  # noqa: BLE001 - 下载重试
            last_err = exc
            log(f"下载增强视频失败({attempt}/3): {exc}")
            await asyncio.sleep(2 * attempt)
    raise MediaKitError(f"failed to download enhanced video: {last_err}")


async def enhance_video_file(
    *,
    source_path: str | Path,
    output_path: str | Path,
    resolution: str = "1080p",
    fps: int | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait: float = _DEFAULT_MAX_WAIT_SECONDS,
    on_log: _LogFn | None = None,
) -> dict[str, Any]:
    """把本地视频经 MediaKit 生成式超分后写到 `output_path`，返回 meta。

    抛 :class:`MediaKitError` 表示任一环节失败；调用方可据此回退 ffmpeg。
    """
    src = Path(source_path)
    out = Path(output_path)
    if not src.exists():
        raise MediaKitError(f"video source not found: {src}")

    resolved_key = (api_key or mediakit_api_key()).strip()
    if not resolved_key:
        raise MediaKitError(
            "MediaKit API Key 未配置：请到 设置 → 模型与渠道 → VolcEngine 卡内的「火山 AI MediaKit」子区块填写并保存 API Key"
        )
    resolved_base = (base_url or mediakit_base_url()).rstrip("/")

    res = str(resolution or "1080p").strip().lower()
    if res not in _SUPPORTED_RESOLUTIONS:
        # 归一到 MediaKit 支持档位；未知值退回 1080p。
        res = "1080p"

    def _log(msg: str) -> None:
        if on_log:
            on_log(msg)

    video_ref = await _resolve_source_ref(
        source_path=src, api_key=resolved_key, base_url=resolved_base, log=_log
    )
    _log(f"提交 MediaKit 生成式超分任务 (resolution={res})...")
    task_id = await _submit_enhance(
        video_url=video_ref, resolution=res, fps=fps,
        api_key=resolved_key, base_url=resolved_base,
    )
    _log(f"MediaKit 任务已提交: {task_id}")
    result = await _poll_task(
        task_id=task_id, api_key=resolved_key, base_url=resolved_base,
        poll_interval=poll_interval, max_wait=max_wait, log=_log,
    )
    enhanced_url = str(result.get("video_url") or "").strip()
    if not enhanced_url:
        raise MediaKitError(f"MediaKit result missing video_url: {result}")
    _log("下载增强后的视频...")
    await _download_to(enhanced_url, out, log=_log)

    return {
        "backend": "mediakit",
        "provider": "volcengine_mediakit",
        "resolution": res,
        "mediakit_task_id": task_id,
        "source_media": "relay_public_url" if video_ref.startswith("http") else "mediakit_upload",
        "enhanced_duration": result.get("duration"),
        "enhanced_fps": result.get("fps"),
        "enhanced_resolution": result.get("resolution"),
    }


async def _submit_erase(
    *,
    body: dict[str, Any],
    api_key: str,
    base_url: str,
) -> str:
    """提交字幕擦除（精细化版）异步任务，返回 task_id。"""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}{_ERASE_ENDPOINT}",
            json=body,
            headers=_auth_headers(api_key),
        )
        if resp.status_code != 200:
            raise MediaKitError(
                f"erase-video-subtitle-pro submit failed: HTTP {resp.status_code} "
                f"- {resp.text[:500]}"
            )
        payload = resp.json()
        task_id = str(payload.get("task_id") or "").strip()
        if not payload.get("success", True) or not task_id:
            raise MediaKitError(f"erase submit rejected: {payload}")
        return task_id


async def erase_video_file(
    *,
    source_path: str | Path,
    output_path: str | Path,
    mode: str = "Subtitle",
    erase_ratio_location: list[dict[str, float]] | None = None,
    model_version: str = "v5",
    api_key: str | None = None,
    base_url: str | None = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait: float = _DEFAULT_MAX_WAIT_SECONDS,
    on_log: _LogFn | None = None,
) -> dict[str, Any]:
    """[aipdd fork delta] 把本地视频经 MediaKit 字幕擦除（精细化版）后写到
    `output_path`，返回 meta。抛 :class:`MediaKitError` 表示任一环节失败；
    调用方可据此回退本地 ffmpeg delogo。

    - `mode="Subtitle"`：智能自动检测（仅处理画面下半部、水平居中的白字）。
    - `mode="Text"` + `erase_ratio_location`：在给定归一化矩形内擦除所有文本
      （水印/台标/人名条/自定义字幕框）。坐标为 [{top_left_x, top_left_y,
      bottom_right_x, bottom_right_y}]，均 0.0~1.0。
    """
    src = Path(source_path)
    out = Path(output_path)
    if not src.exists():
        raise MediaKitError(f"video source not found: {src}")

    resolved_key = (api_key or mediakit_api_key()).strip()
    if not resolved_key:
        raise MediaKitError(
            "MediaKit API Key 未配置：请到 设置 → 模型与渠道 → VolcEngine 卡内的「火山 AI MediaKit」子区块填写并保存 API Key"
        )
    resolved_base = (base_url or mediakit_base_url()).rstrip("/")

    erase_mode = str(mode or "Subtitle").strip().capitalize()
    if erase_mode not in _ERASE_MODES:
        erase_mode = "Subtitle"

    def _log(msg: str) -> None:
        if on_log:
            on_log(msg)

    video_ref = await _resolve_source_ref(
        source_path=src, api_key=resolved_key, base_url=resolved_base, log=_log
    )
    body: dict[str, Any] = {"video_url": video_ref, "mode": erase_mode}
    if model_version:
        body["model_version"] = str(model_version).strip().lower()
    if erase_ratio_location:
        body["erase_ratio_location"] = erase_ratio_location

    _log(f"提交 MediaKit 字幕擦除任务 (mode={erase_mode})...")
    task_id = await _submit_erase(
        body=body, api_key=resolved_key, base_url=resolved_base
    )
    _log(f"MediaKit 擦除任务已提交: {task_id}")
    result = await _poll_task(
        task_id=task_id, api_key=resolved_key, base_url=resolved_base,
        poll_interval=poll_interval, max_wait=max_wait, log=_log,
    )
    erased_url = str(result.get("video_url") or "").strip()
    if not erased_url:
        raise MediaKitError(f"MediaKit result missing video_url: {result}")
    _log("下载擦除后的视频...")
    await _download_to(erased_url, out, log=_log)

    return {
        "backend": "mediakit",
        "provider": "volcengine_mediakit",
        "erase_mode": erase_mode,
        "model_version": body.get("model_version"),
        "mediakit_task_id": task_id,
        "source_media": "relay_public_url" if video_ref.startswith("http") else "mediakit_upload",
        "erased_duration": result.get("duration"),
    }
