"""渠道驱动 ComfyUI 视频生成器（B1 拆包自单文件 video_generator.py）。

_record_video_model_call 经包命名空间延迟解析；_video_dimensions 复用
NewApiVideoGenerator 静态方法（同样经命名空间，避免 backends 内部环）。
"""

import os
from typing import Any, Callable, Optional

from novelvideo.generators import video_generator as _video_generator
from novelvideo.i18n_message import lmsg

from ..egress import VideoEgressError, _video_egress_context
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase

class ComfyUIVideoGenerator(VideoGeneratorBase):
    """[aipdd fork delta] 渠道驱动的 ComfyUI 视频生成器。

    不再内置任何模板 / 节点 ID / 服务器地址。当选中模型属于 comfyui 渠道时，
    读该渠道自身的 ``baseUrl`` 与渠道里存储的「API 格式」工作流，交给通用执行器
    ``ComfyUIWorkflowExecutor`` 直连该 ComfyUI 服务器完成生成；参数优先按「AI分析并
    自动配置」产出的节点绑定注入，没有适配时才回落 class_type 启发式。杜绝旧的
    wan/ltx 硬编码模板与 seetacloud 写死地址。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        egress_context: object | None = None,
        **kwargs: Any,
    ) -> None:
        """初始化渠道驱动生成器。

        Args:
            model: comfyui 渠道模型名（用于解析渠道 baseUrl + 工作流）
            resolution: 分辨率档位（推导 width/height，跟随渠道声明，不写死）
            egress_context: 可信出网上下文（组织闸门判定）

        遗留参数（server_address / workflow_type / use_ssl / timeout）一律忽略，
        以免任何写死地址 / 模板重新流入。
        """
        self.model = str(model or "").strip() or None
        self.resolution = resolution
        self.egress_context = _video_egress_context(egress_context)

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """渠道驱动生成：直连 comfyui 渠道 baseUrl，用渠道工作流交给通用执行器。

        参考图沿用 ``image_path`` / ``last_frame_path`` / ``references``，仅本地
        文件会上传注入到工作流的 ``LoadImage`` 节点（URL 跳过）；尺寸由分辨率
        档位 + 宽高比推导。无任何模板 / 地址 / 节点 ID 硬编码。
        """
        from novelvideo.comfyui_executor import ComfyUIWorkflowExecutor
        from novelvideo.model_gateway_settings import (
            load_comfyui_channel_adaptation,
            resolve_comfyui_channel_execution,
        )

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        # 组织出网闸门：comfyui 为直连渠道，组织上下文默认拒绝（与图片 provider!=newapi 一致）。
        context = _video_egress_context(
            kwargs.get("egress_context") or self.egress_context
        )
        if context is not None and context.is_organization:
            raise VideoEgressError("ORG_EGRESS_DENIED")

        if not self.model:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="ComfyUI 视频需要指定渠道模型名",
            )
        try:
            resolved = resolve_comfyui_channel_execution(self.model, "video")
            adaptation = load_comfyui_channel_adaptation(self.model)
        except (ValueError, RuntimeError) as exc:
            return VideoGenResult(status=VideoGenStatus.FAILED, error=str(exc))
        if not resolved:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"模型 {self.model} 不属于 comfyui 视频渠道或未配置执行",
            )
        base_url, workflow = resolved

        # 收集参考图：首帧 / 尾帧 / 额外 references，仅本地文件。
        def _local_path(candidate: object) -> Optional[str]:
            text = str(candidate or "").strip()
            if not text or text.startswith(("http://", "https://")):
                return None
            return text if os.path.exists(text) else None

        reference_image_paths: list[str] = []
        for candidate in (image_path, last_frame_path):
            local = _local_path(candidate)
            if local:
                reference_image_paths.append(local)
        for ref in kwargs.get("references") or []:
            raw = (
                getattr(ref, "path", None)
                or (ref.get("path") if isinstance(ref, dict) else None)
            )
            local = _local_path(raw)
            if local:
                reference_image_paths.append(local)

        dims = _video_generator.NewApiVideoGenerator._video_dimensions(self.resolution, aspect_ratio)
        width = dims[0] if dims else None
        height = dims[1] if dims else None

        log(f"开始生成视频 | 渠道模型: {self.model} | 服务器: {base_url}")
        if adaptation:
            adaptation_name = str(adaptation.get("workflowName") or "")
            log(
                lmsg(
                    "tasks.log.comfyui.adaptationBound",
                    f"参数按「AI分析并自动配置」的节点绑定注入（工作流：{adaptation_name}）",
                    workflow=adaptation_name,
                )
            )

        executor = ComfyUIWorkflowExecutor(base_url)
        result = await executor.execute(
            workflow=workflow,
            media_type="video",
            prompt=prompt or "",
            reference_image_paths=reference_image_paths,
            width=width,
            height=height,
            duration=duration,
            output_path=output_path,
            adaptation=adaptation,
            log=log,
            progress=progress,
        )
        if not result.success:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=result.error or "ComfyUI 生成失败",
                provider_task_id=result.prompt_id,
            )
        await _video_generator._record_video_model_call(
            model=self.model,
            provider_task_id=result.prompt_id or "",
        )
        return VideoGenResult(
            status=VideoGenStatus.DONE,
            video_path=result.output_path or output_path,
            duration_seconds=duration,
            provider_task_id=result.prompt_id,
        )
