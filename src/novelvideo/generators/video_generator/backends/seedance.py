"""Seedance 1.0/1.5 直连（火山方舟）视频生成器（B1 拆包自单文件 video_generator.py）。"""

import asyncio
import os
from typing import Callable, Optional

from novelvideo.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from ..egress import _deny_direct_organization_video
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase

class SeedanceVideoGenerator(VideoGeneratorBase):
    """火山方舟 Seedance 视频生成器。

    使用火山方舟 Seedance API 生成动态视频。
    支持 Seedance 1.0 Pro Fast 和 Seedance 1.5 Pro 模型。

    示例:
        >>> generator = SeedanceVideoGenerator(
        ...     model="doubao-seedance-1-5-pro-251215",
        ...     generate_audio=True,
        ... )
        >>> result = await generator.generate(
        ...     image_path="frame.png",
        ...     prompt="character smiling and waving",
        ...     output_path="output.mp4"
        ... )
    """

    def __init__(
        self,
        model: str,
        generate_audio: bool = False,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ):
        """初始化 Seedance 生成器。

        Args:
            model: 模型 ID（如 doubao-seedance-1-5-pro-251215）
            generate_audio: 是否生成音频（仅 Seedance 1.5 支持）
            api_key: API Key（默认从环境变量读取）
            endpoint: API 端点（默认从环境变量读取）
        """
        self.model = model
        self.generate_audio = generate_audio
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

    def _local_to_data_url(self, image_path: str) -> str:
        """将本地图片转换为 data URL（base64）。"""
        import base64

        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        else:
            mime_type = "image/png"

        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        return f"data:{mime_type};base64,{image_data}"

    @staticmethod
    def _normalize_aspect_ratio(value: str | None) -> str:
        """Accept both legacy and canonical follow-input ratio values."""

        ratio = str(value or "").strip().lower()
        return ratio if ":" in ratio or ratio in {"auto", "adaptive"} else "9:16"

    async def _download_video(
        self, url: str, output_path: str, max_retries: int = 3
    ) -> bool:
        """下载视频文件，失败自动重试。"""
        import httpx

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        print(
                            f"[download] attempt {attempt}/{max_retries} failed: HTTP {resp.status_code}"
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(2 * attempt)
                            continue
                        return False

                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(resp.content)

                    return True
            except Exception as e:
                print(f"[download] attempt {attempt}/{max_retries} error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 * attempt)
        return False

    async def generate(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 120,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """完整生成流程：提交 + 轮询 + 下载。

        Args:
            image_path: 首帧图像路径（本地路径或 URL）
            prompt: 动作描述
            output_path: 输出视频路径
            aspect_ratio: 宽高比（如 "9:16"）
            duration: 目标时长（秒，限制 4-12）
            poll_interval: 轮询间隔（秒）
            max_polls: 最大轮询次数
            on_log: 日志回调函数
            on_progress: 进度回调函数 (0.0 - 1.0)
            last_frame_path: 尾帧图像路径（可选，提供时启用首尾帧模式）

        Returns:
            生成结果
        """
        _deny_direct_organization_video(kwargs)

        import httpx

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        task_id: str | None = None
        project_output_dir = kwargs.get("project_output_dir")
        tracking_episode = kwargs.get("episode")
        tracking_beat_num = kwargs.get("beat_num")
        tracking_task_type = kwargs.get("task_type", "")
        tracking_cost_estimate = kwargs.get("cost_estimate")

        def _record_request_accepted(request_id: str):
            if not project_output_dir or not request_id:
                return
            try:
                record_video_request(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    provider="seedance",
                    model_name=self.model,
                    episode=tracking_episode,
                    beat_num=tracking_beat_num,
                    task_type=tracking_task_type,
                    duration_seconds=float(duration),
                    cost_estimate=tracking_cost_estimate,
                )
            except Exception as e:
                log(f"记账失败(accepted): {e}")

        def _update_request_status(
            request_id: str, status: str, error_message: str | None = None
        ):
            if not project_output_dir or not request_id:
                return
            try:
                update_video_request_status(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    status=status,
                    error_message=error_message,
                )
            except Exception as e:
                log(f"记账失败({status}): {e}")

        # 与报价/预扣共享同一套时长边界。
        from novelvideo.video_duration import normalize_video_duration_for_backend

        duration = normalize_video_duration_for_backend("seedance_fast", duration)

        # 映射 aspect_ratio 格式
        # Existing direct-Seedance deployments used ``adaptive`` while the
        # canonical media contract uses ``auto``. Accept both at this legacy
        # boundary instead of silently falling back to 9:16.
        ratio = self._normalize_aspect_ratio(aspect_ratio)

        # 处理首帧
        if image_path.startswith(("http://", "https://", "data:")):
            first_frame_url = image_path
        elif os.path.exists(image_path):
            first_frame_url = self._local_to_data_url(image_path)
            log("首帧已转换为 data URL")
        else:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"First frame not found: {image_path}",
            )

        # 处理尾帧（如果有）
        last_frame_url = None
        if last_frame_path is not None:
            if last_frame_path.startswith(("http://", "https://", "data:")):
                last_frame_url = last_frame_path
            elif os.path.exists(last_frame_path):
                last_frame_url = self._local_to_data_url(last_frame_path)
                log("尾帧已转换为 data URL")
            else:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"Last frame not found: {last_frame_path}",
                )

        # 构建 content 数组
        content = [{"type": "text", "text": prompt}]

        # 首帧
        first_frame_item = {
            "type": "image_url",
            "image_url": {"url": first_frame_url},
        }
        if last_frame_url is not None:
            first_frame_item["role"] = "first_frame"
        content.append(first_frame_item)

        # 尾帧
        if last_frame_url is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": last_frame_url},
                    "role": "last_frame",
                }
            )

        # 构建请求体
        request_body = {
            "model": self.model,
            "content": content,
            "resolution": "720p",
            "ratio": ratio,
            "duration": duration,
            "watermark": False,
        }
        request_body["generate_audio"] = self.generate_audio

        mode_desc = "首尾帧模式" if last_frame_url else "首帧模式"
        log(
            f"正在提交 Seedance 视频生成任务 ({mode_desc}, {self.model}, {duration}s)..."
        )
        progress(0.1)

        # 1. 提交任务
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
                _record_request_accepted(task_id)

        except Exception as e:
            log(f"任务提交异常: {e}")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"Submit exception: {e}",
            )

        # 2. 轮询结果
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

                    # 进度从 0.2 到 0.9
                    poll_progress = 0.2 + (poll_count / max_polls) * 0.7
                    progress(poll_progress)

                    if status_str == "succeeded":
                        log("视频生成完成，正在下载...")
                        progress(0.9)
                        _update_request_status(task_id, "completed")

                        # 提取视频 URL
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
                                _update_request_status(task_id, "downloaded")
                                return VideoGenResult(
                                    status=VideoGenStatus.DONE,
                                    video_path=output_path,
                                    video_url=video_url,
                                    task_id=task_id,
                                    duration_seconds=float(duration),
                                )
                            else:
                                log("视频下载失败")
                                _update_request_status(
                                    task_id, "failed", "Download failed"
                                )
                                return VideoGenResult(
                                    status=VideoGenStatus.FAILED,
                                    error="Download failed",
                                    task_id=task_id,
                                )
                        else:
                            log(f"API 未返回视频 URL: {data}")
                            _update_request_status(
                                task_id, "failed", "No video URL in response"
                            )
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
                        _update_request_status(task_id, "failed", error_msg)
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"Generation failed: {error_msg}",
                            task_id=task_id,
                        )

                    # queued / running → 继续轮询
                    if poll_count % 6 == 0:
                        log(f"正在生成中... ({status_str}, {poll_count}/{max_polls})")

            except Exception as e:
                log(f"查询状态异常: {e}")

            await asyncio.sleep(poll_interval)

        log("视频生成超时")
        _update_request_status(
            task_id, "failed", "Timeout waiting for video generation"
        )
        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Timeout waiting for video generation",
            task_id=task_id,
        )
