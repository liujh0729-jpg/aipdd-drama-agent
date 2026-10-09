"""Huimeng 图片 API 调用（B3 拆包自单文件 grid_generation.py）。"""

from novelvideo.shared import openai_compat
from novelvideo.generators.huimengi import (
    HuimengTaskFailed,
    HuimengiTaskClient,
    bytes_to_data_url,
    extract_huimeng_result_url,
    validate_huimeng_media_download,
)

from .image_sizes import _huimeng_image_resolution_for_model
from .constants import _HUIMENG_IMAGE_POLL_INTERVAL_SECONDS, _HUIMENG_IMAGE_MAX_POLLS


async def _call_huimeng_image_api(
    *,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: list[bytes | tuple[bytes, str] | tuple[str, bytes, str]] | None = None,
    image_config: dict | None = None,
) -> tuple[bytes | None, str, str]:
    """Call HuiMeng's async task API for image generation/editing."""
    import httpx

    if not api_key:
        return None, "", "HUIMENGI_API_KEY is missing"

    image_config = image_config or {}
    ratio = str(image_config.get("aspect_ratio") or "1:1").strip() or "1:1"
    resolution = _huimeng_image_resolution_for_model(model, image_config.get("image_size"))
    params: dict[str, object] = {"prompt": prompt, "ratio": ratio}
    if resolution:
        params["resolution"] = resolution
    if model == "image-2-official":
        quality = (
            str(
                image_config.get("quality") or image_config.get("huimeng_image_quality") or "medium"
            )
            .strip()
            .lower()
        )
        params["quality"] = quality if quality in {"low", "medium", "high"} else "medium"
    if reference_images:
        ref_urls = []
        for image_ref in reference_images[:9]:
            if isinstance(image_ref, tuple):
                if len(image_ref) == 3:
                    image_bytes = image_ref[1]
                elif len(image_ref) == 2:
                    image_bytes = image_ref[0]
                else:
                    image_bytes = bytes(image_ref[0])
            else:
                image_bytes = bytes(image_ref)
            ref_urls.append(bytes_to_data_url(bytes(image_bytes)))
        params["image"] = ref_urls[0] if len(ref_urls) == 1 else ref_urls

    request_context = f"model={model}, ratio={ratio}, refs={len(reference_images or [])}"
    try:
        client = HuimengiTaskClient(api_key=api_key)
        submit = await client.submit_task(model=model, params=params)
        task_id = submit["task_id"]
        print(f"[HuiMeng Images] submitted task_id={task_id} ({request_context})")
        task = await client.wait_for_completion(
            task_id,
            poll_interval=_HUIMENG_IMAGE_POLL_INTERVAL_SECONDS,
            max_polls=_HUIMENG_IMAGE_MAX_POLLS,
        )
        result = task.get("result") or {}
        image_url = extract_huimeng_result_url(result, "image_url", "image_urls")
        if not image_url:
            return None, "", f"HuiMeng result missing image_url: {result}"

        content, content_type = await openai_compat.download_bytes(
            image_url, timeout=180.0
        )
        validate_huimeng_media_download(
            content,
            content_type,
            expected_media_type="image",
            url=image_url,
        )
        return content, "", ""
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "")[:500]
        return None, "", f"{request_context} | HTTP {exc.response.status_code}: {body}"
    except HuimengTaskFailed as exc:
        return None, "", f"{request_context} | HuiMeng task failed: {exc}"
    except Exception as exc:
        return None, "", f"{request_context} | request failed: {exc}"
