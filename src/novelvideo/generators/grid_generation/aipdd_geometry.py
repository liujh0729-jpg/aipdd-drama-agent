"""aipdd 平台图像几何协议映射（size/ratio 双写）（B3 拆包自单文件 grid_generation.py）。"""

import math
import re

from .image_sizes import _round_openai_edge


# [aipdd fork delta] aipdd 平台的图像几何协议与 LocalNewAPI 不同：尺寸与
# 比例必须表达为 `size`（模型档位词 1K/2K/4K 或像素值，按模型声明） + 顶层
# `ratio`；LocalNewAPI 协议是 width/height + metadata。resolve_media_provider_override
# 选中 aipdd 渠道直连时，这里把几何双写成平台方言 —— 平台对多余字段宽容
# （width/height/metadata 保留即可），只是实际几何以 size/ratio 为准。
# 渠道 settings 里可用 imageRequestProtocol="dimensions" 显式回退内置协议；
# 非 aipdd 渠道一律不变。词表/比例声明来自 /v1/models（拿不到时只做档位词兜底）。
_AIPDD_IMAGE_TIER_LONG_EDGES = {
    0.5: 1024,
    1.0: 1024,
    2.0: 2048,
    3.0: 3072,
    4.0: 3840,
}
_AIPDD_IMAGE_TIER_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*k$")
_AIPDD_IMAGE_PIXEL_RE = re.compile(r"^(\d+)\s*[xX×]\s*(\d+)$")
_AIPDD_IMAGE_RATIO_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[:：/]\s*(\d+(?:\.\d+)?)$")


def _aipdd_image_geometry_endpoint(endpoint: str) -> bool:
    """该端点是否为 aipdd 风格渠道（size+ratio 几何协议）。"""

    target = str(endpoint or "").strip().rstrip("/")
    if not target:
        return False
    try:
        from novelvideo.model_gateway_settings import get_newapi_provider_channels

        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 判定失败保持内置协议
        return False
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        base = str(channel.get("baseUrl") or "").strip().rstrip("/")
        if not base or base != target:
            continue
        settings = channel.get("settings") if isinstance(channel.get("settings"), dict) else {}
        declared = str(settings.get("imageRequestProtocol") or "").strip().lower()
        if declared in {"tier_ratio", "dimensions"}:
            return declared == "tier_ratio"
        return str(channel.get("provider") or "").strip().lower() == "aipdd"
    return False


def _aipdd_tier_number(value: str) -> float | None:
    match = _AIPDD_IMAGE_TIER_RE.fullmatch(str(value or "").strip().lower())
    return float(match.group(1)) if match else None


def _aipdd_pixel_dimensions(value: str) -> tuple[int, int] | None:
    match = _AIPDD_IMAGE_PIXEL_RE.fullmatch(str(value or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _aipdd_ratio_number(value: str) -> float | None:
    match = _AIPDD_IMAGE_RATIO_RE.fullmatch(str(value or "").strip().lower())
    if not match:
        return None
    left, right = float(match.group(1)), float(match.group(2))
    if left <= 0 or right <= 0:
        return None
    return left / right


def _map_aipdd_image_size(
    image_size: str,
    ratio: str,
    supported_sizes: list | None,
) -> str:
    """内部档位/像素尺寸 → aipdd 模型 size 词表值；无法判定时原样返回。

    词表形态实测有三类：档位词表（agnes 1K/2K/3K/4K）、像素词表（flare
    /sunburst）、混合/未知（拿不到声明）。档位词按数值就近映射（道具的 0.5K
    在档位词表里退到 1K）；像素词表按「比例最接近 → 长边最接近」选条目。
    """

    raw = str(image_size or "").strip()
    if not raw:
        return ""
    candidates = [
        str(item or "").strip()
        for item in (supported_sizes or [])
        if str(item or "").strip()
    ]
    # 1) 词表精确命中（大小写不敏感）
    for candidate in candidates:
        if candidate.casefold() == raw.casefold():
            return candidate
    internal_tier = _aipdd_tier_number(raw)
    # 2) 档位词表：数值就近（0.5K → 1K）
    tier_options = [
        (number, candidate)
        for candidate in candidates
        for number in [_aipdd_tier_number(candidate)]
        if number is not None
    ]
    if internal_tier is not None and tier_options:
        tier_options.sort(key=lambda item: (abs(item[0] - internal_tier), item[0]))
        return tier_options[0][1]
    # 3) 像素词表 + 内部档位：目标 = 档位长边 × 比例，取比例与长边最接近的条目
    pixel_options = [
        (dims, candidate)
        for candidate in candidates
        for dims in [_aipdd_pixel_dimensions(candidate)]
        if dims is not None
    ]
    if internal_tier is not None and pixel_options:
        long_edge = _AIPDD_IMAGE_TIER_LONG_EDGES.get(internal_tier)
        if long_edge is None:
            long_edge = _round_openai_edge(1024 * internal_tier)
        ratio_value = _aipdd_ratio_number(ratio) or 1.0

        def _distance(item: tuple[tuple[int, int], str]) -> tuple[float, float]:
            (width, height), _ = item
            candidate_ratio = width / height
            ratio_delta = abs(math.log(candidate_ratio / ratio_value))
            long_delta = abs(max(width, height) - long_edge) / max(1, long_edge)
            return (round(ratio_delta, 4), long_delta)

        pixel_options.sort(key=_distance)
        return pixel_options[0][1]
    return raw


def _map_aipdd_image_ratio(ratio: str, supported_ratios: list | None) -> str | None:
    """内部比例 → aipdd 顶层 ratio 值；None 表示不下发该字段。

    模型对不在声明词表内的比例会直接拒单（实测 agnes 的 "ratio 不受支持"
    400），故声明非空且不命中时宁可不发（几何退化为模型默认），也不提供
    无效值；auto/adaptive 一律不下发（平台不认 "auto" 为 ratio 值）。
    """

    clean = str(ratio or "").strip().lower()
    if not clean or clean in {"auto", "adaptive"}:
        return None
    candidates = [
        str(item or "").strip()
        for item in (supported_ratios or [])
        if str(item or "").strip()
    ]
    if not candidates:
        return clean
    for candidate in candidates:
        if candidate.casefold() == clean:
            return candidate
    return None


async def _apply_aipdd_image_geometry(payload: dict, *, model: str) -> dict:
    """把 NewAPI 几何方言改写为 aipdd 平台方言（追加 size + 顶层 ratio）。

    width/height 与 metadata 刻意保留：平台实测宽容多余字段，保留让请求在
    平台后来收紧时仍有 LocalNewAPI 语义可退回。
    """

    from novelvideo.media_model_discovery import gateway_image_capabilities_by_name

    try:
        capabilities = await gateway_image_capabilities_by_name()
    except Exception:  # noqa: BLE001 - 声明拿不到也要退到档位词兜底
        capabilities = {}
    caps = capabilities.get(str(model or "").strip()) or {}
    supported_sizes = caps.get("resolutionOptions")
    supported_ratios = caps.get("ratioOptions")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    resolution = str(metadata.get("resolution") or payload.get("resolution") or "").strip()
    ratio = str(
        metadata.get("ratio") or payload.get("ratio") or payload.get("aspect_ratio") or ""
    ).strip()
    size_value = _map_aipdd_image_size(resolution, ratio, supported_sizes)
    if size_value:
        payload["size"] = size_value
    ratio_value = _map_aipdd_image_ratio(ratio, supported_ratios)
    if ratio_value:
        payload["ratio"] = ratio_value
    return payload
