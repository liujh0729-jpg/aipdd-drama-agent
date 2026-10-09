"""Helpers for user-inserted silent manual shots."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

ORDER_STEP = 10
DEFAULT_MANUAL_DURATION = 3.0
DEFAULT_VIDEO_DURATION = 5.0
_BEAT_ASSET_RE = re.compile(r"^beat_(\d+)")


def is_manual_shot(beat: dict | object | None) -> bool:
    if beat is None:
        return False
    if isinstance(beat, dict):
        return bool(beat.get("is_manual_shot"))
    return bool(getattr(beat, "is_manual_shot", False))


def is_manual_space_map_shot(beat: dict | object | None) -> bool:
    if not is_manual_shot(beat):
        return False
    visual = str(_get_value(beat, "visual_description", "") or "").strip().lower()
    return visual.startswith("[space_map")


def storyboard_beats_for_manual_sketches(beats: Sequence[dict]) -> list[dict]:
    """Return beats eligible for manual-shot sketch gap scanning.

    Manual space-map entries are planning artifacts in the NiceGUI flow; they
    must not dispatch sketch regeneration when React asks for missing manual
    shot sketches.
    """
    return [beat for beat in beats if not is_manual_space_map_shot(beat)]


def beat_requires_audio(beat: dict | object | None) -> bool:
    if beat is None:
        return False
    audio_type = str(_get_value(beat, "audio_type", "") or "").strip()
    return audio_type not in {"silence", "action"}


def _normalize_manual_audio_fields(
    *,
    audio_type: str | None,
    speaker: str | None,
    narration_segment: str | None,
) -> tuple[str, str, str]:
    normalized_type = str(audio_type or "silence").strip()
    if normalized_type not in {"silence", "narration", "dialogue"}:
        raise ValueError("audio_type must be silence, narration, or dialogue")

    normalized_text = str(narration_segment or "").strip()
    normalized_speaker = str(speaker or "").strip()

    if normalized_type == "silence":
        return "silence", "", ""
    if normalized_type == "narration":
        if not normalized_text:
            raise ValueError("narration manual shot requires narration_segment")
        return "narration", "", normalized_text
    if not normalized_text:
        raise ValueError("dialogue manual shot requires narration_segment")
    return "dialogue", normalized_speaker, normalized_text


def _get_value(beat: dict | object, name: str, default=None):
    if isinstance(beat, dict):
        return beat.get(name, default)
    return getattr(beat, name, default)


def beat_order_value(beat: dict | object) -> int:
    shot_order = _get_value(beat, "shot_order")
    if shot_order is not None:
        try:
            return int(shot_order)
        except (TypeError, ValueError):
            pass
    beat_number = _get_value(beat, "beat_number", 0) or 0
    return int(beat_number) * ORDER_STEP


def sort_beats_for_display(beats: Iterable[dict]) -> list[dict]:
    return sorted(
        list(beats),
        key=lambda beat: (beat_order_value(beat), int(beat.get("beat_number", 0) or 0)),
    )


def pick_beats_by_number(
    beats: Iterable[dict],
    beat_numbers: Iterable[int],
) -> list[dict]:
    """Return beat dicts in the exact requested beat-number order."""
    beats_by_number: dict[int, dict] = {}
    for beat in beats:
        try:
            beat_number = int(beat.get("beat_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if beat_number > 0 and beat_number not in beats_by_number:
            beats_by_number[beat_number] = beat

    picked: list[dict] = []
    seen: set[int] = set()
    for beat_number in beat_numbers:
        try:
            normalized = int(beat_number)
        except (TypeError, ValueError):
            continue
        if normalized in seen:
            continue
        beat = beats_by_number.get(normalized)
        if beat is not None:
            picked.append(beat)
            seen.add(normalized)
    return picked


def normalize_shot_orders(beats: Sequence[dict]) -> list[tuple[int, int]]:
    ordered = sort_beats_for_display(beats)
    return [
        (int(beat.get("beat_number", 0)), (index + 1) * ORDER_STEP)
        for index, beat in enumerate(ordered)
        if beat.get("beat_number") is not None
    ]


def _episode_asset_beat_numbers(store, episode_number: int) -> set[int]:
    project_dir_value = getattr(store, "project_dir", "")
    if not project_dir_value:
        return set()

    project_dir = Path(project_dir_value)
    if not project_dir.exists():
        return set()

    ep_token = f"ep{episode_number:03d}"
    numbers: set[int] = set()
    try:
        episode_dirs = [path for path in project_dir.rglob(ep_token) if path.is_dir()]
    except OSError:
        return numbers

    for episode_dir in episode_dirs:
        for path in episode_dir.rglob("beat_*"):
            match = _BEAT_ASSET_RE.match(path.name)
            if not match:
                continue
            try:
                numbers.add(int(match.group(1)))
            except ValueError:
                continue
    return numbers


def _next_available_beat_number(
    beats: Sequence[dict],
    store,
    episode_number: int,
) -> int:
    used_numbers = {
        int(beat.get("beat_number", 0) or 0)
        for beat in beats
        if int(beat.get("beat_number", 0) or 0) > 0
    }
    used_numbers.update(_episode_asset_beat_numbers(store, episode_number))
    return (max(used_numbers) if used_numbers else 0) + 1


def calculate_insert_order(
    previous_order: int | None,
    next_order: int | None,
    *,
    step: int = ORDER_STEP,
) -> int | None:
    if previous_order is None and next_order is None:
        return step
    if previous_order is None:
        next_order = int(next_order)
        return next_order // 2 if next_order > 1 else None
    if next_order is None:
        return int(previous_order) + step
    previous_order = int(previous_order)
    next_order = int(next_order)
    if next_order - previous_order <= 1:
        return None
    return (previous_order + next_order) // 2


def resolve_target_video_duration(
    beat: dict,
    audio_duration: float | None = None,
    *,
    default: float = DEFAULT_VIDEO_DURATION,
) -> float:
    duration = beat.get("duration_seconds")
    if duration is not None:
        try:
            parsed = float(duration)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    if audio_duration is not None:
        try:
            parsed = float(audio_duration)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return float(default)


def choose_manual_sketch_mode_key(count: int) -> str:
    from novelvideo.generators.grid_generation import sketch_scene_grid_split

    beats = [{"beat_number": idx} for idx in range(1, max(1, count) + 1)]
    return sketch_scene_grid_split(beats)[0]["mode_key"]


def _scene_id_of(beat: dict) -> str:
    scene_ref = beat.get("scene_ref")
    if isinstance(scene_ref, dict):
        return str(scene_ref.get("scene_id") or "").strip()
    return ""


def missing_manual_shot_segments(
    beats: Sequence[dict],
    sketches_dir: str | Path,
) -> list[list[int]]:
    sketches_path = Path(sketches_dir)
    ordered = sort_beats_for_display(beats)
    segments: list[list[int]] = []
    current: list[int] = []
    current_scene_id = ""

    for beat in ordered:
        beat_num = int(beat.get("beat_number", 0) or 0)
        scene_id = _scene_id_of(beat)
        missing_manual = (
            beat_num > 0
            and is_manual_shot(beat)
            and not (sketches_path / f"beat_{beat_num:02d}.png").exists()
        )
        if missing_manual:
            if current and scene_id != current_scene_id:
                segments.append(current)
                current = []
            current.append(beat_num)
            current_scene_id = scene_id
            continue
        if current:
            segments.append(current)
            current = []
            current_scene_id = ""

    if current:
        segments.append(current)
    return segments


def split_video_generation_prereqs(
    beats: Sequence[dict],
    *,
    status_lookup: Callable[[int], dict],
) -> tuple[list[dict], list[int], list[int]]:
    ready_beats: list[dict] = []
    missing_frames: list[int] = []
    missing_audio: list[int] = []

    for beat in beats:
        beat_num = int(beat.get("beat_number", 0) or 0)
        status = status_lookup(beat_num)
        frame_exists = bool(status.get("frame_exists"))
        audio_exists = bool(status.get("audio_exists"))
        requires_audio = beat_requires_audio(beat)
        if frame_exists and (audio_exists or not requires_audio):
            ready_beats.append(beat)
            continue
        if not frame_exists:
            missing_frames.append(beat_num)
        if not audio_exists and requires_audio:
            missing_audio.append(beat_num)

    return ready_beats, missing_frames, missing_audio


def build_subtitle_timing_entries(
    beats: Sequence[dict],
    *,
    duration_lookup: Callable[[dict], float | None],
) -> list[tuple[int, float, float, str]]:
    current_time = 0.0
    seq = 0
    entries: list[tuple[int, float, float, str]] = []

    for beat in beats:
        duration = resolve_target_video_duration(beat, duration_lookup(beat))
        narration = str(beat.get("narration_segment", "") or "")
        start = current_time
        end = start + duration
        if narration:
            seq += 1
            entries.append((seq, start, end, narration))
        current_time = end

    return entries


async def _normalize_episode_shot_orders(
    store,
    episode_number: int,
    beats: Sequence[dict],
) -> list[dict]:
    updates = normalize_shot_orders(beats)
    for beat_num, shot_order in updates:
        await store.update_beat_asset(
            episode_number,
            beat_num,
            shot_order=shot_order,
        )
    refreshed = await store.get_beats_as_dicts(episode_number)
    return sort_beats_for_display(refreshed)


async def insert_manual_shot(
    store,
    *,
    episode_number: int,
    after_beat_number: int | None,
    visual_description: str,
    duration_seconds: float | None = None,
    scene_ref: dict | None = None,
    time_of_day: str | None = None,
    detected_identities: Sequence[str] | None = None,
    detected_props: Sequence[str] | None = None,
    audio_type: str | None = "silence",
    speaker: str | None = None,
    narration_segment: str | None = None,
) -> dict:
    from novelvideo.models import (
        NovelVisualBeat,
        extract_char_identities_from_markers,
        extract_prop_ids_from_markers,
    )

    beats = sort_beats_for_display(await store.get_beats_as_dicts(episode_number))
    if not beats:
        raise ValueError(f"Episode {episode_number} has no beats")

    if after_beat_number is None:
        previous_beat = None
        next_beat = beats[0]
        insert_index = 0
    else:
        insert_index = next(
            (idx for idx, beat in enumerate(beats) if beat.get("beat_number") == after_beat_number),
            None,
        )
        if insert_index is None:
            raise ValueError(f"Beat {after_beat_number} not found in episode {episode_number}")
        previous_beat = beats[insert_index]
        next_beat = beats[insert_index + 1] if insert_index + 1 < len(beats) else None

    previous_order = beat_order_value(previous_beat) if previous_beat else None
    next_order = beat_order_value(next_beat) if next_beat else None
    new_order = calculate_insert_order(previous_order, next_order)

    if new_order is None:
        beats = await _normalize_episode_shot_orders(store, episode_number, beats)
        if after_beat_number is None:
            previous_beat = None
            next_beat = beats[0]
        else:
            insert_index = next(
                idx
                for idx, beat in enumerate(beats)
                if beat.get("beat_number") == after_beat_number
            )
            previous_beat = beats[insert_index]
            next_beat = beats[insert_index + 1] if insert_index + 1 < len(beats) else None
        previous_order = beat_order_value(previous_beat) if previous_beat else None
        next_order = beat_order_value(next_beat) if next_beat else None
        new_order = calculate_insert_order(previous_order, next_order)
        if new_order is None:
            raise ValueError("Unable to allocate shot_order after normalization")

    source = previous_beat or next_beat or {}
    new_beat_number = _next_available_beat_number(beats, store, episode_number)
    saved_identities = (
        list(detected_identities)
        if detected_identities is not None
        else list(extract_char_identities_from_markers(visual_description, strict=False).values())
    )
    saved_props = (
        list(detected_props)
        if detected_props is not None
        else list(extract_prop_ids_from_markers(visual_description, strict=False))
    )
    inherited_scene_ref = scene_ref if scene_ref is not None else source.get("scene_ref")
    scene_ref_json = (
        json.dumps(inherited_scene_ref, ensure_ascii=False)
        if isinstance(inherited_scene_ref, dict) and inherited_scene_ref
        else ""
    )
    normalized_audio_type, normalized_speaker, normalized_narration = _normalize_manual_audio_fields(
        audio_type=audio_type,
        speaker=speaker,
        narration_segment=narration_segment,
    )
    new_beat = NovelVisualBeat(
        episode_number=episode_number,
        beat_number=new_beat_number,
        shot_order=new_order,
        duration_seconds=duration_seconds or DEFAULT_MANUAL_DURATION,
        is_manual_shot=True,
        narration=normalized_narration,
        visual_description=visual_description,
        video_prompt="",
        keyframe_prompt="",
        video_mode="first_frame",
        scene_ref_json=scene_ref_json,
        time_of_day=source.get("time_of_day", "") if time_of_day is None else time_of_day,
        detected_identities_json=json.dumps(saved_identities, ensure_ascii=False),
        detected_props_json=json.dumps(saved_props, ensure_ascii=False),
        audio_type=normalized_audio_type,
        speaker=normalized_speaker,
    )
    await store.add_visual_beats([new_beat])

    refreshed = await store.get_beats_as_dicts(episode_number)
    return next(
        beat for beat in refreshed if int(beat.get("beat_number", 0)) == new_beat.beat_number
    )


async def delete_manual_shot(
    store,
    *,
    episode_number: int,
    beat_number: int,
) -> list[dict]:
    beats = sort_beats_for_display(await store.get_beats_as_dicts(episode_number))
    target = next(
        (beat for beat in beats if int(beat.get("beat_number", 0) or 0) == int(beat_number)),
        None,
    )
    if target is None:
        raise ValueError(f"Beat {beat_number} not found in episode {episode_number}")
    if not is_manual_shot(target):
        raise ValueError("Only manual shots can be deleted")

    deleted = await store.delete_manual_beat(episode_number, int(beat_number))
    if not deleted:
        raise ValueError(f"Manual shot {beat_number} was not deleted")
    _delete_manual_shot_artifacts(store, episode_number=episode_number, beat_number=beat_number)
    return sort_beats_for_display(await store.get_beats_as_dicts(episode_number))


def _delete_manual_shot_artifacts(store, *, episode_number: int, beat_number: int) -> None:
    project_dir_value = getattr(store, "project_dir", "")
    if not project_dir_value:
        return
    project_dir = Path(project_dir_value)
    ep_token = f"ep{episode_number:03d}"
    candidates = [
        project_dir / "sketches" / ep_token / f"beat_{beat_number:02d}.png",
        project_dir / "frames" / ep_token / f"beat_{beat_number:02d}.png",
        project_dir / "renders" / ep_token / f"beat_{beat_number:02d}.png",
    ]
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            pass

    # Candidate pool cells use timestamped filenames; remove only this manual beat's cells.
    for root in [
        project_dir / "grids" / ep_token / "sketch" / "cells",
        project_dir / "grids" / ep_token / "render" / "cells",
    ]:
        if not root.exists():
            continue
        for path in root.glob(f"beat_{beat_number:02d}_*"):
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
