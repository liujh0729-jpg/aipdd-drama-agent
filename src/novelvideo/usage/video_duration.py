"""Canonical video-duration normalization for quotes and provider requests."""

from __future__ import annotations

import math


def parse_video_duration_bounds(raw: str) -> dict[str, tuple[int, int]]:
    bounds: dict[str, tuple[int, int]] = {}
    for item in str(raw or "").split(","):
        entry = item.strip()
        if not entry or ":" not in entry or "-" not in entry:
            continue
        model, raw_bounds = entry.split(":", 1)
        raw_min, raw_max = raw_bounds.split("-", 1)
        try:
            min_seconds = int(raw_min.strip())
            max_seconds = int(raw_max.strip())
        except ValueError:
            continue
        if min_seconds > 0 and max_seconds >= min_seconds:
            bounds[model.strip()] = (min_seconds, max_seconds)
    return bounds


def _gateway_model_from_backend(backend: str | None) -> str:
    from novelvideo.config import get_newapi_video_model

    value = str(backend or "").strip()
    lowered = value.lower()
    if lowered == "newapi":
        # [aipdd fork delta] 默认视频模型只读设置（env ``LOCALAPI_VIDEO_MODEL`` 已退出解析链）。
        return get_newapi_video_model()
    for prefix in ("newapi_", "huimeng_", "huimengi_"):
        if lowered.startswith(prefix):
            value = value[len(prefix) :].strip()
            break
    else:
        return ""
    # 目录按渠道分行后，调用名是 ``provider::model`` 复合名；时长档位按裸模型名配置。
    if "::" in value:
        value = value.split("::", 1)[1].strip()
    return value


def video_duration_bounds_for_backend(
    backend: str | None,
) -> tuple[int | None, int | None]:
    """Return the provider duration bounds used by the selected backend."""
    from novelvideo.config import LOCALAPI_VIDEO_DURATION_BOUNDS

    model = _gateway_model_from_backend(backend)
    configured = parse_video_duration_bounds(LOCALAPI_VIDEO_DURATION_BOUNDS)
    if model:
        bounds = configured.get(model)
        if bounds:
            return bounds
        lowered_model = model.lower()
        if lowered_model == "seedance-2.0-mini":
            return (4, 15)

    legacy_bounds = {
        "seedance_fast": (4, 12),
        "seedance_pro": (4, 12),
        "seedance_pro_silent": (4, 12),
        "seedance_2": (5, 15),
        "grok_720": (1, 15),
    }
    return legacy_bounds.get(str(backend or "").strip().lower(), (None, None))


def normalize_video_duration_for_backend(
    backend: str | None,
    value: int | float | str | None,
    configured_min_duration: int | None = None,
    configured_max_duration: int | None = None,
) -> int:
    """Resolve the exact integer seconds used for billing and generation."""
    try:
        duration = max(int(math.ceil(float(value if value is not None else 5))), 1)
    except (TypeError, ValueError):
        duration = 5

    min_duration, max_duration = video_duration_bounds_for_backend(backend)
    if configured_min_duration is not None:
        min_duration = configured_min_duration
    if configured_max_duration is not None:
        max_duration = configured_max_duration
    if min_duration is not None:
        duration = max(duration, min_duration)
    if max_duration is not None:
        duration = min(duration, max_duration)
    return duration
