"""Server-authoritative billing metrics for reference-video generation."""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
from collections.abc import Iterable, Sequence

# 上游视频模型能吃的画幅档位就这些（具体某个模型允许哪几档由渠道目录声明）。
STANDARD_VIDEO_ASPECT_RATIOS: tuple[str, ...] = (
    "21:9",
    "16:9",
    "4:3",
    "3:4",
    "1:1",
    "2:3",
    "3:2",
    "9:16",
)


async def probe_video_duration_seconds(path: str) -> float:
    """Read one input video's real duration with ffprobe.

    Downstream code must not trust a duration supplied by the browser. The API
    resolves project-local URLs first and calls this helper before enqueueing.
    """

    def _probe() -> float:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[-500:]
            raise ValueError(detail or "ffprobe could not read video duration")
        try:
            duration = float((result.stdout or "").strip())
        except ValueError as exc:
            raise ValueError("ffprobe returned an invalid video duration") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("video duration must be positive")
        return duration

    return await asyncio.to_thread(_probe)


async def probe_total_video_duration_seconds(paths: Iterable[str]) -> float:
    clean_paths = [str(path or "").strip() for path in paths if str(path or "").strip()]
    if not clean_paths:
        return 0.0
    durations = await asyncio.gather(
        *(probe_video_duration_seconds(path) for path in clean_paths)
    )
    return sum(durations)


def _probe_video_frame_size(path: str) -> tuple[int, int]:
    """``(width, height)``，已经按 rotation 换算成**实际显示**的宽高。

    用 ``-show_streams`` 整段 JSON 而不是 ``-show_entries stream=width,height``：
    手机拍的视频大量带 ``rotate 90/270`` 的显示矩阵，裸读 width/height 会把竖屏
    读成横屏，画幅判据直接反过来。
    """

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[-500:]
        raise ValueError(detail or "ffprobe could not read video frame size")
    try:
        streams = (json.loads(result.stdout or "{}").get("streams") or [])[0]
    except (ValueError, IndexError) as exc:
        raise ValueError("ffprobe returned an invalid video stream descriptor") from exc
    width = int(streams.get("width") or 0)
    height = int(streams.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("video frame size must be positive")
    rotation = 0.0
    for entry in streams.get("side_data_list") or []:
        if str(entry.get("side_data_type") or "").lower() == "display matrix":
            rotation = float(entry.get("rotation") or 0.0)
    if not rotation:
        rotation = float(streams.get("rotation") or 0.0)
    if abs(((rotation + 360) % 360) - 90) < 1 or abs(((rotation + 360) % 360) - 270) < 1:
        width, height = height, width
    return width, height


async def probe_video_aspect_ratio(
    path: str,
    allowed: Sequence[str] | None = None,
) -> str:
    """按**源视频画面**比例，在 ``allowed``（默认通用档位）里选出最接近的一档。

    ``allowed`` 由渠道目录提供时，本函数不会自创上游不认的档位；没声明就退回
    :data:`STANDARD_VIDEO_ASPECT_RATIOS`。距离用对数比，保证 9:16 与 2:3 这种
    「倒数族」不会因为数值差大小选错方向。
    """

    width, height = await asyncio.to_thread(_probe_video_frame_size, path)
    options = [str(item).strip() for item in (allowed or STANDARD_VIDEO_ASPECT_RATIOS) if str(item).strip()]
    if not options:
        options = list(STANDARD_VIDEO_ASPECT_RATIOS)
    wanted = width / height

    def _value(token: str) -> float | None:
        parts = token.replace("：", ":").split(":")
        if len(parts) != 2:
            return None
        try:
            num, den = float(parts[0]), float(parts[1])
        except ValueError:
            return None
        return num / den if den else None

    scored = [
        (abs(math.log(wanted) - math.log(value)), token)
        for token, value in ((_t, _value(_t)) for _t in options)
        if value
    ]
    if not scored:
        return "16:9"
    return min(scored)[1]
