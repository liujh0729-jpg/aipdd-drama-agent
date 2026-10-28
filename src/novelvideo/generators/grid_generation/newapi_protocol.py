"""LocalAPI 图片协议：状态判定、错误提取、输出解析、请求上下文（B3 拆包自单文件 grid_generation.py）。"""

import hashlib
from typing import Any

from novelvideo.manual_shots import beat_order_value

from .constants import HUIMENG_IMAGE2_SINGLE_CELL_SELECTION


LOCALAPI_IMAGE_HTTP_TIMEOUT_SECONDS = 1800.0

# [aipdd fork delta] newapi/aipdd 图片请求瞬时失败重试（见 _call_newapi_image_api）。
LOCALAPI_IMAGE_MAX_ATTEMPTS = 4
LOCALAPI_IMAGE_RETRY_BASE_SECONDS = 1.5

# [aipdd fork delta] 异步图片任务（提交返回 task_id、轮询 GET 取图）的轮询参数与
# 状态词表。迁移到 grid_generation 时这组常量与轮询流程一起丢了，而测试仍在守
# （tests/test_newapi_image_async.py）：FLUX-GGUF-T2I-V2 等网关模型以队列任务
# 形式出图，没有轮询就只会拿回一个没有图片的 queued 载荷。
LOCALAPI_IMAGE_POLL_INTERVAL_SECONDS = 2.0
LOCALAPI_IMAGE_MAX_POLLS = 900

_LOCALAPI_IMAGE_SUCCESS_STATUSES = {"completed", "succeeded", "success", "done"}
_LOCALAPI_IMAGE_FAILURE_STATUSES = {
    "failed",
    "error",
    "canceled",
    "cancelled",
    "expired",
}
_LOCALAPI_IMAGE_PENDING_STATUSES = {
    "queued",
    "pending",
    "processing",
    "running",
    "in_progress",
    "submitted",
}

_LOCALAPI_TRANSIENT_IMAGE_MARKERS = (
    "未通过上游校验",
    "上游校验",
)


def _is_newapi_transient_image_status(status: int, body: str | None) -> bool:
    """Whether a newapi image HTTP failure is a retryable transient blip.

    [aipdd fork delta] aipdd/LocalAPI 会把真实上游图片模型的偶发失败（临时超时、
    限流、瞬时不可用）统一包成 HTTP 400「请求参数或媒体素材未通过上游校验」，而不
    是回真正的 5xx。实测同一条请求重试即可恢复（约 1/4 概率抖动），所以只把这枚带
    上游校验措辞的特定 400 当作可重试瞬态。5xx 维持 app 层不重试（见
    test_newapi_image_http_5xx_does_not_retry_in_app：由上层任务重跑/避免重复提交），
    其余 4xx（401/403 鉴权、404 模型不存在、真正的坏参数）也不重试，避免无效上游调用。
    """
    if status == 400:
        text = (body or "").lower()
        return any(marker.lower() in text for marker in _LOCALAPI_TRANSIENT_IMAGE_MARKERS)
    return False


def _newapi_image_status(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    status = str(payload.get("status") or payload.get("state") or "").strip().lower()
    if status:
        return status
    for key in ("data", "result"):
        status = _newapi_image_status(payload.get(key))
        if status:
            return status
    return ""


def _newapi_image_error(payload: object) -> str:
    if not isinstance(payload, dict):
        return "DramaClawAPI image task failed"
    error = payload.get("error") or payload.get("fail_reason")
    if isinstance(error, dict):
        error = error.get("message") or error.get("code")
    if error:
        return str(error)
    for key in ("data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_error = _newapi_image_error(nested)
            if nested_error != "DramaClawAPI image task failed":
                return nested_error
    message = payload.get("message")
    return str(message) if message else "DramaClawAPI image task failed"


def _newapi_image_output(payload: object) -> tuple[str, str]:
    """从网关的直出或嵌套形状里提取图片输出（b64 或 URL）。

    [aipdd fork delta] 轮询完成的载荷形状多样（result.image_url、output.images[]
    等），旧版 nanobanana_grid 用本函数统一提取；迁移到 grid_generation 时与轮询
    流程一起丢失，这里恢复。返回 (b64, url)，两者都可能为空串。
    """

    if isinstance(payload, str):
        value = payload.strip()
        if value.startswith(("http://", "https://", "data:image")):
            return "", value
        return "", ""
    if isinstance(payload, list):
        for item in payload:
            image_b64, image_url = _newapi_image_output(item)
            if image_b64 or image_url:
                return image_b64, image_url
        return "", ""
    if not isinstance(payload, dict):
        return "", ""

    image_b64 = str(payload.get("b64_json") or payload.get("image_base64") or "").strip()
    image_url = str(payload.get("url") or payload.get("image_url") or "").strip()
    if image_b64 or image_url:
        return image_b64, image_url

    for key in ("data", "result", "output", "images", "image_urls"):
        nested = payload.get(key)
        if nested is payload:
            continue
        image_b64, image_url = _newapi_image_output(nested)
        if image_b64 or image_url:
            return image_b64, image_url
    return "", ""


def _newapi_safe_header_summary(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    safe_keys = ("x-request-id", "x-newapi-request-id", "x-oneapi-request-id", "cf-ray", "date")
    summary: dict[str, str] = {}
    for key in safe_keys:
        value = str(headers.get(key) or "").strip()
        if value:
            summary[key] = value
    return summary


def _newapi_safe_request_context(
    *,
    endpoint: str,
    request_path: str,
    model: str,
    payload: dict[str, object],
    prompt: str,
) -> dict[str, object]:
    reference_images = payload.get("image")
    reference_image_count = len(reference_images) if isinstance(reference_images, list) else 0
    raw_metadata = payload.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    geometry_metadata = {
        key: metadata[key]
        for key in ("ratio", "aspect_ratio", "resolution")
        if key in metadata
    }
    return {
        "endpoint": f"{endpoint}{request_path}",
        "model": model,
        "payload_keys": sorted(payload.keys()),
        "width": payload.get("width") or "",
        "height": payload.get("height") or "",
        "geometry_metadata": geometry_metadata,
        "reference_image_count": reference_image_count,
        "prompt_chars": len(prompt or ""),
        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
    }


def _newapi_context_for_error(context: dict[str, object]) -> str:
    return (
        f"model={context.get('model')}; "
        f"endpoint={context.get('endpoint')}; "
        f"payload_keys={context.get('payload_keys')}; "
        f"width={context.get('width')}; "
        f"height={context.get('height')}; "
        f"geometry_metadata={context.get('geometry_metadata')}; "
        f"reference_image_count={context.get('reference_image_count')}; "
        f"prompt_sha256={context.get('prompt_sha256')}"
    )


def _beat_display_sort_key(beat: dict) -> tuple[int, int]:
    return (beat_order_value(beat), int(beat.get("beat_number", 0) or 0))


def image_generation_selection_forces_single_cell(selection: str | None) -> bool:
    """Whether render planning must split every beat into a 1x1 grid."""
    return str(selection or "").strip() == HUIMENG_IMAGE2_SINGLE_CELL_SELECTION
