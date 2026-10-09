"""Scene voxel world generation pipeline (per-scene AI-authored script).

End-to-end:
    master.png + reverse_master.png
        ↓ k-means palette extraction (hint to the agent)
        ↓ codegen_agent (pydantic-ai, gpt-5.5 by default)
    build_script.py    ← AI-written Python, scene-specific, is the SSOT
        ↓ subprocess: python build_script.py world.vox
    world.vox (MagicaVoxel)
        ↓ vengi-voxconvert
    world.glb (glTF, loads in three.js / model-viewer / Blender)

Outputs written to: <project_dir>/assets/scenes/<scene_name>/voxel_world/
    build_script.py — AI-authored Python (re-runnable; human-editable)
    palette.json    — extracted hint palette
    world.vox       — voxel output
    world.glb       — glTF for browser viewer
    codegen_input/master.png, reverse_master.png  — frozen copies of refs

Iterating: edit build_script.py by hand and re-run; or trigger regen which
re-invokes the codegen agent (overwrites the .py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from novelvideo.generators.scene_voxel.codegen_agent import generate_build_script
from novelvideo.generators.scene_voxel.palette import extract_palette
from novelvideo.generators.scene_voxel.voxconvert import is_available, vox_to_glb


__all__ = [
    "generate_scene_voxel_world",
    "voxel_world_output_dir",
]


def voxel_world_output_dir(project_dir: Path, scene_name: str) -> Path:
    return Path(project_dir) / "assets" / "scenes" / scene_name / "voxel_world"


def _run_build_script(script_path: Path, vox_path: Path, timeout: int = 300) -> None:
    """Execute AI-authored build script in subprocess. Captures stdout/stderr."""
    if vox_path.exists():
        vox_path.unlink()
    cmd = [sys.executable, str(script_path), str(vox_path)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"build_script.py exceeded {timeout}s timeout — script may have an "
            f"infinite loop or be drawing too many voxels"
        ) from e

    if result.returncode != 0:
        # Surface the AI script error to the user so they can see what went wrong
        stderr_tail = "\n".join(result.stderr.splitlines()[-40:])
        raise RuntimeError(
            f"build_script.py failed (rc={result.returncode}):\n"
            f"--- last 40 lines of stderr ---\n{stderr_tail}\n"
            f"--- stdout tail ---\n{result.stdout[-1000:]}"
        )
    if not vox_path.exists():
        raise RuntimeError(
            f"build_script.py returned 0 but did not write {vox_path}. "
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


async def generate_scene_voxel_world(
    *,
    project_dir: Path,
    scene: Any,
    master_path: Path,
    reverse_path: Path | None = None,
    palette_k: int = 28,
    log: Callable[[str], None] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Generate scene voxel world via AI codegen.

    Returns dict with: script_path, vox_path, glb_path, palette_json_path,
    voxel_count (best-effort from .vox size).
    """
    project_dir = Path(project_dir)
    master_path = Path(master_path)
    if not master_path.exists():
        raise FileNotFoundError(f"master.png not found: {master_path}")

    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _progress(p: float, msg: str) -> None:
        if progress:
            progress(p, msg)

    scene_name = str(getattr(scene, "name", "") or "unknown")
    out_dir = voxel_world_output_dir(project_dir, scene_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    _progress(0.05, "检查 vengi-voxconvert...")
    if not is_available():
        raise RuntimeError(
            "vengi-voxconvert not found. Install from https://vengi-voxel.github.io/vengi/"
        )

    _progress(0.10, "k-means 提取参考图调色板...")
    image_paths = [master_path]
    if reverse_path and Path(reverse_path).exists():
        image_paths.append(Path(reverse_path))
    palette = extract_palette(image_paths, k=palette_k)
    palette_json_path = out_dir / "palette.json"
    palette_json_path.write_text(
        json.dumps(palette, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _log(f"调色板提示 {len(palette)} 色 → {palette_json_path}")

    _progress(0.25, "VLM 写 Python 构建脚本（master + reverse → build_script.py）...")
    _log("调用 codegen agent (master + reverse → Python source)...")
    script_source = await generate_build_script(
        scene=scene,
        master_path=master_path,
        reverse_path=reverse_path,
        hint_palette=palette,
    )
    script_path = out_dir / "build_script.py"
    script_path.write_text(script_source, encoding="utf-8")
    _log(
        f"build_script.py 写入 {script_path} ({len(script_source)} chars, "
        f"{script_source.count(chr(10))} lines)"
    )

    _progress(0.55, "subprocess 执行构建脚本 → world.vox...")
    vox_path = out_dir / "world.vox"
    _run_build_script(script_path, vox_path, timeout=300)
    vox_size = vox_path.stat().st_size
    _log(f"world.vox: {vox_path} ({vox_size} bytes)")

    _progress(0.85, "vengi-voxconvert: .vox → .glb...")
    glb_path = out_dir / "world.glb"
    vox_to_glb(vox_path, glb_path)
    _log(f"world.glb: {glb_path} ({glb_path.stat().st_size} bytes)")

    _progress(1.0, "完成")
    return {
        "script_path": str(script_path),
        "vox_path": str(vox_path),
        "glb_path": str(glb_path),
        "palette_json_path": str(palette_json_path),
        "vox_size_bytes": vox_size,
    }
