"""xAI Grok Imagine Video 720p 直连生成器（B1 拆包自单文件 video_generator.py）。"""

import asyncio
import os
from pathlib import Path
from typing import Callable, Optional

import aiohttp

from ..egress import _deny_direct_organization_video
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase

class GrokVideoGenerator(VideoGeneratorBase):
    """xAI Grok 视频生成器（首帧图生视频，固定 720p）。"""

    MODEL = "grok-imagine-video"
    DEFAULT_ENDPOINT = "https://api.x.ai/v1"

    # 单次 HTTP 请求的挂起上限：与其余生成器一致显式设 ClientTimeout，
    # 避免上游连接停摆时无限占用视频 lane（整体时长由 max_polls×poll_interval 兜底）。
    SUBMIT_TIMEOUT_SECONDS = 120.0
    POLL_TIMEOUT_SECONDS = 30.0
    DOWNLOAD_TIMEOUT_SECONDS = 180.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: str = MODEL,
        resolution: str = "720p",
    ):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        self.endpoint = (
            endpoint or os.environ.get("XAI_BASE_URL") or self.DEFAULT_ENDPOINT
        ).rstrip("/")
        self.model = model
        self.resolution = resolution

        if not self.api_key:
            raise ValueError("XAI_API_KEY must be set for Grok video generation")

    def _local_to_data_url(self, image_path: str) -> str:
        import base64

        suffix = Path(image_path).suffix.lower()
        if suffix == ".png":
            mime_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            mime_type = "image/jpeg"
        else:
            mime_type = "image/png"

        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        return f"data:{mime_type};base64,{encoded}"

    async def _download_video(
        self, url: str, output_path: str, max_retries: int = 3
    ) -> bool:
        for attempt in range(1, max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.DOWNLOAD_TIMEOUT_SECONDS)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            if attempt < max_retries:
                                await asyncio.sleep(2 * attempt)
                                continue
                            return False

                        os.makedirs(os.path.dirname(output_path), exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(await resp.read())
                        return True
            except Exception:
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
        max_polls: int = 180,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        _deny_direct_organization_video(kwargs)

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        if last_frame_path:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="Grok 720 does not support keyframe/首尾帧模式",
            )

        from novelvideo.video_duration import normalize_video_duration_for_backend

        duration = normalize_video_duration_for_backend("grok_720", duration)
        ratio = aspect_ratio if ":" in aspect_ratio else "9:16"

        if image_path.startswith(("http://", "https://", "data:")):
            image_url = image_path
        elif os.path.exists(image_path):
            image_url = self._local_to_data_url(image_path)
            log("首帧已转换为 data URL")
        else:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"First frame not found: {image_path}",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_body = {
            "model": self.model,
            "prompt": prompt,
            "image_url": image_url,
            "duration": duration,
            "aspect_ratio": ratio,
            "resolution": self.resolution,
        }

        log(
            f"正在提交 Grok 视频生成任务 ({self.model}, {duration}s, {self.resolution})..."
        )
        progress(0.1)

        try:
            submit_timeout = aiohttp.ClientTimeout(total=self.SUBMIT_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=submit_timeout) as session:
                async with session.post(
                    f"{self.endpoint}/videos/generations",
                    json=request_body,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"Submit failed: HTTP {resp.status} - {error_text[:200]}",
                        )
                    data = await resp.json()
        except Exception as e:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"Submit exception: {e}",
            )

        request_id = data.get("request_id")
        if not request_id:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"No request_id in response: {data}",
            )

        log(f"任务已提交: {request_id}")
        progress(0.2)

        for poll_count in range(max_polls):
            try:
                poll_timeout = aiohttp.ClientTimeout(total=self.POLL_TIMEOUT_SECONDS)
                async with aiohttp.ClientSession(timeout=poll_timeout) as session:
                    async with session.get(
                        f"{self.endpoint}/videos/{request_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    ) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(poll_interval)
                            continue
                        data = await resp.json()
            except Exception:
                await asyncio.sleep(poll_interval)
                continue

            status_str = (data.get("status") or "").lower()
            progress(0.2 + (poll_count / max_polls) * 0.7)

            if status_str == "done":
                video_info = data.get("video") or {}
                video_url = video_info.get("url")
                if not video_url:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="No video URL in response",
                        task_id=request_id,
                    )
                log("视频生成完成，正在下载...")
                progress(0.9)
                success = await self._download_video(video_url, output_path)
                if not success:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="Download failed",
                        task_id=request_id,
                    )
                progress(1.0)
                return VideoGenResult(
                    status=VideoGenStatus.DONE,
                    video_url=video_url,
                    video_path=output_path,
                    task_id=request_id,
                    duration_seconds=float(video_info.get("duration") or duration),
                )

            if status_str in {"failed", "expired"}:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"Grok video generation {status_str}",
                    task_id=request_id,
                )

            if poll_count % 6 == 0:
                log(
                    f"正在生成中... ({status_str or 'pending'}, {poll_count}/{max_polls})"
                )
            await asyncio.sleep(poll_interval)

        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Generation timeout",
            task_id=request_id,
        )
