"""剧集预设上下文（build_episode_preset_context）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .refs import PresetRef
from .ref_collect import (
    _normalize_scene_name,
    _visual_markers,
    _identity_character,
    _as_list,
    _prop_id_from_item,
    _identity_id_from_item,
    _real_identity_ids,
    _real_prop_ids,
    _add_mainline_identity_ref,
    _add_prop_refs,
    _add_selected_background_ref,
)


async def build_episode_preset_context(
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    store: Any,
    episode: int,
) -> dict[str, Any]:
    beats = await store.get_beats_as_dicts(episode)
    try:
        episode_obj = await store.get_episode_from_graph(episode)
    except Exception:
        episode_obj = None

    episode_title = str(getattr(episode_obj, "title", "") or f"EP{episode}").strip()
    known_characters = list(getattr(store, "_characters", {}).keys())
    scene_ids: set[str] = set()
    identity_ids: set[str] = set()
    prop_ids: set[str] = set()
    beat_items: list[dict[str, Any]] = []
    background_items: list[dict[str, Any]] = []

    for beat in beats:
        try:
            beat_number = int(beat.get("beat_number") or 0)
        except (TypeError, ValueError):
            beat_number = 0
        if beat_number <= 0:
            continue
        visual_description = str(beat.get("visual_description") or "")
        marker_identities, marker_props = _visual_markers(visual_description)
        detected_identities = [
            _identity_id_from_item(x) for x in _as_list(beat.get("detected_identities"))
        ]
        detected_props = [_prop_id_from_item(x) for x in _as_list(beat.get("detected_props"))]
        scene_id = _normalize_scene_name(beat.get("scene_ref"))
        if scene_id:
            scene_ids.add(scene_id)
        for identity_id in _real_identity_ids([*marker_identities, *detected_identities]):
            identity_ids.add(identity_id)
        for prop_id in _real_prop_ids([*marker_props, *detected_props]):
            prop_ids.add(prop_id)
        beat_items.append(
            {
                "beat_number": beat_number,
                "visual_description": visual_description,
                "narration_segment": beat.get("narration_segment"),
                "scene_id": scene_id,
                "detected_identities": _real_identity_ids(
                    [*marker_identities, *detected_identities]
                ),
                "detected_props": _real_prop_ids([*marker_props, *detected_props]),
            }
        )
        background_items.append({"beat_number": beat_number, "scene_id": scene_id})

    identity_items = [
        {
            "identity_id": identity_id,
            "character": _identity_character(identity_id, known_characters),
        }
        for identity_id in sorted(identity_ids)
    ]
    prop_items = [{"prop_id": prop_id} for prop_id in sorted(prop_ids)]
    scene_items = [{"scene_id": scene_id} for scene_id in sorted(scene_ids)]

    refs: list[PresetRef] = []
    for item in identity_items:
        # EP canvas:不走 portrait/reference_front fallback。canonical identity
        # 缺失就不发 ref(用户看不到 portrait 替身,避免被误读为"该 identity 已生成")。
        # Beat 工作台保留 fallback(default include_portrait_fallback=True)。
        _add_mainline_identity_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            character=str(item.get("character") or ""),
            identity_id=str(item.get("identity_id") or ""),
            include_portrait_fallback=False,
        )
    for item in prop_items:
        prop_id = str(item.get("prop_id") or "")
        _add_prop_refs(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            prop_id=prop_id,
            meta={"prop_id": prop_id},
        )
    for item in background_items:
        beat_number = int(item.get("beat_number") or 0)
        if beat_number <= 0:
            continue
        _add_selected_background_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            episode=episode,
            beat=beat_number,
            scene_id=str(item.get("scene_id") or ""),
        )

    return {
        "scope": "episode",
        "username": username,
        "project": project,
        "project_id": project_id,
        "project_dir": str(project_dir),
        "episode": episode,
        "episode_title": episode_title,
        "beats": sorted(beat_items, key=lambda item: int(item["beat_number"])),
        "scenes": scene_items,
        "backgrounds": background_items,
        "identities": identity_items,
        "props": prop_items,
        "refs": [r.model_dump() for r in refs],
    }
