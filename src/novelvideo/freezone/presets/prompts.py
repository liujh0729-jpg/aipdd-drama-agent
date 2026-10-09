"""提示词节点内容与节拍标记替换（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .refs import _safe_id
from .aspect import _project_sketch_aspect_ratio
from .ref_collect import (
    _normalize_scene_name,
    _MARKER_RE,
    _visual_markers,
    _identity_character,
    _identity_name,
    _as_list,
    _prop_id_from_item,
    _identity_id_from_item,
    _real_identity_ids,
    _real_prop_ids,
)
from .nodes import _text_node


def _prompt_text_node(
    node_id: str,
    x: int,
    y: int,
    label: str,
    prompt: str,
    *,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node = _text_node(node_id, x, y, label, prompt)
    if source_meta:
        node["data"]["__freezone_source"] = source_meta
    return node


def _display_prompt_text(prompt: str, *, fallback_note: str) -> str:
    normalized = str(prompt or "").strip()
    return normalized or fallback_note


def _character_profile_content(profile: dict[str, Any]) -> str:
    def _text(value: Any) -> str:
        return str(value or "").strip()

    aliases = [_text(alias) for alias in (profile.get("aliases") or []) if _text(alias)]
    lines = [
        f"名称: {_text(profile.get('name')) or '(未填写)'}",
        f"性别: {_text(profile.get('gender')) or '(未填写)'}",
        f"角色定位: {_text(profile.get('role')) or '(未填写)'}",
        f"别名: {', '.join(aliases) if aliases else '(未填写)'}",
        f"年龄段: {_text(profile.get('age_group')) or '(未填写)'}",
    ]
    body_type = _text(profile.get("body_type"))
    if body_type:
        lines.append(f"体型: {body_type}")
    description = _text(profile.get("description"))
    if description:
        lines.extend(["", "描述:", description])
    face_prompt = _text(profile.get("face_prompt"))
    if face_prompt:
        lines.extend(["", "面部特征（face_prompt，不含服装）:", face_prompt])
    return "\n".join(lines)


def _prop_profile_content(profile: dict[str, Any]) -> str:
    def _text(value: Any) -> str:
        return str(value or "").strip()

    aliases = [str(item).strip() for item in profile.get("aliases") or [] if str(item).strip()]
    lines = [
        f"名称: {_text(profile.get('name'))}",
        f"类型: {_text(profile.get('prop_type')) or 'object'}",
    ]
    if aliases:
        lines.append(f"别名: {'、'.join(aliases)}")
    owner = _text(profile.get("owner"))
    if owner:
        lines.append(f"所属角色: {owner}")
    description = _text(profile.get("description"))
    if description:
        lines.extend(["", "描述:", description])
    visual_prompt = _text(profile.get("visual_prompt"))
    if visual_prompt:
        lines.extend(["", "视觉 Prompt:", visual_prompt])
    notes = _text(profile.get("notes"))
    if notes:
        lines.extend(["", "备注:", notes])
    return "\n".join(lines)


def _gender_label(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"男", "男性", "male", "m"}:
        return "MALE"
    if normalized in {"女", "女性", "female", "f"}:
        return "FEMALE"
    return str(value or "").strip()


def _split_marker_color(color: str) -> tuple[str, str]:
    parts = str(color or "").strip().split(" ", 1)
    if not parts or not parts[0]:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else parts[0]


def _identity_marker_context(
    *,
    identity_id: str,
    sketch_context: dict[str, Any],
) -> dict[str, str]:
    character = _identity_character(identity_id, (sketch_context.get("characters") or {}).keys())
    profile = (sketch_context.get("characters") or {}).get(character) or {}
    suffix = _identity_name(identity_id, character)
    identity_data = next(
        (
            item
            for item in (profile.get("identities") or [])
            if str(item.get("identity_id") or "") == identity_id
        ),
        {},
    )
    color = str((sketch_context.get("sketch_colors") or {}).get(identity_id) or "").strip()
    hex_code, color_name = _split_marker_color(color)
    gender = _gender_label(str(profile.get("gender") or ""))
    body_type = str(identity_data.get("body_type") or profile.get("body_type") or "").strip()
    body_desc = ", ".join(part for part in [gender, body_type] if part) or "character figure"
    try:
        from novelvideo.generators.prompt_builder import PromptComponents

        tag = PromptComponents.compute_char_tag(character, identity_id=identity_id)
    except Exception:
        tag = f"[{_safe_id(identity_id, fallback='CHAR').upper()}]"
    return {
        "identity_id": identity_id,
        "character": character,
        "suffix": suffix,
        "tag": tag,
        "color": color,
        "hex": hex_code,
        "color_name": color_name,
        "body_desc": body_desc,
        "appearance_details": str(
            identity_data.get("appearance_details") or profile.get("appearance_details") or ""
        ).strip(),
    }


def _prop_marker_context(
    prop_id: str,
    prop_menu: list[dict[str, Any]],
    prop_marker_colors: dict[str, str] | None = None,
) -> dict[str, str]:
    prop_info = next((item for item in prop_menu if str(item.get("prop_id") or "") == prop_id), {})
    try:
        from novelvideo.generators.prompt_builder import PromptComponents

        tag = PromptComponents.compute_prop_tag(prop_id)
    except Exception:
        tag = f"[{_safe_id(prop_id, fallback='PROP').upper()}]"
    return {
        "prop_id": prop_id,
        "tag": tag,
        "prop_type": str(prop_info.get("prop_type") or "object").strip(),
        "visual_prompt": str(prop_info.get("visual_prompt") or "").strip(),
        "description": str(prop_info.get("description") or "").strip(),
        "marker_color": str((prop_marker_colors or {}).get(prop_id) or "").strip(),
    }


def _replace_beat_markers(
    text: str,
    identity_markers: dict[str, dict[str, str]],
    prop_markers: dict[str, dict[str, str]],
) -> str:
    def replace_match(match: re.Match) -> str:
        identity_id = str(match.group(1) or "").strip()
        prop_id = str(match.group(2) or "").strip()
        if identity_id:
            marker = identity_markers.get(identity_id)
            if marker:
                color_name = marker.get("color_name") or marker.get("color") or "assigned color"
                return f"{marker.get('tag')} ({color_name})"
            return identity_id
        if prop_id:
            marker = prop_markers.get(prop_id)
            if marker:
                marker_color = str(marker.get("marker_color") or "").strip()
                if marker_color:
                    return f"{marker.get('tag')} ({marker_color})"
                return marker.get("tag", prop_id)
            return prop_id
        return match.group(0)

    return _MARKER_RE.sub(replace_match, text or "")


def _beat_sketch_marker_context(context: dict[str, Any]) -> dict[str, Any]:
    beat_data = context.get("beat_data") or {}
    visual_description = str(beat_data.get("visual_description") or "")
    marker_identities, marker_props = _visual_markers(visual_description)
    detected_identities = [
        _identity_id_from_item(item) for item in _as_list(beat_data.get("detected_identities"))
    ]
    detected_props = [
        _prop_id_from_item(item) for item in _as_list(beat_data.get("detected_props"))
    ]
    identity_ids = list(
        dict.fromkeys(_real_identity_ids([*marker_identities, *detected_identities]))
    )
    prop_ids = list(dict.fromkeys(_real_prop_ids([*marker_props, *detected_props])))
    sketch_context = context.get("sketch_context") or {}
    prop_marker_colors = dict(sketch_context.get("prop_marker_colors") or {})
    identities = {
        identity_id: _identity_marker_context(
            identity_id=identity_id,
            sketch_context=sketch_context,
        )
        for identity_id in identity_ids
        if identity_id
    }
    props = {
        prop_id: _prop_marker_context(
            prop_id,
            context.get("prop_menu") or [],
            prop_marker_colors=prop_marker_colors,
        )
        for prop_id in prop_ids
        if prop_id
    }
    return {
        "identities": identities,
        "props": props,
        "visual_with_tags": _replace_beat_markers(visual_description, identities, props),
    }


def _beat_sketch_marker_content(marker_context: dict[str, Any]) -> str:
    identity_lines = []
    for marker in marker_context.get("identities", {}).values():
        color = marker.get("color")
        if color:
            hex_code, color_name = _split_marker_color(color)
            identity_lines.append(
                f"- {marker.get('identity_id')} -> {marker.get('tag')} — "
                f"{color_name} ({hex_code}) figure. {marker.get('body_desc')}."
            )
        else:
            identity_lines.append(
                f"- {marker.get('identity_id')} -> {marker.get('tag')} — "
                f"{marker.get('body_desc')}."
            )

    prop_lines = []
    for marker in marker_context.get("props", {}).values():
        description = marker.get("visual_prompt") or marker.get("description") or "core prop/object"
        marker_color = str(marker.get("marker_color") or "").strip()
        if marker_color:
            prop_lines.append(
                f"- {marker.get('prop_id')} -> {marker.get('tag')} — "
                f"marker color {marker_color}; {description}"
            )
        else:
            prop_lines.append(f"- {marker.get('prop_id')} -> {marker.get('tag')} — {description}")

    lines = [
        "Sketch generation uses TEXT MARKERS, not image references.",
        "reference_images: []",
        "",
        "COLOR-CODED CHARACTERS:",
        *(identity_lines or ["- none"]),
        "",
        "NAMED CORE PROPS:",
        *(prop_lines or ["- none"]),
    ]
    return "\n".join(lines)


def _beat_sketch_prompt(context: dict[str, Any], marker_context: dict[str, Any]) -> str:
    beat_data = context.get("beat_data") or {}
    visual_with_tags = (
        marker_context.get("visual_with_tags")
        or str(beat_data.get("visual_description") or "").strip()
    )
    keyframe_prompt = str(beat_data.get("keyframe_prompt") or "").strip()
    identity_markers = list((marker_context.get("identities") or {}).values())
    prop_markers = list((marker_context.get("props") or {}).values())
    colored_character_targets = [
        marker.get("tag", "")
        for marker in identity_markers
        if marker.get("tag") and marker.get("color")
    ]
    colored_prop_markers = [
        marker
        for marker in prop_markers
        if marker.get("tag") and str(marker.get("marker_color") or "").strip()
    ]
    colored_targets = [
        *colored_character_targets,
        *[str(marker.get("tag") or "") for marker in colored_prop_markers],
    ]
    color_lines = []
    for marker in identity_markers:
        color = marker.get("color")
        if color:
            hex_code, color_name = _split_marker_color(color)
            color_lines.append(
                f"- {marker.get('tag')} — **{color_name} ({hex_code})** figure. "
                f"{marker.get('body_desc')}."
            )
        else:
            color_lines.append(f"- {marker.get('tag')} — {marker.get('body_desc')}.")
    prop_lines = []
    key_prop_color_lines = []
    for marker in prop_markers:
        marker_color = str(marker.get("marker_color") or "").strip()
        if marker_color:
            prop_lines.append(
                f"- {marker.get('tag')} — a solid {marker_color}-filled simple geometric "
                "shape replacing the prop. ZERO internal detail; the color is only an "
                "identity marker for the sketch stage."
            )
            key_prop_color_lines.append(f"- {marker.get('tag')} — flat solid {marker_color} block.")
        else:
            prop_lines.append(f"- {marker.get('prop_id')} {marker.get('tag')} — core prop/object.")
    color_law = ""
    if colored_targets:
        key_prop_colors = (
            ["", "KEY PROP COLORS:", *key_prop_color_lines] if key_prop_color_lines else []
        )
        color_law = "\n".join(
            [
                "COLOR LAW (NON-NEGOTIABLE):",
                "These named characters / named props MUST receive their assigned "
                f"color fill: {', '.join(colored_targets)}",
                "Unnamed people and extras must be gray stick figures only, NO color fill.",
                "Do not add arbitrary color fills to background, furniture, or unmarked props.",
                "",
                "COLOR-CODED CHARACTERS:",
                *(color_lines or ["- none"]),
                *key_prop_colors,
            ]
        )

    sections = [
        "Generate ONE storyboard sketch panel for this NovelVideo beat.",
        "",
        "STYLE: rushed film director's storyboard scribble, rough hand-drawn sketch on cheap white paper, raw thumbnail-grade draft.",
        "This is a blocking sketch: use stick figures, simple poses, sparse background lines, and no polished illustration.",
        "",
        color_law,
        "NAMED CORE PROPS (flat solid color blocks in sketch; rendered as real objects later):",
        *(prop_lines or ["- none"]),
        "",
        "SCENE DESCRIPTION (do NOT render this text on the image):",
        visual_with_tags,
    ]
    if keyframe_prompt:
        sections.extend(["", "KEYFRAME NOTE:", keyframe_prompt])
    sections.extend(
        [
            "",
            "ABSOLUTELY NO TEXT ON IMAGE: no labels, captions, panel numbers, signs, readable paper text, or dialogue bubbles.",
            "Human figures are minimal stick figures. Named props are simple readable shapes in the correct position; their final material is for render stage.",
            "REFERENCE IMAGES: none. This sketch stage follows NovelVideo production logic and uses text/color markers only.",
        ]
    )
    return "\n".join(part for part in sections if part is not None)


def _beat_render_prompt(context: dict[str, Any]) -> str:
    from novelvideo.generators.grid_generation import (
        _resolve_scene_prop_asset_refs,
        filter_character_map_by_precomputed,
        load_precomputed_panel_detected,
    )
    from novelvideo.generators.prompt_builder import (
        PromptMode,
        UnifiedPromptBuilder,
        create_prompt_context,
    )
    from novelvideo.project_config import load_project_config_file
    from novelvideo.services.character_ref_service import build_character_map_for_grid
    from novelvideo.services.style_service import StyleService

    beat_data = context.get("beat_data") or {}
    beat_num = int(context.get("beat") or beat_data.get("beat_number") or 1)
    username = str(context.get("username") or "admin")
    project = str(context.get("project") or "")
    project_dir = Path(str(context.get("project_dir") or ""))
    project_config = load_project_config_file(username, project) if project else {}
    style = str(project_config.get("visual_style") or "chinese_period_drama")
    ethnicity = str(project_config.get("ethnicity") or "Chinese")
    sketch_aspect_ratio = _project_sketch_aspect_ratio(
        project_config,
        context.get("episode") or 1,
    )
    sketch_colors = dict((context.get("sketch_context") or {}).get("sketch_colors") or {})
    characters = list(((context.get("sketch_context") or {}).get("characters") or {}).values())
    user_output_dir = project_dir.parent if project_dir else Path("output") / username

    character_map = build_character_map_for_grid(
        grid_beats=[beat_data],
        characters=characters,
        user_output_dir=user_output_dir,
        project=project,
        sketch_colors=sketch_colors,
        use_detected_identities=True,
    )
    panel_detected = load_precomputed_panel_detected([beat_num], [beat_data])
    character_map = filter_character_map_by_precomputed(character_map, panel_detected)
    scene_refs, prop_asset_refs = _resolve_scene_prop_asset_refs(
        project_dir,
        [beat_data],
        episode_number=int(context.get("episode") or 1),
        sketch=False,
        use_director_refs=False,
        include_pano_view_refs=False,
        scene_menu=context.get("scene_menu") or [],
        prop_menu=context.get("prop_menu") or [],
    )
    style_family, animation_subtype = StyleService.get_style_branch(
        style,
        project_dir=project_dir,
    )
    ctx = create_prompt_context(
        mode=PromptMode.RENDER,
        beats=[beat_data],
        rows=1,
        cols=1,
        character_map=character_map,
        style=style,
        ethnicity=ethnicity,
        aspect_ratio=sketch_aspect_ratio,
        panel_detected_keys=panel_detected,
        scene_refs=scene_refs,
        prop_asset_refs=prop_asset_refs,
        sketch_colors=sketch_colors,
        style_family=style_family,
        animation_subtype=animation_subtype,
        project_dir=str(project_dir),
    )
    prompt = UnifiedPromptBuilder(ctx).build()
    ordered_chars = list(getattr(ctx, "resolved_render_chars", []) or [])
    plan_paths: list[str] = []
    try:
        from novelvideo.generators.prompt_builder import PromptComponents

        for entry in PromptComponents.build_reference_image_plan(ctx, ordered_chars):
            kind = str(entry.get("kind") or "")
            path = ""
            if kind in {"combined_composite", "composite", "portrait_only", "identity_portrait"}:
                path = str(entry.get("path") or "")
            elif kind in {"scene", "prop"}:
                ref = entry.get("ref")
                path = str(((getattr(ref, "image_paths", []) or [""])[0]) or "")
            if path:
                plan_paths.append(str(Path(path).resolve()))
    except Exception:
        plan_paths = []
    context["_freezone_render_reference_paths"] = plan_paths
    return prompt


def _content_for_context(context: dict[str, Any]) -> str:
    if context.get("scope") == "asset":
        lines = [
            f"Asset kind: {context.get('asset_kind')}",
            f"Character: {context.get('character') or ''}",
            f"Identity: {context.get('identity_id') or ''}",
            "",
            "[Notes]",
            "This preset is for editing a global project asset. Push affects every beat that uses it.",
        ]
        return "\n".join(lines)
    beat = context.get("beat_data") or {}
    return str(beat.get("visual_description") or "")


def _episode_overview_content(context: dict[str, Any]) -> str:
    beats = context.get("beats") if isinstance(context.get("beats"), list) else []
    return "\n".join(
        [
            f"Episode: {context.get('episode')}",
            f"Title: {context.get('episode_title') or ''}",
            f"Beats: {len(beats)}",
            "",
            "[Use]",
            "This canvas is an episode overview. Open a Beat Workbench for detailed edits.",
        ]
    )


def _episode_beat_content(episode: Any, beat: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Episode: {episode}",
            f"Beat: {beat.get('beat_number')}",
            f"Scene: {beat.get('scene_id') or ''}",
            "",
            "[Visual Description]",
            str(beat.get("visual_description") or ""),
            "",
            "[Narration]",
            str(beat.get("narration_segment") or ""),
        ]
    )


def _episode_beat_context(episode: Any, beat: dict[str, Any]) -> list[dict[str, Any]]:
    data = {
        "kind": "beat",
        "episode": episode,
        "beat": beat.get("beat_number"),
        "role": "beat_context",
        "label": f"EP{episode} Beat {beat.get('beat_number')} context",
        "visualDescription": beat.get("visual_description"),
        "narrationSegment": beat.get("narration_segment"),
        "sceneId": beat.get("scene_id"),
        "detectedIdentities": beat.get("detected_identities"),
        "detectedProps": beat.get("detected_props"),
    }
    return [{key: value for key, value in data.items() if value not in (None, "", [])}]


def _beat_context_mainline_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    beat = context.get("beat_data") or {}
    sketch_context = context.get("sketch_context") or {}
    scene_id = _normalize_scene_name(beat.get("scene_ref"))
    data = {
        "kind": "beat",
        "episode": context.get("episode"),
        "beat": context.get("beat"),
        "role": "beat_context",
        "label": f"EP{context.get('episode')} Beat {context.get('beat')} context",
        "visualDescription": beat.get("visual_description"),
        "narrationSegment": beat.get("narration_segment"),
        "sceneId": scene_id,
        "detectedIdentities": beat.get("detected_identities"),
        "detectedProps": beat.get("detected_props"),
        "sketchColors": sketch_context.get("sketch_colors"),
        "propMarkerColors": sketch_context.get("prop_marker_colors"),
    }
    return [{key: value for key, value in data.items() if value not in (None, "", [])}]


def _beat_context_edit_fields(context: dict[str, Any]) -> dict[str, Any]:
    beat = context.get("beat_data") or {}
    return {
        "visual_description": str(beat.get("visual_description") or ""),
        "scene_id": _normalize_scene_name(beat.get("scene_ref")),
        "time_of_day": str(beat.get("time_of_day") or ""),
        "detected_identities": [
            str(item or "").strip()
            for item in _as_list(beat.get("detected_identities"))
            if str(item or "").strip()
        ],
        "detected_props": [
            str(item or "").strip()
            for item in _as_list(beat.get("detected_props"))
            if str(item or "").strip()
        ],
    }
