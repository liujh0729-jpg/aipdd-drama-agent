"""ComfyUI 图片尺寸与生成（B3 拆包自单文件 grid_generation.py）。"""

import os
import re
from pathlib import Path

from novelvideo.generators import grid_generation as _gg


def _comfyui_image_dimensions(
    aspect_ratio: str, image_size: str
) -> tuple[int | None, int | None]:
    """[aipdd fork delta] 由宽高比 + 尺寸档位推导图片像素尺寸（不写死具体模型）。

    无法推导时返回 (None, None)，交给工作流自带值。
    """

    text = str(image_size or "").strip().lower()
    k_match = re.fullmatch(r"(\d+(?:\.\d+)?)k", text)
    if k_match is None:
        return None, None
    long_edge = int(round(float(k_match.group(1)) * 1024))
    ratio_text = str(aspect_ratio or "").strip()
    if ":" not in ratio_text:
        return None, None
    try:
        ratio_width, ratio_height = (
            float(part.strip()) for part in ratio_text.split(":", 1)
        )
    except (TypeError, ValueError):
        return None, None
    if ratio_width <= 0 or ratio_height <= 0:
        return None, None
    if ratio_width >= ratio_height:
        width = long_edge
        height = round(long_edge * ratio_height / ratio_width)
    else:
        height = long_edge
        width = round(long_edge * ratio_width / ratio_height)
    width += width % 2
    height += height % 2
    return width, height


async def _generate_comfyui_image(
    *,
    prompt: str,
    reference_image_paths: list[str],
    output_path: str,
    aspect_ratio: str,
    image_size: str,
    model: str,
    quality: str | None = None,
) -> Path:
    """[aipdd fork delta] comfyui 渠道图片：通用执行器直连渠道 ComfyUI。

    无任何硬编码模板 / 节点 ID / 地址：从渠道解析 baseUrl + API 格式工作流，
    参考图仅本地文件会上传注入 ``LoadImage``，尺寸由宽高比 + 档位推导。
    [aipdd fork delta] 渠道指针里的适配条目（若有）优先于 class_type 启发式。
    [aipdd fork delta] WP-D: 上游调用只做一次观测记账。
    """

    from novelvideo.comfyui_executor import ComfyUIWorkflowExecutor
    from novelvideo.model_gateway_settings import (
        load_comfyui_channel_adaptation,
        resolve_comfyui_channel_execution,
    )

    resolved = resolve_comfyui_channel_execution(model, "image")
    if not resolved:
        raise ValueError(
            f"模型 {model or '(未指定)'} 不属于 comfyui 图片渠道，"
            f"请先在渠道配置 comfyui 图片模型与工作流"
        )
    base_url, workflow = resolved
    # 「AI分析并自动配置」做过就按声明的节点写参数，没做过才回落 class_type 启发式。
    adaptation = load_comfyui_channel_adaptation(model)
    width, height = _comfyui_image_dimensions(aspect_ratio, image_size)
    ref_paths = [
        path
        for path in (reference_image_paths or [])
        if path
        and not str(path).startswith(("http://", "https://"))
        and os.path.exists(path)
    ]

    async def _record(provider_request_id: str) -> None:
        """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。"""
        try:
            await _gg.get_usage_meter().bump_model_call(
                user_id=None,
                model=model,
                provider_request_id=provider_request_id,
            )
        except Exception:  # noqa: BLE001
            pass

    executor = ComfyUIWorkflowExecutor(base_url)
    result = await executor.execute(
        workflow=workflow,
        media_type="image",
        prompt=prompt or "",
        reference_image_paths=ref_paths,
        width=width,
        height=height,
        output_path=output_path,
        adaptation=adaptation,
    )
    if not result.success:
        raise ValueError(f"ComfyUI 图片生成失败: {result.error or 'empty image'}")
    output = Path(output_path)
    if not output.exists() and result.data:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(result.data)
    if not output.exists():
        raise ValueError("ComfyUI 图片生成未产出文件")
    await _record(result.prompt_id or "")
    return output
