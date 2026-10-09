"""[aipdd fork delta] req21 音视频提取文案：通用媒体转写管线。

把「抖音文案提取」泛化为两大类：

- **A 视频提取文案**：链接下载（YouTube / B站 / 快手 / 小红书 / TikTok / 通用）
  与上传视频，先本地分离音频再走 ASR；抖音保留 **yt-dlp ↔ 宿主 Chrome（CDP）双通路**
  （一条不通走另一条）；
- **B 音频提取文案**：上传音频直接走 ASR。

设计取舍：

- 统一链接下载用 ``yt-dlp``（自动识别绝大多数平台），运行期依赖 ffmpeg 合流；
- 上传的视频 / 音频由前端先传 ``/freezone/upload`` 落 ``freezone/_uploads/``，
  本叶把同源静态 URL 反解为本地文件（``resolve_static_url_to_path``）；
- 音频分离复用 douyin 叶同一 ``_extract_audio_16k_wav``（16k 单声道 wav）；
- ASR 三通道（网关 / FunASR / 豆包 openspeech）与候选解析全部复用
  ``douyin_copywrite``，组织 fail-closed 口径不变；
- **本叶只做「媒体 → 文案 → 落盘 txt」**，回传本地视频 / 音频路径；资产库入站
  由 runner 完成（runner 持有 ``ctx`` 才能拼静态 URL）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone.douyin_copywrite import (
    _extract_audio_16k_wav,
    _fetch_douyin_detail,
    _fetch_subtitle_text,
    _render_copywrite_file,
    _resolve_local_audio,
    _transcribe_via_doubao_asr,
    _transcribe_via_funasr,
    _transcribe_via_gateway,
    extract_share_url,
    parse_aweme_detail,
    resolve_douyin_asr_candidate,
    resolve_douyin_video_id,
)
from novelvideo.freezone.paths import outputs_dir, resolve_static_url_to_path

logger = logging.getLogger(__name__)

# [aipdd fork delta] 进度回调类型：(progress 0-1, 当前步骤文案)。执行叶拿不到 ctx，
# 由 runner 传入闭包把每一步写进任务进度 / 日志，解决「只有开始下载一句、中间全黑」。
StatusReporter = Callable[[float, str], None]

# 产物落盘目录名（与 outputs_dir 第二参数、runner task_type 一致）。
_TASK_TYPE = "freezone_media_copywrite"

# 来源类型（与前端 / schema 对齐）。
SOURCE_LINK = "link"
SOURCE_UPLOAD_VIDEO = "upload_video"
SOURCE_UPLOAD_AUDIO = "upload_audio"
_SOURCE_TYPES = {SOURCE_LINK, SOURCE_UPLOAD_VIDEO, SOURCE_UPLOAD_AUDIO}

# 平台下拉取值：douyin 走双通路，其余交 yt-dlp 自动识别（值仅作 headers 提示）。
_DOUYIN_PLATFORM = "douyin"
# 抖音桌面 UA / Referer（与 douyin_copywrite 详情口径一致），注入 yt-dlp。
_DOUYIN_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}


@dataclass(frozen=True, slots=True)
class MediaCopywriteResult:
    text_path: Path
    text: str
    source: str  # asr / subtitle
    model: str
    title: str
    author: str
    duration_seconds: int
    video_id: str
    # 供 runner 归档进资产库（可能为 None：纯音频来源无视频、下载失败无音频）。
    video_path: Path | None
    audio_path: Path | None


# ============================================================
# yt-dlp 统一下载
# ============================================================


def _download_via_ytdlp_sync(url: str, work_dir: Path, platform: str) -> dict[str, Any]:
    """yt-dlp 下载最佳合流视频到 ``work_dir/source.*``，返回元信息。

    同步实现，调用方用 ``asyncio.to_thread`` 包裹。失败（网络 / 提取器漂移 /
    需登录）抛异常，由抖音双通路逻辑捕获后回退宿主 Chrome CDP。
    """

    import glob

    import yt_dlp

    # [aipdd fork delta] 分享文案常混带随机口令与中文，直接喂 yt-dlp 会被
    # generic 提取器判「is not a valid URL」立即失败；先抠出真实链接再提交。
    url = extract_share_url(url)
    if not url:
        raise RuntimeError("未从输入中解析到有效链接")

    work_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": True,
    }
    if platform == _DOUYIN_PLATFORM:
        options["http_headers"] = dict(_DOUYIN_HTTP_HEADERS)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp 未返回有效媒体信息")
    # 合流后按 mp4 优先，其次任意 source.* 落盘文件。
    merged = work_dir / "source.mp4"
    if not merged.exists():
        candidates = sorted(glob.glob(str(work_dir / "source.*")))
        if not candidates:
            raise RuntimeError("yt-dlp 下载完成但未找到落盘文件")
        merged = Path(candidates[0])
    return {
        "video_path": merged,
        "title": str(info.get("title") or ""),
        "author": str(info.get("uploader") or info.get("channel") or ""),
        "duration_seconds": int(info.get("duration") or 0),
        "video_id": str(info.get("id") or ""),
    }


async def _download_via_ytdlp(url: str, work_dir: Path, platform: str) -> dict[str, Any]:
    return await asyncio.to_thread(_download_via_ytdlp_sync, url, work_dir, platform)


# ============================================================
# 抖音通路编排（yt-dlp → 纯 HTTP Cookie → 宿主 Chrome CDP 兜底）
# ============================================================


async def _resolve_douyin_link(
    url: str,
    work_dir: Path,
    *,
    on_status: StatusReporter | None = None,
) -> dict[str, Any]:
    """抖音链接：先试 yt-dlp，失败后走详情主链路（纯 HTTP Cookie → CDP 兜底）。

    返回统一形状 ``{video_path|None, music_url, video_url, subtitle_infos,
    title, author, duration_seconds, video_id, via}``。``via`` 标记实际详情通路
    （``http`` / ``cdp``），CDP 通路带 ``music_url``（原声直链，FunASR 可用）与内置字幕兜底。

    [aipdd fork delta req45] 这一路以前直接叫 `_intercept_douyin_detail`（只走 CDP），
    等于把「配了抖音 Cookie 也不再开浏览器」这条主链路在本入口绕开了：同一个抖音提取，
    走白板 / 工具页的另一入口能用 Cookie，走这里却永远要求调试端口的 Chrome 开着。现在
    统一复用 `_fetch_douyin_detail`：渠道选择只有一处真源，Cookie 自动记录也顺带生效。

    [aipdd fork delta] 每一步经 ``on_status`` 上报进度（yt-dlp 下载 → 未通回退
    浏览器），并把 yt-dlp 的失败原因一并带进最终异常，避免「只看到 CDP
    报错、不知道 yt-dlp 试过且为何没通」。
    """

    ytdlp_error: str = ""
    if on_status is not None:
        on_status(0.15, "用 yt-dlp 下载/解析媒体…")
    try:
        info = await _download_via_ytdlp(url, work_dir, _DOUYIN_PLATFORM)
        return {
            "video_path": info["video_path"],
            "music_url": "",
            "video_url": "",
            "subtitle_infos": None,
            "title": info["title"],
            "author": info["author"],
            "duration_seconds": info["duration_seconds"],
            "video_id": info["video_id"],
            "via": "ytdlp",
        }
    except Exception as exc:  # noqa: BLE001 - yt-dlp 抖音提取器易漂移，回退宿主 Chrome CDP
        ytdlp_error = str(exc) or type(exc).__name__
        logger.warning(
            "media copywrite: douyin yt-dlp failed, fallback to host Chrome CDP: %s", exc
        )
    if on_status is not None:
        on_status(0.3, f"yt-dlp 未通，改走详情接口（Cookie / 浏览器兜底）：{ytdlp_error}")

    if on_status is not None:
        on_status(0.35, "解析抖音详情中…")
    try:
        video_id = await resolve_douyin_video_id(url)
        detail, detail_via = await _fetch_douyin_detail(video_id)
    except Exception as exc:  # noqa: BLE001 - 两路都失败时把 yt-dlp 原因也带出去
        if ytdlp_error:
            raise RuntimeError(
                f"{exc}（此前 yt-dlp 已失败：{ytdlp_error}）"
            ) from exc
        raise
    parsed = parse_aweme_detail(detail)
    return {
        "video_path": None,
        "music_url": str(parsed.get("music_audio_url") or ""),
        "video_url": str(parsed.get("video_url") or ""),
        "subtitle_infos": parsed.get("subtitle_infos"),
        "title": str(parsed.get("desc") or ""),
        "author": str(parsed.get("author") or ""),
        "duration_seconds": int(parsed.get("duration_seconds") or 0),
        "video_id": video_id,
        "via": detail_via,
    }


# ============================================================
# ASR 分派（三通道，复用 douyin 实现）
# ============================================================


async def _transcribe(
    candidate: dict[str, str],
    *,
    audio_path: Path | None,
    audio_url: str,
    job_id: str,
    egress_context: TrustedEgressContext | None,
) -> tuple[str, str]:
    """按候选通道转写，返回 ``(text, source_label)``。

    - FunASR：平台只接受公网音频 URL（无上传接口），仅抖音 CDP 原声直链可用；
    - 豆包语音：公网直链优先，否则本地音频 base64；
    - 网关：本地音频 multipart 上传（任意来源都能自动化）。
    """

    display = candidate.get("display") or candidate["model"]
    kind = candidate["kind"]
    if kind == "funasr":
        if not audio_url:
            raise RuntimeError(
                "该来源没有可用的公网音频直链，FunASR 无法转写；请改选网关 ASR 模型"
                "（本地音频可直接上传）"
            )
        text = await _transcribe_via_funasr(audio_url=audio_url, job_id=job_id)
    elif kind == "doubao_asr":
        if audio_path is None and not audio_url:
            raise RuntimeError("缺少音频源，无法使用豆包语音转写")
        text = await _transcribe_via_doubao_asr(
            variant=candidate["model"],
            audio_url=audio_url,
            audio_path=audio_path,
            job_id=job_id,
        )
    else:
        from novelvideo.model_gateway_settings import provider_spot_model

        if audio_path is None:
            raise RuntimeError("缺少本地音频，无法使用网关 ASR 转写")
        text = await _transcribe_via_gateway(
            audio_path=audio_path,
            model=provider_spot_model(candidate["provider"], candidate["model"]),
            job_id=job_id,
            egress_context=egress_context,
        )
    return str(text or "").strip(), f"语音识别（{display}）"


# ============================================================
# 主叶函数（runner 经 _call_freezone_leaf 调用）
# ============================================================


async def run_freezone_media_copywrite(
    *,
    project_dir: Path,
    job_id: str,
    source_type: str,
    url: str,
    platform: str = "",
    model: str = "",
    egress_context: TrustedEgressContext | None = None,
    on_status: StatusReporter | None = None,
) -> MediaCopywriteResult:
    """音视频提取文案执行叶：解析来源 → 分离音频 → 用户指定 ASR 转写 → 落盘 txt。

    出网说明（runner 记为 NETWORK / EG-15c）：链接下载直连公开站点与 CDN（无凭据、
    只传用户显式提供的链接）；网关 ASR 经 ``begin_trusted_audio_operation`` 出网；
    FunASR / 豆包语音直连使用平台侧凭据，组织任务 fail-closed（须改选网关模型）。

    [aipdd fork delta] ``on_status`` 由 runner 传入，将本叶的每一步写回任务进度 /
    日志，确保前端能看到「正在 yt-dlp → 失败原因 → 回退 CDP → 拆音频 → ASR」而
    不是长期卡在「开始下载/解析媒体…」。
    """

    def _report(progress: float, message: str) -> None:
        logger.info("media copywrite [%s] %.2f: %s", job_id, progress, message)
        if on_status is None:
            return
        try:
            on_status(progress, message)
        except Exception:  # noqa: BLE001 - 进度上报失败不得打断主流程
            logger.debug("media copywrite status report failed", exc_info=True)

    candidate = resolve_douyin_asr_candidate(model)
    is_organization = (
        egress_context is not None and bool(egress_context.is_organization)
    )
    if candidate["kind"] in {"funasr", "doubao_asr"} and is_organization:
        raise RuntimeError(
            "ORG_EGRESS_DENIED: "
            f"{candidate.get('display') or candidate['model']} 直连使用平台侧凭据，"
            "组织任务请选择网关 ASR 模型"
        )

    clean_source = str(source_type or "").strip().lower()
    if clean_source not in _SOURCE_TYPES:
        raise ValueError(f"未知的来源类型: {source_type}")
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("链接 / 上传素材地址不能为空")

    outputs = outputs_dir(project_dir, _TASK_TYPE)
    work_dir = outputs / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    video_path: Path | None = None
    audio_path: Path | None = None
    music_url = ""
    subtitle_infos: Any = None
    title = ""
    author = ""
    duration_seconds = 0
    video_id = ""

    if clean_source == SOURCE_UPLOAD_AUDIO:
        _report(0.4, "上传音频，直接准备转写…")
        audio_path = resolve_static_url_to_path(clean_url, project_dir)
        if not audio_path.exists():
            raise OSError(f"上传音频文件不存在: {audio_path}")
    elif clean_source == SOURCE_UPLOAD_VIDEO:
        _report(0.4, "上传视频，本地分离音频…")
        video_path = resolve_static_url_to_path(clean_url, project_dir)
        if not video_path.exists():
            raise OSError(f"上传视频文件不存在: {video_path}")
    else:  # SOURCE_LINK
        plat = str(platform or "").strip().lower()
        if plat == _DOUYIN_PLATFORM:
            resolved = await _resolve_douyin_link(
                clean_url, work_dir, on_status=_report
            )
            video_path = resolved["video_path"]
            music_url = resolved["music_url"]
            subtitle_infos = resolved["subtitle_infos"]
            title = resolved["title"]
            author = resolved["author"]
            duration_seconds = resolved["duration_seconds"]
            video_id = resolved["video_id"]
            # CDP 通路：优先原声直链 / 视频下载取音频；yt-dlp 通路：从视频抽。
            if resolved["via"] == "cdp":
                _report(0.45, "拉取抖音音轨中…")
                audio_path = await _resolve_local_audio(
                    music_url=music_url,
                    video_url=resolved["video_url"],
                    work_dir=work_dir,
                )
        else:
            _report(0.15, "用 yt-dlp 下载/解析媒体…")
            try:
                info = await _download_via_ytdlp(clean_url, work_dir, plat)
            except Exception as exc:  # noqa: BLE001 - 上报失败原因后直接抛出
                _report(0.3, f"yt-dlp 下载/解析失败：{exc}")
                raise
            video_path = info["video_path"]
            title = info["title"]
            author = info["author"]
            duration_seconds = info["duration_seconds"]
            video_id = info["video_id"]

    # 视频来源统一在缺音频时从视频抽 16k 单声道 wav（抖音 CDP 已备好则跳过）。
    if audio_path is None and video_path is not None:
        _report(0.5, "本地分离音频（ffmpeg 16k 单声道 wav）…")
        audio_path = await _extract_audio_16k_wav(video_path, work_dir / "audio.wav")

    _report(
        0.6,
        f"ASR 转写中（模型：{candidate.get('display') or candidate['model']}）…",
    )
    text, source_label = await _transcribe(
        candidate,
        audio_path=audio_path,
        audio_url=music_url,
        job_id=job_id,
        egress_context=egress_context,
    )
    source = "asr"
    if not text:
        # 仅抖音 CDP 通路有内置字幕可兜底。
        _report(0.75, "ASR 未得文案，回退内置字幕…")
        subtitle_text = await _fetch_subtitle_text(subtitle_infos)
        if subtitle_text and len(subtitle_text) > 10:
            text = subtitle_text
            source = "subtitle"
            source_label = "视频内置字幕"
    if not text:
        raise RuntimeError(
            "未能提取到文案：ASR 未识别到有效语音内容，且没有可用的内置字幕"
        )

    display_label = candidate.get("display") or candidate["model"]
    _report(0.85, "写入文案 txt…")
    text_path = outputs / f"{job_id}.txt"
    text_path.write_text(
        _render_copywrite_file(
            video_id=video_id,
            title=title,
            author=author,
            stats={},
            source_label=source_label,
            model_label=display_label if source == "asr" else "",
            text=text,
        ),
        encoding="utf-8",
    )
    return MediaCopywriteResult(
        text_path=text_path,
        text=text,
        source=source,
        model=candidate["id"],
        title=title,
        author=author,
        duration_seconds=duration_seconds,
        video_id=video_id,
        video_path=video_path,
        audio_path=audio_path,
    )
