"""OpenRouter / OpenAI 图片 API 调用（B3 拆包自单文件 grid_generation.py）。"""

import asyncio
import base64
import logging

from novelvideo.shared import openai_compat
from novelvideo.shared.upstream_quota_errors import is_fatal_upstream_quota_error

from .image_sizes import (
    normalize_image_size,
    _check_openrouter_image_capability,
    _truncate_openrouter_debug,
    resolve_openai_image_size,
    normalize_openai_quality,
    _extract_openai_unknown_parameter,
)

from novelvideo.generators import grid_generation as _gg

logger = logging.getLogger("novelvideo.generators.grid_generation.provider_text_api")


async def _call_openrouter_image_api(
    api_key: str,
    model: str,
    prompt: str,
    reference_images: list[bytes] | None = None,
    image_config: dict | None = None,
) -> tuple[bytes | None, str, str]:
    """通过 OpenRouter API 调用 Gemini 图像生成。

    Returns:
        (image_bytes, text_response, error_detail)
        - image_bytes: 生成的图像 bytes，失败返回 None
        - text_response: provider 返回的文本内容（如 panel hints JSON）
        - error_detail: 失败原因摘要，成功时为空字符串

    Args:
        api_key: OpenRouter API Key
        model: 模型名称（如 google/gemini-3-pro-image-preview）
        prompt: 图像生成提示词
        reference_images: 参考图像列表（bytes 格式）
        image_config: 图像配置（aspect_ratio, image_size）
    """
    import httpx

    base_url = openai_compat.OPENROUTER_BASE_URL

    # 构建 content 数组（按 OpenRouter 官方建议：文本在前，图片在后）
    content = []

    # 先添加文本提示词
    content.append({"type": "text", "text": prompt})

    # 再添加参考图（如果有）
    if reference_images:
        for img_bytes in reference_images:
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
    }

    # 添加 image_config
    if image_config:
        effective_image_size = normalize_image_size(
            image_config.get("image_size", "1K"),
            provider="openrouter",
        )
        payload["image_config"] = {
            "aspect_ratio": image_config.get("aspect_ratio", "1:1"),
            "image_size": effective_image_size,
        }

    headers = openai_compat.openrouter_headers(api_key)

    try:
        supports_image, capability_detail = await _check_openrouter_image_capability(api_key, model)
        if not supports_image:
            detail = f"OpenRouter 模型未声明 image output 支持: {capability_detail}"
            logger.warning("[OpenRouter] %s", detail)
            return None, "", detail

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

            result = response.json()

            # 提取图像
            choices = result.get("choices", [])
            if not choices:
                logger.warning(
                    "[OpenRouter] 响应无 choices: %s", _truncate_openrouter_debug(result)
                )
                return None, "", "响应无 choices"

            message = choices[0].get("message", {})

            # 提取文本和图像（兼容多种 OpenRouter 响应格式）
            text_content = ""
            image_data_url = ""

            content = message.get("content", "")
            if isinstance(content, list):
                # content 是 list[{type, text/image_url}]
                text_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            image_data_url = url
                text_content = " ".join(text_parts)
            elif isinstance(content, str):
                text_content = content

            if text_content:
                logger.info("[OpenRouter] 文本响应: %s", text_content[:500])

            # 优先从 message.images 取图（旧格式）
            images = message.get("images", [])
            if images:
                url = images[0].get("image_url", {}).get("url", "")
                if url.startswith("data:image"):
                    image_data_url = url

            if not image_data_url:
                logger.warning(
                    "[OpenRouter] 响应无图像: %s", _truncate_openrouter_debug(message)
                )
                detail = "模型未返回图像"
                if text_content:
                    detail = f"{detail}，仅返回文本: {text_content[:200]}"
                return None, text_content or "", detail

            # 提取 base64 部分
            _, b64_data = image_data_url.split(",", 1)
            return base64.b64decode(b64_data), text_content or "", ""

    except httpx.HTTPStatusError as e:
        logger.warning(
            "[OpenRouter] HTTP 错误: %s - %s",
            e.response.status_code,
            _truncate_openrouter_debug(e.response.text, limit=500),
        )
        body = _truncate_openrouter_debug(e.response.text, limit=280)
        return (
            None,
            "",
            f"HTTP {e.response.status_code}: {body}" if body else f"HTTP {e.response.status_code}",
        )
    except Exception as e:
        if is_fatal_upstream_quota_error(e):
            raise
        detail = f"{type(e).__name__}: {e!r}"
        logger.warning("[OpenRouter] 请求异常: %s", detail)
        return None, "", f"请求异常: {detail}"


async def _call_openai_image_api(
    *,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: list[bytes | tuple[bytes, str] | tuple[str, bytes, str]] | None = None,
    image_config: dict | None = None,
) -> tuple[bytes | None, str, str]:
    """Call OpenAI Image API using GPT Image models.

    Returns:
        (image_bytes, text_response, error_detail)
    """

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None, "", "openai SDK not installed; install openai>=2.14.0"

    if not api_key:
        return None, "", "OPENAI_API_KEY is missing"

    image_config = image_config or {}
    image_size = normalize_image_size(str(image_config.get("image_size") or "1K"), "openai")
    size = resolve_openai_image_size(
        image_config.get("aspect_ratio", "1:1"),
        image_size,
    )
    request_options: dict[str, object] = {"size": size}
    is_gpt_image_2 = str(model or "").strip().lower().startswith("gpt-image-2")
    quality = normalize_openai_quality(str(image_config.get("quality") or ""), default="medium")
    if quality:
        request_options["quality"] = quality
    output_format = str(image_config.get("output_format") or "png").strip().lower()
    # The current Image Edit endpoint rejects output_format for some gpt-image-2
    # reference-image requests. Edits return PNG-compatible b64 output by default.
    if output_format and not reference_images:
        request_options["output_format"] = output_format
    input_fidelity = str(image_config.get("input_fidelity") or "").strip().lower()
    # gpt-image-2 always processes image inputs at high fidelity; the API rejects
    # attempts to change input_fidelity for that model.
    if input_fidelity and not is_gpt_image_2:
        request_options["input_fidelity"] = input_fidelity

    async def _record(
        provider_request_id: str = "",
        response_id: str = "",
    ) -> None:
        """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。"""
        try:
            await _gg.get_usage_meter().bump_model_call(
                user_id=None,
                model=model,
                provider_request_id=provider_request_id,
                metadata={"response_id": response_id} if response_id else None,
            )
        except Exception:  # noqa: BLE001
            pass

    try:

        client = AsyncOpenAI(api_key=api_key, timeout=300.0, max_retries=0)
        result = None
        for _attempt in range(4):
            try:
                if reference_images:
                    image_files = []
                    for idx, image_ref in enumerate(reference_images):
                        filename = f"reference_{idx + 1}.png"
                        mime_type = "image/png"
                        image_bytes: bytes
                        if isinstance(image_ref, tuple):
                            if len(image_ref) == 3:
                                filename, image_bytes, mime_type = image_ref
                            elif len(image_ref) == 2:
                                image_bytes, mime_type = image_ref
                                ext = "jpg" if mime_type == "image/jpeg" else "png"
                                filename = f"reference_{idx + 1}.{ext}"
                            else:
                                image_bytes = bytes(image_ref[0])
                        else:
                            image_bytes = bytes(image_ref)
                        image_files.append((filename, bytes(image_bytes), mime_type))
                    result = await client.images.edit(
                        model=model,
                        image=image_files,
                        prompt=prompt,
                        **request_options,
                    )
                else:
                    result = await client.images.generate(
                        model=model,
                        prompt=prompt,
                        **request_options,
                    )
                break
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc!r}"
                unknown_parameter = _extract_openai_unknown_parameter(detail)
                if unknown_parameter and unknown_parameter in request_options:
                    logger.warning(
                        "[OpenAI Image] 参数 %r 不被当前端点接受，移除后重试",
                        unknown_parameter,
                    )
                    request_options.pop(unknown_parameter, None)
                    continue
                transient_error = any(
                    token in detail
                    for token in (
                        "InternalServerError",
                        "APIConnectionError",
                        "APITimeoutError",
                        "server_error",
                        "Connection error",
                    )
                )
                if transient_error and _attempt < 3:
                    wait_seconds = 2**_attempt
                    logger.warning(
                        "[OpenAI Image] 暂时性错误，%ss 后重试 (%s/4): %s",
                        wait_seconds,
                        _attempt + 1,
                        detail,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise

        if result is None:
            return None, "", "OpenAI Image API returned no response"

        provider_request_id = str(getattr(result, "_request_id", "") or "").strip()
        response_id = str(getattr(result, "id", "") or "").strip()
        if not result.data:
            return None, "", "OpenAI Image API returned no data"

        image_item = result.data[0]
        image_base64 = getattr(image_item, "b64_json", None) or ""
        if not image_base64:
            return None, "", f"OpenAI Image API returned no b64_json: {image_item}"

        image_bytes = base64.b64decode(image_base64)
        await _record(
            provider_request_id=provider_request_id,
            response_id=response_id,
        )
        return image_bytes, "", ""
    except Exception as exc:
        if is_fatal_upstream_quota_error(exc):
            raise
        detail = f"{type(exc).__name__}: {exc!r}"
        logger.warning("[OpenAI Image] 请求异常: %s", detail)
        return None, "", f"请求异常: {detail}"
