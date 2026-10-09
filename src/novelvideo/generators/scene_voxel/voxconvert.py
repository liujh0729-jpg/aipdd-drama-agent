"""vengi-voxconvert wrapper: .vox → .glb (or other formats).

Locates the vengi-voxconvert binary in standard install locations and shells out
to convert. We use vengi for .glb because writing a textured/lit glTF from a .vox
palette is non-trivial; vengi already does it correctly (palette texture + UVs).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


# Standard install locations on macOS
_VENGI_VOXCONVERT_CANDIDATES = [
    "/Applications/vengi-voxconvert.app/Contents/MacOS/vengi-voxconvert",
    "/usr/local/bin/vengi-voxconvert",
    "/opt/homebrew/bin/vengi-voxconvert",
]


def _find_voxconvert() -> str | None:
    # 1. Environment override
    env = os.environ.get("VOXEL_VOXCONVERT_BIN", "").strip()
    if env and Path(env).exists():
        return env
    # 2. PATH lookup
    found = shutil.which("vengi-voxconvert")
    if found:
        return found
    # 3. Standard install candidates
    for c in _VENGI_VOXCONVERT_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def is_available() -> bool:
    """Whether vengi-voxconvert is callable."""
    return _find_voxconvert() is not None


def vox_to_glb(input_vox: Path, output_glb: Path, timeout_seconds: int = 120) -> Path:
    """Convert .vox → .glb via vengi-voxconvert."""
    bin_path = _find_voxconvert()
    if not bin_path:
        raise RuntimeError(
            "vengi-voxconvert not found. Install from https://vengi-voxel.github.io/vengi/ "
            "or set VOXEL_VOXCONVERT_BIN env var."
        )
    input_vox = Path(input_vox)
    output_glb = Path(output_glb)
    output_glb.parent.mkdir(parents=True, exist_ok=True)
    # voxconvert refuses to overwrite — remove first
    if output_glb.exists():
        output_glb.unlink()

    cmd = [
        bin_path,
        "--input", str(input_vox),
        "--output", str(output_glb),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"vengi-voxconvert timed out after {timeout_seconds}s") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"vengi-voxconvert failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    if not output_glb.exists():
        raise RuntimeError(
            f"vengi-voxconvert returned 0 but {output_glb} not produced.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return output_glb
