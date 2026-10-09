"""Generate voxel-world reference views from an equirectangular panorama."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class PanoViewSpec:
    key: str
    filename: str
    yaw_deg: float
    pitch_deg: float
    fov_deg: float = 90.0


DEFAULT_PANO_VIEW_SPECS: tuple[PanoViewSpec, ...] = (
    PanoViewSpec("front", "pano_front.jpg", 0.0, 0.0),
    PanoViewSpec("back", "pano_back.jpg", 180.0, 0.0),
    PanoViewSpec("left", "pano_left.jpg", -90.0, 0.0),
    PanoViewSpec("right", "pano_right.jpg", 90.0, 0.0),
    # These are not true architectural top/down plans; they are tilted pano
    # crops from the same 360 center. They provide ceiling and floor/layout hints.
    PanoViewSpec("top_hint", "pano_top_hint.jpg", 0.0, -62.0, 105.0),
    PanoViewSpec("down_hint", "pano_down_hint.jpg", 0.0, 62.0, 105.0),
)


def _sample_bilinear(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    u = np.mod(u, w)
    v = np.clip(v, 0, h - 1)

    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    u1 = (u0 + 1) % w
    v1 = np.clip(v0 + 1, 0, h - 1)

    du = (u - u0)[..., None]
    dv = (v - v0)[..., None]

    top = image[v0, u0] * (1.0 - du) + image[v0, u1] * du
    bottom = image[v1, u0] * (1.0 - du) + image[v1, u1] * du
    return top * (1.0 - dv) + bottom * dv


def equirectangular_to_perspective(
    pano: Image.Image,
    *,
    yaw_deg: float,
    pitch_deg: float,
    fov_deg: float,
    width: int = 1024,
    height: int = 768,
) -> Image.Image:
    """Project a 2:1 panorama into a rectilinear perspective crop."""
    src = np.asarray(pano.convert("RGB"), dtype=np.float32)
    src_h, src_w = src.shape[:2]

    aspect = width / height
    fov_y = math.radians(float(fov_deg))
    fov_x = 2.0 * math.atan(math.tan(fov_y / 2.0) * aspect)

    xs = (np.arange(width, dtype=np.float32) + 0.5) / width * 2.0 - 1.0
    ys = 1.0 - (np.arange(height, dtype=np.float32) + 0.5) / height * 2.0
    xx, yy = np.meshgrid(xs, ys)

    x = xx * math.tan(fov_x / 2.0)
    y = yy * math.tan(fov_y / 2.0)
    z = np.ones_like(x)

    norm = np.sqrt(x * x + y * y + z * z)
    x, y, z = x / norm, y / norm, z / norm

    pitch = math.radians(float(pitch_deg))
    cp, sp = math.cos(pitch), math.sin(pitch)
    y_pitch = y * cp - z * sp
    z_pitch = y * sp + z * cp

    yaw = math.radians(float(yaw_deg))
    cy, sy = math.cos(yaw), math.sin(yaw)
    x_world = x * cy + z_pitch * sy
    y_world = y_pitch
    z_world = -x * sy + z_pitch * cy

    lon = np.arctan2(x_world, z_world)
    lat = np.arcsin(np.clip(y_world, -1.0, 1.0))

    u = (lon / (2.0 * math.pi) + 0.5) * src_w
    v = (0.5 - lat / math.pi) * src_h

    sampled = _sample_bilinear(src, u, v)
    return Image.fromarray(np.clip(sampled, 0, 255).astype(np.uint8), mode="RGB")


def generate_pano_voxel_refs(
    pano_path: Path,
    output_dir: Path,
    *,
    width: int = 640,
    height: int = 480,
    jpeg_quality: int = 70,
    specs: tuple[PanoViewSpec, ...] = DEFAULT_PANO_VIEW_SPECS,
) -> list[Path]:
    """Write perspective reference images for voxel-world generation."""
    pano_path = Path(pano_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jpeg_quality = max(40, min(95, int(jpeg_quality)))

    with Image.open(pano_path) as pano:
        pano_rgb = pano.convert("RGB")
        pano_size = pano_rgb.size
        if pano_size[0] < pano_size[1] * 1.8:
            raise ValueError(f"expected a 2:1 panorama, got {pano_size[0]}x{pano_size[1]}")

        outputs: list[Path] = []
        meta_views = []
        for spec in specs:
            view = equirectangular_to_perspective(
                pano_rgb,
                yaw_deg=spec.yaw_deg,
                pitch_deg=spec.pitch_deg,
                fov_deg=spec.fov_deg,
                width=width,
                height=height,
            )
            out = output_dir / spec.filename
            if out.suffix.lower() in {".jpg", ".jpeg"}:
                view.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            else:
                view.save(out, format="PNG", optimize=True)
            outputs.append(out)
            meta_views.append(
                {
                    "key": spec.key,
                    "filename": spec.filename,
                    "yaw_deg": spec.yaw_deg,
                    "pitch_deg": spec.pitch_deg,
                    "fov_deg": spec.fov_deg,
                    "path": str(out),
                }
            )

    meta = {
        "schema_version": "pano_voxel_refs_v1",
        "source_pano": str(pano_path),
        "source_size": list(pano_size),
        "output_size": [width, height],
        "encoding": {
            "format": (
                "jpeg"
                if all(p.suffix.lower() in {".jpg", ".jpeg"} for p in outputs)
                else "mixed"
            ),
            "jpeg_quality": jpeg_quality,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "views": meta_views,
        "notes": "Generated from a 2:1 equirectangular panorama.",
    }
    (output_dir / "refs_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate voxel-world reference views from a 2:1 panorama."
    )
    parser.add_argument("--pano", required=True, help="Input 2:1 equirectangular panorama.")
    parser.add_argument("--output-dir", required=True, help="Directory for generated reference views.")
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=576)
    parser.add_argument("--jpeg-quality", type=int, default=78)
    parser.add_argument("--json", action="store_true", help="Print generated paths as JSON.")
    args = parser.parse_args()

    paths = generate_pano_voxel_refs(
        Path(args.pano),
        Path(args.output_dir),
        width=args.width,
        height=args.height,
        jpeg_quality=args.jpeg_quality,
    )
    if args.json:
        print(json.dumps([str(path) for path in paths], ensure_ascii=False))
    else:
        for path in paths:
            print(path)


if __name__ == "__main__":
    main()
