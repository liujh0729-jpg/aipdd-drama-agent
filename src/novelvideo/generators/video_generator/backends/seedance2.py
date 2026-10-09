"""Seedance 2.0 直连生成器与 ShotReference 素材引用（B1 拆包自单文件 video_generator.py）。"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from ..egress import _deny_direct_organization_video
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase

@dataclass
class ShotReference:
    """Seedance 2.0 素材引用。"""

    type: str  # "image" / "video" / "audio" / "file" / "link"
    path: str  # 本地文件路径或公开 URL
    role: str  # "首帧" / "角色参考" / "场景参考" / "配乐" / "音色参考"


class Seedance2VideoGenerator(VideoGeneratorBase):
    """火山方舟 Seedance 2.0 全能参考模式视频生成器。

    支持多模态输入：文本 + 最多9图 + 3视频 + 3音频。
    原生音视频同步生成（对话、BGM、音效）。
    内置智能运镜系统。

    注意：API格式需在API正式开放后确认，以下基于已知信息推断。

    示例:
        >>> generator = Seedance2VideoGenerator()
        >>> result = await generator.generate(
        ...     prompt="古老书房中，少女翻阅古籍...",
        ...     references=[
        ...         ShotReference("image", "frame.png", "首帧"),
        ...         ShotReference("image", "char_ref.png", "角色参考"),
        ...     ],
        ...     output_path="output.mp4",
        ...     duration=10,
        ...     audio=True,
        ... )
    """

    MODEL = "seedance-2.0"
    MODEL_I2V = "seedance-2.0-i2v"

    def _select_generation_model(
        self,
        *,
        image_count: int,
        video_count: int,
        audio_count: int,
    ) -> str:
        """Choose the Seedance model variant for the current reference mix.

        Mixed-reference omni generation should stay on the general Seedance 2.0
        model. The i2v variant is only used for pure single-image first-frame
        generation.
        """
        if image_count == 1 and video_count == 0 and audio_count == 0:
            return self.MODEL_I2V
        return self.MODEL

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ):
        self.model = model or self.MODEL
        self.api_key = (
            api_key
            or os.environ.get("VOLCENGINE_VISUAL_API_KEY")
            or os.environ.get("ARK_API_KEY")
        )
        self.endpoint = endpoint or os.environ.get(
            "VOLCENGINE_VISUAL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
        )
        if not self.api_key:
            raise ValueError("VOLCENGINE_VISUAL_API_KEY or ARK_API_KEY must be set")

    def _file_to_data_url(self, file_path: str) -> str:
        """将本地文件转换为 data URL（base64）。"""
        import base64
        import mimetypes

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            ext = file_path.lower().rsplit(".", 1)[-1]
            mime_map = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
                "bmp": "image/bmp",
                "gif": "image/gif",
                "mp4": "video/mp4",
                "mov": "video/quicktime",
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
            }
            mime_type = mime_map.get(ext, "application/octet-stream")

        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:{mime_type};base64,{data}"

    async def _download_video(self, url: str, output_path: str) -> bool:
        """下载视频文件。"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return False
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception:
            return False

    async def submit_task(
        self, image_url: str, prompt: str, aspect_ratio: str = "9:16"
    ) -> str:
        return f"seedance2-{uuid.uuid4().hex[:8]}"

    async def get_result(self, task_id: str) -> VideoGenResult:
        return VideoGenResult(status=VideoGenStatus.PROCESSING, task_id=task_id)

    async def generate(
        self,
        prompt: str,
        output_path: str,
        references: Optional[list[ShotReference]] = None,
        duration: float = 10.0,
        audio: bool = True,
        aspect_ratio: str = "9:16",
        resolution: str = "2k",
        poll_interval: float = 5.0,
        max_polls: int = 120,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        # 兼容 VideoGeneratorBase 接口
        image_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """Seedance 2.0 全能参考模式生成。

        Args:
            prompt: 中文自然语言描述（含@素材引用）
            output_path: 输出视频路径
            references: 素材引用列表（图片/视频/音频）
            duration: 目标时长（5-15秒）
            audio: 是否生成原生音频
            aspect_ratio: 宽高比
            resolution: 分辨率（720p/1080p/2k）
            poll_interval: 轮询间隔
            max_polls: 最大轮询次数
            on_log: 日志回调
            on_progress: 进度回调
            image_path: 兼容旧接口的首帧图路径
        """
        _deny_direct_organization_video(kwargs)

        import httpx

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        refs = references or []

        # 兼容旧接口：如果传了 image_path 但没有 references，自动作为首帧
        if image_path and not refs:
            refs = [ShotReference("image", image_path, "首帧")]

        from novelvideo.usage.video_duration import normalize_video_duration_for_backend

        duration = normalize_video_duration_for_backend("seedance_2", duration)

        # 映射 aspect_ratio 格式
        ratio = aspect_ratio if ":" in aspect_ratio else "9:16"

        # 构建 content 数组
        content = [{"type": "text", "text": prompt}]

        # 添加素材引用
        image_count, video_count, audio_count = 0, 0, 0
        for ref in refs:
            if not os.path.exists(ref.path):
                log(f"警告: 素材不存在: {ref.path}")
                continue

            data_url = self._file_to_data_url(ref.path)

            if ref.type == "image" and image_count < 9:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                        "role": ref.role,
                    }
                )
                image_count += 1
            elif ref.type == "video" and video_count < 3:
                content.append(
                    {
                        "type": "video_url",
                        "video_url": {"url": data_url},
                        "role": ref.role,
                    }
                )
                video_count += 1
            elif ref.type == "audio" and audio_count < 3:
                content.append(
                    {
                        "type": "audio_url",
                        "audio_url": {"url": data_url},
                        "role": ref.role,
                    }
                )
                audio_count += 1

        model = self._select_generation_model(
            image_count=image_count,
            video_count=video_count,
            audio_count=audio_count,
        )

        # 构建请求体
        request_body = {
            "model": model,
            "content": content,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
            "watermark": False,
        }
        if audio:
            request_body["audio"] = True

        ref_summary = f"{image_count}图+{video_count}视频+{audio_count}音频"
        log(
            f"正在提交 Seedance 2.0 视频生成任务 "
            f"({ref_summary}, model={model}, {duration}s, audio={audio})..."
        )
        progress(0.1)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.endpoint}/contents/generations/tasks",
                    json=request_body,
                    headers=headers,
                )
                if resp.status_code != 200:
                    error_text = resp.text
                    log(f"任务提交失败: HTTP {resp.status_code} - {error_text[:500]}")
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"Submit failed: HTTP {resp.status_code} - {error_text[:200]}",
                    )
                data = resp.json()
                task_id = data.get("id")
                if not task_id:
                    log(f"未获取到 task_id: {data}")
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"No task_id in response: {data}",
                    )
                log(f"任务已提交: {task_id}")
        except Exception as e:
            log(f"任务提交异常: {e}")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"Submit exception: {e}",
            )

        # 轮询结果
        progress(0.2)
        for poll_count in range(max_polls):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"{self.endpoint}/contents/generations/tasks/{task_id}",
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        log(f"查询状态失败: HTTP {resp.status_code}")
                        await asyncio.sleep(poll_interval)
                        continue

                    data = resp.json()
                    status_str = data.get("status", "")
                    poll_progress = 0.2 + (poll_count / max_polls) * 0.7
                    progress(poll_progress)

                    if status_str == "succeeded":
                        log("视频生成完成，正在下载...")
                        progress(0.9)

                        video_url = None
                        resp_content = data.get("content")
                        if isinstance(resp_content, dict):
                            video_url = resp_content.get("video_url")
                        elif isinstance(resp_content, list):
                            for item in resp_content:
                                if isinstance(item, dict) and item.get("video_url"):
                                    video_url = item["video_url"]
                                    break

                        if video_url:
                            success = await self._download_video(video_url, output_path)
                            if success:
                                log(f"视频已保存: {output_path}")
                                progress(1.0)
                                return VideoGenResult(
                                    status=VideoGenStatus.DONE,
                                    video_path=output_path,
                                    video_url=video_url,
                                    task_id=task_id,
                                    duration_seconds=float(duration),
                                )
                            else:
                                log("视频下载失败")
                                return VideoGenResult(
                                    status=VideoGenStatus.FAILED,
                                    error="Download failed",
                                    task_id=task_id,
                                )
                        else:
                            log(f"API 未返回视频 URL: {data}")
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="No video URL in response",
                                task_id=task_id,
                            )

                    elif status_str == "failed":
                        error_msg = data.get("error", {}).get(
                            "message", "Unknown error"
                        )
                        log(f"视频生成失败: {error_msg}")
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"Generation failed: {error_msg}",
                            task_id=task_id,
                        )

                    if poll_count % 6 == 0:
                        log(f"正在生成中... ({status_str}, {poll_count}/{max_polls})")

            except Exception as e:
                log(f"查询状态异常: {e}")

            await asyncio.sleep(poll_interval)

        log("视频生成超时")
        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Timeout waiting for video generation",
            task_id=task_id,
        )
