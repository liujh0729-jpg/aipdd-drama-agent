"""节拍预设上下文（collect_beat_preset_refs / build_beat_preset_context）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.models import build_prop_menu
from novelvideo.utils.path_resolver import PathResolver, canonical_scene_master_path

from .refs import PresetRef, _make_url, _path_rel_if_inside
from .aspect import (
    _project_sketch_aspect_ratio,
    _media_type_for_path,
    _aspect_ratio_for_ref,
)
from .ref_collect import (
    _add_file_ref,
    _normalize_scene_name,
    _jsonable,
    _visual_markers,
    _identity_character,
    _as_list,
    _prop_id_from_item,
    _identity_id_from_item,
    _real_identity_ids,
    _real_prop_ids,
    _add_mainline_identity_ref,
    _add_prop_refs,
)


def collect_beat_preset_refs(
    *,
    username: str,
    project: str,
    project_dir: Path,
    store: Any,
    episode: int,
    beat: int,
    primary_slot: str = "render",
) -> dict[str, Any]:
    """Collect canonical refs for a beat preset.

    The return value is API-ready JSON so it can be used by both a spike script
    and the FastAPI factory endpoint.
    """
    # The store methods are async; this function is sync by design only for the
    # small helpers above. Use `build_beat_preset_context` for the public async API.
    raise RuntimeError("use build_beat_preset_context")


async def build_beat_preset_context(
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    store: Any,
    episode: int,
    beat: int,
    primary_slot: str = "render",
) -> dict[str, Any]:
    beats = await store.get_beats_as_dicts(episode)
    target = next((b for b in beats if int(b.get("beat_number") or -1) == int(beat)), None)
    if not target:
        raise ValueError(f"beat not found: ep{episode} beat{beat}")

    episode_obj = await store.get_episode_from_graph(episode)
    prop_menu = [
        item.model_dump()
        for item in build_prop_menu(prop_menu=getattr(episode_obj, "prop_menu", []) or [])
    ]
    try:
        from novelvideo.services.prop_ref_service import runtime_prop_menu_with_cached_global_props

        prop_menu = runtime_prop_menu_with_cached_global_props(
            prop_menu=prop_menu,
            beats=[target],
            store=store,
        )
    except Exception:
        pass
    scene_menu = [
        item.model_dump() if hasattr(item, "model_dump") else _jsonable(item)
        for item in (getattr(episode_obj, "scene_menu", []) or [])
    ]
    prop_by_id = {item["prop_id"]: item for item in prop_menu if item.get("prop_id")}
    known_characters = list(getattr(store, "_characters", {}).keys())

    refs: list[PresetRef] = []
    ep_dir = f"ep{episode:03d}"
    beat_dir = f"beat_{beat:02d}"
    control_base = f"director_control_frames/{ep_dir}/{beat_dir}"
    paths = PathResolver(str(project_dir), episode)

    def _project_rel(path: Path) -> str:
        return path.relative_to(project_dir).as_posix()

    canonical_sketch_path = paths.sketch(beat)
    for rel_path, kind, role, label in [
        (_project_rel(canonical_sketch_path), "sketch", "current_sketch", "当前草图"),
        (_project_rel(paths.frame(beat)), "frame", "current_frame", "当前分镜"),
        (
            _project_rel(paths.video(beat)),
            "video",
            "current_video",
            "当前视频",
        ),
        (_project_rel(paths.audio(beat)), "audio", "current_audio", "当前音频"),
        (f"{control_base}/combined.png", "director", "director_combined", "导演合成图"),
    ]:
        _add_file_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            kind=kind,
            role=role,
            label=label,
            rel_path=rel_path,
            meta={"episode": episode, "beat": beat, "primary_slot": primary_slot},
        )

    scene_name = _normalize_scene_name(target.get("scene_ref"))

    # selected_background 不走 _add_file_ref(那个函数会在文件缺失时直接跳过) ——
    # 这个 slot 必须有站位节点,用户才知道往哪 commit 选定背景图。文件存在时
    # 走正常 asset node 渲染;缺失时 emit 一个 url=None 的 placeholder ref,
    # 让画布上 generic ref loop 给它建一个空 imageGenNode 占位。
    selected_bg_rel = f"{control_base}/selected_background.png"
    selected_bg_path = project_dir / selected_bg_rel
    selected_bg_exists = selected_bg_path.exists()
    fallback_bg_rel: str | None = None
    fallback_bg_path: Path | None = None
    if not selected_bg_exists and scene_name:
        scene_master_path = canonical_scene_master_path(project_dir, scene_name)
        if scene_master_path.exists():
            fallback_bg_rel = _path_rel_if_inside(project_dir, scene_master_path)
            fallback_bg_path = scene_master_path if fallback_bg_rel else None
    preview_bg_path = selected_bg_path if selected_bg_exists else fallback_bg_path
    preview_bg_rel = selected_bg_rel if selected_bg_exists else fallback_bg_rel
    preview_bg_exists = selected_bg_exists or bool(fallback_bg_path)
    selected_bg_meta = {
        "episode": episode,
        "beat": beat,
        "primary_slot": primary_slot,
        "scene_id": scene_name,
    }
    if fallback_bg_rel:
        selected_bg_meta.update(
            {
                "fallback_source": "scene_master",
                "fallback_rel_path": fallback_bg_rel,
            }
        )
    refs.append(
        PresetRef(
            kind="director",
            role="selected_background",
            label=f"当前背景 · Beat {beat}",
            rel_path=selected_bg_rel,
            url=(
                _make_url(project_id, project_dir, preview_bg_rel)
                if preview_bg_rel and preview_bg_exists
                else None
            ),
            exists=preview_bg_exists,
            media_type=_media_type_for_path(preview_bg_path) if preview_bg_path else "image",
            aspect_ratio=_aspect_ratio_for_ref(preview_bg_path) if preview_bg_path else "16:9",
            meta=selected_bg_meta,
        )
    )

    visual_description = str(target.get("visual_description") or "")
    marker_identities, marker_props = _visual_markers(visual_description)
    detected_identities = [
        _identity_id_from_item(x) for x in _as_list(target.get("detected_identities"))
    ]
    identity_ids = _real_identity_ids([*marker_identities, *detected_identities])
    seen_identities: set[str] = set()
    for identity_id in identity_ids:
        if identity_id in seen_identities:
            continue
        seen_identities.add(identity_id)
        character = _identity_character(identity_id, known_characters)
        _add_mainline_identity_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            character=character,
            identity_id=identity_id,
        )

    detected_props = [_prop_id_from_item(x) for x in _as_list(target.get("detected_props"))]
    prop_ids = _real_prop_ids([*marker_props, *detected_props])
    seen_props: set[str] = set()
    for prop_id in prop_ids:
        if prop_id in seen_props:
            continue
        seen_props.add(prop_id)
        _add_prop_refs(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            prop_id=prop_id,
            meta=prop_by_id.get(prop_id, {"prop_id": prop_id}),
        )

    # Beat canvas does not project scene assets as nodes. Scene master /
    # reverse / 360 / 3GS remain available through the set-selected-background
    # SkillNode's embedded source picker.

    sketch_colors: dict[str, str] = {}
    get_sketch_colors = getattr(store, "get_sketch_colors", None)
    if callable(get_sketch_colors):
        try:
            sketch_colors = get_sketch_colors(episode) or {}
        except Exception:
            sketch_colors = {}
    if not sketch_colors:
        try:
            from novelvideo.generators.episode_optimizer import EpisodeOptimizer

            sketch_colors = EpisodeOptimizer.assign_sketch_colors(
                [
                    {
                        "name": getattr(character_obj, "name", ""),
                        "identities": [
                            (
                                identity.model_dump()
                                if hasattr(identity, "model_dump")
                                else _jsonable(identity)
                            )
                            for identity in (getattr(character_obj, "identities", []) or [])
                        ],
                    }
                    for character_obj in getattr(store, "get_all_characters", lambda: [])()
                ],
                episode_beats=beats,
            )
        except Exception:
            sketch_colors = {}

    try:
        from novelvideo.project_config import load_project_config_file

        project_config = load_project_config_file(username, project) if project else {}
    except Exception:
        project_config = {}
    sketch_aspect_ratio = _project_sketch_aspect_ratio(project_config, episode)

    prop_marker_colors: dict[str, str] = {}
    try:
        from novelvideo.generators.grid_generation import _global_prop_marker_colors

        prop_marker_colors = _global_prop_marker_colors(
            [target],
            prop_menu=prop_menu,
            sketch_colors=sketch_colors,
        )
    except Exception:
        prop_marker_colors = {}

    character_profiles: dict[str, Any] = {}
    for character_obj in getattr(store, "get_all_characters", lambda: [])():
        character_name = str(getattr(character_obj, "name", "") or "").strip()
        if not character_name:
            continue
        character_profiles[character_name] = {
            "name": character_name,
            "gender": str(getattr(character_obj, "gender", "") or "").strip(),
            "body_type": str(getattr(character_obj, "body_type", "") or "").strip(),
            "appearance_details": str(
                getattr(character_obj, "appearance_details", "") or ""
            ).strip(),
            "identities": [
                identity.model_dump() if hasattr(identity, "model_dump") else _jsonable(identity)
                for identity in (getattr(character_obj, "identities", []) or [])
            ],
        }

    return {
        "scope": "beat",
        "username": username,
        "project": project,
        "project_id": project_id,
        "project_dir": str(project_dir),
        "episode": episode,
        "beat": beat,
        "primary_slot": primary_slot,
        "sketch_aspect_ratio": sketch_aspect_ratio,
        "beat_data": {
            "beat_number": target.get("beat_number"),
            "narration_segment": target.get("narration_segment"),
            "visual_description": visual_description,
            "video_prompt": target.get("video_prompt"),
            "keyframe_prompt": target.get("keyframe_prompt"),
            "scene_ref": target.get("scene_ref"),
            "detected_identities": target.get("detected_identities"),
            "detected_props": target.get("detected_props"),
        },
        "prop_menu": prop_menu,
        "scene_menu": scene_menu,
        "sketch_context": {
            "sketch_colors": sketch_colors,
            "prop_marker_colors": prop_marker_colors,
            "characters": character_profiles,
        },
        "refs": [r.model_dump() for r in refs],
    }
