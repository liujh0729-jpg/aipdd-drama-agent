"""Render repair 的本地上下文准备。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from novelvideo.models import real_detected_identities
from novelvideo.task_identity import selection_scope

from .episode_reviewer import _build_numbered_grid
from .utils import (
    compress_image,
    find_active_sketch_for_beat,
    find_frame_for_beat,
    safe_resolve_under,
)
from .sketch_edit_tasks import load_script_payload

_MODE_FOR_GROUP_SIZE = {
    1: "1x1_2-3",
    2: "1x2_4-3",
    3: "2x2_2-3",
    4: "2x2_2-3",
    5: "2x3_1-1",
    6: "2x3_1-1",
}


@dataclass(slots=True)
class RenderRepairEntry:
    beat_number: int
    beat_data: dict
    frame_path: Path
    sketch_path: Path | None
    detected_identities: list[str]
    render_reference_ready: bool


def get_render_repair_run_dir(project_dir: Path, episode_num: int) -> Path:
    return Path(project_dir) / "verify_reports" / f"ep{episode_num:03d}" / "render_repair_run"


def load_render_repair_context(project_dir: Path, episode_num: int) -> dict:
    context_path = get_render_repair_run_dir(project_dir, episode_num) / "render_repair_context.json"
    if not context_path.exists():
        raise FileNotFoundError(f"Render repair context not found: {context_path}")
    return json.loads(context_path.read_text(encoding="utf-8"))


def split_bad_beats_into_regen_groups(
    bad_beats: list[int] | tuple[int, ...],
) -> list[tuple[str, list[int]]]:
    normalized = sorted({int(beat) for beat in bad_beats if int(beat) > 0})
    groups: list[tuple[str, list[int]]] = []
    remaining = list(normalized)

    while remaining:
        if len(remaining) > 6:
            groups.append(("2x3_1-1", remaining[:6]))
            remaining = remaining[6:]
            continue
        mode_key = _MODE_FOR_GROUP_SIZE[len(remaining)]
        groups.append((mode_key, list(remaining)))
        remaining = []

    return groups


def collect_active_render_entries(
    project_dir: Path,
    episode_num: int,
    beats: list[dict],
) -> list[RenderRepairEntry]:
    project_dir = Path(project_dir)
    rows: list[RenderRepairEntry] = []
    for beat in beats:
        beat_number = int(beat.get("beat_number") or 0)
        if beat_number <= 0:
            continue

        frame_path = find_frame_for_beat(project_dir, episode_num, beat_number)
        if not frame_path:
            continue
        safe_frame_path = safe_resolve_under(project_dir, frame_path)
        if not safe_frame_path:
            continue

        sketch_path = find_active_sketch_for_beat(project_dir, episode_num, beat_number)
        safe_sketch_path = safe_resolve_under(project_dir, sketch_path) if sketch_path else None
        detected_identities = real_detected_identities(beat.get("detected_identities") or [])
        rows.append(
            RenderRepairEntry(
                beat_number=beat_number,
                beat_data=beat,
                frame_path=safe_frame_path,
                sketch_path=safe_sketch_path,
                detected_identities=detected_identities,
                render_reference_ready=bool(detected_identities),
            )
        )
    return rows


def compute_missing_render_beats(
    beats: list[dict],
    entries: list[RenderRepairEntry],
) -> list[int]:
    beat_numbers = sorted(
        {
            int(beat.get("beat_number") or 0)
            for beat in beats
            if int(beat.get("beat_number") or 0) > 0
        }
    )
    active_numbers = {entry.beat_number for entry in entries}
    return [beat_number for beat_number in beat_numbers if beat_number not in active_numbers]


def build_render_repair_context(
    *,
    project_dir: Path,
    episode_num: int,
    beat_numbers: list[int] | None = None,
) -> dict:
    project_dir = Path(project_dir)
    payload = load_script_payload(project_dir, episode_num)
    beats = list(payload.get("beats") or [])
    if beat_numbers is not None:
        requested = {int(beat_number) for beat_number in beat_numbers}
        beats = [beat for beat in beats if int(beat.get("beat_number") or 0) in requested]

    entries = collect_active_render_entries(project_dir, episode_num, beats)
    run_dir = get_render_repair_run_dir(project_dir, episode_num)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    compressed_dir = run_dir / "compressed"
    compressed_dir.mkdir(parents=True, exist_ok=True)

    overview_inputs: list[tuple[int, Path]] = []
    beat_rows: list[dict] = []

    for entry in entries:
        frame_compressed_path = compressed_dir / f"frame_{entry.beat_number:02d}.jpg"
        frame_compressed_path.write_bytes(
            compress_image(str(entry.frame_path), quality=35, max_long_edge=512)
        )

        sketch_compressed_path: Path | None = None
        if entry.sketch_path:
            sketch_compressed_path = compressed_dir / f"sketch_{entry.beat_number:02d}.jpg"
            sketch_compressed_path.write_bytes(
                compress_image(str(entry.sketch_path), quality=35, max_long_edge=512)
            )

        overview_inputs.append((entry.beat_number, entry.frame_path))
        beat_rows.append(
            {
                "beat_number": entry.beat_number,
                "visual_description": entry.beat_data.get("visual_description", ""),
                "narration_segment": entry.beat_data.get("narration_segment", ""),
                "detected_identities": entry.detected_identities,
                "render_reference_ready": entry.render_reference_ready,
                "frame_path": str(entry.frame_path.resolve()),
                "frame_compressed_path": str(frame_compressed_path.resolve()),
                "sketch_path": str(entry.sketch_path.resolve()) if entry.sketch_path else "",
                "sketch_compressed_path": str(sketch_compressed_path.resolve()) if sketch_compressed_path else "",
            }
        )

    overview_grid_path = run_dir / "overview_grid.jpg"
    if overview_inputs:
        _build_numbered_grid(overview_inputs, overview_grid_path)
    else:
        overview_grid_path.parent.mkdir(parents=True, exist_ok=True)

    context = {
        "project_dir": str(project_dir.resolve()),
        "episode_num": episode_num,
        "beat_count": len(beat_rows),
        "beats": beat_rows,
        "overview_grid_path": str(overview_grid_path.resolve()) if overview_inputs else "",
        "missing_render_beats": compute_missing_render_beats(beats, entries),
    }
    context_path = run_dir / "render_repair_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "beat_count": len(beat_rows),
        "run_dir": str(run_dir.resolve()),
        "context_path": str(context_path.resolve()),
    }


def prepare_render_regen_task_plan(
    *,
    project_dir: Path,
    episode_num: int,
    bad_beat_numbers: list[int] | tuple[int, ...],
) -> dict:
    project_dir = Path(project_dir)
    run_dir = get_render_repair_run_dir(project_dir, episode_num)
    context = load_render_repair_context(project_dir, episode_num)
    context_path = run_dir / "render_repair_context.json"
    analyze_path = run_dir / "render_analyze_result.json"
    task_path = run_dir / "render_regen_tasks.json"

    requested_bad_beats = sorted({int(beat) for beat in bad_beat_numbers if int(beat) > 0})
    beat_rows = list(context.get("beats") or [])
    beat_map = {int(row.get("beat_number") or 0): row for row in beat_rows}

    blocked_beats: list[dict] = []
    executable_beats: list[int] = []

    for beat_number in requested_bad_beats:
        row = beat_map.get(beat_number)
        if not row:
            blocked_beats.append(
                {
                    "beat_number": beat_number,
                    "render_reference_ready": False,
                    "detected_identities": [],
                    "reasons": [
                        {
                            "code": "beat_not_in_context",
                            "message": "Beat is missing from render_repair_context.json",
                        }
                    ],
                }
            )
            continue

        detected_identities = real_detected_identities(row.get("detected_identities") or [])
        render_reference_ready = bool(row.get("render_reference_ready"))
        if not render_reference_ready:
            blocked_beats.append(
                {
                    "beat_number": beat_number,
                    "render_reference_ready": False,
                    "detected_identities": detected_identities,
                    "reasons": [
                        {
                            "code": "render_reference_not_ready",
                            "message": "No detected identities available for render preflight",
                        }
                    ],
                }
            )
            continue

        executable_beats.append(beat_number)

    executable_batches = []
    for batch_index, (mode_key, beat_indices) in enumerate(
        split_bad_beats_into_regen_groups(executable_beats),
        start=1,
    ):
        executable_batches.append(
            {
                "batch_index": batch_index,
                "mode_key": mode_key,
                "beat_indices": beat_indices,
                "scope": selection_scope(mode_key, beat_indices),
            }
        )

    plan = {
        "episode_num": episode_num,
        "requested_bad_beats": requested_bad_beats,
        "context_path": str(context_path.resolve()),
        "analysis_path": str(analyze_path.resolve()),
        "blocked_beats": blocked_beats,
        "executable_batches": executable_batches,
        "all_blocked": bool(requested_bad_beats) and not executable_batches,
    }
    task_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan
