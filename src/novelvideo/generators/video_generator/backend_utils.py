"""提示词翻译 / Seedance2 配置解析 / newAPI 后端与地址工具（B1 拆包自单文件 video_generator.py）。"""

import asyncio
import ipaddress
import json
import os
import urllib.parse

from .models import VideoBackend

async def translate_prompt_to_english(prompt: str) -> str:
    """将中文视频提示词翻译并优化为 WAN I2V 最佳格式。

    使用 Gemini 进行翻译，按照 WAN 模型最佳实践结构化输出。

    Args:
        prompt: 中文提示词

    Returns:
        优化后的英文提示词
    """
    if not prompt:
        return prompt

    # 检查是否已经是英文（简单判断：不含中文字符）
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in prompt)
    if not has_chinese:
        return prompt

    try:
        import google.genai as genai

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("[VIDEO] 警告: 未配置 GOOGLE_API_KEY，跳过翻译")
            return prompt

        client = genai.Client(api_key=api_key)

        translation_prompt = f"""You are an expert at writing prompts for WAN 2.1 Image-to-Video AI model.

Convert this Chinese video motion description into an optimized English prompt following WAN I2V best practices.

## WAN I2V Prompt Rules:
1. Focus on MOTION and CAMERA MOVEMENT (the image already defines the subject/scene)
2. Use speed adverbs: "slowly", "gently", "dramatically", "subtly"
3. Use effective camera keywords: "push in", "pull back", "tracking shot", "static shot"
4. AVOID: "whip pan", "crash zoom", "dolly out" (these don't work well)
5. Add lighting if relevant: "soft lighting", "rim lighting", "backlit"
6. Keep it concise (under 80 words)

## Output Format:
[Lighting if relevant], [Shot type if relevant]. [Subject motion description]. [Camera movement].

## Chinese Input:
{prompt}

## English Output (ONLY the optimized prompt, nothing else):"""

        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.5-flash",
            contents=translation_prompt,
        )

        english_prompt = response.text.strip()
        # 移除可能的引号包裹
        if english_prompt.startswith('"') and english_prompt.endswith('"'):
            english_prompt = english_prompt[1:-1]
        print(f"[VIDEO] 优化提示词: {prompt} -> {english_prompt}")
        return english_prompt

    except Exception as e:
        print(f"[VIDEO] 翻译失败，使用原文: {e}")
        return prompt


def _seedance2_config_mapping(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"final_prompt": text}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _seedance2_duration_from_config(config: dict, fallback: float) -> float:
    if "duration" not in config:
        return fallback
    try:
        return int(float(config.get("duration") or fallback))
    except (TypeError, ValueError):
        return fallback

LOCALAPI_VIDEO_BACKEND_PREFIX = "newapi_"
LOCALAPI_VIDEO_DISPLAY_LABELS = {
    "seedance-1.0-pro-fast": "Seedance1.0 Pro Fast",
    "seedance-1.5-pro": "Seedance1.5 Pro",
    "seedance-2.0": "Seedance2.0",
    "seedance-2.0-fast": "Seedance2.0 Fast",
    "seedance-2.0-value": "Seedance2.0 Value",
    "seedance-2.0-fast-value": "Seedance2.0 Fast Value",
    "seedance-2.0-mini": "Seedance2.0 Mini",
    "seedance-2.5": "Seedance2.5",
}
LOCALAPI_MAINLINE_SEEDANCE2_MODELS = (
    "seedance-2.0",
    "seedance-2.0-fast",
    "seedance-2.0-mini",
)
LOCALAPI_DISABLED_VIDEO_MODELS = {"grok-video-channel"}


def parse_newapi_video_backend(backend: str | None) -> str | None:
    value = str(backend or "").strip()
    lowered = value.lower()
    if lowered == "newapi":
        from novelvideo.config import get_newapi_video_model

        # [aipdd fork delta] 默认视频模型只读设置：env ``LOCALAPI_VIDEO_MODEL`` 已退出解析链。
        return get_newapi_video_model()
    if lowered.startswith(LOCALAPI_VIDEO_BACKEND_PREFIX):
        model = value[len(LOCALAPI_VIDEO_BACKEND_PREFIX) :].strip()
        return model or None
    return None


def newapi_video_backend_options(
    *, include_seedance2_variants: bool = False
) -> dict[str, str]:
    from novelvideo.config import LOCALAPI_VIDEO_MODELS
    from novelvideo.shared.runtime_env import is_ce_effective

    # [aipdd fork delta req40] CE：候选只来自用户配置（媒体模型映射 = 渠道目录里挑
    # 出来的那批），随包写死的 seedance 清单不再参与 —— 渠道摘掉模型后，下拉里不该
    # 还留着能选中的死名字。EE 官方网关仍用随包清单。
    models: list[str] = []
    if not is_ce_effective():
        models = [
            model
            for model in LOCALAPI_VIDEO_MODELS
            if model not in LOCALAPI_DISABLED_VIDEO_MODELS
        ]
    else:
        try:
            from novelvideo.model_gateway_settings import (
                get_newapi_media_model_mappings,
            )

            for model, mapping in get_newapi_media_model_mappings().items():
                media_type = str(mapping.get("mediaType") or "video").strip().lower()
                if (
                    mapping.get("enabled") is not False
                    and media_type == "video"
                    and model not in models
                    and model not in LOCALAPI_DISABLED_VIDEO_MODELS
                ):
                    models.append(model)
        except (RuntimeError, ImportError):
            pass
    if include_seedance2_variants:
        for model in LOCALAPI_MAINLINE_SEEDANCE2_MODELS:
            if model not in models:
                models.append(model)
    return {
        f"{LOCALAPI_VIDEO_BACKEND_PREFIX}{model}": LOCALAPI_VIDEO_DISPLAY_LABELS.get(
            model, model
        )
        for model in models
    }


def _coerce_video_backend_value(backend: VideoBackend | str | None) -> str:
    if backend is None:
        backend = os.environ.get("VIDEO_BACKEND", "comfyui")
    if isinstance(backend, VideoBackend):
        return backend.value
    value = str(backend).strip()
    lowered = value.lower()
    if lowered.startswith(LOCALAPI_VIDEO_BACKEND_PREFIX):
        return (
            f"{LOCALAPI_VIDEO_BACKEND_PREFIX}{value[len(LOCALAPI_VIDEO_BACKEND_PREFIX) :]}"
        )
    return "comfyui" if lowered == "jimeng" else lowered


def _server_address_host(server_address: object) -> str:
    text = str(server_address or "").strip()
    parsed = urllib.parse.urlsplit(text if "://" in text else f"//{text}")
    return str(parsed.hostname or "").strip().lower()


def _is_loopback_server_address(server_address: object) -> bool:
    host = _server_address_host(server_address)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
