"""图片尺寸/质量归一化与 OpenRouter/OpenAI/Huimeng 能力解析（B3 拆包自单文件 grid_generation.py）。"""

import hashlib
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

from novelvideo.shared import openai_compat
from novelvideo.models import collect_prop_marker_ids_from_beat
from novelvideo.utils.asset_resolver import AssetResolver

from .constants import (
    _OPENAI_MAX_RATIO,
    _OPENAI_MAX_EDGE,
    _OPENAI_MAX_PIXELS,
    _OPENAI_MIN_PIXELS,
    _OPENAI_VALID_QUALITIES,
    _OPENROUTER_IMAGE_CAPABILITY_CACHE,
    _VALID_IMAGE_SIZES,
)


class _InlineImagePart:
    """Provider-neutral image part used by OpenRouter/OpenAI branches."""

    def __init__(self, data: bytes, mime_type: str = "image/png"):
        self.inline_data = SimpleNamespace(data=data, mime_type=mime_type)


def _infer_project_dir(*paths: str | None) -> Optional[Path]:
    for path_str in paths:
        if not path_str:
            continue
        path = Path(path_str)
        parts = list(path.parts)
        if "grids" in parts:
            grids_idx = parts.index("grids")
            if grids_idx > 0:
                return Path(*parts[:grids_idx])
    return None


def _resolve_scene_prop_asset_refs(
    project_dir: Optional[Path],
    beats: List[dict],
    *,
    episode_number: int | None = None,
    sketch: bool = False,
    use_director_refs: bool = False,
    include_pano_view_refs: bool = False,
    director_ref_beat_numbers: list[int] | None = None,
    director_control_frames_dir: str | Path | None = None,
    scene_menu: list[dict] | list | None = None,
    prop_menu: list[dict] | list | None = None,
    allow_beat_background_anchor: bool | None = None,
) -> tuple[dict[int, list], dict[int, list]]:
    if not project_dir:
        return {}, {}
    resolver = AssetResolver(
        project_dir,
        episode_number=episode_number,
        scene_menu=scene_menu,
        prop_menu=prop_menu,
        scene_reference_kind="sketch" if sketch else "render",
        use_director_refs=use_director_refs,
        include_pano_view_refs=include_pano_view_refs,
        director_ref_beat_numbers=director_ref_beat_numbers,
        director_control_frames_dir=director_control_frames_dir,
        allow_beat_background_anchor=allow_beat_background_anchor,
    )
    return resolver.resolve_all_for_beats(beats)


def _global_prop_marker_colors(
    beats: List[dict],
    prop_menu: list[dict] | list | None = None,
    sketch_colors: dict[str, str] | None = None,
    *,
    assign_missing: bool = False,
) -> dict[str, str]:
    """Return episode-persisted marker colors for global [[prop]] markers.

    Generation/export paths must be WYSIWYG: prop colors come from the episode
    prop_menu marker_color field only. The color assignment UI flow may pass
    assign_missing=True to compute missing colors before persisting them.
    """
    active_prop_ids: list[str] = []
    seen: set[str] = set()
    for beat in beats:
        for prop_id in collect_prop_marker_ids_from_beat(beat):
            if prop_id and prop_id not in seen:
                seen.add(prop_id)
                active_prop_ids.append(prop_id)
    if not active_prop_ids:
        return {}

    colorable_prop_ids: set[str] = set()
    explicit_colors: dict[str, str] = {}
    for raw_item in prop_menu or []:
        if isinstance(raw_item, dict):
            prop_id = str(
                raw_item.get("prop_id") or raw_item.get("base_id") or raw_item.get("name") or ""
            ).strip()
            asset_scope = str(raw_item.get("asset_scope") or "").strip().lower()
            is_global_asset = raw_item.get("is_global_asset") is True
            marker_color = str(raw_item.get("marker_color") or "").strip()
        else:
            prop_id = str(getattr(raw_item, "prop_id", "") or getattr(raw_item, "name", "")).strip()
            asset_scope = str(getattr(raw_item, "asset_scope", "")).strip().lower()
            is_global_asset = getattr(raw_item, "is_global_asset", False) is True
            marker_color = str(getattr(raw_item, "marker_color", "") or "").strip()
        if prop_id and (asset_scope == "global" or is_global_asset or marker_color):
            colorable_prop_ids.add(prop_id)
        if prop_id and marker_color:
            explicit_colors[prop_id] = marker_color
    if not colorable_prop_ids:
        return {}
    if not assign_missing:
        return {
            prop_id: explicit_colors[prop_id]
            for prop_id in active_prop_ids
            if prop_id in explicit_colors
        }

    from novelvideo.generators.episode_optimizer import (
        PROP_MARKER_PALETTE,
        _hex_to_hue,
    )

    used_hexes = {
        str(value or "").strip().split(" ", 1)[0].lower()
        for value in (sketch_colors or {}).values()
        if str(value or "").strip()
    }
    used_hexes.update(
        str(value or "").strip().split(" ", 1)[0].lower()
        for value in explicit_colors.values()
        if str(value or "").strip()
    )
    used_hues = [_hex_to_hue(h) for h in used_hexes if h.startswith("#") and len(h) == 7]

    def _min_hue_gap(candidate_hex: str) -> float:
        if not used_hues:
            return 360.0
        h = _hex_to_hue(candidate_hex)
        gaps = []
        for used_h in used_hues:
            diff = abs(h - used_h) % 360
            gaps.append(min(diff, 360 - diff))
        return min(gaps)

    # Prop 用专用调色板 PROP_MARKER_PALETTE（深色 / 非荧光），跟角色调色板（荧光高饱和）
    # 形成 value contrast。即使色相意外接近，亮度/饱和度差异让 prop 仍能跟 character 视觉分开。
    not_used = [item for item in PROP_MARKER_PALETTE if item[0].lower() not in used_hexes]
    # 视觉上跟已用色色相差 ≥60° 才"安全"；不够则退化用未占用 hex 全集，最后兜底全 prop 调色板
    safe = [item for item in not_used if _min_hue_gap(item[0]) >= 60.0]
    available = safe or not_used or list(PROP_MARKER_PALETTE)
    # 按距离已用色相由远到近排序，让最反差的色优先被选
    available = sorted(available, key=lambda it: -_min_hue_gap(it[0]))

    colors: dict[str, str] = {}
    color_index = 0
    for prop_id in active_prop_ids:
        if prop_id not in colorable_prop_ids:
            continue
        if prop_id in explicit_colors:
            colors[prop_id] = explicit_colors[prop_id]
            continue
        hex_code, color_name = available[color_index % len(available)]
        colors[prop_id] = f"{hex_code} {color_name}"
        color_index += 1
    return colors


def normalize_image_size(size: str, provider: str = "google") -> str:
    """Normalize image_size across providers.

    Internal configs may still use 0.5K, but providers expect different values:
    - Gemini direct: 512
    - OpenRouter: 1K (0.5K currently triggers INVALID_ARGUMENT on Gemini image routes)
    """
    if provider in {"huimeng", "newapi"} and size == "0.5K":
        return "1K"
    if size == "0.5K":
        return "1K" if provider == "openrouter" else "512"
    return size


def _legacy_newapi_image_quality_default(model: str | None) -> bool:
    """没走媒体目录时的历史兜底：按模型名判断这个模型吃不吃 quality。

    [aipdd fork delta] 仅供「完全没有目录 schema」的旧链路（主线渲染等配置驱
    动的路径）使用。画布 / freezone 链路一律以目录声明为准，不要再往这里加名字。

    [aipdd fork delta wave2] 名单里不再携带发行方私有别名（那一族旧别名已删）：
    官方目录与渠道清单都没有这个名字，留着就是给一个不存在的模型开画质档位。
    """
    model_name = str(model or "").strip().lower()
    return model_name in {
        "gpt-image-2",
        "image-2",
        "image-2-official",
    } or "gpt-image" in model_name


def _huimeng_image_resolution_for_model(model: str, image_size: str | None) -> str:
    """Map local image_size labels to HuiMeng model resolution params when supported."""
    model_name = (model or "").strip()
    image2_family = model_name in {"image-2", "image-2-official"}
    if not (
        model_name.startswith(("nb-", "seedream-")) or image2_family or "gpt-image" in model_name
    ):
        return ""

    normalized = normalize_image_size(str(image_size or "").strip(), provider="huimeng")
    if image2_family:
        lower = normalized.lower()
        return lower if lower in {"1k", "2k", "4k"} else ""
    return normalized if normalized in {"1K", "2K", "3K", "4K"} else ""


def _round_openai_edge(value: float) -> int:
    return max(16, int(math.ceil(value / 16.0)) * 16)


def resolve_openai_image_size(
    aspect_ratio: str = "1:1",
    image_size: str = "1K",
    model: str | None = None,
    *,
    allow_dynamic_resolution: bool = False,
    min_pixels: int | None = None,
) -> str:
    """Map internal aspect/image_size labels to GPT Image 2 size strings.

    gpt-image-2 supports flexible sizes, but they must satisfy OpenAI's documented
    constraints: both edges are multiples of 16, max edge <= 3840, ratio <= 3:1,
    and total pixels within the valid range. "1K" here means the smallest valid
    draft size near a 1024px long edge.
    """

    ratio_text = str(aspect_ratio or "1:1").replace("-", ":")
    try:
        raw_w, raw_h = [float(part) for part in ratio_text.split(":", 1)]
        if raw_w <= 0 or raw_h <= 0:
            raise ValueError
    except Exception:
        raw_w, raw_h = 1.0, 1.0

    ratio = raw_w / raw_h
    if ratio > _OPENAI_MAX_RATIO:
        ratio = _OPENAI_MAX_RATIO
    elif ratio < 1.0 / _OPENAI_MAX_RATIO:
        ratio = 1.0 / _OPENAI_MAX_RATIO

    normalized_size = normalize_image_size(str(image_size or "1K"), provider="openai")
    long_edges = {
        "512": 1024,
        "0.5K": 1024,
        "0.5k": 1024,
        "1K": 1024,
        "1k": 1024,
        "2K": 2048,
        "2k": 2048,
        "3K": 3072,
        "3k": 3072,
        "4K": 3840,
        "4k": 3840,
    }
    long_edge = long_edges.get(normalized_size)
    dynamic_max_edge = _OPENAI_MAX_EDGE
    dynamic_max_pixels = _OPENAI_MAX_PIXELS
    explicit_dimensions: tuple[int, int] | None = None
    if long_edge is None and allow_dynamic_resolution:
        explicit_size = re.fullmatch(r"(\d+)\s*[xX×]\s*(\d+)", normalized_size)
        if explicit_size:
            width_i, height_i = (int(value) for value in explicit_size.groups())
            if width_i <= 0 or height_i <= 0:
                raise ValueError(f"invalid image resolution: {image_size}")
            explicit_dimensions = (width_i, height_i)
        k_size = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[kK]", normalized_size)
        if explicit_dimensions is not None:
            pass
        elif k_size:
            long_edge = max(16, _round_openai_edge(float(k_size.group(1)) * 1024))
            dynamic_max_edge = long_edge
            dynamic_max_pixels = long_edge * long_edge
        else:
            raise ValueError(
                f"unsupported image resolution: {image_size}; "
                "use a value such as 2K, 3K, 8K, or 2048x2048"
            )
    if long_edge is None:
        long_edge = 1024
    configured_min_pixels = (
        min_pixels
        if type(min_pixels) is int and min_pixels > 0
        else None
    )
    effective_min_pixels = configured_min_pixels or _OPENAI_MIN_PIXELS
    max_pixels = dynamic_max_pixels

    if explicit_dimensions is not None:
        width, height = (float(value) for value in explicit_dimensions)
    elif ratio >= 1:
        width = float(long_edge)
        height = width / ratio
    else:
        height = float(long_edge)
        width = height * ratio

    pixel_count = width * height
    if pixel_count < effective_min_pixels:
        scale = math.sqrt(effective_min_pixels / pixel_count)
        width *= scale
        height *= scale
    elif explicit_dimensions is None and pixel_count > max_pixels:
        scale = math.sqrt(max_pixels / pixel_count)
        width *= scale
        height *= scale

    if explicit_dimensions is not None and configured_min_pixels is None:
        return f"{explicit_dimensions[0]}x{explicit_dimensions[1]}"

    max_edge = (
        max(dynamic_max_edge, _round_openai_edge(width), _round_openai_edge(height))
        if explicit_dimensions is not None
        else dynamic_max_edge
    )
    width_i = min(max_edge, _round_openai_edge(width))
    height_i = min(max_edge, _round_openai_edge(height))

    if width_i * height_i < effective_min_pixels:
        scale = math.sqrt(effective_min_pixels / max(1, width_i * height_i))
        width_i = min(max_edge, _round_openai_edge(width_i * scale))
        height_i = min(max_edge, _round_openai_edge(height_i * scale))

    return f"{width_i}x{height_i}"


def normalize_openai_quality(value: str | None, default: str = "medium") -> str:
    quality = str(value or default or "medium").strip().lower()
    return quality if quality in _OPENAI_VALID_QUALITIES else default


def _extract_openai_unknown_parameter(error_detail: str) -> str:
    match = re.search(r"Unknown parameter:\s*'([^']+)'", error_detail or "")
    if match:
        return match.group(1)
    match = re.search(r'Unknown parameter:\s*"([^"]+)"', error_detail or "")
    if match:
        return match.group(1)
    match = re.search(r"Unsupported parameter:\s*'([^']+)'", error_detail or "")
    if match:
        return match.group(1)
    match = re.search(r'Unsupported parameter:\s*"([^"]+)"', error_detail or "")
    if match:
        return match.group(1)
    match = re.search(r"'param':\s*'([^']+)'", error_detail or "")
    if match:
        return match.group(1)
    match = re.search(r'"param":\s*"([^"]+)"', error_detail or "")
    if match:
        return match.group(1)
    for parameter in ("output_format", "quality", "input_fidelity"):
        if parameter in (error_detail or ""):
            return parameter
    if "input_fidelity" in (error_detail or ""):
        return "input_fidelity"
    return ""


def _truncate_openrouter_debug(value: object, limit: int = 240) -> str:
    """截断 OpenRouter 调试字段，避免日志过长。"""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


async def _check_openrouter_image_capability(api_key: str, model: str) -> tuple[bool, str]:
    """检查 OpenRouter 模型是否声明支持 image output。"""
    import httpx

    cache_key = f"{model}:{hashlib.sha1((api_key or '').encode('utf-8')).hexdigest()[:8]}"
    cached = _OPENROUTER_IMAGE_CAPABILITY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    base_url = openai_compat.OPENROUTER_BASE_URL
    headers = openai_compat.openrouter_headers(api_key)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            result = response.json()

        models = result.get("data", [])
        model_info = next((item for item in models if item.get("id") == model), None)
        if not model_info:
            detail = f"模型 {model} 不在 OpenRouter /models 列表中，跳过 image capability 预检"
            print(f"[OpenRouter] {detail}")
            outcome = (True, detail)
            _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
            return outcome

        output_modalities = (model_info.get("architecture") or {}).get("output_modalities") or []
        supports_image = "image" in output_modalities
        detail = (
            f"model={model}, output_modalities={output_modalities}"
            if output_modalities
            else f"model={model}, output_modalities=[]"
        )
        outcome = (supports_image, detail)
        _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
        return outcome
    except Exception as exc:
        detail = "image capability 预检失败，跳过阻断: " f"{type(exc).__name__}: {exc!r}"
        print(f"[OpenRouter] {detail}")
        outcome = (True, detail)
        _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
        return outcome


def clamp_image_size(size: str) -> str:
    """Clamp image_size to values accepted by Gemini image APIs."""
    normalized = normalize_image_size(size, provider="google")
    return normalized if normalized in _VALID_IMAGE_SIZES else "1K"
