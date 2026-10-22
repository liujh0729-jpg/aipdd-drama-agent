# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片图册的素材通路：海报随包，原片按需取。

图册里的每条案例有两段素材：一张海报（静帧，几百 KB）和作者的原片（几十 MB，
37 条合计约 0.75 GB）。原片全塞进安装包不现实 —— 装完就 1 GB 起步，而且多数
人只想看海报和拿走提示词。于是按两套走：

- **海报**随包发布，落在 `motion_posters/<case_id>/poster.webp`，同源免鉴权提供，
  和画风图墙一样是 `<asset_base>/<清单相对路径>` 的拼法。
- **原片**不随包。默认也走同源 `/api/v1/motion-gallery/<case_id>/<文件名>`：
  本机缓存里没有就按 `MOTION_GALLERY_MIRROR` 现取、校验后落进状态卷再回给前端；
  没配镜像就 404，前端只显示海报，不影响取提示词。运维也可以把
  `MOTION_GALLERY_ASSET_BASE` 直接指到 OSS/CDN，那样两段素材都由外部提供，
  本模块完全不参与。

严格用清单做 allowlist：case_id 与文件名必须逐条对上清单里的 `media.file`，
路径解析后必须仍在缓存根内，挡掉 `..` 穿越与任意文件读取。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx

from novelvideo.config import STATE_DIR
from novelvideo.freezone.motion_templates import load_motion_cases

logger = logging.getLogger(__name__)

POSTER_BASENAME = "poster.webp"
PREVIEW_BASENAME = "preview.mp4"
MIRROR_ENV = "MOTION_GALLERY_MIRROR"
# 清单没登记字节数时的下载上限：镜像换错内容（或被人改成错误页/大文件）时，
# 边下边判，不把状态卷写满再报错。
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

# 随包海报的根目录；与清单里 cover 的相对路径（`<case_id>/poster.webp`）对齐。
PACKAGE_POSTER_ROOT = Path(__file__).with_name("motion_posters")
# 随包小样片：480p/几秒/无声，卡片悬停与详情首屏用它，不占原片那 0.75 GB。
PACKAGE_PREVIEW_ROOT = Path(__file__).with_name("motion_previews")

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$", re.IGNORECASE)

# 下载中的 (case_id, filename) -> Lock：同一原片被连点时只落一次盘。
_download_locks: dict[tuple[str, str], asyncio.Lock] = {}


def gallery_root() -> Path:
    """状态卷里的素材根（容器重建不丢、安装后可写）。"""
    return Path(STATE_DIR) / "_shared" / "motion_gallery"


def video_cache_path(case_id: str, filename: str) -> Optional[Path]:
    """原片在本机的缓存路径；不在清单 allowlist 上时 None。"""
    case = get_case(case_id)
    if not case or filename != case["media"]["file"] or not filename:
        return None
    root = (gallery_root() / "videos").resolve()
    target = (root / case_id / filename).resolve()
    if not target.is_relative_to(root):
        return None
    return target


def poster_path(case_id: str) -> Optional[Path]:
    """海报的随包路径；清单里没有这条案例时 None。

    文件名固定 `poster.webp`（与清单 cover 的 basename 对齐），真实 content-type
    由字节魔数决定，所以打包时留 png/jpg 也能正常显示。
    """
    case = get_case(case_id)
    if not case:
        return None
    root = PACKAGE_POSTER_ROOT.resolve()
    target = (root / case_id / POSTER_BASENAME).resolve()
    if not target.is_relative_to(root):
        return None
    return target


def preview_path(case_id: str) -> Optional[Path]:
    """小样片的随包路径；清单里没有这条案例时 None。"""
    case = get_case(case_id)
    if not case:
        return None
    root = PACKAGE_PREVIEW_ROOT.resolve()
    target = (root / case_id / PREVIEW_BASENAME).resolve()
    if not target.is_relative_to(root):
        return None
    return target


def get_case(case_id: str) -> Optional[dict]:
    for case in load_motion_cases():
        if case["id"] == case_id:
            return case
    return None


def mirror_base() -> str:
    return os.environ.get(MIRROR_ENV, "").strip().rstrip("/")


def has_poster(case_id: str) -> bool:
    path = poster_path(case_id)
    return bool(path and path.is_file())


def has_preview(case_id: str) -> bool:
    path = preview_path(case_id)
    return bool(path and path.is_file())


def has_video(case_id: str) -> bool:
    case = get_case(case_id)
    if not case:
        return False
    path = video_cache_path(case_id, case["media"]["file"])
    return bool(path and path.is_file())


def video_ready(case_id: str) -> bool:
    """这条案例的原片现在放得出来吗：本机已缓存，或配了镜像可以现取。

    前端拿逐条的它决定「播放原片」按钮 —— 只用全局 ``videos_available`` 会在
    「缓存过几条但没配镜像」时给一条根本没下载的案例也摆出按钮，点了只剩转圈。
    """
    if mirror_base():
        return True
    return has_video(case_id)


def poster_cache_token(case_id: str) -> str:
    """mtime_ns 做 cache-busting：换海报后 URL 变，浏览器不会命中旧缓存。"""
    path = poster_path(case_id)
    if not path or not path.is_file():
        return ""
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return ""


def preview_cache_token(case_id: str) -> str:
    """小样片的版本号；重截后 URL 跟着变，避免浏览器回放旧片。"""
    path = preview_path(case_id)
    if not path or not path.is_file():
        return ""
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return ""


def sniff_content_type(data: bytes) -> str:
    """按魔数判定图片 content-type（照 style_gallery 的口径）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def media_type_for(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".mp4"):
        return "video/mp4"
    if lowered.endswith(".webm"):
        return "video/webm"
    if lowered.endswith(".mov"):
        return "video/quicktime"
    if lowered.endswith(".mkv"):
        return "video/x-matroska"
    return "application/octet-stream"


def _digest_matches(path: Path, sha256_hex: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == sha256_hex.lower()


def _looks_like_video_container(path: Path, filename: str) -> bool:
    """没带 sha256 时的最低限度体检：头几字节得是它声称的那种容器。

    镜像挂了 / 返回登录页时会给 200 + HTML，不校验就等于把一块垃圾永久缓存下来。
    认不出的扩展名（.avi、裸流）放行，别为了体检把真素材挡在门外。
    """
    lowered = filename.lower()
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if lowered.endswith((".mp4", ".mov", ".m4v")):
        return head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")
    if lowered.endswith((".webm", ".mkv")):
        return head.startswith(b"\x1a\x45\xdf\xa3")
    return True


async def _download(case_id: str, filename: str, target: Path, expect_bytes: int, sha256_hex: str) -> None:
    url = f"{mirror_base()}/{case_id}/{filename}"
    tmp = target.with_name(f"{target.name}.part.{os.getpid()}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=600.0),
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                ceiling = (expect_bytes + 1024 * 1024) if expect_bytes else MAX_DOWNLOAD_BYTES
                written = 0
                with tmp.open("wb") as fh:
                    async for chunk in response.aiter_bytes(1024 * 256):
                        written += len(chunk)
                        # 超出上限就当场断流：后面的字节一个都不该落盘。
                        if written > ceiling:
                            raise ValueError(
                                f"镜像返回的内容超出清单登记的大小（已读 {written} 字节，"
                                f"上限 {ceiling}），已中止下载"
                            )
                        fh.write(chunk)
        size = tmp.stat().st_size
        if expect_bytes and size != expect_bytes:
            raise ValueError(f"字节数不符：期望 {expect_bytes}，实得 {size}")
        if sha256_hex:
            if not _digest_matches(tmp, sha256_hex):
                raise ValueError("sha256 校验不通过")
        elif not _looks_like_video_container(tmp, filename):
            raise ValueError("清单没带 sha256，且下载内容的文件头不是视频容器（疑似镜像返回了错误页）")
        os.replace(tmp, target)
    finally:
        # 成功路径上 tmp 已经被 replace 掉；失败/异常时把半成品清干净。
        if tmp.exists():
            tmp.unlink(missing_ok=True)


async def ensure_video(case_id: str) -> Optional[Path]:
    """取原片：命中本机缓存直接回；否则按镜像现取。取不到回 None（前端只显示海报）。"""
    case = get_case(case_id)
    if not case:
        return None
    media = case["media"]
    filename = media["file"]
    if not filename:
        return None
    target = video_cache_path(case_id, filename)
    if target is None:
        return None
    if target.is_file():
        return target

    base = mirror_base()
    if not base:
        logger.info("[motion-gallery] 案例 %s 未缓存，且未配置 %s", case_id, MIRROR_ENV)
        return None

    lock = _download_locks.setdefault((case_id, filename), asyncio.Lock())
    async with lock:
        # 等锁期间别的请求可能已经下好了，别重复下载一遍。
        if target.is_file():
            return target
        try:
            await _download(
                case_id,
                filename,
                target,
                int(media.get("bytes") or 0),
                str(media.get("sha256") or ""),
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("[motion-gallery] 下载原片失败 %s/%s: %s", case_id, filename, exc)
            return None
        logger.info("[motion-gallery] 原片已缓存 %s/%s", case_id, filename)
    return target if target.is_file() else None


async def resolve_video(case_id: str, filename: str) -> Optional[Path]:
    """serve 用：文件名先过清单 allowlist，再走缓存/镜像。不合法与取不到都回 None。"""
    target = video_cache_path(case_id, filename)
    if target is None:
        return None
    if target.is_file():
        return target
    return await ensure_video(case_id)


def videos_available() -> bool:
    """原片到底取不取到：配了镜像，或本机已经缓存过任意一条。

    前端据此决定「播放原片」出不出现 —— 取不到时按钮留着只会点了转圈，不如不给。
    """
    if mirror_base():
        return True
    return any(has_video(case["id"]) for case in load_motion_cases())


def parse_range(header: str, size: int) -> Optional[tuple[int, int]]:
    """解析单段 Range；不支持多段与非法值时回 None（调用方回 416 或整段）。"""
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    start_text, end_text = match.group(1), match.group(2)
    if not start_text and not end_text:
        return None
    if not start_text:
        # `bytes=-N`：尾部 N 字节。
        suffix = int(end_text)
        if suffix <= 0:
            return None
        return (max(size - suffix, 0), size - 1)
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or end < start:
        return None
    return (start, min(end, size - 1))


def video_headers(size: int, cache_control: str, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Cache-Control": cache_control,
    }
    if extra:
        headers.update(extra)
    return headers


def video_partial_headers(start: int, end: int, size: int, cache_control: str) -> dict[str, str]:
    headers = video_headers(end - start + 1, cache_control)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return headers


def open_chunk(path: Path, start: int, length: int, chunk_size: int = 1024 * 512):
    """从 start 起读 length 字节，分块 yield；生成器自己负责关文件。"""
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
