"""Shared NewAPI transport for Freezone vision-understanding tasks."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from novelvideo.official_defaults import DEFAULT_FREEZONE_VISION_MODEL

FREEZONE_VIDEO_ANALYSIS_TIMEOUT_SECONDS = 300.0
FREEZONE_IMAGE_REVERSE_PROMPT_TIMEOUT_SECONDS = 180.0
FREEZONE_MARK_TIMEOUT_SECONDS = 90.0
FREEZONE_VISION_IMAGE_MAX_EDGE = 1280
FREEZONE_VISION_IMAGE_JPEG_QUALITY = 92
FREEZONE_VISION_IMAGE_COMPACT_CONCURRENCY = 1
FREEZONE_VISION_IMAGE_MAX_SOURCE_BYTES = 64 * 1024 * 1024
FREEZONE_VISION_IMAGE_MAX_SOURCE_PIXELS = 40_000_000

logger = logging.getLogger(__name__)
_VISION_IMAGE_COMPACTION_SLOTS = threading.BoundedSemaphore(
    FREEZONE_VISION_IMAGE_COMPACT_CONCURRENCY
)


@dataclass(frozen=True)
class VisionInput:
    data: bytes
    media_type: str = "image/png"


def _compact_vision_images(
    paths: Sequence[str | Path],
    *,
    max_edge: int,
    jpeg_quality: int,
) -> list[VisionInput]:
    """Load image paths sequentially and return compact JPEG model inputs."""
    from PIL import Image, ImageOps

    if max_edge <= 0:
        raise ValueError("max_edge must be positive")
    if not 1 <= jpeg_quality <= 95:
        raise ValueError("jpeg_quality must be between 1 and 95")

    started_at = time.perf_counter()
    inputs: list[VisionInput] = []
    original_bytes = 0
    compact_bytes = 0
    resized_count = 0

    for raw_path in paths:
        path = Path(raw_path)
        source_bytes = path.stat().st_size
        if source_bytes > FREEZONE_VISION_IMAGE_MAX_SOURCE_BYTES:
            raise ValueError(
                f"vision image exceeds source byte limit: {source_bytes} > "
                f"{FREEZONE_VISION_IMAGE_MAX_SOURCE_BYTES}"
            )
        original_bytes += source_bytes
        with ExitStack() as stack:
            source = stack.enter_context(Image.open(path))

            # Image.open only reads the header. Validate the dimensions before
            # exif_transpose, convert, resize, or any other operation that can
            # allocate the full decoded bitmap. Highly compressible PNGs can be
            # tiny on disk while expanding to hundreds of MiB in memory.
            source_width, source_height = source.size
            if source_width <= 0 or source_height <= 0:
                raise ValueError("vision image dimensions must be positive")
            source_pixels = source_width * source_height
            if source_pixels > FREEZONE_VISION_IMAGE_MAX_SOURCE_PIXELS:
                raise ValueError(
                    f"vision image exceeds pixel limit: {source_pixels} > "
                    f"{FREEZONE_VISION_IMAGE_MAX_SOURCE_PIXELS}"
                )

            image = ImageOps.exif_transpose(source)
            if image is not source:
                stack.callback(image.close)

            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                stack.callback(rgba.close)
                rgb = Image.new("RGB", rgba.size, (255, 255, 255))
                stack.callback(rgb.close)
                alpha = rgba.getchannel("A")
                stack.callback(alpha.close)
                rgb.paste(rgba, mask=alpha)
                image = rgb
            elif image.mode != "RGB":
                rgb = image.convert("RGB")
                stack.callback(rgb.close)
                image = rgb

            width, height = image.size
            longest = max(width, height)
            if longest > max_edge:
                scale = max_edge / longest
                resized = image.resize(
                    (
                        max(1, int(round(width * scale))),
                        max(1, int(round(height * scale))),
                    ),
                    Image.Resampling.LANCZOS,
                )
                stack.callback(resized.close)
                image = resized
                resized_count += 1

            with io.BytesIO() as buffer:
                image.save(
                    buffer,
                    format="JPEG",
                    quality=jpeg_quality,
                    optimize=True,
                )
                data = buffer.getvalue()
            compact_bytes += len(data)
            inputs.append(VisionInput(data=data, media_type="image/jpeg"))

    logger.info(
        "Freezone vision images compacted: count=%d resized=%d "
        "original_bytes=%d compact_bytes=%d elapsed_ms=%.1f max_edge=%d quality=%d",
        len(inputs),
        resized_count,
        original_bytes,
        compact_bytes,
        (time.perf_counter() - started_at) * 1000,
        max_edge,
        jpeg_quality,
    )
    return inputs


def _compact_vision_images_with_slot(
    paths: Sequence[str | Path],
    *,
    max_edge: int,
    jpeg_quality: int,
) -> list[VisionInput]:
    """Bound per-process image decoding so concurrent tasks cannot multiply RSS."""
    with _VISION_IMAGE_COMPACTION_SLOTS:
        return _compact_vision_images(
            paths,
            max_edge=max_edge,
            jpeg_quality=jpeg_quality,
        )


async def load_compact_vision_inputs(
    paths: Sequence[str | Path],
    *,
    max_edge: int = FREEZONE_VISION_IMAGE_MAX_EDGE,
    jpeg_quality: int = FREEZONE_VISION_IMAGE_JPEG_QUALITY,
) -> list[VisionInput]:
    """Compact model-bound images without blocking the async task loop."""
    return await asyncio.to_thread(
        _compact_vision_images_with_slot,
        paths,
        max_edge=max_edge,
        jpeg_quality=jpeg_quality,
    )


@dataclass(frozen=True, slots=True)
class VisionTransportContext:
    """A request-scoped PydanticAI model and its logical model name."""

    model_name: str
    model: Any = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.model_name) is not str or not self.model_name.strip():
            raise ValueError("model_name is required")
        if self.model is None:
            raise ValueError("model is required")


def image_media_type(path: str) -> str:
    """Return the image MIME type expected by multimodal model providers."""
    suffix = str(path).lower().rsplit(".", 1)[-1] if "." in str(path) else ""
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    if suffix == "gif":
        return "image/gif"
    return "image/png"


def resolve_freezone_vision_model(model_override: str | None = None) -> str:
    """Return the logical NewAPI model shared by Freezone vision tasks.

    [aipdd fork delta] 视觉/多模态理解绝不能被只支持文本的 ``TEXT_MODEL_FALLBACK``
    接管（否则纯文本模型被塞进图片请求，网关回 400「不支持图片能力」）。这里显式
    把全局兜底隔离到 ``VISION_MODEL_FALLBACK``，让运维把理解类任务指到网关真正
    开通的视觉模型；官方部署两者都不设 → 行为不变。
    """
    clean_override = str(model_override or "").strip()
    if clean_override:
        return clean_override

    from novelvideo.config import get_newapi_text_model_name

    return get_newapi_text_model_name(
        "FREEZONE_VISION_MODEL",
        DEFAULT_FREEZONE_VISION_MODEL,
        fallback_env="VISION_MODEL_FALLBACK",
    )


async def call_freezone_vision_model(
    *,
    prompt: str,
    images: list[VisionInput],
    timeout_seconds: float,
    model_override: str | None = None,
    transport_context: VisionTransportContext | None = None,
    frame_seconds: Sequence[float] | None = None,
) -> tuple[str, str]:
    """Run a PydanticAI vision Agent through the effective NewAPI gateway.

    [aipdd fork delta] ``frame_seconds`` 为视频理解时序语义入口：传入与 ``images``
    等长的秒数列表时，按火山方舟「多图请求等效」格式把每一帧前面插入
    ``"{x:.1f} second"`` 时间戳文本（prompt → 时间戳 → 帧 → 时间戳 → 帧 …），
    PydanticAI OpenAI 模型按序映射到 ``content`` 数组，等效于视频输入。不传则
    维持旧「prompt + 无序多图」形态。
    """
    if not images:
        raise ValueError("at least one image is required")

    from pydantic_ai import Agent, BinaryContent

    if (
        transport_context is not None
        and type(transport_context) is not VisionTransportContext
    ):
        raise TypeError("transport_context must be a VisionTransportContext")

    if transport_context is None:
        from novelvideo.config import get_newapi_text_pydantic_model

        model = resolve_freezone_vision_model(model_override)
        transport_model = get_newapi_text_pydantic_model(
            "FREEZONE_VISION_MODEL",
            DEFAULT_FREEZONE_VISION_MODEL,
            model_name_override=model,
            timeout_seconds_override=timeout_seconds,
        )
    else:
        clean_override = str(model_override or "").strip()
        if clean_override and clean_override != transport_context.model_name:
            raise ValueError("model_override must match the explicit transport model")
        model = transport_context.model_name
        transport_model = transport_context.model
    agent = Agent(
        transport_model,
        output_type=str,
        name="Freezone Vision Analyzer",
    )
    user_content: list[Any] = [prompt]
    if frame_seconds is not None:
        seconds = list(frame_seconds)
        if len(seconds) != len(images):
            raise ValueError("frame_seconds must match the number of images")
        for second, image in zip(seconds, images):
            user_content.append(f"{float(second):.1f} second")
            user_content.append(BinaryContent(data=image.data, media_type=image.media_type))
    else:
        user_content.extend(
            BinaryContent(data=image.data, media_type=image.media_type)
            for image in images
        )
    result = await agent.run(user_content)
    text = str(result.output or "").strip()
    if not text:
        raise RuntimeError("视觉模型返回空内容")
    return model, text
