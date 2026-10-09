"""宽高比与媒体类型工具（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from PIL import Image

from .constants import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    AUDIO_EXTS,
    TEXT_EXTS,
    FREEZONE_PRESET_IMAGE_ASPECT_RATIOS,
)


def _greatest_common_divisor(a: int, b: int) -> int:
    x = abs(int(a))
    y = abs(int(b))
    while y:
        x, y = y, x % y
    return x or 1


def _image_aspect_ratio(path: Path) -> str:
    if not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        return "1:1"
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return "1:1"
    if width <= 0 or height <= 0:
        return "1:1"
    gcd = _greatest_common_divisor(width, height)
    return f"{round(width / gcd)}:{round(height / gcd)}"


def _parse_aspect_ratio_value(value: str) -> float | None:
    try:
        left, right = str(value or "").split(":", 1)
        width = float(left)
        height = float(right)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _nearest_supported_image_aspect_ratio(value: str, fallback: str = "1:1") -> str:
    if value in FREEZONE_PRESET_IMAGE_ASPECT_RATIOS:
        return value
    actual = _parse_aspect_ratio_value(value)
    if actual is None:
        return fallback

    def distance(candidate: str) -> float:
        candidate_value = _parse_aspect_ratio_value(candidate)
        if candidate_value is None:
            return float("inf")
        return abs(math.log(actual / candidate_value))

    return min(FREEZONE_PRESET_IMAGE_ASPECT_RATIOS, key=distance)


def _normalize_supported_image_aspect_ratio(value: Any, fallback: str = "2:3") -> str:
    ratio = str(value or "").strip().replace("-", ":")
    if not ratio:
        return fallback
    if _parse_aspect_ratio_value(ratio) is None:
        return fallback
    return _nearest_supported_image_aspect_ratio(ratio, fallback=fallback)


def _project_sketch_aspect_ratio(
    project_config: dict[str, Any] | None,
    episode: Any,
    fallback: str = "2:3",
) -> str:
    config = project_config or {}
    ep_key = str(episode or "").strip()
    by_episode = config.get("sketch_aspect_ratio_by_episode") or {}
    if isinstance(by_episode, dict) and ep_key:
        value = by_episode.get(ep_key)
        if value is None:
            try:
                value = by_episode.get(int(ep_key))
            except ValueError:
                value = None
        ratio = _normalize_supported_image_aspect_ratio(value, fallback="")
        if ratio:
            return ratio
    for key in ("sketch_aspect_ratio", "aspect_ratio"):
        ratio = _normalize_supported_image_aspect_ratio(config.get(key), fallback="")
        if ratio:
            return ratio
    return fallback


def _context_sketch_aspect_ratio(context: dict[str, Any], fallback: str = "2:3") -> str:
    return _normalize_supported_image_aspect_ratio(
        context.get("sketch_aspect_ratio"),
        fallback=fallback,
    )


def _media_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in TEXT_EXTS:
        return "text"
    return "file"


def _aspect_ratio_for_ref(path: Path) -> str:
    media_type = _media_type_for_path(path)
    if media_type == "image":
        return _image_aspect_ratio(path)
    if media_type == "video":
        # Avoid probing video during canvas creation; frontend can refine from
        # metadata after loading, while 16:9 is the common novelvideo beat slot.
        return "16:9"
    return "1:1"
