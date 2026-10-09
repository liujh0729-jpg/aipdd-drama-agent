"""视频生成器工厂 create_video_generator（B1 拆包自单文件 video_generator.py）。

保持从包命名空间可导入 / 可 patch：tests 用字符串路径
monkeypatch.setattr("novelvideo.generators.video_generator.create_video_generator", ...)，
调用方均为函数内延迟导入，零改动兼容。
"""

from typing import Optional

from .backend_utils import _coerce_video_backend_value, parse_newapi_video_backend
from .backends.comfyui import ComfyUIVideoGenerator
from .backends.grok import GrokVideoGenerator
from .backends.huimeng import HuimengVideoGenerator
from .backends.mock import MockVideoGenerator
from .backends.newapi import NewApiVideoGenerator
from .backends.seedance import SeedanceVideoGenerator
from .backends.seedance2 import Seedance2VideoGenerator
from .egress import (
    VideoEgressError,
    _direct_video_context_denied,
    _video_egress_context,
)
from .models import VideoBackend, VideoGeneratorBase

def create_video_generator(
    backend: Optional[VideoBackend | str] = None,
    use_mock: bool = False,
    workflow_type: Optional[str] = None,
    **kwargs,
) -> VideoGeneratorBase:
    """创建视频生成器。

    Args:
        backend: 视频后端选择
            - newapi_<model>: newAPI 视频模型，如 newapi_seedance-1.0-pro-fast
            - huimeng_<model>: HuiMeng 视频模型，如 huimeng_seedance-2.0-fast
            - SEEDANCE_FAST: Seedance 1.0 Pro Fast（火山方舟）
            - SEEDANCE_PRO: Seedance 1.5 Pro 有声（火山方舟）
            - SEEDANCE_PRO_SILENT: Seedance 1.5 Pro 无声（火山方舟）
            - COMFYUI: Claymore 1.0 本地服务
            - GROK_720: xAI Grok Imagine Video 720p
        use_mock: 兼容旧接口，True 时使用 MockVideoGenerator
        workflow_type: ComfyUI 工作流类型 ("gguf" 或 "fp8")，默认从环境变量读取
        **kwargs: 传递给具体生成器的参数

    Returns:
        视频生成器实例

    环境变量:
        VIDEO_BACKEND: 默认后端
            (newapi_seedance-1.0-pro-fast/huimeng_seedance-2.0-fast/comfyui/...)
        HUIMENGI_API_KEY: HuiMeng API 密钥
        COMFYUI_WORKFLOW: ComfyUI 工作流类型 (gguf/fp8)
        VOLCENGINE_VISUAL_API_KEY: Seedance API 密钥
        COMFYUI_ADDRESS: ComfyUI 服务器地址
        XAI_API_KEY: Grok 视频生成 API 密钥
    """
    from novelvideo.config import SEEDANCE_FAST_MODEL, SEEDANCE_PRO_MODEL
    from novelvideo.model_gateway_settings import (
        default_comfyui_channel_model,
        is_comfyui_channel_model,
    )

    egress_context = _video_egress_context(kwargs.get("egress_context"))

    # 兼容旧接口
    if use_mock:
        return MockVideoGenerator(**kwargs)

    backend_str = _coerce_video_backend_value(backend)
    newapi_model = parse_newapi_video_backend(backend_str)
    if newapi_model:
        # [aipdd fork delta] comfyui 渠道模型绝不走 NewAPI 桥接：直连渠道 ComfyUI。
        if is_comfyui_channel_model(newapi_model, "video"):
            return ComfyUIVideoGenerator(model=newapi_model, **kwargs)
        return NewApiVideoGenerator(model=newapi_model, **kwargs)

    from novelvideo.generators.huimengi import parse_huimeng_video_backend

    huimeng_model = parse_huimeng_video_backend(backend_str)
    if huimeng_model:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return HuimengVideoGenerator(model=huimeng_model, **kwargs)

    try:
        backend_enum = VideoBackend(backend_str)
    except ValueError:
        if backend is None:
            backend_enum = VideoBackend.COMFYUI
        else:
            raise ValueError(f"Unknown video backend: {backend_str}") from None

    # 创建对应的生成器
    if backend_enum == VideoBackend.SEEDANCE_FAST:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return SeedanceVideoGenerator(
            model=SEEDANCE_FAST_MODEL, generate_audio=False, **kwargs
        )
    elif backend_enum == VideoBackend.SEEDANCE_PRO:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return SeedanceVideoGenerator(
            model=SEEDANCE_PRO_MODEL, generate_audio=True, **kwargs
        )
    elif backend_enum == VideoBackend.SEEDANCE_PRO_SILENT:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return SeedanceVideoGenerator(
            model=SEEDANCE_PRO_MODEL, generate_audio=False, **kwargs
        )
    elif backend_enum == VideoBackend.COMFYUI:
        # [aipdd fork delta] 字面量 comfyui 后端解析到渠道首个 comfyui 视频模型，
        # 不再回落任何硬编码模板 / 地址；组织上下文拒绝直连。
        if egress_context is not None and egress_context.is_organization:
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        comfyui_model = (
            workflow_type
            if workflow_type and is_comfyui_channel_model(workflow_type, "video")
            else default_comfyui_channel_model("video")
        )
        if not comfyui_model:
            raise ValueError(
                "未配置任何 comfyui 视频渠道模型，请在模型渠道里添加 comfyui 模型与工作流"
            )
        return ComfyUIVideoGenerator(model=comfyui_model, **kwargs)
    elif backend_enum == VideoBackend.SEEDANCE_2:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return Seedance2VideoGenerator(**kwargs)
    elif backend_enum == VideoBackend.LTX23:
        # [aipdd fork delta] LTX23 字面量后端同样走渠道驱动，不再回落硬编码模板。
        if egress_context is not None and egress_context.is_organization:
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        comfyui_model = (
            "ltx23"
            if is_comfyui_channel_model("ltx23", "video")
            else default_comfyui_channel_model("video")
        )
        if not comfyui_model:
            raise ValueError(
                "未配置任何 comfyui 视频渠道模型，请在模型渠道里添加 comfyui 模型与工作流"
            )
        return ComfyUIVideoGenerator(model=comfyui_model, **kwargs)
    elif backend_enum == VideoBackend.GROK_720:
        if _direct_video_context_denied(egress_context):
            raise VideoEgressError("ORG_EGRESS_DENIED")
        kwargs.pop("egress_context", None)
        return GrokVideoGenerator(**kwargs)
    else:
        raise ValueError(f"Unknown video backend: {backend_str}")
