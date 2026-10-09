"""顶层出图入口：generate_text_to_image / generate_reference_edit_image（B3 拆包自单文件 grid_generation.py）。"""

import os
from pathlib import Path
from typing import Optional

from novelvideo.egress_context import TrustedEgressContext

from .regen_modes import get_sketch_nxn_modes
from .dispatch import _generate_image


# (capacity, mode_key, rows, cols) — 按 capacity 升序
SKETCH_NXN_MODES = get_sketch_nxn_modes()


async def generate_text_to_image(
    prompt: str,
    output_path: str,
    *,
    aspect_ratio: str = "1:1",
    image_size: str = "2K",
    quality: str | None = None,
    api_key: Optional[str] = None,
    config: Optional[dict] = None,
    egress_context: TrustedEgressContext | None = None,
    egress_capability: str = "image.generate",
) -> Path:
    """Generate one image from a prompt only — no reference images.

    Routes through the same 4 providers as `generate_reference_edit_image`
    (google / openrouter / huimeng / openai). Use `config` to override the
    provider/model picked from env defaults.
    """
    return await _generate_image(
        prompt=prompt,
        reference_image_paths=[],
        output_path=output_path,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        api_key=api_key,
        config=config,
        egress_context=egress_context,
        egress_capability=egress_capability,
    )


async def generate_reference_edit_image(
    prompt: str,
    reference_images: list[str],
    output_path: str,
    *,
    aspect_ratio: str = "2:3",
    image_size: str = "2K",
    quality: str | None = None,
    api_key: Optional[str] = None,
    config: Optional[dict] = None,
    egress_context: TrustedEgressContext | None = None,
    egress_capability: str = "image.edit",
) -> Path:
    """Generate one edited image from reference images plus a free-form edit prompt.

    `config` lets callers override the provider / model selection (passed through
    to `GridGenerator`); when None, falls back to env-var defaults via
    `get_grid_generation_config()`.
    """
    ref_paths = [path for path in reference_images if path and os.path.exists(path)]
    if not ref_paths:
        raise FileNotFoundError("No valid reference images provided for edit generation")
    return await _generate_image(
        prompt=prompt,
        reference_image_paths=ref_paths,
        output_path=output_path,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        api_key=api_key,
        config=config,
        egress_context=egress_context,
        egress_capability=egress_capability,
    )
