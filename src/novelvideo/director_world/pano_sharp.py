#!/usr/bin/env python3
"""Minimal panorama -> cubemap -> SHARP -> merged 3DGS builder.

This intentionally avoids importing the full WorldGen package. WorldGen's
`pano_sharp.py` depends on pytorch3d and other Linux/CUDA-heavy pieces; this
script copies the small equirectangular/cubemap path and uses Apple ml-sharp
directly.

The first pass uses a constant equirectangular distance map to align all faces
to one global scale. That is enough to validate the Mac/SHARP/cubemap/PLY chain.
For production-quality geometry, replace the constant distance map with DA-2
equirectangular depth like WorldGen does.
"""

from __future__ import annotations

import argparse
import functools
import importlib
import importlib.util
import json
import math
import os
import sys
import types
from collections import namedtuple
from contextlib import nullcontext
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_MODEL_URL = "https://ml-site.cdn-apple.com/models/sharp/sharp_2572gikvuh.pt"
DA2_HUB_ID = "haodongli/DA-2"
DA2_MAX_DISTANCE = 20.0
DA2_CONFIG = {
    "inference": {
        "min_pixels": 580000,
        "max_pixels": 620000,
    },
    "spherevit": {
        "vit_w_esphere": {
            "input_dims": [1024, 1024, 1024, 1024],
            "hidden_dim": 512,
            "num_heads": 8,
            "expansion": 4,
            "num_layers_head": [2, 2, 2],
            "dropout": 0.0,
            "layer_scale": 0.0001,
            "out_dim": 64,
            "kernel_size": 3,
            "num_prompt_blocks": 1,
            "use_norm": False,
        },
        "sphere": {
            "width": 1092,
            "height": 546,
            "hfov": 6.2832,
            "vfov": 3.1416,
        },
    },
}
CUBEMAP_FACE_NAMES = ("front", "back", "right", "left", "up", "down")
CubemapFaces = namedtuple("CubemapFaces", CUBEMAP_FACE_NAMES)


class Sharp3DUnavailable(RuntimeError):
    """Raised when optional SHARP/3DGS dependencies are not installed."""

    error_code = "SHARP_3D_UNAVAILABLE"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "SHARP/3DGS dependencies are unavailable. Install the optional world extra "
            "and the Apple ml-sharp package; model weights are downloaded at runtime."
        )


class _LazyModule:
    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


torch = _LazyModule("torch")
F = _LazyModule("torch.nn.functional")


def _inference_mode(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with torch.inference_mode():
            return func(*args, **kwargs)

    return wrapper


def sharp_available() -> bool:
    return importlib.util.find_spec("sharp") is not None


def da2_available() -> bool:
    return importlib.util.find_spec("da2") is not None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a minimal WorldGen-style pano_sharp pass on a 2:1 panorama."
    )
    parser.add_argument("--pano", default="", help="Input 2:1 equirectangular panorama image.")
    parser.add_argument(
        "--image",
        default="",
        help="Input a single perspective image and reconstruct one SHARP face directly.",
    )
    parser.add_argument(
        "--cubemap-dir",
        default="",
        help="Optional directory with front/back/right/left/up/down PNGs. "
        "If provided, RGB faces are loaded directly instead of extracted from --pano.",
    )
    parser.add_argument(
        "--output-dir", required=True, help="Directory to write cubemap faces and PLY."
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "mps", "cuda"),
        help="Torch device. auto prefers cuda, then mps, then cpu.",
    )
    parser.add_argument(
        "--face-size",
        type=int,
        default=768,
        help="Cubemap face size before SHARP's internal 1536 resize. WorldGen default: 768.",
    )
    parser.add_argument(
        "--internal-size",
        type=int,
        default=1536,
        help="SHARP inference resolution. Official scripts use 1536.",
    )
    parser.add_argument(
        "--depth-meters",
        type=float,
        default=8.0,
        help="Constant distance map used for quick validation.",
    )
    parser.add_argument(
        "--depth-source",
        default="constant",
        choices=("constant", "da2"),
        help="Depth source for cubemap alignment.",
    )
    parser.add_argument(
        "--geometry-mode",
        default="sharp",
        choices=("sharp", "pano-depth", "pano-depth-debug"),
        help=(
            "sharp: six cubemap faces through SHARP and merge. "
            "pano-depth: build a production PLY directly from equirect RGB + panorama depth."
        ),
    )
    parser.add_argument(
        "--depth-path",
        default="",
        help="Optional existing equirectangular depth tensor (.pt) for pano-depth.",
    )
    parser.add_argument(
        "--no-global-depth-align",
        action="store_true",
        help=(
            "Disable the panorama-depth scale alignment applied after each SHARP face. "
            "By default, pano SHARP faces are aligned to the global equirect depth."
        ),
    )
    parser.add_argument(
        "--alignment-samples",
        type=int,
        default=80000,
        help="Number of Gaussian samples per face for global panorama-depth alignment.",
    )
    parser.add_argument(
        "--alignment-clamp-min",
        type=float,
        default=0.5,
        help="Minimum extra per-face scale applied by global depth alignment.",
    )
    parser.add_argument(
        "--alignment-clamp-max",
        type=float,
        default=2.0,
        help="Maximum extra per-face scale applied by global depth alignment.",
    )
    parser.add_argument(
        "--global-depth-warp-strength",
        type=float,
        default=1.0,
        help=(
            "After face-level alignment, warp each SHARP Gaussian radially toward "
            "the global equirect depth. 1.0 fully matches the pano depth."
        ),
    )
    parser.add_argument(
        "--global-depth-ratio-clamp-min",
        type=float,
        default=0.35,
        help="Minimum per-Gaussian radial warp ratio.",
    )
    parser.add_argument(
        "--global-depth-ratio-clamp-max",
        type=float,
        default=3.0,
        help="Maximum per-Gaussian radial warp ratio.",
    )
    parser.add_argument(
        "--pano-depth-width",
        type=int,
        default=2048,
        help="pano-depth output equirect sample width. Height is width/2.",
    )
    parser.add_argument(
        "--pano-depth-radius-scale",
        type=float,
        default=1.0,
        help="Multiplier applied to depth values when writing pano-depth PLY.",
    )
    parser.add_argument(
        "--pano-depth-point-scale",
        type=float,
        default=0.72,
        help="Angular footprint multiplier for pano-depth Gaussian scales.",
    )
    parser.add_argument(
        "--pano-depth-min-scale",
        type=float,
        default=0.0008,
        help=(
            "Minimum Gaussian world scale for pano-depth. Applied as an angular-aware "
            "floor (capped to each splat's depth-proportional ideal) so near-camera "
            "splats do not balloon and overdraw."
        ),
    )
    parser.add_argument(
        "--pano-depth-max-scale",
        type=float,
        default=0.045,
        help="Maximum Gaussian scale for pano-depth.",
    )
    parser.add_argument(
        "--pano-depth-opacity",
        type=float,
        default=0.96,
        help="Gaussian opacity for pano-depth.",
    )
    parser.add_argument(
        "--pano-depth-output-name",
        default="pano_depth.ply",
        help="Output PLY filename for pano-depth.",
    )
    parser.add_argument("--debug-width", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-radius-scale", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-point-scale", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-min-scale", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-max-scale", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-opacity", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--debug-output-name", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--depth-device",
        default="auto",
        choices=("auto", "cpu", "mps", "cuda"),
        help="Torch device for DA-2 depth inference.",
    )
    parser.add_argument(
        "--model-url",
        default=DEFAULT_MODEL_URL,
        help="SHARP checkpoint URL/path accepted by torch.hub.load_state_dict_from_url.",
    )
    parser.add_argument(
        "--faces",
        default="",
        help="Comma-separated cubemap faces to run. Default: all 6 for --pano, front for --image.",
    )
    parser.add_argument(
        "--single-face-name",
        default="front",
        choices=CUBEMAP_FACE_NAMES,
        help="World orientation for --image single-face reconstruction.",
    )
    parser.add_argument(
        "--max-gaussians-per-face",
        type=int,
        default=0,
        help="Optional deterministic stride downsample per face for quick validation.",
    )
    parser.add_argument(
        "--front-yaw-deg",
        type=float,
        default=0.0,
        help="Yaw in the source panorama that should become the canonical front face.",
    )
    parser.add_argument(
        "--sphere-yaw-deg",
        type=float,
        default=0.0,
        help="Additional panorama sphere-correction yaw/pan in degrees.",
    )
    parser.add_argument(
        "--sphere-pitch-deg",
        type=float,
        default=0.0,
        help="Panorama sphere-correction pitch/tilt in degrees.",
    )
    parser.add_argument(
        "--sphere-roll-deg",
        type=float,
        default=0.0,
        help="Panorama sphere-correction roll in degrees.",
    )
    return parser.parse_args()


def choose_device(name: str) -> torch.device:
    if name == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but not available.")
    if name == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS requested but not available.")
    return torch.device(name)


def find_installed_da2_root() -> Path:
    for entry in sys.path:
        root = Path(entry) / "da2"
        if (root / "model" / "spherevit.py").exists():
            return root
    raise ModuleNotFoundError("Could not locate installed da2 package on sys.path.")


def load_da2_spherevit_class():
    """Load DA-2 SphereViT without executing da2/__init__.py.

    DA-2's package __init__ imports UI/point-cloud utilities that are not needed
    for inference and pull in extra dependencies. The model module itself is
    enough for panorama depth.
    """
    da2_root = find_installed_da2_root()

    fake_da2 = types.ModuleType("da2")
    fake_da2.__path__ = [str(da2_root)]
    sys.modules["da2"] = fake_da2

    fake_model = types.ModuleType("da2.model")
    fake_model.__path__ = [str(da2_root / "model")]
    sys.modules["da2.model"] = fake_model

    spec = importlib.util.spec_from_file_location(
        "da2.model.spherevit",
        da2_root / "model" / "spherevit.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load DA-2 SphereViT from {da2_root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["da2.model.spherevit"] = module
    spec.loader.exec_module(module)
    return module.SphereViT


def build_da2_model(device: torch.device):
    SphereViT = load_da2_spherevit_class()
    local_only_env = os.environ.get("DA2_LOCAL_FILES_ONLY")
    local_only = (
        local_only_env.strip().lower() in {"1", "true", "yes", "on"}
        if local_only_env is not None
        else False
    )
    source = "local cache" if local_only else "Hugging Face"
    print(f"Loading DA-2 checkpoint from {source}: {DA2_HUB_ID}", flush=True)
    try:
        model = SphereViT.from_pretrained(
            DA2_HUB_ID,
            config=DA2_CONFIG,
            local_files_only=local_only,
        )
    except TypeError:
        model = SphereViT.from_pretrained(DA2_HUB_ID, config=DA2_CONFIG)
    model.eval()
    return model.to(device)


@_inference_mode
def predict_da2_distance(
    image: Image.Image, device: torch.device, output_dir: Path
) -> torch.Tensor:
    model = build_da2_model(device)
    rgb_np = np.asarray(image.convert("RGB")).copy()
    rgb = torch.from_numpy(rgb_np).permute(2, 0, 1).float() / 255.0
    rgb = rgb.unsqueeze(0).to(device)

    autocast_context = (
        torch.autocast(device_type=device.type) if device.type in {"cuda", "mps"} else nullcontext()
    )
    with autocast_context:
        distance = model(rgb)

    distance = distance.squeeze(0).float()
    distance = distance / distance.max().clamp(min=1e-6) * DA2_MAX_DISTANCE

    torch.save(distance.detach().cpu(), output_dir / "da2_distance.pt")
    distance_np = distance.detach().cpu().numpy()
    depth_vis = (distance_np / max(float(distance_np.max()), 1e-6) * 255.0).astype(np.uint8)
    Image.fromarray(depth_vis, mode="L").save(output_dir / "da2_distance_vis.png")
    return distance.to(device)


def image_to_tensor(image: Image.Image, device: torch.device) -> torch.Tensor:
    rgb = image.convert("RGB")
    array = np.asarray(rgb).copy()
    return torch.from_numpy(array).float().permute(2, 0, 1).to(device) / 255.0


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    array = (tensor.detach().clamp(0.0, 1.0).permute(1, 2, 0).cpu().numpy() * 255.0).astype(
        np.uint8
    )
    return Image.fromarray(array, mode="RGB")


def normalize_pano_2to1(image: Image.Image, output_dir: Path) -> Image.Image:
    """Normalize panorama to exact 2:1 before cubemap sampling."""
    if image.width == image.height * 2:
        return image
    target_width = image.height * 2
    normalized = image.resize((target_width, image.height), Image.Resampling.LANCZOS)
    normalized.save(output_dir / "pano_360_normalized_2to1.png")
    print(
        f"Normalized panorama to exact 2:1: {image.width}x{image.height} -> "
        f"{normalized.width}x{normalized.height}",
        flush=True,
    )
    return normalized


def direction_to_equirectangular(
    dx: torch.Tensor,
    dy: torch.Tensor,
    dz: torch.Tensor,
    width: int,
    height: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    theta = torch.atan2(dx, dz)
    phi = torch.asin((-dy).clamp(-1, 1))
    x = (theta + math.pi) / (2 * math.pi) * (width - 1)
    y = (math.pi / 2 - phi) / math.pi * (height - 1)
    return x, y


def create_rotation_matrix(forward: torch.Tensor, up: torch.Tensor) -> torch.Tensor:
    forward = forward / forward.norm()
    right = torch.linalg.cross(forward, up)
    right = right / right.norm()
    down = torch.linalg.cross(forward, right)
    return torch.stack([right, down, forward], dim=1)


def axis_rotation_matrix(axis: str, angle_deg: float, device: torch.device) -> torch.Tensor:
    angle = math.radians(float(angle_deg or 0.0))
    c = math.cos(angle)
    s = math.sin(angle)
    if axis == "x":
        values = [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]
    elif axis == "y":
        values = [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]
    elif axis == "z":
        values = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    else:
        raise ValueError(f"Unknown rotation axis: {axis}")
    return torch.tensor(values, dtype=torch.float32, device=device)


def pano_sampling_rotation(
    device: torch.device,
    *,
    front_yaw_deg: float = 0.0,
    sphere_yaw_deg: float = 0.0,
    sphere_pitch_deg: float = 0.0,
    sphere_roll_deg: float = 0.0,
) -> torch.Tensor:
    """Rotation applied to cubemap rays before sampling the source panorama.

    The output PLY keeps the canonical cubemap world axes. These angles only
    decide which source-pano direction is sampled into each canonical face.
    """
    yaw = axis_rotation_matrix("y", front_yaw_deg + sphere_yaw_deg, device)
    pitch = axis_rotation_matrix("x", sphere_pitch_deg, device)
    roll = axis_rotation_matrix("z", sphere_roll_deg, device)
    return yaw @ pitch @ roll


def cubemap_face_params(device: torch.device) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    return {
        "front": (
            torch.tensor([0.0, 0.0, 1.0], device=device),
            torch.tensor([0.0, -1.0, 0.0], device=device),
        ),
        "back": (
            torch.tensor([0.0, 0.0, -1.0], device=device),
            torch.tensor([0.0, -1.0, 0.0], device=device),
        ),
        "right": (
            torch.tensor([1.0, 0.0, 0.0], device=device),
            torch.tensor([0.0, -1.0, 0.0], device=device),
        ),
        "left": (
            torch.tensor([-1.0, 0.0, 0.0], device=device),
            torch.tensor([0.0, -1.0, 0.0], device=device),
        ),
        "up": (
            torch.tensor([0.0, -1.0, 0.0], device=device),
            torch.tensor([0.0, 0.0, -1.0], device=device),
        ),
        "down": (
            torch.tensor([0.0, 1.0, 0.0], device=device),
            torch.tensor([0.0, 0.0, 1.0], device=device),
        ),
    }


def extract_perspective_from_equirectangular(
    equirect: torch.Tensor,
    direction: torch.Tensor,
    up: torch.Tensor,
    output_size: int,
    fov_deg: float = 90.0,
    sampling_rotation: torch.Tensor | None = None,
) -> torch.Tensor:
    device = equirect.device
    dtype = equirect.dtype
    _, eq_h, eq_w = equirect.shape

    rotation = create_rotation_matrix(direction.to(device), up.to(device))
    fov_rad = math.radians(fov_deg)
    focal = output_size / (2 * math.tan(fov_rad / 2))

    y_coords = torch.arange(output_size, device=device, dtype=dtype)
    x_coords = torch.arange(output_size, device=device, dtype=dtype)
    yy, xx = torch.meshgrid(y_coords, x_coords, indexing="ij")
    cx = (output_size - 1) / 2
    cy = (output_size - 1) / 2
    dx_cam = (xx - cx) / focal
    dy_cam = (yy - cy) / focal
    dz_cam = torch.ones_like(dx_cam)
    norm = torch.sqrt(dx_cam.square() + dy_cam.square() + dz_cam.square())
    dirs_cam = torch.stack([dx_cam / norm, dy_cam / norm, dz_cam / norm], dim=-1)
    dirs_world = torch.einsum("ij,hwj->hwi", rotation, dirs_cam)
    if sampling_rotation is not None:
        dirs_world = torch.einsum("ij,hwj->hwi", sampling_rotation.to(device), dirs_world)

    eq_x, eq_y = direction_to_equirectangular(
        dirs_world[..., 0],
        dirs_world[..., 1],
        dirs_world[..., 2],
        eq_w,
        eq_h,
    )
    grid_x = (eq_x / (eq_w - 1)) * 2 - 1
    grid_y = (eq_y / (eq_h - 1)) * 2 - 1
    grid = torch.stack([grid_x, grid_y], dim=-1).unsqueeze(0)
    sample_input = equirect.unsqueeze(0)
    sample_grid = grid
    sample_device = device
    if device.type == "mps":
        # PyTorch MPS does not support grid_sample padding_mode="border".
        # The cubemap/depth extraction is small relative to SHARP inference, so
        # run this interpolation on CPU and move the face back to the target device.
        sample_input = sample_input.cpu()
        sample_grid = sample_grid.cpu()
    sampled = F.grid_sample(
        sample_input,
        sample_grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )
    return sampled[0].to(sample_device)


def extract_cubemap_from_equirectangular(
    equirect: torch.Tensor,
    face_size: int,
    *,
    sampling_rotation: torch.Tensor | None = None,
) -> CubemapFaces:
    faces = {}
    for face_name, (direction, up) in cubemap_face_params(equirect.device).items():
        faces[face_name] = extract_perspective_from_equirectangular(
            equirect,
            direction,
            up,
            output_size=face_size,
            sampling_rotation=sampling_rotation,
        )
    return CubemapFaces(**faces)


def load_cubemap_faces_from_dir(
    cubemap_dir: Path,
    face_size: int,
    device: torch.device,
) -> CubemapFaces:
    faces = {}
    missing = []
    for face_name in CUBEMAP_FACE_NAMES:
        path = cubemap_dir / f"{face_name}.png"
        if not path.exists():
            missing.append(str(path))
            continue
        image = (
            Image.open(path)
            .convert("RGB")
            .resize(
                (face_size, face_size),
                Image.Resampling.LANCZOS,
            )
        )
        faces[face_name] = image_to_tensor(image, device)
    if missing:
        raise FileNotFoundError("Missing cubemap face(s): " + ", ".join(missing))
    return CubemapFaces(**faces)


def fit_image_to_square_without_distortion(image: Image.Image, size: int) -> Image.Image:
    """Fit a perspective image into a square canvas without changing aspect ratio."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image size: {width}x{height}")

    scale = min(size / width, size / height)
    resized = rgb.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.LANCZOS,
    )

    array = np.asarray(rgb)
    edge_pixels = np.concatenate(
        [
            array[0, :, :],
            array[-1, :, :],
            array[:, 0, :],
            array[:, -1, :],
        ],
        axis=0,
    )
    fill_color = tuple(int(v) for v in edge_pixels.mean(axis=0))
    canvas = Image.new("RGB", (size, size), fill_color)
    offset = ((size - resized.width) // 2, (size - resized.height) // 2)
    canvas.paste(resized, offset)
    return canvas


def single_image_to_cubemap_faces(
    image: Image.Image,
    face_size: int,
    device: torch.device,
    *,
    face_name: str = "front",
) -> CubemapFaces:
    """Put one perspective image into a cubemap-shaped container for SHARP."""
    if face_name not in CUBEMAP_FACE_NAMES:
        raise ValueError(f"Unknown single face: {face_name}")

    resized = fit_image_to_square_without_distortion(image, face_size)
    face_tensor = image_to_tensor(resized, device)
    blank = torch.zeros_like(face_tensor)
    faces = {name: blank for name in CUBEMAP_FACE_NAMES}
    faces[face_name] = face_tensor
    return CubemapFaces(**faces)


def load_depth_tensor(path: Path) -> torch.Tensor:
    depth = torch.load(path, map_location="cpu")
    if isinstance(depth, dict):
        for key in ("distance", "depth", "pred", "prediction"):
            if key in depth:
                depth = depth[key]
                break
    if not isinstance(depth, torch.Tensor):
        depth = torch.as_tensor(depth)
    depth = depth.detach().float().cpu()
    if depth.ndim == 3 and depth.shape[0] == 1:
        depth = depth[0]
    if depth.ndim != 2:
        raise ValueError(f"Expected equirectangular depth tensor [H,W], got {tuple(depth.shape)}")
    return torch.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0).clamp(min=0.0)


def inverse_equirectangular_directions(
    width: int,
    height: int,
    device: torch.device,
) -> torch.Tensor:
    dtype = torch.float32
    y_coords = torch.arange(height, device=device, dtype=dtype)
    x_coords = torch.arange(width, device=device, dtype=dtype)
    yy, xx = torch.meshgrid(y_coords, x_coords, indexing="ij")
    theta = (xx / max(width - 1, 1)) * (2 * math.pi) - math.pi
    phi = math.pi / 2 - (yy / max(height - 1, 1)) * math.pi
    cos_phi = torch.cos(phi)
    dx = torch.sin(theta) * cos_phi
    dy = -torch.sin(phi)
    dz = torch.cos(theta) * cos_phi
    return torch.stack([dx, dy, dz], dim=-1)


def sample_equirectangular_tensor(
    tensor: torch.Tensor,
    dirs_source: torch.Tensor,
) -> torch.Tensor:
    if tensor.ndim != 3:
        raise ValueError(f"Expected tensor [C,H,W], got {tuple(tensor.shape)}")
    _, height, width = tensor.shape
    eq_x, eq_y = direction_to_equirectangular(
        dirs_source[..., 0],
        dirs_source[..., 1],
        dirs_source[..., 2],
        width,
        height,
    )
    grid_x = (eq_x / max(width - 1, 1)) * 2 - 1
    grid_y = (eq_y / max(height - 1, 1)) * 2 - 1
    grid = torch.stack([grid_x, grid_y], dim=-1).unsqueeze(0)
    sampled = F.grid_sample(
        tensor.unsqueeze(0),
        grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )
    return sampled[0]


def sample_equirectangular_points(
    tensor: torch.Tensor,
    dirs_source: torch.Tensor,
) -> torch.Tensor:
    """Sample an equirectangular tensor at arbitrary unit directions."""
    if tensor.ndim == 2:
        tensor = tensor.unsqueeze(0)
    if tensor.ndim != 3:
        raise ValueError(f"Expected tensor [C,H,W], got {tuple(tensor.shape)}")
    tensor = tensor.detach().float().cpu()
    dirs_source = dirs_source.detach().float().cpu()
    _, height, width = tensor.shape
    eq_x, eq_y = direction_to_equirectangular(
        dirs_source[..., 0],
        dirs_source[..., 1],
        dirs_source[..., 2],
        width,
        height,
    )
    grid_x = (eq_x / max(width - 1, 1)) * 2 - 1
    grid_y = (eq_y / max(height - 1, 1)) * 2 - 1
    grid = torch.stack([grid_x, grid_y], dim=-1).view(1, -1, 1, 2)
    sampled = F.grid_sample(
        tensor.unsqueeze(0),
        grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )
    return sampled[0, :, :, 0]


def estimate_global_depth_alignment_scale(
    positions_world: torch.Tensor,
    equirect_depth: torch.Tensor,
    sampling_rotation: torch.Tensor,
    *,
    sample_count: int,
    clamp_min: float,
    clamp_max: float,
) -> tuple[float, dict[str, float | int]]:
    """Estimate an extra face scale against the global panorama depth.

    SHARP predicts each cubemap face in a local perspective frame. The initial
    median-depth scale is useful but can still leave face-to-face scale drift.
    This estimates a second scalar by comparing world-space Gaussian radii to
    the equirectangular depth sampled along the same canonical world rays.
    """
    positions = positions_world.detach().reshape(-1, 3).float().cpu()
    radii = torch.linalg.norm(positions, dim=-1)
    valid = torch.isfinite(radii) & (radii > 0.01)
    valid_indices = torch.where(valid)[0]
    if len(valid_indices) < 32:
        return 1.0, {"sample_count": int(len(valid_indices)), "valid_count": 0}

    max_samples = max(32, int(sample_count))
    if len(valid_indices) > max_samples:
        pick = torch.linspace(0, len(valid_indices) - 1, steps=max_samples).long()
        valid_indices = valid_indices[pick]

    sampled_positions = positions[valid_indices]
    sampled_radii = radii[valid_indices]
    dirs_world = sampled_positions / sampled_radii[:, None].clamp(min=1e-6)
    dirs_source = dirs_world @ sampling_rotation.detach().float().cpu().T
    target_depth = sample_equirectangular_points(equirect_depth, dirs_source).squeeze(0)

    ratios = target_depth / sampled_radii.clamp(min=1e-6)
    ratio_valid = (
        torch.isfinite(ratios)
        & torch.isfinite(target_depth)
        & (target_depth > 0.01)
        & (ratios > 0.05)
        & (ratios < 20.0)
    )
    ratios = ratios[ratio_valid]
    if len(ratios) < 32:
        return 1.0, {
            "sample_count": int(len(valid_indices)),
            "valid_count": int(len(ratios)),
        }

    q = torch.quantile(ratios, torch.tensor([0.10, 0.50, 0.90], dtype=torch.float32))
    trimmed = ratios[(ratios >= q[0]) & (ratios <= q[2])]
    scale = float(trimmed.median().item() if len(trimmed) else q[1].item())
    scale = max(float(clamp_min), min(float(clamp_max), scale))
    stats: dict[str, float | int] = {
        "sample_count": int(len(valid_indices)),
        "valid_count": int(len(ratios)),
        "ratio_p10": float(q[0].item()),
        "ratio_p50": float(q[1].item()),
        "ratio_p90": float(q[2].item()),
        "applied_scale": float(scale),
    }
    return scale, stats


def apply_global_depth_radial_warp(
    positions_world: torch.Tensor,
    singular_values: torch.Tensor,
    equirect_depth: torch.Tensor,
    sampling_rotation: torch.Tensor,
    *,
    strength: float,
    ratio_clamp_min: float,
    ratio_clamp_max: float,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, float | int]]:
    """Warp each Gaussian radius toward the global equirectangular depth map."""
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.0:
        return positions_world, singular_values, {"strength": 0.0, "valid_count": 0}

    original_shape = positions_world.shape
    positions = positions_world.detach().reshape(-1, 3).float().cpu()
    radii = torch.linalg.norm(positions, dim=-1)
    valid = torch.isfinite(radii) & (radii > 0.01)
    if int(valid.sum().item()) < 32:
        return positions_world, singular_values, {
            "strength": float(strength),
            "valid_count": int(valid.sum().item()),
        }

    dirs_world = torch.zeros_like(positions)
    dirs_world[valid] = positions[valid] / radii[valid, None].clamp(min=1e-6)
    dirs_source = dirs_world @ sampling_rotation.detach().float().cpu().T
    target_depth = sample_equirectangular_points(equirect_depth, dirs_source).squeeze(0)
    ratios = target_depth / radii.clamp(min=1e-6)
    ratio_valid = (
        valid
        & torch.isfinite(ratios)
        & torch.isfinite(target_depth)
        & (target_depth > 0.01)
        & (ratios > 0.02)
        & (ratios < 50.0)
    )
    factors = torch.ones_like(radii)
    clamped = ratios[ratio_valid].clamp(
        min=float(ratio_clamp_min),
        max=float(ratio_clamp_max),
    )
    factors[ratio_valid] = 1.0 + (clamped - 1.0) * strength

    if int(ratio_valid.sum().item()) >= 32:
        q = torch.quantile(ratios[ratio_valid], torch.tensor([0.10, 0.50, 0.90]))
        fq = torch.quantile(factors[ratio_valid], torch.tensor([0.10, 0.50, 0.90]))
        stats: dict[str, float | int] = {
            "strength": float(strength),
            "valid_count": int(ratio_valid.sum().item()),
            "ratio_p10": float(q[0].item()),
            "ratio_p50": float(q[1].item()),
            "ratio_p90": float(q[2].item()),
            "factor_p10": float(fq[0].item()),
            "factor_p50": float(fq[1].item()),
            "factor_p90": float(fq[2].item()),
        }
    else:
        stats = {
            "strength": float(strength),
            "valid_count": int(ratio_valid.sum().item()),
        }

    factors = factors.to(device=positions_world.device, dtype=positions_world.dtype).view(
        original_shape[:-1] + (1,)
    )
    return positions_world * factors, singular_values * factors, stats


def save_basic_gaussian_ply(gaussians, path: Path) -> None:
    """Write standard Gaussian PLY vertex fields without camera metadata.

    ml-sharp's save_ply computes disparity metadata from the Z coordinate,
    which is not well-defined for a full 360 point cloud because half the scene
    is behind the canonical +Z camera. PlayCanvas only needs the vertex fields.
    """
    from plyfile import PlyData, PlyElement
    from sharp.utils.gaussians import convert_rgb_to_spherical_harmonics

    def inverse_sigmoid(tensor: torch.Tensor) -> torch.Tensor:
        tensor = tensor.clamp(1e-4, 1 - 1e-4)
        return torch.log(tensor / (1.0 - tensor))

    xyz = gaussians.mean_vectors.flatten(0, 1)
    sh = convert_rgb_to_spherical_harmonics(gaussians.colors.flatten(0, 1).clamp(0.0, 1.0))
    opacity_logits = inverse_sigmoid(gaussians.opacities).flatten(0, 1).unsqueeze(-1)
    scale_logits = torch.log(gaussians.singular_values.clamp(min=1e-5)).flatten(0, 1)
    quaternions = gaussians.quaternions.flatten(0, 1)
    attributes = torch.cat((xyz, sh, opacity_logits, scale_logits, quaternions), dim=1)
    dtype_full = [
        (attribute, "f4")
        for attribute in ["x", "y", "z"]
        + [f"f_dc_{i}" for i in range(3)]
        + ["opacity"]
        + [f"scale_{i}" for i in range(3)]
        + [f"rot_{i}" for i in range(4)]
    ]
    elements = np.empty(len(xyz), dtype=dtype_full)
    elements[:] = list(map(tuple, attributes.detach().cpu().numpy()))
    PlyData([PlyElement.describe(elements, "vertex")]).write(path)


def build_pano_depth_gaussians(
    pano_rgb: torch.Tensor,
    distance: torch.Tensor,
    *,
    output_width: int,
    sampling_rotation: torch.Tensor,
    radius_scale: float,
    point_scale: float,
    min_scale: float,
    max_scale: float,
    opacity: float,
):
    from sharp.utils.gaussians import Gaussians3D

    output_width = max(64, int(output_width))
    if output_width % 2:
        output_width += 1
    output_height = output_width // 2
    cpu = torch.device("cpu")
    pano_rgb = pano_rgb.detach().float().cpu()
    distance = distance.detach().float().cpu()
    if distance.ndim == 2:
        distance = distance.unsqueeze(0)
    if distance.ndim != 3 or distance.shape[0] != 1:
        raise ValueError(f"Expected depth [1,H,W], got {tuple(distance.shape)}")

    dirs_world = inverse_equirectangular_directions(output_width, output_height, cpu)
    dirs_source = torch.einsum("ij,hwj->hwi", sampling_rotation.detach().float().cpu(), dirs_world)
    sampled_rgb = sample_equirectangular_tensor(pano_rgb, dirs_source)
    sampled_depth = sample_equirectangular_tensor(distance, dirs_source).squeeze(0)

    depth_values = (sampled_depth * float(radius_scale)).clamp(min=0.01)
    positions = dirs_world * depth_values[..., None]
    angular_step = 2 * math.pi / output_width
    # Each Gaussian's ideal world scale is depth-proportional so that its on-screen
    # angular footprint (scale / depth) stays constant at angular_step * point_scale,
    # which makes neighbouring splats just tile without overdraw.
    ideal_scale = depth_values * angular_step * float(point_scale)
    # `min_scale` is a *world-space* floor. For near-camera splats (small depth) a
    # fixed world floor inflates the angular footprint (scale / depth grows as depth
    # shrinks), which is the dominant overdraw cost on outdoor panos that mix
    # very-near geometry with far sky. Apply it as an angular-aware floor: never let
    # the floor push a splat above its depth-proportional ideal, so near geometry
    # keeps the design footprint instead of ballooning. Far splats are unaffected.
    angular_floor = torch.minimum(
        torch.full_like(ideal_scale, float(min_scale)),
        ideal_scale,
    )
    scale_values = torch.maximum(ideal_scale, angular_floor).clamp(max=float(max_scale))

    valid = torch.isfinite(positions).all(dim=-1) & torch.isfinite(scale_values)
    positions = positions[valid]
    colors = sampled_rgb.permute(1, 2, 0)[valid].clamp(0.0, 1.0)
    scale_values = scale_values[valid]

    singular_values = scale_values[:, None].repeat(1, 3)
    quaternions = torch.zeros((positions.shape[0], 4), dtype=torch.float32)
    quaternions[:, 0] = 1.0
    opacities = torch.full(
        (positions.shape[0],),
        float(max(0.01, min(0.99, opacity))),
        dtype=torch.float32,
    )

    return Gaussians3D(
        mean_vectors=positions.unsqueeze(0),
        singular_values=singular_values.unsqueeze(0),
        quaternions=quaternions.unsqueeze(0),
        colors=colors.unsqueeze(0),
        opacities=opacities.unsqueeze(0),
    )


def get_cubemap_extrinsics(face_name: str, device: torch.device) -> torch.Tensor:
    forward, up = cubemap_face_params(device)[face_name]
    camera_to_world = create_rotation_matrix(forward, up)
    world_to_camera = camera_to_world.T
    extrinsics = torch.eye(4, device=device, dtype=torch.float32)
    extrinsics[:3, :3] = world_to_camera
    return extrinsics


def rotate_quaternions(quaternions: torch.Tensor, rotation: torch.Tensor) -> torch.Tensor:
    from sharp.utils.linalg import (
        quaternions_from_rotation_matrices,
        rotation_matrices_from_quaternions,
    )

    rotation_matrices = rotation_matrices_from_quaternions(quaternions)
    world_matrices = rotation @ rotation_matrices
    return quaternions_from_rotation_matrices(world_matrices)


def downsample_gaussians(gaussians, max_gaussians: int):
    if max_gaussians <= 0 or gaussians.mean_vectors.shape[1] <= max_gaussians:
        return gaussians

    from sharp.utils.gaussians import Gaussians3D

    total = gaussians.mean_vectors.shape[1]
    indices = torch.linspace(
        0,
        total - 1,
        steps=max_gaussians,
        device=gaussians.mean_vectors.device,
    ).long()
    return Gaussians3D(
        mean_vectors=gaussians.mean_vectors[:, indices],
        singular_values=gaussians.singular_values[:, indices],
        quaternions=gaussians.quaternions[:, indices],
        colors=gaussians.colors[:, indices],
        opacities=gaussians.opacities[:, indices],
    )


def build_sharp_model(model_url: str, device: torch.device):
    from sharp.models import PredictorParams, create_predictor

    print(f"Loading SHARP checkpoint: {model_url}", flush=True)
    state_dict = torch.hub.load_state_dict_from_url(model_url, progress=True)
    predictor = create_predictor(PredictorParams())
    predictor.load_state_dict(state_dict)
    predictor.eval()
    return predictor.to(device)


@_inference_mode
def predict_cubemap_face(
    predictor,
    face_rgb: torch.Tensor,
    face_depth: torch.Tensor,
    face_size: int,
    internal_size: int,
    device: torch.device,
):
    from sharp.utils.gaussians import Gaussians3D, unproject_gaussians

    image_resized = F.interpolate(
        face_rgb.unsqueeze(0),
        size=(internal_size, internal_size),
        mode="bilinear",
        align_corners=True,
    )
    f_px = face_size / 2.0
    disparity_factor = torch.tensor([f_px / face_size], dtype=torch.float32, device=device)
    gaussians_ndc = predictor(image_resized, disparity_factor)

    f_resized = f_px * internal_size / face_size
    intrinsics_resized = torch.tensor(
        [
            [f_resized, 0, internal_size / 2, 0],
            [0, f_resized, internal_size / 2, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
        dtype=torch.float32,
        device=device,
    )
    gaussians_cam = unproject_gaussians(
        gaussians_ndc,
        torch.eye(4, device=device),
        intrinsics_resized,
        (internal_size, internal_size),
    )

    sharp_depths = gaussians_cam.mean_vectors[0, :, 2]
    valid = sharp_depths > 0.01
    target_median = face_depth[face_depth > 0.01].median()
    sharp_median = sharp_depths[valid].median()
    if sharp_median > 1e-6 and target_median > 1e-6:
        scale = target_median / sharp_median
        positions = gaussians_cam.mean_vectors * scale
        singular_values = gaussians_cam.singular_values * scale
    else:
        scale = torch.tensor(1.0, device=device)
        positions = gaussians_cam.mean_vectors
        singular_values = gaussians_cam.singular_values

    return Gaussians3D(
        mean_vectors=positions,
        singular_values=singular_values,
        quaternions=gaussians_cam.quaternions,
        colors=gaussians_cam.colors,
        opacities=gaussians_cam.opacities,
    ), float(scale.detach().cpu().item())


def run(args: argparse.Namespace) -> None:
    if not sharp_available():
        raise Sharp3DUnavailable()

    from sharp.utils.gaussians import Gaussians3D, save_ply

    device = choose_device(args.device)
    output_dir = Path(args.output_dir).expanduser()
    faces_dir = output_dir / "cubemap_faces"
    faces_dir.mkdir(parents=True, exist_ok=True)

    has_pano = bool(str(args.pano or "").strip())
    has_image = bool(str(args.image or "").strip())
    if has_pano == has_image:
        raise ValueError("Pass exactly one of --pano or --image.")
    if has_image and args.cubemap_dir:
        raise ValueError("--cubemap-dir is only valid with --pano.")
    if args.geometry_mode in {"pano-depth", "pano-depth-debug"}:
        if not has_pano:
            raise ValueError(f"--geometry-mode {args.geometry_mode} requires --pano.")
        if args.cubemap_dir:
            raise ValueError(f"--cubemap-dir is not used by {args.geometry_mode}.")

        image = Image.open(args.pano).convert("RGB")
        image = normalize_pano_2to1(image, output_dir)
        pano_tensor = image_to_tensor(image, torch.device("cpu"))
        sampling_rotation = pano_sampling_rotation(
            torch.device("cpu"),
            front_yaw_deg=args.front_yaw_deg,
            sphere_yaw_deg=args.sphere_yaw_deg,
            sphere_pitch_deg=args.sphere_pitch_deg,
            sphere_roll_deg=args.sphere_roll_deg,
        )

        if args.depth_path:
            depth_path = Path(args.depth_path).expanduser()
            if not depth_path.exists():
                raise FileNotFoundError(f"depth tensor not found: {depth_path}")
            distance_2d = load_depth_tensor(depth_path)
            depth_device = None
            depth_source = "existing_pt"
        elif args.depth_source == "da2":
            if da2_available():
                depth_device = choose_device(args.depth_device)
                distance_2d = predict_da2_distance(image, depth_device, output_dir).detach().cpu()
                depth_source = "da2"
            else:
                print(
                    "DA-2 package is not installed; falling back to constant depth. "
                    "Geometry quality will be lower.",
                    flush=True,
                )
                depth_device = None
                distance_2d = torch.full(
                    (image.height, image.width),
                    float(args.depth_meters),
                    dtype=torch.float32,
                )
                depth_source = "constant"
        else:
            depth_device = None
            distance_2d = torch.full(
                (image.height, image.width),
                float(args.depth_meters),
                dtype=torch.float32,
            )
            depth_source = "constant"

        output_width = int(args.debug_width or args.pano_depth_width)
        radius_scale = float(args.debug_radius_scale or args.pano_depth_radius_scale)
        point_scale = float(args.debug_point_scale or args.pano_depth_point_scale)
        min_scale = float(args.debug_min_scale or args.pano_depth_min_scale)
        max_scale = float(args.debug_max_scale or args.pano_depth_max_scale)
        opacity = float(args.debug_opacity or args.pano_depth_opacity)
        output_name = str(args.debug_output_name or args.pano_depth_output_name)
        if args.geometry_mode == "pano-depth-debug" and not args.debug_output_name:
            output_name = "pano_depth_debug.ply"

        pano_depth_gaussians = build_pano_depth_gaussians(
            pano_tensor,
            distance_2d,
            output_width=output_width,
            sampling_rotation=sampling_rotation,
            radius_scale=radius_scale,
            point_scale=point_scale,
            min_scale=min_scale,
            max_scale=max_scale,
            opacity=opacity,
        )
        ply_path = output_dir / output_name
        save_basic_gaussian_ply(pano_depth_gaussians, ply_path)
        summary = {
            "pano": str(Path(args.pano).expanduser()),
            "image": "",
            "cubemap_dir": "",
            "output_ply": str(ply_path),
            "geometry_mode": args.geometry_mode,
            "device": "cpu",
            "depth_source": depth_source,
            "depth_path": str(Path(args.depth_path).expanduser()) if args.depth_path else "",
            "depth_device": str(depth_device) if depth_device is not None else None,
            "pano_depth_width": int(output_width),
            "pano_depth_height": int(max(64, output_width) // 2),
            "pano_depth_radius_scale": float(radius_scale),
            "pano_depth_point_scale": float(point_scale),
            "pano_depth_min_scale": float(min_scale),
            "pano_depth_max_scale": float(max_scale),
            "pano_depth_opacity": float(opacity),
            "front_yaw_deg": args.front_yaw_deg,
            "sphere_correction_deg": {
                "yaw": args.sphere_yaw_deg,
                "pitch": args.sphere_pitch_deg,
                "roll": args.sphere_roll_deg,
            },
            "total_gaussians": int(pano_depth_gaussians.mean_vectors.shape[1]),
            "note": (
                "Equirect RGB + panorama depth -> Gaussian PLY. "
                "Bypasses six-face SHARP geometry for continuous 360 scene splats."
            ),
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return

    single_image_mode = has_image
    depth_source = "constant" if single_image_mode else args.depth_source
    if single_image_mode:
        image = Image.open(args.image).convert("RGB")
        cubemap_rgb = single_image_to_cubemap_faces(
            image,
            args.face_size,
            device,
            face_name=args.single_face_name,
        )
        distance = torch.full(
            (1, args.face_size, args.face_size),
            float(args.depth_meters),
            dtype=torch.float32,
            device=device,
        )
        cubemap_depth = CubemapFaces(
            **{
                name: (distance if name == args.single_face_name else torch.zeros_like(distance))
                for name in CUBEMAP_FACE_NAMES
            }
        )
        depth_device = None
    else:
        image = Image.open(args.pano).convert("RGB")
        image = normalize_pano_2to1(image, output_dir)
        pano_tensor = image_to_tensor(image, device)
        sampling_rotation = pano_sampling_rotation(
            device,
            front_yaw_deg=args.front_yaw_deg,
            sphere_yaw_deg=args.sphere_yaw_deg,
            sphere_pitch_deg=args.sphere_pitch_deg,
            sphere_roll_deg=args.sphere_roll_deg,
        )
        if args.cubemap_dir:
            cubemap_rgb = load_cubemap_faces_from_dir(
                Path(args.cubemap_dir).expanduser(),
                args.face_size,
                device,
            )
        else:
            cubemap_rgb = extract_cubemap_from_equirectangular(
                pano_tensor,
                args.face_size,
                sampling_rotation=sampling_rotation,
            )

        depth_source = args.depth_source
        if args.depth_source == "da2":
            if da2_available():
                depth_device = choose_device(args.depth_device)
                distance_2d = predict_da2_distance(image, depth_device, output_dir).to(device)
                distance = distance_2d.unsqueeze(0)
            else:
                print(
                    "DA-2 package is not installed; falling back to constant depth. "
                    "Geometry quality will be lower.",
                    flush=True,
                )
                depth_device = None
                distance = torch.full(
                    (1, image.height, image.width),
                    float(args.depth_meters),
                    dtype=torch.float32,
                    device=device,
                )
                depth_source = "constant"
        else:
            depth_device = None
            distance = torch.full(
                (1, image.height, image.width),
                float(args.depth_meters),
                dtype=torch.float32,
                device=device,
            )
        cubemap_depth = extract_cubemap_from_equirectangular(
            distance,
            args.face_size,
            sampling_rotation=sampling_rotation,
        )

    default_faces = args.single_face_name if single_image_mode else ",".join(CUBEMAP_FACE_NAMES)
    selected_faces = tuple(
        face.strip() for face in (args.faces or default_faces).split(",") if face.strip()
    )
    unknown_faces = sorted(set(selected_faces) - set(CUBEMAP_FACE_NAMES))
    if unknown_faces:
        raise ValueError(f"Unknown cubemap face(s): {unknown_faces}")
    if single_image_mode and tuple(selected_faces) != (args.single_face_name,):
        raise ValueError("--image mode can only process its --single-face-name face.")
    use_global_depth_align = (
        (not single_image_mode)
        and (not args.no_global_depth_align)
        and isinstance(locals().get("distance"), torch.Tensor)
        and distance.ndim == 3
        and distance.shape[1] > 1
        and distance.shape[2] > 1
    )

    for face_name in CUBEMAP_FACE_NAMES:
        tensor_to_pil(getattr(cubemap_rgb, face_name)).save(faces_dir / f"{face_name}.png")

    predictor = build_sharp_model(args.model_url, device)

    all_positions = []
    all_singular_values = []
    all_quaternions = []
    all_colors = []
    all_opacities = []
    face_stats = []

    print(
        f"Running SHARP on {len(selected_faces)} cubemap face(s), "
        f"face_size={args.face_size}, internal_size={args.internal_size}, device={device}",
        flush=True,
    )
    for index, face_name in enumerate(selected_faces, start=1):
        print(f"[{index}/{len(selected_faces)}] {face_name}", flush=True)
        face_rgb = getattr(cubemap_rgb, face_name)
        face_depth = getattr(cubemap_depth, face_name).squeeze(0)
        gaussians_cam, scale = predict_cubemap_face(
            predictor,
            face_rgb,
            face_depth,
            args.face_size,
            args.internal_size,
            device,
        )
        before_downsample = int(gaussians_cam.mean_vectors.shape[1])
        gaussians_cam = downsample_gaussians(gaussians_cam, args.max_gaussians_per_face)
        after_downsample = int(gaussians_cam.mean_vectors.shape[1])

        extrinsics = get_cubemap_extrinsics(face_name, device)
        world_from_camera = torch.linalg.inv(extrinsics)
        rotation = world_from_camera[:3, :3]

        positions_world = gaussians_cam.mean_vectors @ rotation.T
        quaternions_world = rotate_quaternions(gaussians_cam.quaternions, rotation)
        singular_values = gaussians_cam.singular_values

        global_align_scale = 1.0
        global_align_stats: dict[str, float | int] | None = None
        global_warp_stats: dict[str, float | int] | None = None
        if use_global_depth_align:
            global_align_scale, global_align_stats = estimate_global_depth_alignment_scale(
                positions_world,
                distance,
                sampling_rotation,
                sample_count=int(args.alignment_samples),
                clamp_min=float(args.alignment_clamp_min),
                clamp_max=float(args.alignment_clamp_max),
            )
            positions_world = positions_world * global_align_scale
            singular_values = singular_values * global_align_scale
            positions_world, singular_values, global_warp_stats = apply_global_depth_radial_warp(
                positions_world,
                singular_values,
                distance,
                sampling_rotation,
                strength=float(args.global_depth_warp_strength),
                ratio_clamp_min=float(args.global_depth_ratio_clamp_min),
                ratio_clamp_max=float(args.global_depth_ratio_clamp_max),
            )

        all_positions.append(positions_world.squeeze(0))
        all_singular_values.append(singular_values.squeeze(0))
        all_quaternions.append(quaternions_world.squeeze(0))
        all_colors.append(gaussians_cam.colors.squeeze(0))
        all_opacities.append(gaussians_cam.opacities.squeeze(0))
        face_stats.append(
            {
                "face": face_name,
                "gaussians": after_downsample,
                "gaussians_before_downsample": before_downsample,
                "depth_scale": scale,
                "global_depth_align_scale": global_align_scale,
                "final_depth_scale": scale * global_align_scale,
                "global_depth_align_stats": global_align_stats,
                "global_depth_warp_stats": global_warp_stats,
            }
        )

    if not all_positions:
        raise RuntimeError("No faces were processed.")

    merged = Gaussians3D(
        mean_vectors=torch.cat(all_positions, dim=0).unsqueeze(0),
        singular_values=torch.cat(all_singular_values, dim=0).unsqueeze(0),
        quaternions=torch.cat(all_quaternions, dim=0).unsqueeze(0),
        colors=torch.cat(all_colors, dim=0).unsqueeze(0),
        opacities=torch.cat(all_opacities, dim=0).unsqueeze(0),
    )

    ply_path = output_dir / "pano_sharp_merged.ply"
    save_ply(
        merged,
        f_px=args.face_size / 2.0,
        image_shape=(args.face_size, args.face_size),
        path=ply_path,
    )

    summary = {
        "pano": str(Path(args.pano).expanduser()) if has_pano else "",
        "image": str(Path(args.image).expanduser()) if has_image else "",
        "cubemap_dir": args.cubemap_dir,
        "output_ply": str(ply_path),
        "device": str(device),
        "depth_source": depth_source,
        "depth_device": str(depth_device) if depth_device is not None else None,
        "face_size": args.face_size,
        "internal_size": args.internal_size,
        "depth_meters_constant": args.depth_meters,
        "max_gaussians_per_face": args.max_gaussians_per_face,
        "global_depth_align": use_global_depth_align,
        "alignment_samples": int(args.alignment_samples),
        "alignment_clamp": {
            "min": float(args.alignment_clamp_min),
            "max": float(args.alignment_clamp_max),
        },
        "global_depth_warp_strength": float(args.global_depth_warp_strength),
        "global_depth_ratio_clamp": {
            "min": float(args.global_depth_ratio_clamp_min),
            "max": float(args.global_depth_ratio_clamp_max),
        },
        "front_yaw_deg": args.front_yaw_deg,
        "sphere_correction_deg": {
            "yaw": args.sphere_yaw_deg,
            "pitch": args.sphere_pitch_deg,
            "roll": args.sphere_roll_deg,
        },
        "faces": face_stats,
        "total_gaussians": int(merged.mean_vectors.shape[1]),
        "note": (
            "Single perspective image -> one SHARP face -> PLY."
            if single_image_mode
            else (
                "Uses DA-2 panorama depth for face scale alignment."
                if depth_source == "da2"
                else "Constant-depth mode; replace with DA-2 depth for real global geometry."
            )
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
