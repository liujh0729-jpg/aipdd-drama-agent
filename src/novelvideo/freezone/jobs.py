"""Pure async jobs for freezone single-image gen / edit.

These are called from the task backend runners and from unit-test harnesses.
They never touch the queue backend, the API layer, or task_state.

Provider selection (since v1.1):
- `provider` / `model` / `quality` get threaded into
  `get_grid_generation_config(provider_override=, model_override=)` for the
          image generation/edit path so the caller can pick the supported SuperTale
          providers: `newapi` / `huimeng` / `openrouter` / `openai`.
- A legacy `provider="volcengine"` branch remains for old canvases/scripts, but
  the Freezone UI no longer exposes it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from novelvideo.freezone.paths import output_path_for_job, outputs_dir
from novelvideo.video_encoder import video_encode_args
from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.ports.egress import EgressError
from novelvideo.task_backend.subprocesses import (
    EgressBoundaryError,
    RestrictedSubprocessPolicy,
    run_project_subprocess,
)

logger = logging.getLogger(__name__)


async def run_freezone_gen(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str = "2K",
    reference_paths: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    model_params: Optional[dict[str, Any]] = None,
    request_schema: Optional[dict[str, Any]] = None,
    output_task_type: str = "freezone_gen",
    egress_context: TrustedEgressContext | None = None,
) -> Path:
    """text → image (with optional reference images).

    Routes through grid_generation for the supported SuperTale providers.
    """
    out = output_path_for_job(project_dir, output_task_type or "freezone_gen", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Routing (v1.2):
    #   provider == "volcengine"  → Volcengine Seedream (text-only path; refs ignored)
    #   anything else (including None default) → grid_generation:
    #     - with refs → generate_reference_edit_image
    #     - no refs   → generate_text_to_image  (NEW v1.2)
    if egress_context is not None and type(egress_context) is not TrustedEgressContext:
        raise TypeError("egress_context must be a TrustedEgressContext")
    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if (
        egress_context is not None
        and egress_context.is_organization
        and (provider or "").lower() == "volcengine"
    ):
        raise EgressError("ORG_EGRESS_DENIED")
    if (provider or "").lower() == "volcengine":
        return await _run_volcengine_text_to_image(
            out=out,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.grid_generation import (
        generate_reference_edit_image,
        generate_text_to_image,
    )

    if egress_context is not None and egress_context.is_organization:
        cfg = {
            "provider": "newapi",
            "api_key": "request-scoped",
            "base_url": "https://request-scoped.invalid/v1",
            "model": model or "gpt-image-2",
            "mode": "1x1",
            "rows": 1,
            "cols": 1,
            "total_panels": 1,
        }
    else:
        cfg = get_grid_generation_config(
            provider_override=provider,
            model_override=model,
            image_size_override=image_size,
        )
    cfg["newapi_model_params"] = model_params or {}
    cfg["newapi_request_schema"] = request_schema or {}
    if reference_paths:
        await generate_reference_edit_image(
            prompt=prompt,
            reference_images=reference_paths,
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
            egress_context=egress_context,
            egress_capability="freezone.image.generate",
        )
    else:
        await generate_text_to_image(
            prompt=prompt,
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
            egress_context=egress_context,
            egress_capability="freezone.image.generate",
        )
    return out


async def run_freezone_mask_edit(
    *,
    project_dir: Path,
    job_id: str,
    base_path: str,
    mask_path: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str = "2K",
    quality: str = "medium",
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    egress_context: TrustedEgressContext | None = None,
) -> Path:
    """Masked erase/edit via the same provider routing used by Freezone image edit."""
    out = output_path_for_job(project_dir, "freezone_mask_edit", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    base_p = Path(base_path)
    mask_p = Path(mask_path)
    if not base_p.exists():
        raise FileNotFoundError(f"base not found: {base_p}")
    if not mask_p.exists():
        raise FileNotFoundError(f"mask not found: {mask_p}")

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.grid_generation import generate_reference_edit_image
    from novelvideo.utils.error_redaction import redact_secrets

    if egress_context is not None and type(egress_context) is not TrustedEgressContext:
        raise TypeError("egress_context must be a TrustedEgressContext")
    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if egress_context is not None and egress_context.is_organization:
        # 与 run_freezone_edit 同一口径：组织下不读本地目录配置。
        # `get_grid_generation_config` 可能返回非 newapi provider，而组织出网闸门
        # （`grid_generation/egress.py:47-48`）对非 newapi 一律 ORG_EGRESS_DENIED。
        cfg = {
            "provider": "newapi",
            "api_key": "request-scoped",
            "base_url": "https://request-scoped.invalid/v1",
            "model": model or "gpt-image-2",
            "mode": "1x1",
            "rows": 1,
            "cols": 1,
            "total_panels": 1,
        }
    else:
        cfg = get_grid_generation_config(
            provider_override=provider,
            model_override=model,
            image_size_override=image_size,
        )
    provider_name = str(cfg.get("provider") or provider or "newapi").strip().lower()
    mask_prompt = (
        f"{prompt}\n\n"
        "Use Image 1 as the source image. Image 2 is the same image with a translucent RED "
        "highlight painted over the region to edit. Edit ONLY the red-highlighted region; the "
        "red highlight is just an annotation marking where to work and must NOT appear in the "
        "output. Preserve all pixels outside the highlighted region — composition, identity, "
        "lighting, and texture — as much as possible."
    ).strip()
    try:
        await generate_reference_edit_image(
            prompt=mask_prompt,
            reference_images=[str(base_p), str(mask_p)],
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
            egress_context=egress_context,
            egress_capability="freezone.image.generate",
        )
    except Exception as exc:
        raise RuntimeError(
            f"{provider_name} 图像擦除失败：{redact_secrets(exc)}"
        ) from exc
    if not out.exists():
        raise RuntimeError(f"{provider_name} 图像擦除未生成输出文件")
    return out


async def run_freezone_upscale(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    target_width: int = 2048,
    target_height: int = 2048,
    strength: float = 0.9,
    enhancement_prompt: Optional[str] = None,
) -> Path:
    """High-res restoration via Seedream img2img with strength≈0.9.

    Reuses `VolcengineImageGenerator.upscale_with_img2img()` — preserves the
    input image's content while bumping resolution. Output lands under
    `freezone/_outputs/freezone_upscale/<job_id>.png`.
    """
    out = output_path_for_job(project_dir, "freezone_upscale", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    from novelvideo.generators.image_generator import create_image_generator

    generator = create_image_generator()
    result = await generator.upscale_with_img2img(
        input_path=source_path,
        output_path=str(out),
        target_width=target_width,
        target_height=target_height,
        strength=strength,
        enhancement_prompt=enhancement_prompt,
    )
    if not result or not result.success:
        err = result.error if result else "unknown error"
        raise RuntimeError(f"upscale failed: {err}")
    if not out.exists():
        if result.image_base64:
            import base64

            out.write_bytes(base64.b64decode(result.image_base64))
        else:
            raise RuntimeError("upscale produced no file or bytes")
    return out


async def _run_volcengine_text_to_image(
    *,
    out: Path,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
) -> Path:
    """Volcengine Seedream 4.0 text→image (no provider/model override)."""
    from novelvideo.generators.image_generator import create_image_generator

    width, height = _aspect_to_dims(aspect_ratio, image_size)
    generator = create_image_generator()
    result = await generator.generate(
        prompt=prompt,
        output_path=str(out),
        width=width,
        height=height,
    )
    if not result or not result.success:
        err = result.error if result else "unknown error"
        raise RuntimeError(f"Volcengine text→image generation failed: {err}")
    if not out.exists():
        if result.image_base64:
            import base64

            out.write_bytes(base64.b64decode(result.image_base64))
        else:
            raise RuntimeError("Volcengine text→image produced no file or bytes")
    return out


async def run_freezone_edit(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    base_path: str,
    extra_reference_paths: Optional[list[str]] = None,
    aspect_ratio: str = "2:3",
    image_size: str = "2K",
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    model_params: Optional[dict[str, Any]] = None,
    request_schema: Optional[dict[str, Any]] = None,
    output_task_type: str = "freezone_edit",
    egress_context: TrustedEgressContext | None = None,
) -> Path:
    """image + reference + prompt → new image.

    v1 doesn't enforce a hard mask — most providers (nanobanana, OpenAI image
    edit) treat reference images plus a prompt as soft guidance. The base
    image is passed first in the references list so the model anchors on it.
    """
    out = output_path_for_job(project_dir, output_task_type or "freezone_edit", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    refs: list[str] = [base_path]
    if extra_reference_paths:
        refs.extend(extra_reference_paths)

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.grid_generation import generate_reference_edit_image

    if egress_context is not None and type(egress_context) is not TrustedEgressContext:
        raise TypeError("egress_context must be a TrustedEgressContext")
    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if egress_context is not None and egress_context.is_organization:
        cfg = {
            "provider": "newapi",
            "api_key": "request-scoped",
            "base_url": "https://request-scoped.invalid/v1",
            "model": model or "gpt-image-2",
            "mode": "1x1",
            "rows": 1,
            "cols": 1,
            "total_panels": 1,
        }
    else:
        cfg = get_grid_generation_config(
            provider_override=provider,
            model_override=model,
            image_size_override=image_size,
        )
    # 与 run_freezone_gen 同一口径：目录声明的动态参数按 schema 里的 requestPath
    # 写进网关请求体。少了这两行，图编辑侧的 model_params 会在这里被丢掉。
    # 组织支同样要带上——请求作用域凭据只换掉出网身份，不改请求体形状。
    cfg["newapi_model_params"] = model_params or {}
    cfg["newapi_request_schema"] = request_schema or {}
    await generate_reference_edit_image(
        prompt=prompt,
        reference_images=refs,
        output_path=str(out),
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        api_key=api_key,
        config=cfg,
        egress_context=egress_context,
        egress_capability="freezone.image.generate",
    )
    return out


def ensure_freezone_dirs(project_dir: Path) -> None:
    """Create freezone subdirectories on first use; cheap and idempotent."""
    (project_dir / "freezone" / "_uploads").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_gen").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_edit").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_upscale").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_gen").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_compose").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_extract").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_analyze").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_mask_edit").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_erase").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_upscale").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_audio_separate").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_audio_speech").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_audio_eleven_music").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_image_to_3gs").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_douyin_copywrite").mkdir(
        parents=True, exist_ok=True
    )
    # [aipdd fork delta req46] 工具页「音乐创作」产物目录（mp3/wav/pcm）。
    outputs_dir(project_dir, "freezone_music_generate").mkdir(
        parents=True, exist_ok=True
    )


FREEZONE_VIDEO_RESOLUTION_MAP: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}

FREEZONE_VIDEO_UPSCALE_LONG_EDGE: dict[str, int] = {
    "1080p": 1920,
    "2k": 2560,
    "4k": 3840,
}


def _video_upscale_filter(resolution: str, denoise_strength: str) -> str:
    target = FREEZONE_VIDEO_UPSCALE_LONG_EDGE.get(resolution.lower())
    if not target:
        raise ValueError(f"unsupported video upscale resolution: {resolution}")
    filters = [
        f"scale='if(gte(iw,ih),{target},-2)':"
        f"'if(gte(iw,ih),-2,{target})':flags=lanczos"
    ]
    denoise = (denoise_strength or "1x").lower()
    if denoise == "1x":
        filters.append("hqdn3d=1.2:1.2:4:4")
    elif denoise == "2x":
        filters.append("hqdn3d=2.0:2.0:6:6")
    elif denoise != "none":
        raise ValueError(f"unsupported denoise_strength: {denoise_strength}")
    filters.append("unsharp=5:5:0.55:3:3:0.25")
    filters.append("format=yuv420p")
    return ",".join(filters)


async def run_freezone_video_upscale(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    resolution: str = "1080p",
    frame_interpolation: str = "none",
    denoise_strength: str = "1x",
    egress_context: TrustedEgressContext | None = None,
) -> tuple[Path, dict]:
    """[aipdd fork delta] 视频高清：优先走火山 MediaKit 生成式超分，未配置/组织身份/
    失败时回退原本地 ffmpeg（缩放+降噪+锐化）。签名与产物路径保持不变。

    `egress_context` 是受限出网治理要求：MediaKit 是直连第三方主机、不可经网关
    路由，组织身份必须 fail-closed（不得用平台凭据直连），因此组织任务直接走本地
    ffmpeg。个人/平台身份才允许直连 MediaKit。
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"video source not found: {src}")

    out = outputs_dir(project_dir, "freezone_video_upscale") / f"{job_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    from novelvideo.generators import mediakit

    # 组织身份不下直连第三方主机的赌注：走本地 ffmpeg。
    is_organization = egress_context is not None and egress_context.is_organization
    # [aipdd fork delta] 引擎策略固定为「有 Key 就走 MediaKit，否则本地 ffmpeg」：
    # 不再读用户可选的 backend 模式，见 mediakit.mediakit_configured()。
    use_mediakit = mediakit.mediakit_configured() and not is_organization

    if use_mediakit:
        try:
            meta = await mediakit.enhance_video_file(
                source_path=src,
                output_path=out,
                resolution=resolution,
                on_log=lambda msg: logger.info("[video-upscale] %s", msg),
            )
            return out, meta
        except Exception as exc:  # noqa: BLE001 - 有 Key 但 MediaKit 失败时回退本地 ffmpeg
            logger.warning(
                "[video-upscale] MediaKit 增强失败，回退 ffmpeg: %s", exc
            )

    # ---- 兜底：原本地 ffmpeg 高清（不出网、不取凭证）----
    if frame_interpolation != "none":
        raise ValueError("basic video upscale only supports frame_interpolation='none'")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")

    vf = _video_upscale_filter(resolution, denoise_strength)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        *video_encode_args(preset="slow", crf="18"),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg video upscale failed: {proc.stderr[-1000:]}")
    meta = {
        "backend": "ffmpeg",
        "resolution": resolution,
        "frame_interpolation": frame_interpolation,
        "denoise_strength": denoise_strength,
        "video_filter": vf,
    }
    return out, meta


async def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    egress_context=None,
) -> None:
    if egress_context is None:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    else:
        if cwd is None:
            raise EgressBoundaryError("ORG_SERVICE_EGRESS_DENIED")
        minimal_env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        policy = RestrictedSubprocessPolicy(
            command=tuple(cmd),
            cwd=cwd.resolve(),
            env=minimal_env,
        )
        proc = await asyncio.to_thread(
            run_project_subprocess,
            cmd,
            cwd=cwd,
            env=minimal_env,
            egress_context=egress_context,
            restricted_policy=policy,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    if proc.returncode != 0:
        if egress_context is not None:
            raise EgressBoundaryError("ORG_SERVICE_EGRESS_DENIED")
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(stderr[-1000:] or f"command failed: {' '.join(cmd)}")


async def _probe_has_audio(source_path: str) -> bool:
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


async def _render_gap_clip(
    *,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    background_color: str,
) -> None:
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background_color}:s={width}x{height}:r={fps}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{duration:.3f}",
            *video_encode_args(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(output_path),
        ]
    )


async def _render_video_clip(
    *,
    source_path: str,
    output_path: Path,
    source_start: float,
    duration: float,
    width: int,
    height: int,
    fps: int,
    background_color: str,
    keep_original_audio: bool,
    volume: float,
    muted: bool,
) -> None:
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background_color},fps={fps}"
    )
    has_audio = (
        keep_original_audio and (not muted) and await _probe_has_audio(source_path)
    )

    if has_audio:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-vf",
            video_filter,
            *video_encode_args(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-af",
            f"volume={volume:.4f}",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            video_filter,
            *video_encode_args(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    await _run_cmd(cmd)


async def _render_audio_clip(
    *,
    source_path: str,
    output_path: Path,
    source_start: float,
    duration: float,
    volume: float,
) -> None:
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-vn",
            "-af",
            f"volume={volume:.4f}",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    )


async def _concat_media_segments(segment_paths: list[Path], output_path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False
    ) as handle:
        for path in segment_paths:
            safe_path = str(path).replace("'", "'\\''")
            handle.write(f"file '{safe_path}'\n")
        list_path = Path(handle.name)
    try:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                *video_encode_args(),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)


async def stitch_freezone_video_clone_segments(
    *,
    segment_paths: list[Path],
    shot_durations: list[float],
    output_path: Path,
    audio_mode: str = "generate",
    source_video_path: Path | None = None,
) -> None:
    """把逐镜复刻片段按镜头原时长裁齐后拼接成片。

    逐段超出目标时长 0.3 秒以上时先重编码裁到目标时长，保留复刻节奏；
    ``audio_mode="mute"`` 剥掉模型音轨，``"source"`` 挂回参考视频原声
    （参考视频无音轨时退化为静音），``"generate"`` 直接使用各段自带音频。
    纯本地 ffmpeg，不出网、不取凭证。
    """
    if not segment_paths:
        raise ValueError("no segments to stitch")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    trimmed_dir = output_path.parent / f"{output_path.stem}_segments"
    trimmed_dir.mkdir(parents=True, exist_ok=True)

    stitched_inputs: list[Path] = []
    for index, segment_path in enumerate(segment_paths, start=1):
        if not segment_path.exists():
            raise FileNotFoundError(f"clone segment missing: {segment_path}")
        target = (
            float(shot_durations[index - 1]) if index <= len(shot_durations) else 0.0
        )
        segment_seconds = await _probe_video_duration(str(segment_path))
        if target > 0 and segment_seconds > target + 0.3:
            trimmed = trimmed_dir / f"trim_{index:02d}.mp4"
            await _run_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(segment_path),
                    "-t",
                    f"{target:.3f}",
                    *video_encode_args(),
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(trimmed),
                ]
            )
            stitched_inputs.append(trimmed)
        else:
            stitched_inputs.append(segment_path)

    if audio_mode == "generate":
        await _concat_media_segments(stitched_inputs, output_path)
        return

    concat_path = output_path.parent / f"{output_path.stem}_concat.mp4"
    await _concat_media_segments(stitched_inputs, concat_path)
    try:
        has_source_audio = False
        if (
            audio_mode == "source"
            and source_video_path is not None
            and source_video_path.exists()
        ):
            has_source_audio = await _probe_has_audio(str(source_video_path))
        if audio_mode == "source" and has_source_audio:
            await _run_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(concat_path),
                    "-i",
                    str(source_video_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ]
            )
        else:
            # mute（或参考视频无原声）：剥掉生成音轨。
            await _run_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(concat_path),
                    "-map",
                    "0:v:0",
                    "-c:v",
                    "copy",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ]
            )
    finally:
        concat_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 一句话生成短视频（freezone_video_idea）：本地拼接与「新机位」抽帧
# --------------------------------------------------------------------------- #


async def concat_freezone_video_segments(
    *, segment_paths: list[Path], output_path: Path
) -> None:
    """按顺序把多段视频拼成一条成片（重编码，兼容不同编码参数）。纯本地 ffmpeg。

    与 ViMax 的 moviepy ``concatenate_videoclips`` 同语义：镜头按序直接相接。
    """
    if not segment_paths:
        raise ValueError("no segments to concat")
    for path in segment_paths:
        if not path.exists():
            raise FileNotFoundError(f"video segment missing: {path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _concat_media_segments(segment_paths, output_path)


async def extract_idea_new_camera_frame(
    *,
    transition_video_path: Path,
    output_path: Path,
    scene_threshold: float = 0.3,
) -> Path:
    """从过渡视频里提取「新机位」参考帧（ViMax ``get_new_camera_image`` 口径）。

    scenedetect 语义的 ffmpeg 等价：`select='gt(scene,threshold)'` 选出的第一帧
    即「第二个场景的首帧」（第一个画面突变点之后的第一帧）；没有检测到突变时
    退回过渡视频末帧（i2v 视频尾部通常已稳定到目标机位）。纯本地 ffmpeg。
    """
    if not transition_video_path.exists():
        raise FileNotFoundError(
            f"transition video not found: {transition_video_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene_dir = output_path.parent / f"{output_path.stem}_scene_frames"
    scene_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(scene_dir / "cam_%03d.png")
    try:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(transition_video_path),
                "-vf",
                f"select='gt(scene,{scene_threshold})'",
                "-vsync",
                "vfr",
                "-frames:v",
                "1",
                pattern,
            ]
        )
    except RuntimeError:
        pass  # 无匹配帧时 ffmpeg 可能非零退出：按「无突变」处理，走末帧兜底。
    candidates = sorted(scene_dir.glob("cam_*.png"))
    if candidates:
        shutil.copy(candidates[0], output_path)
        return output_path

    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-sseof",
            "-0.1",
            "-i",
            str(transition_video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_path),
        ]
    )
    if not output_path.exists():
        raise RuntimeError("unable to extract new camera frame from transition video")
    return output_path


# --------------------------------------------------------------------------- #
# 混剪短视频渲染（freezone_video_mix）：素材切段 → 拼接 → 字幕/混音
# --------------------------------------------------------------------------- #

# 混剪字幕字体：运行时自由选用部署环境上可用的字体，不绑定特定字体族。
# 优先级：跨平台 CJK 候选清单（部署服务器装/挂了哪个就用哪个）→ fontconfig
# 对简体中文的最佳匹配 → 留空（交给 libass/fontconfig 自行 fallback）。
_MIX_SUBTITLE_FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "Droid Sans Fallback",
    "SimHei",
    "Arial Unicode MS",
)
# 图片 Ken Burns 的峰值放大倍率与片段间淡入淡出时长（秒）。
_MIX_KENBURNS_ZOOM = 0.12
_MIX_FADE_SECONDS = 0.25
# BGM 自动音量：有配音 0.2（MoneyPrinterTurbo bgm_volume 默认），静音唯一音轨 1.0。
_MIX_BGM_VOLUME_WITH_VOICE = 0.2
_MIX_BGM_VOLUME_MUTE = 1.0
# BGM 尾部淡出时长（秒）。
_MIX_BGM_FADE_SECONDS = 1.2
# 字幕字号档位：相对 min(画布宽,高) 的比例（medium 为原自动换算口径）。
_MIX_SUBTITLE_FONT_SIZE_SCALES = {
    "small": 0.046,
    "medium": 0.058,
    "large": 0.072,
}


@lru_cache(maxsize=1)
def _mix_available_font_families() -> frozenset[str] | None:
    """部署环境的字体族清单（fontconfig `fc-list`）；不可探测时返回 None。"""
    try:
        completed = subprocess.run(
            ["fc-list", "--format", "%{family}\n"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    families: set[str] = set()
    for line in completed.stdout.splitlines():
        for family in line.split(","):
            name = family.strip()
            if name:
                families.add(name)
    return frozenset(families)


@lru_cache(maxsize=1)
def _mix_subtitle_font_name() -> str:
    """自由选用部署环境可用的字幕字体；无匹配时返回空串（libass fallback）。"""
    families = _mix_available_font_families()
    if not families:
        return ""
    for candidate in _MIX_SUBTITLE_FONT_CANDIDATES:
        if candidate in families:
            return candidate
    # 候选清单未命中：让 fontconfig 给出简体中文 sans-serif 的最佳匹配。
    try:
        completed = subprocess.run(
            ["fc-match", "--format", "%{family}", "sans-serif:lang=zh-cn"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    matched = str(completed.stdout).split(",")[0].strip()
    return matched if matched in families else ""


def _mix_subtitle_force_style(*, width: int, height: int, size: str = "medium") -> str:
    """按画布尺寸换算字幕 force_style（字号/底边距随高度缩放）。

    ``size`` 为字号档位（small/medium/large，默认 medium=原自动换算口径），
    未知值回退 medium。FontName 取部署环境运行时探测结果；探测为空则不写
    该字段，交给 libass 自行 fallback。样式为白字半透明黑边、底部居中。
    """
    scale = _MIX_SUBTITLE_FONT_SIZE_SCALES.get(
        str(size or "").strip().lower(), _MIX_SUBTITLE_FONT_SIZE_SCALES["medium"]
    )
    font_size = max(28, int(round(min(width, height) * scale)))
    margin_v = max(24, int(round(height * 0.06)))
    font_family = _mix_subtitle_font_name()
    font_part = f"FontName={font_family}," if font_family else ""
    return (
        f"{font_part}FontSize={font_size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H66000000,"
        f"BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV={margin_v}"
    )


def _escape_filter_path(path: str) -> str:
    """filtergraph 引号内的路径转义（反斜杠与单引号）。"""
    return path.replace("\\", "\\\\").replace("'", "\\'")


async def _render_mix_segment(
    *,
    source_path: str,
    kind: str,
    output_path: Path,
    source_start: float,
    duration: float,
    loop: bool,
    width: int,
    height: int,
    fps: int,
    fit_mode: str,
    transition: str,
    zoom_in: bool,
) -> None:
    """渲染一个混剪片段：统一画布规格与帧率，丢弃素材原声。

    图片走 `-loop 1` 输入；``cover`` 时用 2x 超采样 + zoompan 做 Ken Burns
    （奇偶片段交替推近/拉远），``contain`` 保持静态适配。视频按切段窗口裁取，
    ``loop`` 片段在素材不足时循环输入。
    """
    chains: list[str] = []
    if kind == "image":
        if fit_mode == "cover":
            # 2x 超采样再 zoompan 输出半尺寸，x/y 采样更细、缩放不抖。
            frames = max(1, int(round(duration * fps)))
            step = _MIX_KENBURNS_ZOOM / frames
            if zoom_in:
                zoom_expr = f"min({1.0 + _MIX_KENBURNS_ZOOM:.4f},1.0+on*{step:.7f})"
            else:
                zoom_expr = f"max(1.0,{1.0 + _MIX_KENBURNS_ZOOM:.4f}-on*{step:.7f})"
            chains.append(
                f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},"
                f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s={width}x{height}:fps={fps}"
            )
        else:
            chains.append(
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    else:
        if fit_mode == "cover":
            chains.append(
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        else:
            chains.append(
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    chains.append(f"fps={fps}")
    if transition == "fade":
        fade = min(_MIX_FADE_SECONDS, duration / 3.0)
        chains.append(f"fade=t=in:st=0:d={fade:.3f}")
        chains.append(f"fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}")
    chains.append("format=yuv420p")

    cmd = ["ffmpeg", "-y"]
    if kind == "image":
        cmd += [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
        ]
    else:
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += [
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
        ]
    cmd += [
        "-vf",
        ",".join(chains),
        "-an",
        *video_encode_args(),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await _run_cmd(cmd)


async def run_freezone_video_mix_render(
    *,
    job_id: str,
    material_paths: list[str],
    material_kinds: list[str],
    plan: list[dict[str, Any]],
    canvas_width: int,
    canvas_height: int,
    fps: int = 30,
    fit_mode: str = "cover",
    transition: str = "fade",
    audio_mode: str = "generate",
    narration_path: str | None = None,
    bgm_path: str | None = None,
    bgm_volume: float | None = None,
    subtitle_srt_text: str = "",
    subtitle_font_size: str = "medium",
    output_path: Path,
    on_progress: Callable[[float, str], None] | None = None,
) -> Path:
    """把素材池按规划渲染成一条混剪成片。纯本地 ffmpeg，不出网、不取凭证。

    逐段渲染（图片 Ken Burns/静态适配、视频裁段适配）→ 拼接 → 一次性完成
    字幕烧录与配音/BGM 混音；素材原声一律丢弃，最终音轨 = 配音 + 循环 BGM
    （MoneyPrinterTurbo local 模式口径）。成功后清理中间目录，失败时保留
    ``{job_id}_work`` 供排查。
    """
    if not plan:
        raise ValueError("mix plan is empty")
    paths = list(material_paths)
    kinds = list(material_kinds)
    if len(paths) != len(kinds):
        raise ValueError("mix material paths/kinds length mismatch")

    def _notify(fraction: float, message: str) -> None:
        if on_progress is not None:
            on_progress(max(0.0, min(1.0, float(fraction))), message)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / f"{output_path.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    fit = "contain" if str(fit_mode).strip().lower() == "contain" else "cover"
    fade_enabled = str(transition).strip().lower() == "fade"
    total_clips = len(plan)
    total_seconds = 0.0
    for raw in plan:
        total_seconds += max(0.1, float(raw.get("duration") or 0.0))

    completed = False
    try:
        # 1) 逐段渲染（统一规格、无音轨），完成后才能进 concat。
        segment_paths: list[Path] = []
        for index, raw in enumerate(plan, start=1):
            material_index = int(raw.get("material_index") or 0)
            if material_index < 0 or material_index >= len(paths):
                raise ValueError(f"mix plan references unknown material {material_index}")
            _notify(
                0.70 * (index - 1) / total_clips,
                f"渲染第 {index}/{total_clips} 个混剪片段...",
            )
            segment_path = work_dir / f"clip_{index:03d}.mp4"
            await _render_mix_segment(
                source_path=paths[material_index],
                kind="image" if kinds[material_index] == "image" else "video",
                output_path=segment_path,
                source_start=max(0.0, float(raw.get("source_start") or 0.0)),
                duration=max(0.1, float(raw.get("duration") or 0.0)),
                loop=bool(raw.get("loop")),
                width=int(canvas_width),
                height=int(canvas_height),
                fps=int(fps),
                fit_mode=fit,
                transition="fade" if fade_enabled else "none",
                zoom_in=(index % 2 == 1),
            )
            segment_paths.append(segment_path)

        # 2) 拼接为统一时间线。
        _notify(0.74, "拼接混剪片段...")
        concat_path = work_dir / "concat.mp4"
        await _concat_media_segments(segment_paths, concat_path)

        # 3) 字幕 + 音轨终混（一次性完成）。
        narration = str(narration_path or "")
        bgm = str(bgm_path or "")
        has_narration = str(audio_mode) == "generate" and bool(narration)
        if has_narration and not Path(narration).exists():
            raise FileNotFoundError(f"mix narration missing: {narration}")
        if bgm and not Path(bgm).exists():
            raise FileNotFoundError(f"mix bgm missing: {bgm}")

        if not has_narration and not bgm:
            # 静音且无 BGM：拼接产物即最终成片。
            shutil.move(str(concat_path), str(output_path))
            _notify(1.0, "混剪成片完成（静音）")
            completed = True
            return output_path

        srt_text = str(subtitle_srt_text or "").strip()
        subs_path: Path | None = None
        if has_narration and srt_text:
            subs_path = work_dir / "subtitles.srt"
            subs_path.write_text(srt_text, encoding="utf-8")

        resolved_bgm_volume = bgm_volume
        if resolved_bgm_volume is None:
            resolved_bgm_volume = (
                _MIX_BGM_VOLUME_WITH_VOICE if has_narration else _MIX_BGM_VOLUME_MUTE
            )
        resolved_bgm_volume = max(0.0, min(2.0, float(resolved_bgm_volume)))
        bgm_fade_start = max(0.0, total_seconds - _MIX_BGM_FADE_SECONDS)

        filter_parts: list[str] = []
        if subs_path is not None:
            style = _mix_subtitle_force_style(
                width=int(canvas_width),
                height=int(canvas_height),
                size=subtitle_font_size,
            )
            filter_parts.append(
                f"[0:v]subtitles=filename='{_escape_filter_path(str(subs_path))}':"
                f"force_style='{style}'[v]"
            )

        cmd = ["ffmpeg", "-y", "-i", str(concat_path)]
        audio_label = ""
        if has_narration:
            cmd += ["-i", narration]
            filter_parts.append(
                "[1:a]volume=1.0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a1]"
            )
            if bgm:
                cmd += ["-stream_loop", "-1", "-t", f"{total_seconds:.3f}", "-i", bgm]
                filter_parts.append(
                    f"[2:a]volume={resolved_bgm_volume:.4f},"
                    f"afade=t=out:st={bgm_fade_start:.3f}:d={_MIX_BGM_FADE_SECONDS:.3f},"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a2]"
                )
                filter_parts.append(
                    "[a1][a2]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
                )
                audio_label = "[aout]"
            else:
                audio_label = "[a1]"
        elif bgm:
            cmd += ["-stream_loop", "-1", "-t", f"{total_seconds:.3f}", "-i", bgm]
            filter_parts.append(
                f"[1:a]volume={resolved_bgm_volume:.4f},"
                f"afade=t=out:st={bgm_fade_start:.3f}:d={_MIX_BGM_FADE_SECONDS:.3f},"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
            )
            audio_label = "[aout]"

        _notify(0.84, "烧录字幕与混音...")
        cmd += ["-filter_complex", ";".join(filter_parts)]
        if subs_path is not None:
            cmd += ["-map", "[v]"]
        else:
            cmd += ["-map", "0:v:0"]
        cmd += ["-map", audio_label]
        cmd += [
            *video_encode_args(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        await _run_cmd(cmd)
        _notify(1.0, "混剪成片完成")
        completed = True
        return output_path
    finally:
        if completed:
            shutil.rmtree(work_dir, ignore_errors=True)


async def _mix_audio_tracks(
    *,
    base_video_path: Path,
    final_output_path: Path,
    audio_items: list[dict[str, Any]],
    temp_dir: Path,
) -> None:
    audio_inputs: list[tuple[Path, float]] = []
    for index, item in enumerate(audio_items):
        if bool(item.get("muted")):
            continue
        volume = float(item.get("volume", 1.0) or 1.0)
        if volume <= 0:
            continue
        source_start = float(item.get("source_start", 0.0) or 0.0)
        source_end = float(item.get("source_end", 0.0) or 0.0)
        duration = source_end - source_start
        if duration <= 0:
            continue
        audio_path = temp_dir / f"audio_track_{index:03d}.m4a"
        await _render_audio_clip(
            source_path=str(item["source_path"]),
            output_path=audio_path,
            source_start=source_start,
            duration=duration,
            volume=volume,
        )
        audio_inputs.append((audio_path, float(item.get("timeline_start", 0.0) or 0.0)))

    if not audio_inputs:
        shutil.move(str(base_video_path), str(final_output_path))
        return

    cmd = ["ffmpeg", "-y", "-i", str(base_video_path)]
    filter_parts: list[str] = []
    labels = ["[0:a]"]
    for idx, (audio_path, timeline_start) in enumerate(audio_inputs, start=1):
        delay_ms = max(0, int(round(timeline_start * 1000.0)))
        cmd.extend(["-i", str(audio_path)])
        filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
        labels.append(f"[a{idx}]")
    filter_parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0[aout]"
    )
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(final_output_path),
        ]
    )
    await _run_cmd(cmd)


async def run_freezone_video_compose(
    *,
    project_dir: Path,
    job_id: str,
    title: str = "",
    canvas_id: str = "",
    resolution: str = "1080p",
    fps: int = 30,
    background_color: str = "#000000",
    keep_original_audio: bool = True,
    tracks: list[dict[str, Any]],
) -> Path:
    """Compose a minimal timeline JSON into a final mp4."""
    del title, canvas_id

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    width, height = FREEZONE_VIDEO_RESOLUTION_MAP.get(
        resolution, FREEZONE_VIDEO_RESOLUTION_MAP["1080p"]
    )
    output_dir = outputs_dir(project_dir, "freezone_video_compose")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_output_path = output_dir / f"{job_id}.mp4"

    video_items = [
        item
        for track in tracks
        if str(track.get("kind") or "") == "video"
        for item in (track.get("items") or [])
    ]
    audio_items = [
        item
        for track in tracks
        if str(track.get("kind") or "") == "audio"
        for item in (track.get("items") or [])
    ]
    if not video_items:
        raise RuntimeError("video compose requires at least one video clip")

    sorted_video_items = sorted(
        video_items,
        key=lambda item: (
            float(item.get("timeline_start", 0.0) or 0.0),
            str(item.get("item_id") or ""),
        ),
    )

    with tempfile.TemporaryDirectory(
        prefix=f"freezone_compose_{job_id}_"
    ) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        segment_paths: list[Path] = []
        cursor = 0.0
        for index, item in enumerate(sorted_video_items):
            timeline_start = float(item.get("timeline_start", 0.0) or 0.0)
            source_start = float(item.get("source_start", 0.0) or 0.0)
            source_end = float(item.get("source_end", 0.0) or 0.0)
            duration = source_end - source_start
            if duration <= 0:
                raise RuntimeError(
                    f"compose item {item.get('item_id') or index} has invalid source range"
                )
            if timeline_start < cursor - 1e-6:
                raise RuntimeError(
                    "overlapping video clips are not supported in MVP compose"
                )
            if timeline_start > cursor + 1e-6:
                gap_path = temp_dir / f"gap_{index:03d}.mp4"
                await _render_gap_clip(
                    output_path=gap_path,
                    duration=timeline_start - cursor,
                    width=width,
                    height=height,
                    fps=fps,
                    background_color=background_color,
                )
                segment_paths.append(gap_path)
                cursor = timeline_start

            clip_path = temp_dir / f"video_{index:03d}.mp4"
            await _render_video_clip(
                source_path=str(item["source_path"]),
                output_path=clip_path,
                source_start=source_start,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                background_color=background_color,
                keep_original_audio=keep_original_audio,
                volume=float(item.get("volume", 1.0) or 1.0),
                muted=bool(item.get("muted")),
            )
            segment_paths.append(clip_path)
            cursor = timeline_start + duration

        concatenated_path = temp_dir / "concatenated.mp4"
        await _concat_media_segments(segment_paths, concatenated_path)
        await _mix_audio_tracks(
            base_video_path=concatenated_path,
            final_output_path=final_output_path,
            audio_items=audio_items,
            temp_dir=temp_dir,
        )

    if not final_output_path.exists():
        raise RuntimeError("video compose finished without output file")
    return final_output_path


async def _probe_video_size(source_path: str) -> tuple[int, int]:
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-500:] or "ffprobe size failed")
    text = (proc.stdout or "").strip()
    try:
        width_text, height_text = text.split("x", 1)
        return int(width_text), int(height_text)
    except Exception as exc:
        raise RuntimeError(f"unable to parse video size: {text}") from exc


async def _probe_video_duration(source_path: str) -> float:
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or "").strip()[-500:] or "ffprobe duration failed"
        )
    try:
        return max(0.1, float((proc.stdout or "").strip()))
    except ValueError as exc:
        raise RuntimeError("unable to parse video duration") from exc


def _expand_mask(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    expanded = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            expanded |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return expanded


def _safe_box_from_pixels(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    width: int,
    height: int,
    *,
    pad_x: int = 12,
    pad_y: int = 10,
) -> tuple[int, int, int, int]:
    left = max(0, x0 - pad_x)
    top = max(0, y0 - pad_y)
    right = min(width, x1 + pad_x)
    bottom = min(height, y1 + pad_y)
    return left, top, max(8, right - left), max(8, bottom - top)


def _fallback_subtitle_box(width: int, height: int) -> tuple[int, int, int, int]:
    box_w = int(width * 0.8)
    box_h = max(24, int(height * 0.16))
    x = int((width - box_w) / 2)
    y = int(height * 0.78)
    y = min(max(0, y), max(0, height - box_h))
    return x, y, box_w, box_h


def _detect_subtitle_box_from_image(
    image_path: Path,
) -> tuple[int, int, int, int] | None:
    image = Image.open(image_path).convert("RGB")
    arr = np.asarray(image, dtype=np.int16)
    height, width = arr.shape[:2]
    start_y = int(height * 0.55)
    roi = arr[start_y:, :, :]
    gray = (
        (roi[:, :, 0] * 299 + roi[:, :, 1] * 587 + roi[:, :, 2] * 114) // 1000
    ).astype(np.int16)
    edge = np.zeros_like(gray)
    edge[:, 1:] += np.abs(gray[:, 1:] - gray[:, :-1])
    edge[1:, :] += np.abs(gray[1:, :] - gray[:-1, :])
    candidate = ((gray >= 205) | (gray <= 50)) & (edge >= 42)
    candidate = _expand_mask(candidate, radius=2)

    ys, xs = np.where(candidate)
    if len(xs) < max(80, width // 120):
        return None
    x0 = int(xs.min())
    x1 = int(xs.max()) + 1
    y0 = int(ys.min()) + start_y
    y1 = int(ys.max()) + 1 + start_y
    if (x1 - x0) < width * 0.12 or (y1 - y0) < 10:
        return None
    if (y1 - y0) > height * 0.22:
        return None
    return _safe_box_from_pixels(x0, y0, x1, y1, width, height)


async def _extract_sample_frames(
    video_path: str, temp_dir: Path, count: int = 6
) -> list[Path]:
    duration = await _probe_video_duration(video_path)
    sample_paths: list[Path] = []
    for index in range(count):
        ts = duration * (index + 1) / (count + 1)
        output_path = temp_dir / f"sample_{index:02d}.png"
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                video_path,
                "-frames:v",
                "1",
                str(output_path),
            ]
        )
        if output_path.exists():
            sample_paths.append(output_path)
    return sample_paths


async def _detect_subtitle_box(
    video_path: str, temp_dir: Path
) -> tuple[int, int, int, int]:
    width, height = await _probe_video_size(video_path)
    sample_paths = await _extract_sample_frames(video_path, temp_dir)
    boxes = [
        box
        for box in (_detect_subtitle_box_from_image(path) for path in sample_paths)
        if box
    ]
    if not boxes:
        return _fallback_subtitle_box(width, height)

    left = int(np.median([box[0] for box in boxes]))
    top = int(np.median([box[1] for box in boxes]))
    right = int(np.median([box[0] + box[2] for box in boxes]))
    bottom = int(np.median([box[1] + box[3] for box in boxes]))
    return _safe_box_from_pixels(left, top, right, bottom, width, height)


def _normalized_box_to_pixels(
    *,
    box_x: float,
    box_y: float,
    box_width: float,
    box_height: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x = int(round(box_x * width))
    y = int(round(box_y * height))
    w = int(round(box_width * width))
    h = int(round(box_height * height))
    x = min(max(0, x), max(0, width - 8))
    y = min(max(0, y), max(0, height - 8))
    w = min(max(8, w), width - x)
    h = min(max(8, h), height - y)
    return x, y, w, h


async def _render_delogo_video(
    *,
    source_path: str,
    output_path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            source_path,
            "-vf",
            f"delogo=x={x}:y={y}:w={w}:h={h}:show=0",
            *video_encode_args(preset="medium", crf="18"),
            "-c:a",
            "copy",
            str(output_path),
        ]
    )


async def run_freezone_video_erase(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    mode: str,
    engine: str = "auto",
    box_x: float | None = None,
    box_y: float | None = None,
    box_width: float | None = None,
    box_height: float | None = None,
    egress_context: TrustedEgressContext | None = None,
) -> tuple[Path, dict[str, Any]]:
    """[aipdd fork delta] 视频擦除，引擎三态（工具页显式选择）：

    - ``engine="local"``：强制本机 ffmpeg ``delogo``（不出网、无视 MediaKit Key）；
    - ``engine="mediakit"``：用户显式选择火山 AI MediaKit 字幕擦除（精细化版，
      高帧率 OCR + AIGC 图像修复，无痕重建背景）；
    - ``engine="auto"``（默认，画布等既有入口）：隐式路由，有 Key 走 MediaKit。

    mediakit / auto 在未配置 Key、组织身份或调用失败时一律回退原本地
    ffmpeg `delogo`（邻域插值，稳定但对复杂背景较弱）。`egress_context` 是受限
    出网治理要求：MediaKit 是直连第三方主机、不可经网关路由，组织身份必须
    fail-closed（不得用平台凭据直连），因此组织任务直接走本地 ffmpeg。个人 /
    平台身份才允许直连 MediaKit。签名与产物路径保持不变。
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"video source not found: {src}")
    if mode not in {"smart_subtitle", "box"}:
        raise RuntimeError(f"unsupported erase mode: {mode}")
    if mode == "box" and None in {box_x, box_y, box_width, box_height}:
        raise RuntimeError(
            "box mode requires box_x, box_y, box_width and box_height"
        )
    engine_key = str(engine or "auto").strip().lower()
    if engine_key not in {"auto", "local", "mediakit"}:
        engine_key = "auto"

    output_dir = outputs_dir(project_dir, "freezone_video_erase")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}.mp4"

    from novelvideo.generators import mediakit

    # 归一化框 -> MediaKit erase_ratio_location（Text 模式，坐标 0~1，右下角=左上+宽高）。
    erase_locations: list[dict[str, float]] | None = None
    if mode == "box":
        top_left_x = min(max(0.0, float(box_x)), 1.0)  # type: ignore[arg-type]
        top_left_y = min(max(0.0, float(box_y)), 1.0)  # type: ignore[arg-type]
        bottom_right_x = min(
            max(0.0, float(box_x) + float(box_width)), 1.0  # type: ignore[arg-type]
        )
        bottom_right_y = min(
            max(0.0, float(box_y) + float(box_height)), 1.0  # type: ignore[arg-type]
        )
        erase_locations = [
            {
                "top_left_x": top_left_x,
                "top_left_y": top_left_y,
                "bottom_right_x": bottom_right_x,
                "bottom_right_y": bottom_right_y,
            }
        ]

    # 组织身份不下直连第三方主机的赌注：走本地 ffmpeg。
    is_organization = egress_context is not None and egress_context.is_organization
    # [aipdd fork delta] 引擎三态：local 强制本机（无视 Key）；mediakit / auto
    # 有 Key 且非组织身份优先云端 MediaKit（见 mediakit.mediakit_configured()），
    # 未配置 / 失败回退本地 ffmpeg。
    if engine_key == "local":
        use_mediakit = False
    else:
        use_mediakit = mediakit.mediakit_configured() and not is_organization

    if use_mediakit:
        try:
            meta = await mediakit.erase_video_file(
                source_path=src,
                output_path=output_path,
                mode="Text" if mode == "box" else "Subtitle",
                erase_ratio_location=erase_locations,
                on_log=lambda msg: logger.info("[video-erase] %s", msg),
            )
            meta["requested_engine"] = engine_key
            return output_path, meta
        except Exception as exc:  # noqa: BLE001 - 有 Key 但 MediaKit 失败时回退本地 ffmpeg
            logger.warning(
                "[video-erase] MediaKit 擦除失败，回退 ffmpeg: %s", exc
            )

    # ---- 兜底：原本地 ffmpeg delogo（不出网、不取凭证）----
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    width, height = await _probe_video_size(source_path)
    with tempfile.TemporaryDirectory(
        prefix=f"freezone_erase_{job_id}_"
    ) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        if mode == "smart_subtitle":
            x, y, w, h = await _detect_subtitle_box(source_path, temp_dir)
        else:  # box
            x, y, w, h = _normalized_box_to_pixels(
                box_x=float(box_x),  # type: ignore[arg-type]
                box_y=float(box_y),  # type: ignore[arg-type]
                box_width=float(box_width),  # type: ignore[arg-type]
                box_height=float(box_height),  # type: ignore[arg-type]
                width=width,
                height=height,
            )
        await _render_delogo_video(
            source_path=source_path,
            output_path=output_path,
            x=x,
            y=y,
            w=w,
            h=h,
        )
    if not output_path.exists():
        raise RuntimeError("video erase finished without output file")
    return output_path, {
        "backend": "ffmpeg",
        "requested_engine": engine_key,
        "mode": mode,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
    }


# ---------------------------------------------------------------------------
# [aipdd fork delta] 视频配音：把配音音轨回填进原视频（替换整条音轨）。
#   纯本地 ffmpeg，不出网、不取凭证。命令构造与执行分离，命令形状可零 IO 单测。
# ---------------------------------------------------------------------------
def _replace_audio_cmd(
    source_video_path: str | Path,
    dub_audio_path: str | Path,
    output_path: str | Path,
) -> list[str]:
    """构造「以 dub 音频替换视频音轨」的 ffmpeg 命令（视频流 copy、成片取较短）。"""
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video_path),
        "-i",
        str(dub_audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


async def replace_video_audio_track(
    *,
    source_video_path: str | Path,
    dub_audio_path: str | Path,
    output_path: Path,
) -> Path:
    """用配音音轨替换原视频音轨并落 ``output_path``（纯本地 ffmpeg）。"""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_cmd(_replace_audio_cmd(source_video_path, dub_audio_path, output_path))
    if not output_path.exists():
        raise RuntimeError("视频配音音轨回填失败：ffmpeg 未产出成片")
    return output_path


async def run_freezone_audio_separate(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
) -> dict[str, Path | None]:
    """Split a video into extracted audio and muted video using ffmpeg only."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    output_dir = outputs_dir(project_dir, "freezone_audio_separate")
    output_dir.mkdir(parents=True, exist_ok=True)
    # MP3 而非 AAC/.m4a：分离出的音频常被接到视频模型当参考音频，Seedance 不收 m4a。
    audio_path = output_dir / f"{job_id}.mp3"
    mute_video_path = output_dir / f"{job_id}_mute.mp4"

    has_audio = await _probe_has_audio(source_path)
    if has_audio:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(audio_path),
            ]
        )

    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            source_path,
            "-c:v",
            "copy",
            "-an",
            str(mute_video_path),
        ]
    )

    if not mute_video_path.exists():
        raise RuntimeError("audio separate finished without muted video output")
    return {
        "audio_path": audio_path if audio_path.exists() else None,
        "mute_video_path": mute_video_path,
    }


async def run_freezone_video_gen(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    reference_items: Optional[list[dict[str, str]]] = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 5,
    generate_audio: bool = False,
    human_review: bool = False,
    scene_optimize: str | None = None,
    backend: str = "huimeng_seedance-2.0-fast",
    last_frame_path: Optional[str] = None,
    audio_setting: Optional[str] = None,
    gen_mode: Optional[str] = None,
    model_params: Optional[dict[str, Any]] = None,
    request_schema: Optional[dict[str, Any]] = None,
) -> Path:
    """Freezone 文生视频。

    统一承接 Freezone 视频生成，支持：
    - 纯 prompt 文生视频
    - prompt + 角色参考图
    - 首帧 / 尾帧参考
    - 原生音频开关（由具体模型决定）
    """
    out = outputs_dir(project_dir, "freezone_video_gen") / f"{job_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    from novelvideo.generators.video_generator import (
        ShotReference,
        create_video_generator,
        parse_newapi_video_backend,
    )

    references = [
        ShotReference(
            str(item.get("type") or "image"),
            str(item.get("path") or ""),
            str(item.get("role") or ""),
        )
        for item in (reference_items or [])
        if str(item.get("path") or "").strip()
    ]
    from novelvideo.freezone.video_node import is_freezone_seedance2_backend

    video_gen = create_video_generator(
        backend=backend,
        resolution=resolution,
        generate_audio=generate_audio,
        model_params=model_params,
        request_schema=request_schema,
    )
    if backend == "seedance_2":
        result = await video_gen.generate(
            prompt=prompt,
            output_path=str(out),
            references=references,
            duration=float(duration_seconds),
            audio=bool(generate_audio),
            human_review=bool(human_review),
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
    else:
        normalized_mode = {
            "firstLastFrame": "first_last_frame",
            "imageToVideo": "image_reference",
            "imageReference": "image_reference",
        }.get(str(gen_mode or "").strip(), str(gen_mode or "").strip())
        if normalized_mode in {"first_frame", "first_last_frame"}:
            first_image_ref = next(
                (
                    ref
                    for ref in references
                    if ref.type == "image" and "首帧" in str(ref.role or "")
                ),
                None,
            )
        else:
            first_image_ref = next((ref for ref in references if ref.type == "image"), None)
        if (
            (first_image_ref is None or not first_image_ref.path)
            and not str(backend).startswith("huimeng_")
            and not parse_newapi_video_backend(backend)
            and not is_freezone_seedance2_backend(backend)
        ):
            raise RuntimeError(
                f"backend {backend} requires a first-frame image reference"
            )
        extra_kwargs: dict[str, object] = {}
        if audio_setting:
            extra_kwargs["audio_setting"] = audio_setting
        result = await video_gen.generate(
            image_path=(
                first_image_ref.path
                if first_image_ref and first_image_ref.path
                else None
            ),
            prompt=prompt,
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            duration=float(duration_seconds),
            last_frame_path=last_frame_path,
            references=references,
            human_review=bool(human_review),
            seedance2_config=(
                {"scene_optimize": scene_optimize} if scene_optimize else None
            ),
            gen_mode=gen_mode,
            **extra_kwargs,
        )
    if not result or result.status.value != "done":
        err = result.error if result else "unknown error"
        raise RuntimeError(f"freezone video generation failed: {err}")
    if not out.exists():
        raise RuntimeError(
            "video generation returned success but no output file was written"
        )
    return out


# ============================================================
# Extract frames (M1a) — 视频拉片
# ============================================================


def sort_extracted_frames_by_pts(paths: Iterable[Path]) -> list[Path]:
    """按文件名尾部的数值排序抽帧结果，而不是按字典序。

    ``-frame_pts true`` 让 ffmpeg 把 ``%03d`` 填成该帧的 PTS 而不是递增计数，
    于是文件名位数不固定：字典序会把 ``scene_1000.png`` 排到 ``scene_900.png``
    前面，送进模型的关键帧顺序和 ``keyframe_index`` 映射就全乱了。
    """

    def sort_key(path: Path) -> tuple[int, int, str]:
        match = re.search(r"(\d+)$", path.stem)
        if match is None:
            # 没有数字后缀的（理论上不该出现）排到最后，按名字兜底。
            return (1, 0, path.name)
        return (0, int(match.group(1)), path.name)

    return sorted(paths, key=sort_key)


async def run_freezone_extract_frames(
    *,
    project_dir: Path,
    job_id: str,
    video_path: Path,
    max_frames: int = 20,
    scene_threshold: float = 0.3,
) -> list[Path]:
    """ffmpeg pixel-diff scene detection → up to `max_frames` keyframes.

    Uses ffmpeg's built-in `scene` filter (returns 0-1 confidence per frame
    transition); we pick frames where confidence > threshold. If the video
    has fewer scene cuts than `max_frames` we fall back to evenly-spaced
    sampling to guarantee at least a few frames.

    Returns absolute paths to the saved frame PNGs.
    """
    import asyncio
    import shutil
    import subprocess

    out_dir = outputs_dir(project_dir, "freezone_extract") / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    # Pass 1: scene detection extraction.
    pattern = str(out_dir / "scene_%03d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"select='gt(scene,{scene_threshold})'",
        "-vsync",
        "vfr",
        "-frames:v",
        str(max_frames),
        "-frame_pts",
        "true",
        pattern,
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg scene detect failed: {proc.stderr[-500:]}")

    scene_files = sort_extracted_frames_by_pts(out_dir.glob("scene_*.png"))

    # Fallback: if too few scene cuts (e.g. talking head static video),
    # sample evenly-spaced frames so the user always gets *something*.
    if len(scene_files) < 3:
        for f in scene_files:
            f.unlink(missing_ok=True)
        scene_files = await _sample_evenly(video_path, out_dir, max_frames)

    return scene_files


# ============================================================
# Analyze shots (M1b) — Gemini Vision 拆分镜
# ============================================================


SHOT_ANALYSIS_PROMPT = """你是一个专业的电影分镜师。下面给你一组视频关键帧（按时间顺序），请逐帧分析每帧的电影语言。

对每帧输出一个 JSON 对象，字段：
- shot_type: 景别（"特写" | "近景" | "中景" | "全景" | "远景" | "大远景"）
- angle: 镜头角度（"平视" | "俯拍" | "仰拍" | "鸟瞰" | "倾斜" 等）
- camera_movement: 推测的运镜（"静止" | "推镜" | "拉镜" | "摇镜" | "移镜" | "升降" | "跟镜" 等，没有上下文则填"静止"）
- subject_action: 主体动作的简短描述（中文，<= 20 字，没有主体则"环境镜头"）
- mood: 氛围（"温馨" | "紧张" | "压抑" | "明快" | "孤独" 等）
- color_tone: 色调（"暖色调" | "冷色调" | "高饱和" | "低饱和" | "黑白" 等）
- suggested_prompt: 一句中文文生图 prompt，用于让 AI 重现这帧的视觉风格（包含上面所有元素，<= 80 字）

输出格式严格为 JSON 数组（不要任何解释 / markdown 包裹），第 i 个元素对应第 i 帧。例如：
[
  {"shot_type": "近景", "angle": "平视", "camera_movement": "静止", "subject_action": "环境镜头", "mood": "明快", "color_tone": "高饱和", "suggested_prompt": "..."},
  ...
]
"""


def build_video_story_analysis_prompt(
    *, frame_count: int, duration_sec: Optional[float] = None
) -> str:
    duration_hint = (
        f"视频总时长约 {duration_sec:.2f} 秒。"
        f"请把 start_time/end_time 分配在 0 到 {duration_sec:.2f} 秒之间。"
        if duration_sec and duration_sec > 0
        else (
            "未知视频总时长。请根据关键帧顺序给出相对合理的 "
            "start_time/end_time，第一镜从 0 开始。"
        )
    )
    return f"""你是专业影视导演和分镜解析师。下面给你 {frame_count} 张
按时间顺序抽取的视频关键帧，请解析成 libtv 风格的“视频故事”表。

{duration_hint}

要求：
- 不要逐帧机械描述，要把连续关键帧归纳成 3-12 个叙事镜头/动作段落。
- 保持同一视频内部的故事连续性：谁在做什么，发生了什么变化，
  镜头如何推进。
- 时间字段使用数字秒，duration = end_time - start_time。
- 画面描述写清主体、动作、环境、构图、情绪、重要道具。
- 叙事内容写这一镜在故事中的作用，而不是重复画面描述。
- 图生视频提示词和视频运动提示词用英文，适合直接用于视频生成。
- 背景音乐、人声/音效用中文，简洁描述。
- 关键帧使用输入帧序号，1 到 {frame_count}。
- 如果看不出声音，不要编对白，只写可由画面推断的音效/氛围。
- 严格输出 JSON 对象，不要 markdown，不要解释。

JSON schema:
{{
  "title": "中文短标题",
  "summary": "中文一句话概括视频故事",
  "duration": 数字秒或 null,
  "shots": [
    {{
      "shot": 1,
      "start_time": 0.0,
      "end_time": 1.2,
      "duration": 1.2,
      "visual_description": "中文画面描述",
      "narrative": "中文叙事内容",
      "shot_size": "特写/近景/中近景/中景/全景/远景/大远景",
      "camera_angle": "平视/俯拍/仰拍/倾斜/高角度/低角度",
      "camera_movement": "固定/推镜/拉镜/摇镜/移镜/跟镜/手持/缓慢推进",
      "focus_depth": "浅景深/中等景深/深景深",
      "lighting": "中文光线描述",
      "background_music": "中文背景音乐建议",
      "voice_sound": "中文人声或音效",
      "image_prompt": "English image-to-video visual prompt",
      "motion_prompt": "English motion prompt",
      "keyframes": [1, 2]
    }}
  ]
}}
"""


async def run_freezone_analyze_shots(
    *,
    project_dir: Path,
    job_id: str,
    frame_paths: list[str],
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    analysis_mode: str = "shots",
    duration_sec: Optional[float] = None,
    egress_context: TrustedEgressContext | None = None,
) -> dict:
    """Send N frames to a Vision model and parse a structured JSON response.

    Product requests always use the effective LocalAPI gateway. ``provider`` is
    retained only for payload compatibility with older saved canvases.
    """
    import json

    from novelvideo.freezone import vision_gateway
    from novelvideo.freezone.vision_gateway import (
        FREEZONE_VIDEO_ANALYSIS_TIMEOUT_SECONDS,
        load_compact_vision_inputs,
        resolve_freezone_vision_model,
    )

    # 与 `image_node.reverse_prompt_from_image` 同一口径：出网 helper 在函数体内
    # 引进来，不放模块级——leaf 模块的导入面本身是分类契约的一部分。
    from novelvideo.freezone.presets import (
        abandon_freezone_vision_egress,
        complete_freezone_vision_egress,
        prepare_freezone_vision_egress,
    )

    if not frame_paths:
        raise ValueError("no frames to analyze")

    out_dir = outputs_dir(project_dir, "freezone_analyze") / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    del provider
    mode = (analysis_mode or "shots").strip().lower()
    if mode not in {"shots", "video_story"}:
        raise ValueError(f"unsupported analysis_mode: {analysis_mode}")
    prompt = (
        build_video_story_analysis_prompt(
            frame_count=len(frame_paths),
            duration_sec=duration_sec,
        )
        if mode == "video_story"
        else SHOT_ANALYSIS_PROMPT
    )

    del api_key
    frame_inputs = await load_compact_vision_inputs(
        [path for path in frame_paths if Path(path).exists()]
    )
    if not frame_inputs:
        raise ValueError("no readable frames to analyze")
    vision_egress = await prepare_freezone_vision_egress(
        egress_context=egress_context,
        model_name=resolve_freezone_vision_model(model),
        prompt=prompt,
        images=[image.data for image in frame_inputs],
        timeout_seconds=FREEZONE_VIDEO_ANALYSIS_TIMEOUT_SECONDS,
    )
    # [aipdd fork delta] 视频理解时序语义：video_story 把抽帧按「时间戳+图像」交错
    # 提交（方舟「多图请求等效」）。有 duration_sec 就按帧均匀切分真实时间轴；
    # 缺时长则退化为逐帧 +1s 递增，保证模型仍拿到单调时序标记。shots 模式不强制。
    frame_seconds: Optional[list[float]] = None
    if mode == "video_story":
        frame_count = len(frame_inputs)
        try:
            total_sec = float(duration_sec) if duration_sec is not None else 0.0
        except (TypeError, ValueError):
            total_sec = 0.0
        if total_sec > 0 and frame_count > 0:
            step = total_sec / frame_count
            frame_seconds = [index * step for index in range(frame_count)]
        else:
            frame_seconds = [float(index) for index in range(frame_count)]
    try:
        vision_model, text = await vision_gateway.call_freezone_vision_model(
            prompt=prompt,
            images=frame_inputs,
            model_override=model,
            timeout_seconds=FREEZONE_VIDEO_ANALYSIS_TIMEOUT_SECONDS,
            transport_context=(
                vision_egress.transport_context if vision_egress else None
            ),
            frame_seconds=frame_seconds,
        )
        used_provider = "newapi"

        if not text:
            raise RuntimeError(f"{used_provider} Vision returned no text")

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(
                line
                for line in cleaned.splitlines()
                if not line.strip().startswith("```")
            ).strip()
        try:
            analyses = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            (out_dir / "raw_response.txt").write_text(text, encoding="utf-8")
            raise RuntimeError(
                f"{used_provider} returned non-JSON: {exc}; raw saved"
            ) from exc

        if mode == "video_story":
            if not isinstance(analyses, dict):
                raise RuntimeError(f"{used_provider} response is not an object")
        elif not isinstance(analyses, list):
            raise RuntimeError(f"{used_provider} response is not a list")
    except BaseException:
        # 出网点一抛就到不了 complete_*，claim 会停在 dispatching 等收割器。
        # 这里恒 submitted=True：帧字节在 claim 之前就读完了（digest 要用），claim
        # 与提交之间没有会抛的步骤，所以任何失败都发生在「已经交出去、结果不明」之后。
        await abandon_freezone_vision_egress(vision_egress, submitted=True)
        raise
    await complete_freezone_vision_egress(vision_egress, result=text)

    payload = {
        "provider": used_provider,
        "model": vision_model,
        "analysis_mode": mode,
        "frame_count": len(frame_paths),
    }
    if mode == "video_story":
        payload["video_story"] = analyses
        payload["analyses"] = analyses.get("shots", [])
    else:
        payload["analyses"] = analyses
    out_file = out_dir / "analysis.json"
    out_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    payload["output_path"] = str(out_file)
    return payload


async def _sample_evenly(
    video_path: Path, out_dir: Path, max_frames: int
) -> list[Path]:
    """Fallback when scene detection finds nothing — sample at regular intervals."""
    import asyncio
    import json
    import subprocess

    probe = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration,nb_frames",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    duration = 60.0
    if probe.returncode == 0:
        try:
            payload = json.loads(probe.stdout)
            duration = float(payload["streams"][0].get("duration") or 60.0)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            pass

    n = min(max(3, max_frames // 2), max_frames)
    fps_expr = f"1/{max(1.0, duration / n)}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps_expr}",
        "-frames:v",
        str(n),
        str(out_dir / "even_%03d.png"),
    ]
    await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=300
    )
    return sort_extracted_frames_by_pts(out_dir.glob("even_*.png"))


# ============================================================
# 视频复刻台词转写 (M1c) — 参考视频人声 → 台词文本
# ============================================================


def _pick_clone_asr_candidate(
    asr_model: str, *, is_organization: bool
) -> dict[str, str] | None:
    """复刻台词转写用的 ASR 候选：直接委派统一口径。

    本地音频的 ASR 候选选择只有一个真源：
    ``douyin_copywrite.pick_asr_candidate_for_local_audio``（FunASR 永不参与 / 组织任务不用
    直连通道 / 自动顺序豆包→网关）。此处只保留历史名字供复刻叶调用。

    返回 None 表示「当前没有能用的 ASR 通道」，由调用方决定跳过，不抛错。
    """

    from novelvideo.freezone.douyin_copywrite import pick_asr_candidate_for_local_audio

    return pick_asr_candidate_for_local_audio(asr_model, is_organization=is_organization)


async def transcribe_freezone_clone_dialogue(
    *,
    project_dir: Path,
    job_id: str,
    video_path: str | Path,
    asr_model: str = "",
    egress_context: TrustedEgressContext | None = None,
) -> str:
    """把参考视频的人声转写成台词文本，交给逐镜复刻规划逐字分配。

    ``asr_model`` 是候选 id（与音视频文案下拉同一口径）；复刻 runner 不传，走
    「豆包语音 → 网关 ASR」的自动选择。

    best-effort：抽不出音轨、没有可用 ASR 通道、转写失败都返回空串而不是抛错。
    台词是加分项——拿不到时复刻照跑，提示词里仍带解读阶段从画面推断的
    ``voice_sound`` 声音设计；音画同步模型（实测）至少不会连声音设计都没有。

    出网构成（与 EG-15c 同族）：本地 ffmpeg 抽音轨不出网；转写经网关 ASR 的受信
    multipart 上传，或豆包语音直连（组织任务在候选选择阶段就排除直连）。
    与 ``voice_replace`` / ``media_copywrite`` 同为「单叶内部多跳出网」：受信
    ``egress_context`` 由分发器注入本叶，本叶再透传给 ASR。
    """

    from novelvideo.freezone.media_copywrite import _transcribe

    is_organization = egress_context is not None and bool(egress_context.is_organization)
    try:
        separated = await run_freezone_audio_separate(
            project_dir=project_dir,
            job_id=f"{job_id}_asr",
            source_path=str(video_path),
        )
    except Exception:  # noqa: BLE001 - 音轨拿不到就不带台词
        logger.warning("clone dialogue: 参考视频抽音轨失败，跳过台词转写", exc_info=True)
        return ""
    audio_path = separated.get("audio_path")
    if not audio_path:
        # 参考视频本来就没有音轨，属于正常情况。
        logger.info("clone dialogue: 参考视频没有可用音轨，跳过台词转写")
        return ""

    candidate = _pick_clone_asr_candidate(asr_model, is_organization=is_organization)
    if candidate is None:
        logger.info(
            "clone dialogue: 没有可用的 ASR 通道（组织任务请在设置页配置网关 ASR 模型），"
            "跳过台词转写"
        )
        return ""
    try:
        text, _source_label = await _transcribe(
            candidate,
            audio_path=Path(audio_path),
            audio_url="",
            job_id=job_id,
            egress_context=egress_context,
        )
    except Exception:  # noqa: BLE001 - 转写失败不阻断复刻
        logger.warning(
            "clone dialogue: ASR 转写失败（%s），跳过台词转写",
            candidate.get("display") or candidate.get("kind"),
            exc_info=True,
        )
        return ""
    text = str(text or "").strip()
    if not text:
        logger.info("clone dialogue: ASR 未识别到人声内容，跳过台词转写")
    return text


_SIZE_BASE = {
    "0.5K": 512,
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


def _aspect_to_dims(aspect_ratio: str, image_size: str) -> tuple[int, int]:
    base = _SIZE_BASE.get(image_size.upper(), 1024)
    try:
        w_part, h_part = aspect_ratio.split(":", 1)
        w_ratio = float(w_part)
        h_ratio = float(h_part)
    except (ValueError, AttributeError):
        return base, base
    if w_ratio <= 0 or h_ratio <= 0:
        return base, base
    if w_ratio >= h_ratio:
        return base, max(64, round(base * h_ratio / w_ratio))
    return max(64, round(base * w_ratio / h_ratio)), base
