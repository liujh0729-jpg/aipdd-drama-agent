"""Prepare episode-level sketch edit teacher tasks from a SuperTale project."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str], None]
SELECT_RESULT_JSONL_NAME = "select_result.jsonl"
SELECT_SUMMARY_JSON_NAME = "select_summary.json"


def _notify(progress_callback: ProgressCallback | None, progress: float, task: str) -> None:
    if progress_callback is not None:
        progress_callback(progress, task)


def _log(log_callback: LogCallback | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def truncate_text(value: Any, max_chars: int) -> str:
    text = clean_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


IDENTITY_REF_RE = re.compile(r"\{\{([^{}]+)\}\}")


def load_script_payload(project_dir: Path, episode_num: int) -> dict[str, Any]:
    sqlite_payload = _load_sqlite_script_payload(project_dir, episode_num)
    if sqlite_payload is not None:
        return sqlite_payload
    raise FileNotFoundError(
        f"SQLite beat source not found for episode {episode_num}: {_project_sqlite_path(project_dir)}"
    )


def _project_sqlite_path(project_dir: Path) -> Path:
    from novelvideo.config import OUTPUT_DIR, STATE_DIR

    resolved_project_dir = Path(project_dir).resolve()
    path_parts = resolved_project_dir.parts
    if "output" in path_parts:
        output_index = path_parts.index("output")
        if len(path_parts) >= output_index + 3:
            repo_root = Path(*path_parts[:output_index])
            user = path_parts[output_index + 1]
            project = path_parts[output_index + 2]
            return repo_root / "state" / user / project / "data.db"
    try:
        relative_project = resolved_project_dir.relative_to(Path(OUTPUT_DIR).resolve())
    except ValueError:
        raise FileNotFoundError(f"Project dir is not under OUTPUT_DIR: {resolved_project_dir}")
    if len(relative_project.parts) < 2:
        raise FileNotFoundError(f"Cannot resolve project state db from: {resolved_project_dir}")
    return Path(STATE_DIR).resolve().joinpath(*relative_project.parts) / "data.db"


def _load_sqlite_script_payload(project_dir: Path, episode_num: int) -> dict[str, Any] | None:
    try:
        db_path = _project_sqlite_path(project_dir)
    except FileNotFoundError:
        return None
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            beat_rows = conn.execute(
                """
                SELECT * FROM beats
                WHERE episode_number = ?
                ORDER BY COALESCE(shot_order, beat_number * 10), beat_number
                """,
                (episode_num,),
            ).fetchall()
            if not beat_rows:
                return None
            episode_row = conn.execute(
                "SELECT * FROM episodes WHERE number = ?",
                (episode_num,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None

    beats: list[dict[str, Any]] = []
    for row in beat_rows:
        scene_ref: dict[str, Any] | None = None
        scene_ref_json = row["scene_ref_json"] if "scene_ref_json" in row.keys() else ""
        if scene_ref_json:
            try:
                parsed = json.loads(scene_ref_json)
                if isinstance(parsed, dict):
                    scene_ref = parsed
            except json.JSONDecodeError:
                scene_ref = None
        detected_props: list[str] = []
        detected_props_json = row["detected_props_json"] if "detected_props_json" in row.keys() else "[]"
        try:
            parsed_props = json.loads(detected_props_json or "[]")
            if isinstance(parsed_props, list):
                detected_props = [str(item) for item in parsed_props]
        except json.JSONDecodeError:
            detected_props = []
        beats.append(
            {
                "beat_number": int(row["beat_number"]),
                "narration_segment": row["narration"] or "",
                "visual_description": row["visual_description"] or "",
                "time_of_day": row["time_of_day"] if "time_of_day" in row.keys() else "",
                "scene_ref": scene_ref,
                "audio_type": row["audio_type"] or "narration",
                "speaker": row["speaker"] or "",
                "speaker_kind": (
                    row["speaker_kind"] if "speaker_kind" in row.keys() else "character"
                )
                or "character",
                "video_mode": row["video_mode"] if "video_mode" in row.keys() else "first_frame",
                "video_prompt": row["video_prompt"] if "video_prompt" in row.keys() else "",
                "keyframe_prompt": row["keyframe_prompt"] if "keyframe_prompt" in row.keys() else "",
                "shot_order": row["shot_order"] if "shot_order" in row.keys() else None,
                "duration_seconds": row["duration_seconds"] if "duration_seconds" in row.keys() else None,
                "is_manual_shot": bool(row["is_manual_shot"]) if "is_manual_shot" in row.keys() else False,
                "detected_props": detected_props,
                "detected_props_json": json.dumps(detected_props, ensure_ascii=False),
            }
        )

    payload: dict[str, Any] = {
        "episode_number": episode_num,
        "beats": beats,
        "source": "sqlite",
    }
    if episode_row is not None:
        payload["title"] = episode_row["title"] or ""
        sketch_colors_json = (
            episode_row["sketch_colors_json"]
            if "sketch_colors_json" in episode_row.keys()
            else "{}"
        )
        try:
            sketch_colors = json.loads(sketch_colors_json or "{}")
            if isinstance(sketch_colors, dict):
                payload["sketch_colors"] = sketch_colors
        except json.JSONDecodeError:
            pass
    return payload


def format_identity_colors(mapping: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for identity, raw in sorted((mapping or {}).items()):
        text = clean_text(raw)
        color_value = ""
        color_name = text
        if text.startswith("#"):
            parts = text.split(" ", 1)
            color_value = parts[0]
            color_name = parts[1] if len(parts) > 1 else parts[0]
        rows.append(
            {
                "identity": str(identity),
                "color_name": color_name,
                "color_value": color_value,
                "raw": text,
            }
        )
    return rows


def filter_identity_colors_for_beat(
    mapping: dict[str, Any],
    *,
    narration_segment: str,
    visual_description: str,
) -> list[dict[str, str]]:
    mentioned: set[str] = set()
    for text in (visual_description, narration_segment):
        for match in IDENTITY_REF_RE.findall(str(text or "")):
            identity = clean_text(match)
            if identity:
                mentioned.add(identity)
    all_rows = format_identity_colors(mapping)
    if not mentioned:
        return []
    return [row for row in all_rows if row.get("identity") in mentioned]


def load_selected_sketch_paths(project_dir: Path, episode_num: int) -> dict[int, str]:
    sketch_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    if not sketch_dir.exists():
        return {}
    selected: dict[int, str] = {}
    for path in sorted(sketch_dir.glob("beat_*.png")):
        stem = path.stem
        try:
            beat_number = int(stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        selected[beat_number] = str(path.resolve())
    return selected


def load_select_result_rows(project_dir: Path, episode_num: int) -> tuple[list[dict[str, Any]], str]:
    reports_dir = project_dir / "verify_reports" / f"ep{episode_num:03d}"

    jsonl_path = reports_dir / SELECT_RESULT_JSONL_NAME
    if jsonl_path.exists():
        summary_path = reports_dir / SELECT_SUMMARY_JSON_NAME
        if not summary_path.exists():
            raise ValueError(
                f"{jsonl_path} exists but {summary_path} is missing; rerun select before preparing tasks"
            )
        rows: list[dict[str, Any]] = []
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                row = json.loads(text)
                if isinstance(row, dict):
                    rows.append(row)

        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary_payload, dict):
            raise ValueError(f"Unsupported select summary shape: {summary_path}")
        accept_count = int(summary_payload.get("accept_count") or 0)
        edit_count = int(summary_payload.get("edit_count") or 0)
        beat_count = int(summary_payload.get("beat_count") or 0)
        expected_rows = accept_count + edit_count
        if expected_rows != len(rows):
            raise ValueError(
                f"Select result row count mismatch for episode {episode_num}: "
                f"{len(rows)} rows in {jsonl_path.name}, expected {expected_rows} from {summary_path.name}"
            )
        script_payload = load_script_payload(project_dir, episode_num)
        script_beat_count = len(list(script_payload.get("beats") or []))
        if beat_count <= 0 or beat_count != script_beat_count:
            raise ValueError(
                f"Select summary beat_count mismatch for episode {episode_num}: "
                f"{summary_path.name} says {beat_count}, current script beat count is {script_beat_count}"
            )
        return rows, SELECT_RESULT_JSONL_NAME

    legacy_json_path = reports_dir / "select_result.json"
    if legacy_json_path.exists():
        payload = json.loads(legacy_json_path.read_text(encoding="utf-8"))
        beats = payload.get("beats") if isinstance(payload, dict) else None
        if isinstance(beats, list):
            return [row for row in beats if isinstance(row, dict)], "select_result.json"

    return [], ""


def build_task(
    project_dir: Path,
    project_id: str,
    payload: dict[str, Any],
    beat: dict[str, Any],
    selected_map: dict[int, str],
) -> dict[str, Any]:
    beat_number = int(beat.get("beat_number") or 0)
    narration_segment = beat.get("narration_segment", "")
    visual_description = beat.get("visual_description", "")
    return {
        "task_type": "sketch_edit_teacher_label",
        "task_id": f"{project_id}-ep{int(payload.get('episode_number') or 0):03d}-b{beat_number:03d}",
        "project_dir": str(project_dir.resolve()),
        "episode_num": int(payload.get("episode_number") or 0),
        "beat_number": beat_number,
        "execution_mode": "polish",
        "sketch_path": selected_map[beat_number],
        "narration_segment": narration_segment,
        "visual_description": visual_description,
        "sketch_colors": filter_identity_colors_for_beat(
            payload.get("sketch_colors") or {},
            narration_segment=narration_segment,
            visual_description=visual_description,
        ),
        "teacher_output_schema": {
            "execution_mode": "polish",
            "decision": "revise",
            "main_problem": (
                "identity_color_mismatch|staging_unclear|"
                "scene_mismatch|character_count_wrong|pose_action_wrong|null"
            ),
            "reasoning": "1-3 concise sentences, <= 150 chars",
            "edit_instruction": (
                "nanobanana-ready edit prompt, about 120-180 chars; "
                "if identities are mentioned, use exact identity + hex color"
            ),
            "confidence": 0.92,
        },
    }


def prepare_sketch_edit_tasks(
    *,
    project_dir: Path,
    episode_num: int,
    output_path: Path,
    limit: int = 0,
    beat_numbers: list[int] | None = None,
    project_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict[str, Any]:
    """Build `tasks.jsonl` for sketch edit labeling and return the summary payload."""

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _notify(progress_callback, 0.05, "读取脚本")
    payload = load_script_payload(project_dir, episode_num)
    payload.setdefault("episode_number", episode_num)

    _notify(progress_callback, 0.15, "读取已选草图")
    selected_map = load_selected_sketch_paths(project_dir, episode_num)
    if not selected_map:
        raise FileNotFoundError(
            f"No selected sketches found for episode {episode_num} in {project_dir}"
        )

    selected_beat_numbers = sorted(selected_map)
    task_source = "selected_sketches"
    if beat_numbers is not None:
        requested = {int(num) for num in beat_numbers}
        selected_beat_numbers = [num for num in selected_beat_numbers if num in requested]
        task_source = "explicit_beat_numbers"
    else:
        select_rows, select_source = load_select_result_rows(project_dir, episode_num)
        edit_beats = sorted(
            int(row.get("beat_number") or 0)
            for row in select_rows
            if row.get("recommended_action") == "edit" and int(row.get("beat_number") or 0) > 0
        )
        if select_rows:
            selected_beat_numbers = [num for num in selected_beat_numbers if num in set(edit_beats)]
            task_source = select_source
    if limit > 0:
        selected_beat_numbers = selected_beat_numbers[:limit]

    tasks: list[dict[str, Any]] = []
    task_total = len(selected_beat_numbers) or 1
    resolved_project_id = project_id or project_dir.name
    for index, beat_number in enumerate(selected_beat_numbers, start=1):
        progress = 0.25 + (index / task_total) * 0.6
        _notify(progress_callback, progress, f"构建 beat {beat_number} edit task")
        beat = next(
            (
                item
                for item in payload.get("beats", [])
                if int(item.get("beat_number") or 0) == int(beat_number)
            ),
            None,
        )
        if not beat:
            raise IndexError(f"Beat {beat_number} not found in episode {episode_num}")
        tasks.append(
            build_task(
                project_dir,
                resolved_project_id,
                payload,
                beat,
                selected_map,
            )
        )

    _notify(progress_callback, 0.9, "写入 tasks.jsonl")
    with output_path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")

    summary = {
        "project_dir": str(project_dir),
        "episode_num": episode_num,
        "task_count": len(tasks),
        "selected_sketch_count": len(selected_map),
        "requested_beats": selected_beat_numbers,
        "task_source": task_source,
        "output_jsonl": str(output_path),
    }
    summary_path = output_path.with_name(output_path.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_json"] = str(summary_path)
    _log(log_callback, f"已生成 {len(tasks)} 条 sketch edit tasks")
    _notify(progress_callback, 1.0, "完成")
    return summary
