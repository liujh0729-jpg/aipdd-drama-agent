"""Prepare local-first sketch selection context for skill-driven selection."""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .sketch_edit_tasks import load_script_payload
ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str], None]

SELECT_RUN_DIRNAME = "select_run"
DIRECTOR_RUN_DIRNAME = "storyboard_director_run"
COMPRESSED_DIRNAME = "compressed"
SELECT_RESULT_JSONL_NAME = "select_result.jsonl"
SELECT_SUMMARY_JSON_NAME = "select_summary.json"
DEFAULT_MAX_EDGE = 512
DEFAULT_JPEG_QUALITY = 35
DEFAULT_GRID_CELL_SIZE = (256, 256)


def _extract_scene_id(beat: dict[str, Any]) -> str:
    """Best-effort scene grouping id for a beat.

    Prefers `scene_ref.scene_id` if the beat payload carries it; falls
    back to `time_of_day`-based synthesis so director can still group
    beats without a pre-existing scene_ref.
    """
    scene_ref = beat.get("scene_ref") or beat.get("scene_ref_json")
    if isinstance(scene_ref, dict):
        sid = scene_ref.get("scene_id")
        if sid:
            return str(sid)
    elif isinstance(scene_ref, str) and scene_ref.strip():
        try:
            parsed = json.loads(scene_ref)
            if isinstance(parsed, dict) and parsed.get("scene_id"):
                return str(parsed["scene_id"])
        except json.JSONDecodeError:
            pass
    tod = str(beat.get("time_of_day") or "").strip()
    return f"scene_{tod or 'default'}"


def _is_space_map_beat(beat: dict[str, Any]) -> bool:
    """Legacy Space Map beats are not ordinary storyboard panels."""

    fields = (
        beat.get("narration_segment"),
        beat.get("visual_description"),
        beat.get("audio_type"),
    )
    text = "\n".join(str(value or "") for value in fields)
    markers = ("[SPACE_MAP", "[SPACE_ANCHOR_MAP", "[ABSOLUTE_LAYOUT_MAP")
    return any(marker in text for marker in markers)


def _notify(progress_callback: ProgressCallback | None, progress: float, task: str) -> None:
    if progress_callback is not None:
        progress_callback(progress, task)


def _log(log_callback: LogCallback | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def _prepare_select_run_dir(project_dir: Path, episode_num: int) -> Path:
    run_dir = project_dir / "verify_reports" / f"ep{episode_num:03d}" / SELECT_RUN_DIRNAME
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / COMPRESSED_DIRNAME).mkdir(parents=True, exist_ok=True)
    return run_dir


_PRESERVE_ACROSS_RUN_REBUILD = (
    # Human notes may survive a context refresh.
    "storyboard_director_notes.md",
)


def _prepare_storyboard_director_run_dir(project_dir: Path, episode_num: int) -> Path:
    run_dir = project_dir / "verify_reports" / f"ep{episode_num:03d}" / DIRECTOR_RUN_DIRNAME
    if run_dir.exists():
        # Preserve project-fact files before wiping disposable artifacts.
        preserved: list[tuple[str, bytes]] = []
        for name in _PRESERVE_ACROSS_RUN_REBUILD:
            candidate = run_dir / name
            if candidate.is_file():
                preserved.append((name, candidate.read_bytes()))
        shutil.rmtree(run_dir)
        (run_dir / COMPRESSED_DIRNAME).mkdir(parents=True, exist_ok=True)
        for name, data in preserved:
            (run_dir / name).write_bytes(data)
    else:
        (run_dir / COMPRESSED_DIRNAME).mkdir(parents=True, exist_ok=True)
    return run_dir


def _move_previous_select_results(project_dir: Path, episode_num: int, run_dir: Path) -> dict[str, str]:
    reports_dir = project_dir / "verify_reports" / f"ep{episode_num:03d}"
    moved: dict[str, str] = {
        "previous_select_result_jsonl_path": "",
        "previous_select_summary_json_path": "",
        "previous_select_result_path": "",
    }

    current_jsonl = reports_dir / SELECT_RESULT_JSONL_NAME
    if current_jsonl.exists():
        backup_jsonl = run_dir / f"previous_{SELECT_RESULT_JSONL_NAME}"
        shutil.move(str(current_jsonl), str(backup_jsonl))
        moved["previous_select_result_jsonl_path"] = str(backup_jsonl.resolve())
        moved["previous_select_result_path"] = moved["previous_select_result_jsonl_path"]

    current_summary = reports_dir / SELECT_SUMMARY_JSON_NAME
    if current_summary.exists():
        backup_summary = run_dir / f"previous_{SELECT_SUMMARY_JSON_NAME}"
        shutil.move(str(current_summary), str(backup_summary))
        moved["previous_select_summary_json_path"] = str(backup_summary.resolve())

    legacy_json = reports_dir / "select_result.json"
    if legacy_json.exists():
        backup_legacy = run_dir / "previous_select_result.json"
        shutil.move(str(legacy_json), str(backup_legacy))
        if not moved["previous_select_result_path"]:
            moved["previous_select_result_path"] = str(backup_legacy.resolve())

    return moved


def _compress_image(src_path: Path, dst_path: Path, max_edge: int, quality: int) -> Path:
    with Image.open(src_path) as img:
        if img.mode in {"RGBA", "P"}:
            img = img.convert("RGB")
        else:
            img = img.copy()
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst_path, format="JPEG", quality=quality, optimize=True)
    return dst_path


def _choose_grid_dims(image_count: int) -> tuple[int, int]:
    if image_count <= 0:
        return 1, 1
    cols = max(1, math.ceil(math.sqrt(image_count)))
    rows = math.ceil(image_count / cols)
    return rows, cols


def _build_overview_grid_preserve_aspect(
    images: list[Path],
    output_path: Path,
    *,
    rows: int,
    cols: int,
    cell_size: tuple[int, int],
) -> Path:
    cell_width, cell_height = cell_size
    grid = Image.new("RGB", (cols * cell_width, rows * cell_height), color=(18, 18, 18))

    for index, image_path in enumerate(images):
        if index >= rows * cols:
            break
        with Image.open(image_path) as img:
            if img.mode in {"RGBA", "P"}:
                img = img.convert("RGB")
            else:
                img = img.copy()
            img.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
            x = (index % cols) * cell_width + (cell_width - img.width) // 2
            y = (index // cols) * cell_height + (cell_height - img.height) // 2
            grid.paste(img, (x, y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(output_path, format="JPEG", quality=DEFAULT_JPEG_QUALITY, optimize=True)
    return output_path


def prepare_sketch_select_context(
    *,
    project_dir: Path,
    episode_num: int,
    output_path: Path,
    include_stale: bool = False,
    limit: int = 0,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict[str, Any]:
    """Write a local-first select context JSON for skill-driven episode selection.

    The default flow is "current image only":
    - clear and rebuild verify_reports/epXXX/select_run/
    - copy no historical candidates into the default context
    - heavily compress current selected sketches
    """

    del include_stale  # Historical pool candidates are intentionally excluded by default.

    project_dir = project_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    _notify(progress_callback, 0.05, "读取脚本")
    payload = load_script_payload(project_dir, episode_num)
    payload.setdefault("episode_number", episode_num)
    beats = [beat for beat in list(payload.get("beats") or []) if not _is_space_map_beat(beat)]
    if limit > 0:
        beats = beats[:limit]

    _notify(progress_callback, 0.18, "准备 select_run 目录")
    run_dir = _prepare_select_run_dir(project_dir, episode_num)
    compressed_dir = run_dir / COMPRESSED_DIRNAME
    previous_select_results = _move_previous_select_results(project_dir, episode_num, run_dir)

    selected_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    beat_rows: list[dict[str, Any]] = []
    total = len(beats) or 1

    for index, beat in enumerate(beats, start=1):
        beat_number = int(beat.get("beat_number") or 0)
        if beat_number <= 0:
            continue
        _notify(progress_callback, 0.18 + (index / total) * 0.62, f"压缩 beat {beat_number} 当前草图")
        current_selected = selected_dir / f"beat_{beat_number:02d}.png"
        compressed_path = compressed_dir / f"beat_{beat_number:02d}.jpg"
        current_selected_path = ""
        current_selected_compressed_path = ""
        candidate_rows: list[dict[str, Any]] = []

        if current_selected.exists():
            current_selected_path = str(current_selected.resolve())
            _compress_image(
                current_selected,
                compressed_path,
                max_edge=DEFAULT_MAX_EDGE,
                quality=DEFAULT_JPEG_QUALITY,
            )
            current_selected_compressed_path = str(compressed_path.resolve())
            candidate_rows.append(
                {
                    "pool_id": "current_selected",
                    "path": current_selected_path,
                    "compressed_path": current_selected_compressed_path,
                    "source": "current_selected",
                    "stale": False,
                }
            )

        beat_rows.append(
            {
                "beat_number": beat_number,
                "narration_segment": beat.get("narration_segment", ""),
                "visual_description": beat.get("visual_description", ""),
                "location": beat.get("location", ""),
                "time_of_day": beat.get("time_of_day", ""),
                "audio_type": beat.get("audio_type", ""),
                "speaker": beat.get("speaker", ""),
                "set_description": beat.get("set_description", ""),
                "current_selected_path": current_selected_path,
                "current_selected_compressed_path": current_selected_compressed_path,
                "candidates": candidate_rows,
            }
        )

    _notify(progress_callback, 0.88, "写入 select_context.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "project_dir": str(project_dir),
        "episode_num": episode_num,
        "episode_title": payload.get("title", ""),
        "sketch_colors": payload.get("sketch_colors", {}),
        "select_run_dir": str(run_dir.resolve()),
        **previous_select_results,
        "compression": {
            "max_edge": DEFAULT_MAX_EDGE,
            "jpeg_quality": DEFAULT_JPEG_QUALITY,
            "grid_cell_size": list(DEFAULT_GRID_CELL_SIZE),
            "policy": "current-only, heavily compressed for low-token select review",
        },
        "beats": beat_rows,
        "selection_output_schema": {
            "row_shape": {
                "beat_number": 1,
                "selected_pool_id": "current_selected",
                "keepability_score": 0.82,
                "observed_image_summary": "Describe only what is actually visible in the sketch.",
                "mismatch_summary": "Optional for accept; required when the image materially disagrees with the beat.",
                "reason": "1-3 concise sentences",
                "recommended_action": "accept|edit",
                "edit_mode": "polish (optional metadata when recommended_action=edit)",
            },
            "summary_shape": {
                "project_dir": str(project_dir),
                "episode_num": episode_num,
                "summary": "10 beats accept, 5 beats edit",
                "beat_count": len(beat_rows),
                "accept_count": 10,
                "edit_count": 5,
                "output_jsonl": str((project_dir / "verify_reports" / f"ep{episode_num:03d}" / SELECT_RESULT_JSONL_NAME).resolve()),
            },
        },
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "project_dir": str(project_dir),
        "episode_num": episode_num,
        "beat_count": len(beat_rows),
        "select_run_dir": str(run_dir),
        **previous_select_results,
        "output_json": str(output_path),
    }
    _log(log_callback, f"已生成 {len(beat_rows)} 条 current-only select context")
    _notify(progress_callback, 1.0, "完成")
    return summary


def prepare_storyboard_director_context(
    *,
    project_dir: Path,
    episode_num: int,
    output_path: Path,
    limit: int = 0,
    beat_numbers: list[int] | None = None,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict[str, Any]:
    """Write a directing context JSON with overview grid plus per-beat compressed images."""

    project_dir = project_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    _notify(progress_callback, 0.05, "读取脚本")
    payload = load_script_payload(project_dir, episode_num)
    payload.setdefault("episode_number", episode_num)
    beats = [beat for beat in list(payload.get("beats") or []) if not _is_space_map_beat(beat)]
    if beat_numbers is not None:
        requested = {int(num) for num in beat_numbers}
        beats = [beat for beat in beats if int(beat.get("beat_number") or 0) in requested]
    if limit > 0:
        beats = beats[:limit]

    _notify(progress_callback, 0.16, "准备 storyboard director 目录")
    run_dir = _prepare_storyboard_director_run_dir(project_dir, episode_num)
    compressed_dir = run_dir / COMPRESSED_DIRNAME

    selected_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    beat_rows: list[dict[str, Any]] = []
    compressed_paths: list[Path] = []
    total = len(beats) or 1

    for index, beat in enumerate(beats, start=1):
        beat_number = int(beat.get("beat_number") or 0)
        if beat_number <= 0:
            continue
        _notify(progress_callback, 0.16 + (index / total) * 0.5, f"压缩 beat {beat_number} 当前草图")
        current_selected = selected_dir / f"beat_{beat_number:02d}.png"
        compressed_path = compressed_dir / f"beat_{beat_number:02d}.jpg"
        current_selected_path = ""
        current_selected_compressed_path = ""

        if current_selected.exists():
            current_selected_path = str(current_selected.resolve())
            _compress_image(
                current_selected,
                compressed_path,
                max_edge=DEFAULT_MAX_EDGE,
                quality=DEFAULT_JPEG_QUALITY,
            )
            current_selected_compressed_path = str(compressed_path.resolve())
            compressed_paths.append(compressed_path)

        beat_rows.append(
            {
                "beat_number": beat_number,
                "narration_segment": beat.get("narration_segment", ""),
                "visual_description": beat.get("visual_description", ""),
                "audio_type": beat.get("audio_type", ""),
                "speaker": beat.get("speaker", ""),
                "location": beat.get("location", ""),
                "time_of_day": beat.get("time_of_day", ""),
                "scene_id": _extract_scene_id(beat),
                "current_selected_path": current_selected_path,
                "current_selected_compressed_path": current_selected_compressed_path,
            }
        )

    _notify(progress_callback, 0.75, "生成 overview grid")
    overview_grid_path = run_dir / "overview_grid.jpg"
    overview_grid = ""
    if compressed_paths:
        rows, cols = _choose_grid_dims(len(compressed_paths))
        _build_overview_grid_preserve_aspect(
            compressed_paths,
            overview_grid_path,
            rows=rows,
            cols=cols,
            cell_size=DEFAULT_GRID_CELL_SIZE,
        )
        overview_grid = str(overview_grid_path.resolve())

    _notify(progress_callback, 0.9, "写入 director_context.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "project_dir": str(project_dir),
        "episode_num": episode_num,
        "episode_title": payload.get("title", ""),
        "storyboard_director_run_dir": str(run_dir.resolve()),
        "overview_grid_path": overview_grid,
        "compression": {
            "max_edge": DEFAULT_MAX_EDGE,
            "jpeg_quality": DEFAULT_JPEG_QUALITY,
            "grid_cell_size": list(DEFAULT_GRID_CELL_SIZE),
            "policy": "overview-first directing review, then per-beat drill-down",
        },
        "beats": beat_rows,
        "output_target": str(
            project_dir / "verify_reports" / f"ep{episode_num:03d}" / "storyboard_director_notes.md"
        ),
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "project_dir": str(project_dir),
        "episode_num": episode_num,
        "beat_count": len(beat_rows),
        "storyboard_director_run_dir": str(run_dir),
        "overview_grid_path": overview_grid,
        "output_json": str(output_path),
    }
    _log(log_callback, f"已生成 {len(beat_rows)} 条 storyboard director context")
    _notify(progress_callback, 1.0, "完成")
    return summary
