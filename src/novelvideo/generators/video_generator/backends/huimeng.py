"""HuiMeng 异步任务视频生成器（B1 拆包自单文件 video_generator.py）。"""

import os
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from novelvideo.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from ..backend_utils import _seedance2_config_mapping, _seedance2_duration_from_config
from ..egress import _deny_direct_organization_video
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase
from .seedance2 import ShotReference

class HuimengVideoGenerator(VideoGeneratorBase):
    """HuiMeng async-task video generator."""

    DEFAULT_MODEL = "seedance-1.0-pro-fast"

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        client=None,
    ):
        from novelvideo.config import (
            HUIMENGI_VIDEO_GENERATE_AUDIO,
            HUIMENGI_VIDEO_RESOLUTION,
        )
        from novelvideo.generators.huimengi import HuimengiTaskClient

        self.model = model or self.DEFAULT_MODEL
        self.resolution = resolution or HUIMENGI_VIDEO_RESOLUTION
        self.generate_audio = (
            HUIMENGI_VIDEO_GENERATE_AUDIO if generate_audio is None else generate_audio
        )
        self.client = client or HuimengiTaskClient(api_key=api_key, base_url=endpoint)

    def _duration_bounds(self) -> tuple[int, int]:
        from novelvideo.video_duration import video_duration_bounds_for_backend

        bounds = video_duration_bounds_for_backend(f"huimeng_{self.model}")
        if bounds[0] is None or bounds[1] is None:
            return 2, 12
        return int(bounds[0]), int(bounds[1])

    def _supports_audio_param(self) -> bool:
        # [aipdd fork delta #6] 容错识别: 兼容 susciyuan 等网关带空格/中文的模型 id
        # (如 "AP Seedance-2.0 标准版"),改用大小写不敏感子串匹配而非精确集合。
        m = self.model.strip().lower()
        return "seedance-2." in m or "seedance-1.5" in m

    def _is_seedance2_model(self) -> bool:
        # [aipdd fork delta #6] 见 _supports_audio_param 注释。涵盖 seedance-2.0/2.5。
        return "seedance-2." in self.model.strip().lower()

    def _is_happyhorse_model(self) -> bool:
        return self.model.strip().lower() == "happyhorse-1.0"

    def _to_upload_url(
        self,
        value: str | None,
        *,
        label: str,
        log: Callable[[str], None],
        require_http_url: bool = False,
    ) -> str | None:
        from novelvideo.generators.huimengi import local_file_to_data_url

        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith(("http://", "https://")):
            return text
        if text.startswith("data:"):
            if require_http_url:
                raise ValueError(
                    "human_review requires HTTP/HTTPS media URLs; "
                    f"unsupported direct media reference for {label}: data:"
                )
            return text
        if os.path.exists(text):
            if require_http_url:
                from novelvideo.utils.oss_client import presign_or_upload_output

                oss_url = presign_or_upload_output(text)
                if not oss_url:
                    raise ValueError(
                        "human_review requires OSS presigned HTTP media URLs. "
                        f"Failed to upload or presign local file: {text}"
                    )
                log(f"{label}已上传/复用 OSS URL")
                return oss_url
            log(f"{label}已转换为 data URL")
            return local_file_to_data_url(text)
        raise FileNotFoundError(f"{label} not found: {text}")

    def _build_reference_params(
        self,
        references: list["ShotReference"] | None,
        *,
        log: Callable[[str], None],
        require_http_url: bool = False,
    ) -> tuple[dict[str, list[str]], dict[str, int]]:
        params: dict[str, list[str]] = {}
        image_urls: list[str] = []
        video_urls: list[str] = []
        audio_urls: list[str] = []

        for ref in references or []:
            path = str(getattr(ref, "path", "") or "").strip()
            if not path:
                continue
            ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
            role = str(getattr(ref, "role", "") or "").strip()
            label = f"{role or ref_type}参考"
            data_url = self._to_upload_url(
                path,
                label=label,
                log=log,
                require_http_url=require_http_url,
            )
            if not data_url:
                continue
            if ref_type == "image":
                image_urls.append(data_url)
            elif ref_type == "video":
                video_urls.append(data_url)
            elif ref_type == "audio":
                audio_urls.append(data_url)

        if image_urls:
            params["reference_images"] = image_urls[:9]
        if video_urls:
            params["reference_videos"] = video_urls[:3]
        if audio_urls:
            params["reference_audios"] = audio_urls[:3]

        return params, {
            "image_count": len(params.get("reference_images", [])),
            "video_count": len(params.get("reference_videos", [])),
            "audio_count": len(params.get("reference_audios", [])),
        }

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "adaptive",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 120,
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

        project_output_dir = kwargs.get("project_output_dir")
        tracking_episode = kwargs.get("episode")
        tracking_beat_num = kwargs.get("beat_num")
        tracking_task_type = kwargs.get("task_type", "")
        tracking_cost_estimate = kwargs.get("cost_estimate")

        def record_accepted(request_id: str):
            if not project_output_dir or not request_id:
                return
            try:
                record_video_request(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    provider="huimeng",
                    model_name=self.model,
                    episode=tracking_episode,
                    beat_num=tracking_beat_num,
                    task_type=tracking_task_type,
                    duration_seconds=float(duration),
                    cost_estimate=tracking_cost_estimate,
                )
            except Exception as exc:
                log(f"记账失败(accepted): {exc}")

        def update_request_status(
            request_id: str,
            status: str,
            error_message: str | None = None,
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
            except Exception as exc:
                log(f"记账失败({status}): {exc}")

        is_seedance2_model = self._is_seedance2_model()
        seedance2_config = (
            _seedance2_config_mapping(kwargs.get("seedance2_config"))
            if is_seedance2_model
            else {}
        )
        duration = _seedance2_duration_from_config(seedance2_config, duration)
        original_duration = duration
        from novelvideo.video_duration import normalize_video_duration_for_backend

        duration = normalize_video_duration_for_backend(
            f"huimeng_{self.model}",
            duration,
        )
        if duration != original_duration:
            log(f"时长已调整: {original_duration:.1f}s -> {duration:.0f}s")

        ratio = aspect_ratio if ":" in aspect_ratio else "adaptive"

        references = kwargs.get("references") or []
        require_http_media = (
            bool(seedance2_config.get("human_review"))
            if "human_review" in seedance2_config
            else bool(kwargs.get("human_review", False))
        )

        try:
            first_frame = self._to_upload_url(
                image_path,
                label="首帧",
                log=log,
                require_http_url=require_http_media,
            )
            last_frame = self._to_upload_url(
                last_frame_path,
                label="尾帧",
                log=log,
                require_http_url=require_http_media,
            )
            reference_params, ref_counts = self._build_reference_params(
                references,
                log=log,
                require_http_url=require_http_media,
            )
        except (FileNotFoundError, ValueError) as exc:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
            )

        has_multimodal_refs = any(ref_counts.values())

        if is_seedance2_model:
            from novelvideo.seedance2_i2v.models import Seedance2I2VMode
            from novelvideo.seedance2_i2v.request import build_seedance2_huimeng_params

            if last_frame:
                mode = Seedance2I2VMode.FIRST_LAST_FRAME
            elif has_multimodal_refs:
                mode = Seedance2I2VMode.MULTIMODAL_REFERENCE
            elif first_frame:
                mode = Seedance2I2VMode.FIRST_FRAME
            else:
                mode = Seedance2I2VMode.TEXT_TO_VIDEO

            config = seedance2_config
            config_resolution = str(
                config.get("resolution") or self.resolution or "720p"
            ).strip()
            config_ratio = str(config.get("ratio") or ratio or "adaptive").strip()
            config.update(
                {
                    "mode": mode.value,
                    "final_prompt": prompt,
                    "duration": duration,
                    "resolution": config_resolution,
                    "ratio": config_ratio,
                }
            )
            if "generate_audio" not in config:
                config["generate_audio"] = bool(self.generate_audio)
                config["generate_audio_user_set"] = True
            elif "generate_audio_user_set" not in config:
                config["generate_audio_user_set"] = True
            if "return_last_frame" not in config:
                config["return_last_frame"] = False
            if "human_review" not in config:
                # Direct generator calls default to no material-review upload unless
                # the caller passes the per-beat Seedance2 config or an explicit kwarg.
                config["human_review"] = bool(kwargs.get("human_review", False))
                config["human_review_user_set"] = True
            elif "human_review_user_set" not in config:
                config["human_review_user_set"] = True
            try:
                params = build_seedance2_huimeng_params(
                    config,
                    first_frame=first_frame or "",
                    last_frame=last_frame or "",
                    reference_images=reference_params.get("reference_images"),
                    reference_videos=reference_params.get("reference_videos"),
                    reference_audios=reference_params.get("reference_audios"),
                )
            except ValueError as exc:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=str(exc),
                )
        else:
            params = {
                "prompt": prompt,
                "duration": duration,
                "resolution": self.resolution,
                "ratio": ratio,
                "return_last_frame": False,
            }

            if last_frame:
                if not first_frame:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="First frame is required when last_frame_path is provided",
                    )
                params["first_frame_image"] = first_frame
                params["last_frame_image"] = last_frame
            elif has_multimodal_refs:
                params.update(reference_params)
            elif first_frame:
                params["image_url"] = first_frame

            if self._supports_audio_param():
                params["generate_audio"] = bool(self.generate_audio)

        task_id: str | None = None
        try:
            request_resolution = str(
                params.get("resolution") or self.resolution or ""
            ).strip()
            log(
                f"正在提交 HuiMeng 视频任务 "
                f"({self.model}, refs={ref_counts['image_count']}图/{ref_counts['video_count']}视频/{ref_counts['audio_count']}音频, "
                f"{duration}s, {request_resolution})..."
            )
            progress(0.1)
            submitted = await self.client.submit_task(model=self.model, params=params)
            task_id = submitted["task_id"]
            record_accepted(task_id)
            log(f"任务已提交: {task_id}")
            progress(0.2)

            task = await self.client.wait_for_completion(
                task_id,
                poll_interval=poll_interval,
                max_polls=max_polls,
                on_log=on_log,
                on_progress=on_progress,
            )

            from novelvideo.generators.huimengi import (
                extract_huimeng_result_duration,
                extract_huimeng_result_last_frame_url,
                extract_huimeng_result_url,
            )

            result = task.get("result") or {}
            task_result_payload = {**result, **task}
            video_url = extract_huimeng_result_url(result, "video_url", "video_urls")
            if not video_url:
                update_request_status(
                    task_id, "failed", "No video_url in HuiMeng result"
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"No video_url in HuiMeng result: {result}",
                    task_id=task_id,
                )

            log("视频生成完成，正在下载...")
            await self.client.download_url(video_url, output_path)
            last_frame_url = ""
            last_frame_path = ""
            if bool(params.get("return_last_frame")):
                last_frame_url = extract_huimeng_result_last_frame_url(
                    task_result_payload
                )
                if last_frame_url:
                    video_output_path = Path(output_path)
                    parsed_last_frame_url = urllib.parse.urlparse(last_frame_url)
                    last_frame_suffix = Path(parsed_last_frame_url.path).suffix.lower()
                    if last_frame_suffix not in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".gif",
                    }:
                        last_frame_suffix = ".png"
                    last_frame_output_path = (
                        video_output_path.parent
                        / "returned_last_frames"
                        / f"{video_output_path.stem}{last_frame_suffix}"
                    )
                    await self.client.download_image_url(
                        last_frame_url,
                        str(last_frame_output_path),
                    )
                    last_frame_path = last_frame_output_path.as_posix()
                    log("已保存 HuiMeng 返回尾帧")
            progress(1.0)
            update_request_status(task_id, "completed")
            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_url=video_url,
                video_path=output_path,
                last_frame_url=last_frame_url or None,
                last_frame_path=last_frame_path or None,
                task_id=task_id,
                provider_task_id=task_id,
                duration_seconds=extract_huimeng_result_duration(result)
                or float(duration),
            )

        except Exception as exc:
            if task_id:
                update_request_status(task_id, "failed", str(exc))
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
                task_id=task_id,
            )
