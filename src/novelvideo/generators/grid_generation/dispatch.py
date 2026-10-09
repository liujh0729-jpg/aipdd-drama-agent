"""统一出图分发 _generate_image（B3 拆包自单文件 grid_generation.py）。"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.ports.egress import EgressError

from .egress import _validate_egress_context, _complete_organization_image_egress
from .comfyui import _generate_comfyui_image

from novelvideo.generators import grid_generation as _gg


async def _generate_image(
    *,
    prompt: str,
    reference_image_paths: list[str],
    output_path: str,
    aspect_ratio: str,
    image_size: str,
    quality: str | None,
    api_key: Optional[str],
    config: Optional[dict],
    egress_context: TrustedEgressContext | None,
    egress_capability: str,
) -> Path:
    """Shared body for text-only and image-edit single-image generation."""
    ref_paths = list(reference_image_paths or [])
    context = _validate_egress_context(egress_context)
    if context is None:
        # claim 本身不漏（`_prepare_organization_image_egress` 自己回落到作用域），漏的是
        # 转发给叶子的身份：`context` 留成 None，参考图中继就静默走平台分支而不是
        # `relay_tenant_image_bytes_from_context`。只补组织这一支——把平台身份也回落
        # 进去，会让平台流量撞上组织 deny 闸门（OI-48 的房规）。
        context = ambient_organization_egress_context()
    configured_provider = (
        str((config or {}).get("provider") or "newapi").strip().lower()
    )
    # [aipdd fork delta] comfyui 渠道图片：直连渠道 ComfyUI + 渠道存储的 API 格式
    # 工作流，经通用执行器完成。组织出网闸门与既有 provider!=newapi 一致（拒绝直连）。
    if configured_provider == "comfyui":
        if context is not None and context.is_organization:
            raise EgressError("ORG_EGRESS_DENIED")
        return await _generate_comfyui_image(
            prompt=prompt,
            reference_image_paths=ref_paths,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            model=str((config or {}).get("model") or ""),
            quality=quality,
        )
    request = {
        "model": str((config or {}).get("model") or ""),
        "prompt": prompt,
        "reference_sha256": [
            hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in ref_paths
        ],
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "quality": quality or "",
    }
    organization_egress = await _gg._prepare_organization_image_egress(
        egress_context=context,
        provider=configured_provider,
        capability=egress_capability,
        request=request,
    )
    effective_config = config
    effective_api_key = api_key
    if organization_egress is not None:
        effective_config = dict(config or {})
        effective_config.update(
            {
                "provider": "newapi",
                "api_key": organization_egress.credential.api_key,
                "base_url": organization_egress.credential.base_url,
            }
        )
        effective_api_key = organization_egress.credential.api_key
    generator = _gg.GridGenerator(
        api_key=effective_api_key, config=effective_config
    )

    if generator.provider == "openrouter":
        ref_bytes = [Path(path).read_bytes() for path in ref_paths]
        image_bytes, _, error_detail = await _gg._call_openrouter_image_api(
            api_key=generator.api_key,
            model=generator.model,
            prompt=prompt,
            reference_images=ref_bytes,
            image_config={"aspect_ratio": aspect_ratio, "image_size": image_size},
        )
        if not image_bytes:
            raise ValueError(
                f"OpenRouter edit image generation failed: {error_detail or 'empty image'}"
            )
    elif generator.provider == "huimeng":
        ref_bytes = [Path(path).read_bytes() for path in ref_paths]
        image_bytes, _, error_detail = await _gg._call_huimeng_image_api(
            api_key=generator.api_key,
            model=generator.model,
            prompt=prompt,
            reference_images=ref_bytes,
            image_config={
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality or generator.huimeng_image_quality,
                "huimeng_image_quality": quality or generator.huimeng_image_quality,
            },
        )
        if not image_bytes:
            raise ValueError(
                f"HuiMeng edit image generation failed: {error_detail or 'empty image'}"
            )
    elif generator.provider == "openai":
        ref_bytes = [Path(path).read_bytes() for path in ref_paths]
        image_bytes, _, error_detail = await _gg._call_openai_image_api(
            api_key=generator.api_key,
            model=generator.model,
            prompt=prompt,
            reference_images=ref_bytes,
            image_config={
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality or generator.openai_image_quality,
                "output_format": "png",
            },
        )
        if not image_bytes:
            raise ValueError(
                f"OpenAI edit image generation failed: {error_detail or 'empty image'}"
            )
    elif generator.provider == "newapi":
        ref_bytes = [(Path(path).read_bytes(), path) for path in ref_paths]
        trace: dict[str, str] = {}
        delivery_state: dict[str, bool | str] = {}
        image_bytes, _, error_detail = await _gg._call_newapi_image_api(
            api_key=generator.api_key,
            model=generator.model,
            prompt=prompt,
            reference_images=ref_bytes or None,
            image_config={
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality or generator.openai_image_quality,
                "request_schema": generator.newapi_request_schema,
                "model_params": generator.newapi_model_params,
            },
            base_url=generator.base_url,
            trace=trace,
            egress_context=context,
            delivery_path=output_path,
            delivery_state=delivery_state,
            read_copied_bytes=False,
        )
        if not image_bytes and not delivery_state.get("copied"):
            raise ValueError(
                f"DramaClawAPI image generation failed: {error_detail or 'empty image'}"
            )
    else:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=generator.api_key)
        contents = [prompt]
        for ref_path in ref_paths:
            ref_image = generator._load_image_as_part(ref_path)
            if ref_image:
                contents.append(ref_image)

        is_gemini3 = "gemini-3" in generator.model
        if is_gemini3:
            image_config = types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            )
        else:
            image_config = types.ImageConfig(aspect_ratio=aspect_ratio)

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=generator.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=image_config,
            ),
        )
        image_bytes = None
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    image_bytes = part.inline_data.data
                    break
        if not image_bytes:
            raise ValueError("Google edit image generation returned no image data")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not (generator.provider == "newapi" and delivery_state.get("copied")):
        output.write_bytes(image_bytes)
    await _complete_organization_image_egress(
        organization_egress,
        trace=trace if generator.provider == "newapi" else {},
        result_ref=str(output),
    )
    return output
