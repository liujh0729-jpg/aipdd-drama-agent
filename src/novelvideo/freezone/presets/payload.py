"""画布载荷构建（build_canvas_payload_from_context）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .refs import _safe_id
from .constants import IMAGE_EXTS
from .aspect import _context_sketch_aspect_ratio
from .nodes import (
    _node_source_from_ref,
    _beat_target_placeholder_ref,
    _text_node,
    _skill_node,
    _beat_context_node,
    _beat_context_display_name,
    _image_gen_node,
    _asset_image_node,
    _ref_image_node,
    _edge,
    _skill_role_edge,
    _is_asset_library_reference,
)
from .prompts import (
    _prompt_text_node,
    _display_prompt_text,
    _character_profile_content,
    _prop_profile_content,
    _beat_sketch_marker_context,
    _beat_sketch_marker_content,
    _beat_sketch_prompt,
    _content_for_context,
    _episode_overview_content,
    _episode_beat_content,
    _episode_beat_context,
    _beat_context_mainline_context,
    _beat_context_edit_fields,
)
from .ref_collect import (
    _normalize_scene_name,
    _as_list,
    _prop_id_from_item,
    _identity_id_from_item,
    _real_identity_ids,
    _real_prop_ids,
    _beat_scene_source_urls,
)
from .roles import (
    DIRECTOR_CAPTURE_IMAGE_ROLES,
    DIRECTOR_CAPTURE_LAYOUT_POSITIONS,
    SKETCH_DIRECTOR_CAPTURE_LAYOUT_POSITIONS,
)
from .workflows import _append_scene_asset_workflow, _append_director_capture_workflow

from novelvideo.freezone import presets as _presets


def build_canvas_payload_from_context(
    *,
    context: dict[str, Any],
    preset_key: str,
    default_push_target: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    # 默认丢掉文件不存在的 ref(画布只显示已生成的资产),但是
    # 部分 canonical slots 缺文件时也要显示 placeholder,否则用户无法在
    # Freezone 里生成并 commit 回主线。
    _always_keep_roles = {
        "selected_background",
        "character_identity",
        "identity_portrait",
        "identity_costume",
        "prop_reference",
        "scene_master",
        "scene_reverse_master",
        "scene_director_pano_360",
    }
    _hidden_roles = {"scene_spatial_layout"}
    refs = [
        r
        for r in context.get("refs", [])
        if str(r.get("role") or "") not in _hidden_roles
        and ((r.get("exists") and r.get("url")) or str(r.get("role") or "") in _always_keep_roles)
    ]
    image_refs = [
        r
        for r in refs
        if Path(str(r.get("rel_path") or "")).suffix.lower()
        in {*IMAGE_EXTS, ".ply", ".sog", ".splat", ".ksplat", ".spz"}
    ]
    if context.get("scope") == "asset":
        image_refs = [
            r
            for r in image_refs
            if str(r.get("role") or "") not in {"related_sketch", "related_director_combined"}
        ]
    is_beat_scope = context.get("scope") == "beat"
    is_sketch_primary = is_beat_scope and str(context.get("primary_slot") or "").strip() == "sketch"
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if context.get("scope") == "beat":
        beat_context_pos = (-1840, -820) if is_sketch_primary else (-1840, -720)
        nodes.append(
            _beat_context_node(
                "context_beat",
                beat_context_pos[0],
                beat_context_pos[1],
                _beat_context_display_name(context),
                _content_for_context(context),
                mainline_context=_beat_context_mainline_context(context),
                edit_fields=_beat_context_edit_fields(context),
                extra_data={
                    "__freezone_source": {
                        "kind": "beat_context",
                        "role": "beat_context",
                        "label": f"EP{context.get('episode')} Beat {context.get('beat')} context",
                        "meta": {
                            "episode": context.get("episode"),
                            "beat": context.get("beat"),
                        },
                    },
                },
            )
        )
    elif context.get("scope") == "episode":
        episode = context.get("episode")
        nodes.append(
            _text_node(
                "context_episode",
                -1120,
                -520,
                f"Episode {episode} Context",
                _episode_overview_content(context),
                {
                    "__freezone_source": {
                        "kind": "episode_context",
                        "role": "episode_context",
                        "label": f"EP{episode} context",
                        "meta": {"episode": episode},
                    },
                },
            )
        )
        background_node_ids: dict[int, str] = {}
        identity_node_ids: dict[str, str] = {}
        prop_node_ids: dict[str, str] = {}
        beat_row_y_by_number: dict[int, int] = {}
        for beat_idx, beat in enumerate(context.get("beats") or []):
            try:
                beat_number = int(beat.get("beat_number") or 0)
            except (TypeError, ValueError):
                beat_number = 0
            if beat_number > 0:
                beat_row_y_by_number[beat_number] = -520 + beat_idx * 260

        def _episode_beat_row_y(beat_number: int, fallback_index: int) -> int:
            return beat_row_y_by_number.get(beat_number, -520 + fallback_index * 260)

        def _episode_ref_for(
            *,
            role: str,
            meta_key: str,
            meta_value: str,
        ) -> dict[str, Any] | None:
            for ref in image_refs:
                if str(ref.get("role") or "") != role:
                    continue
                meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
                if str(meta.get(meta_key) or "") == meta_value:
                    return ref
            return None

        def _episode_identity_ref(identity_id: str, character: str) -> dict[str, Any] | None:
            return _episode_ref_for(
                role="character_identity",
                meta_key="identity_id",
                meta_value=identity_id,
            )

        def _episode_background_ref(beat_number: int) -> dict[str, Any] | None:
            for ref in image_refs:
                if str(ref.get("role") or "") != "selected_background":
                    continue
                meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
                try:
                    if int(meta.get("beat") or 0) == int(beat_number):
                        return ref
                except (TypeError, ValueError):
                    continue
            return None

        for idx, background in enumerate(context.get("backgrounds") or []):
            try:
                beat_number = int(background.get("beat_number") or 0)
            except (TypeError, ValueError):
                beat_number = 0
            if beat_number <= 0:
                continue
            node_id = f"context_background_{beat_number:03d}"
            background_node_ids[beat_number] = node_id
            background_ref = _episode_background_ref(beat_number)
            row_y = _episode_beat_row_y(beat_number, idx)
            if background_ref:
                nodes.append(_ref_image_node(node_id, -560, row_y, background_ref))
            else:
                nodes.append(
                    _text_node(
                        node_id,
                        -560,
                        row_y,
                        f"Background · Beat {beat_number}",
                        "\n".join(
                            [
                                f"Beat: {beat_number}",
                                f"Scene: {background.get('scene_id') or ''}",
                                "No selected_background image found yet.",
                            ]
                        ),
                        {
                            "mainline_context": [
                                {
                                    "kind": "selected_background",
                                    "episode": episode,
                                    "beat": beat_number,
                                    "sceneId": background.get("scene_id"),
                                    "role": "selected_background",
                                }
                            ],
                            "mainline_role": "context",
                        },
                    )
                )
        for idx, identity in enumerate(context.get("identities") or []):
            identity_id = str(identity.get("identity_id") or "").strip()
            if not identity_id:
                continue
            node_id = f"context_identity_{_safe_id(identity_id, fallback='identity')}"
            identity_node_ids[identity_id] = node_id
            identity_ref = _episode_identity_ref(
                identity_id,
                str(identity.get("character") or ""),
            )
            if identity_ref:
                nodes.append(_ref_image_node(node_id, -1120, 160 + idx * 260, identity_ref))
            else:
                nodes.append(
                    _text_node(
                        node_id,
                        -1120,
                        160 + idx * 180,
                        f"Identity · {identity_id}",
                        "\n".join(
                            [
                                f"Identity: {identity_id}",
                                f"Character: {identity.get('character') or ''}",
                            ]
                        ),
                        {
                            "mainline_context": [
                                {
                                    "kind": "identity",
                                    "identityId": identity_id,
                                    "character": identity.get("character"),
                                    "role": "identity_context",
                                }
                            ],
                            "mainline_role": "context",
                        },
                    )
                )
        for idx, prop in enumerate(context.get("props") or []):
            prop_id = str(prop.get("prop_id") or "").strip()
            if not prop_id:
                continue
            node_id = f"context_prop_{_safe_id(prop_id, fallback='prop')}"
            prop_node_ids[prop_id] = node_id
            prop_ref = _episode_ref_for(
                role="prop_reference",
                meta_key="prop_id",
                meta_value=prop_id,
            )
            if prop_ref:
                nodes.append(_ref_image_node(node_id, -560, 160 + idx * 260, prop_ref))
            else:
                nodes.append(
                    _text_node(
                        node_id,
                        -560,
                        160 + idx * 180,
                        f"Prop · {prop_id}",
                        f"Prop: {prop_id}",
                        {
                            "mainline_context": [
                                {"kind": "prop", "propId": prop_id, "role": "prop_context"}
                            ],
                            "mainline_role": "context",
                        },
                    )
                )
        for idx, beat in enumerate(context.get("beats") or []):
            beat_number = int(beat.get("beat_number") or 0)
            if beat_number <= 0:
                continue
            row_y = -520 + idx * 260
            node_id = f"context_beat_{beat_number:03d}"
            beat_mainline_context = _episode_beat_context(episode, beat)
            nodes.append(
                _beat_context_node(
                    node_id,
                    160,
                    row_y,
                    f"Beat {beat_number}",
                    _episode_beat_content(episode, beat),
                    mainline_context=beat_mainline_context,
                    edit_fields={
                        "visual_description": beat.get("visual_description") or "",
                        "scene_id": (
                            (beat.get("scene_ref") or {}).get("scene_id")
                            if isinstance(beat.get("scene_ref"), dict)
                            else ""
                        ),
                        "time_of_day": beat.get("time_of_day") or "",
                        "detected_identities": beat.get("detected_identities") or [],
                        "detected_props": beat.get("detected_props") or [],
                    },
                    extra_data={
                        "mainline_role": "context",
                        "workbench_target": {
                            "scope": "beat",
                            "episode": episode,
                            "beat": beat_number,
                        },
                    },
                )
            )
            edges.append(
                _edge(
                    f"edge_episode_to_beat_{beat_number:03d}",
                    node_id,
                    "context_episode",
                )
            )
            if beat_number in background_node_ids:
                edges.append(
                    _edge(
                        f"edge_background_{beat_number:03d}_to_beat_{beat_number:03d}",
                        node_id,
                        background_node_ids[beat_number],
                        {"edgeKind": "mainline_data", "propagates": True},
                    )
                )
            linked_identity_ids: set[str] = set()
            for identity_id in beat.get("detected_identities") or []:
                identity_id = str(identity_id or "").strip()
                if identity_id in identity_node_ids and identity_id not in linked_identity_ids:
                    linked_identity_ids.add(identity_id)
                    edges.append(
                        _edge(
                            f"edge_identity_{_safe_id(identity_id)}_to_beat_{beat_number:03d}",
                            node_id,
                            identity_node_ids[identity_id],
                        )
                    )
            linked_prop_ids: set[str] = set()
            for prop_id in beat.get("detected_props") or []:
                prop_id = str(prop_id or "").strip()
                if prop_id in prop_node_ids and prop_id not in linked_prop_ids:
                    linked_prop_ids.add(prop_id)
                    edges.append(
                        _edge(
                            f"edge_prop_{_safe_id(prop_id)}_to_beat_{beat_number:03d}",
                            node_id,
                            prop_node_ids[prop_id],
                        )
                    )
    ref_node_ids: list[tuple[dict[str, Any], str]] = []

    if context.get("scope") == "beat":
        beat_data = context.get("beat_data") or {}
        marker_context = _beat_sketch_marker_context(context)
        if is_sketch_primary:
            render_prompt = _beat_sketch_prompt(context, marker_context)
        else:
            render_prompt = _presets._beat_render_prompt(context)
        if is_sketch_primary:
            nodes.append(
                _text_node(
                    "sketch_marker_context",
                    -1840,
                    -420,
                    "Sketch marker context",
                    _beat_sketch_marker_content(marker_context),
                    {
                        "__freezone_source": {
                            "kind": "beat_marker_context",
                            "role": "sketch_marker_context",
                            "label": f"EP{context.get('episode')} Beat {context.get('beat')} marker context",
                            "meta": {
                                "episode": context.get("episode"),
                                "beat": context.get("beat"),
                            },
                        }
                    },
                )
            )
        nodes.append(
            _prompt_text_node(
                "prompt_beat_visual",
                -1840,
                0 if is_sketch_primary else -260,
                "Beat sketch prompt" if is_sketch_primary else "Beat render prompt",
                _display_prompt_text(
                    render_prompt,
                    fallback_note="（当前 Beat 没有视觉描述或关键帧提示词）",
                ),
                source_meta={
                    "kind": "beat_prompt",
                    "role": "beat_visual_prompt",
                    "label": f"EP{context.get('episode')} Beat {context.get('beat')} visual prompt",
                    "meta": {"episode": context.get("episode"), "beat": context.get("beat")},
                },
            )
        )
        by_role = {str(ref.get("role") or ""): ref for ref in image_refs}
        primary_slot = str(context.get("primary_slot") or "").strip()
        beat_layout: list[tuple[str, int, int]] = (
            [("current_sketch", 520, -700)]
            if is_sketch_primary
            else [
                ("current_sketch", 520, -700),
                ("current_frame", 1680, 260),
            ]
        )
        preset_aspect_ratio = _context_sketch_aspect_ratio(context)
        for role_name, x, y in beat_layout:
            ref = by_role.get(role_name)
            if not ref:
                ref = _beat_target_placeholder_ref(
                    episode=context.get("episode"),
                    beat=context.get("beat"),
                    primary_slot=primary_slot,
                    role=role_name,
                    aspect_ratio=preset_aspect_ratio,
                )
                if not ref:
                    continue
            role = _safe_id(role_name, fallback="beat_ref")
            node_id = f"ref_{role}_1"
            if ref.get("url"):
                slot_node = _asset_image_node(node_id, x, y, ref)
            else:
                slot_node = _image_gen_node(
                    node_id,
                    x,
                    y,
                    str(ref.get("label") or role_name),
                    "",
                    aspect_ratio=str(ref.get("aspect_ratio") or "1:1"),
                    source_meta=_node_source_from_ref(ref),
                )
            # Preset-managed canonical slots keep only commit metadata here.
            # Generic SkillNode runtime metadata is introduced in later phases.
            ep_num = context.get("episode")
            beat_num = context.get("beat")
            if (
                role_name == "current_frame"
                and isinstance(ep_num, int)
                and isinstance(beat_num, int)
            ):
                slot_node.setdefault("data", {}).update(
                    {
                        "committed_slot_url": ref.get("url"),
                        "slot_target": {
                            "kind": "frame",
                            "episode": ep_num,
                            "beat": beat_num,
                        },
                    }
                )
            elif (
                role_name == "current_sketch"
                and isinstance(ep_num, int)
                and isinstance(beat_num, int)
            ):
                slot_node.setdefault("data", {}).update(
                    {
                        "committed_slot_url": ref.get("url"),
                        "slot_target": {
                            "kind": "sketch",
                            "episode": ep_num,
                            "beat": beat_num,
                        },
                    }
                )
            nodes.append(slot_node)
            ref_node_ids.append((ref, node_id))

        def _beat_ref_position(
            ref: dict[str, Any],
            *,
            kind: str,
            index: int,
        ) -> tuple[int, int]:
            role = str(ref.get("role") or "")
            if is_sketch_primary:
                if role == "character_portrait":
                    return -1500, 520 + (index // 2) * 360
                if role == "character_identity":
                    return -1060, 520 + (index // 2) * 360
                if role in DIRECTOR_CAPTURE_IMAGE_ROLES:
                    fallback_x = -1500 + (index % 2) * 440
                    fallback_y = 1420 + (index // 2) * 360
                    return SKETCH_DIRECTOR_CAPTURE_LAYOUT_POSITIONS.get(
                        role,
                        (fallback_x, fallback_y),
                    )
                base_positions = {
                    "identity": (-1060, 520),
                    "prop": (-1060, 1040),
                    "scene": (50, 510),
                    "director": (2520, -640),
                    "sketch": (520, 1040),
                    "frame": (1680, 1040),
                    "render": (2120, 1040),
                }
                base_x, base_y = base_positions.get(kind, (80, 820))
                if kind == "director":
                    return base_x + (index % 2) * 620, base_y + (index // 2) * 330
                return base_x + (index % 2) * 420, base_y + (index // 2) * 360
            if role in DIRECTOR_CAPTURE_IMAGE_ROLES:
                if role == "selected_background":
                    return -1080, -1040
                if role == "director_combined":
                    return -1080, -360
                return DIRECTOR_CAPTURE_LAYOUT_POSITIONS.get(role, (-1500, 1420))
            if role in {"prop_reference", "prop_3view", "prop_turnaround"}:
                return -1080, 1040 + index * 360
            if role == "character_portrait":
                return -1500, 420 + ((max(index, 1) - 1) // 2) * 360
            if role == "character_identity":
                return -1080, 420 + (index // 2) * 360
            base_positions = {
                "identity": (-1080, 420),
                "prop": (-1080, 1040),
                "scene": (80, 300),
                "director": (-1500, 1420),
                "sketch": (520, 1040),
                "frame": (1680, 1040),
                "render": (2120, 1040),
            }
            base_x, base_y = base_positions.get(kind, (80, 1120))
            return base_x + (index % 2) * 360, base_y + (index // 2) * 360

        beat_bucket_order = ["identity", "prop", "scene", "director", "sketch", "frame", "render"]
        role_counts: dict[str, int] = {
            str(ref.get("role") or ""): 1 for ref, _node_id in ref_node_ids
        }
        for kind in beat_bucket_order:
            kind_refs = [r for r in image_refs if r.get("kind") == kind]
            if not kind_refs:
                continue
            idx_for_kind = 0
            for ref in kind_refs[:12]:
                role_name = str(ref.get("role") or "")
                kind_name = str(ref.get("kind") or "")
                if (
                    kind_name == "scene" or role_name.startswith("scene_")
                ) and role_name not in DIRECTOR_CAPTURE_IMAGE_ROLES:
                    continue
                if role_name in {
                    "current_sketch",
                    "current_frame",
                }:
                    continue
                role = _safe_id(str(ref.get("role") or kind), fallback=kind)
                role_counts[role] = role_counts.get(role, 0) + 1
                node_id = f"ref_{role}_{role_counts[role]}"
                x, y = _beat_ref_position(ref, kind=kind, index=idx_for_kind)
                ref_node = _ref_image_node(node_id, x, y, ref)
                if role_name == "selected_background":
                    ref_node.setdefault("data", {}).update(
                        {
                            "committed_slot_url": ref.get("url"),
                            "slot_target": {
                                "kind": "selected_background",
                                "episode": context.get("episode"),
                                "beat": context.get("beat"),
                            },
                        }
                    )
                nodes.append(ref_node)
                ref_node_ids.append((ref, node_id))
                idx_for_kind += 1

        current_sketch_id = next(
            (node_id for ref, node_id in ref_node_ids if ref.get("role") == "current_sketch"),
            None,
        )
        current_frame_id = next(
            (node_id for ref, node_id in ref_node_ids if ref.get("role") == "current_frame"),
            None,
        )
        selected_background_id = next(
            (node_id for ref, node_id in ref_node_ids if ref.get("role") == "selected_background"),
            None,
        )
        director_combined_id = next(
            (node_id for ref, node_id in ref_node_ids if ref.get("role") == "director_combined"),
            None,
        )
        beat_data = context.get("beat_data") if isinstance(context.get("beat_data"), dict) else {}
        detected_identity_id_list = _real_identity_ids(
            _identity_id_from_item(item) for item in _as_list(beat_data.get("detected_identities"))
        )
        detected_identity_ids = set(detected_identity_id_list)
        detected_prop_id_list = _real_prop_ids(
            _prop_id_from_item(item) for item in _as_list(beat_data.get("detected_props"))
        )
        detected_prop_ids = set(detected_prop_id_list)

        def _matched_identity_target_id(ref: dict[str, Any]) -> str:
            if not detected_identity_ids:
                return ""
            meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
            identity_id = str(meta.get("identity_id") or "").strip()
            if identity_id and identity_id in detected_identity_ids:
                return identity_id
            character = str(meta.get("character") or "").strip()
            label = str(ref.get("label") or "").strip()
            for candidate in (character, label):
                if candidate and candidate in detected_identity_ids:
                    return candidate
            if character:
                character_identity_ids = [
                    item for item in detected_identity_id_list if item.startswith(f"{character}_")
                ]
                if len(character_identity_ids) == 1:
                    return character_identity_ids[0]
            return identity_id if identity_id and not detected_identity_ids else ""

        def _matched_prop_target_id(ref: dict[str, Any]) -> str:
            if not detected_prop_ids:
                return ""
            meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
            prop_id = str(meta.get("prop_id") or "").strip()
            if prop_id and prop_id in detected_prop_ids:
                return prop_id
            label = str(ref.get("label") or "").strip()
            if label and label in detected_prop_ids:
                return label
            return prop_id if prop_id and not detected_prop_ids else ""

        def _ref_matches_detected_identity(ref: dict[str, Any]) -> bool:
            return bool(_matched_identity_target_id(ref))

        def _ref_matches_detected_prop(ref: dict[str, Any]) -> bool:
            return bool(_matched_prop_target_id(ref))

        identity_node_refs_by_target: dict[str, tuple[int, str, str]] = {}
        for ref, node_id in ref_node_ids:
            role = str(ref.get("role") or "").strip()
            if role not in {"character_identity", "character_portrait"}:
                continue
            target_identity_id = _matched_identity_target_id(ref)
            if not target_identity_id:
                continue
            priority = 0 if role == "character_identity" else 1
            existing = identity_node_refs_by_target.get(target_identity_id)
            if existing is None or priority < existing[0]:
                identity_node_refs_by_target[target_identity_id] = (
                    priority,
                    node_id,
                    target_identity_id,
                )
        identity_node_refs = [
            (node_id, target_identity_id)
            for _priority, node_id, target_identity_id in identity_node_refs_by_target.values()
        ]
        prop_node_refs = [
            (node_id, _matched_prop_target_id(ref))
            for ref, node_id in ref_node_ids
            if ref.get("role") == "prop_reference" and _ref_matches_detected_prop(ref)
        ]

        skill_background_sketch_id = "skill_sketch_from_background"
        skill_director_sketch_id = "skill_sketch_from_director_combined"
        skill_set_background_id = "skill_set_selected_background"
        skill_set_director_combined_id = "skill_set_director_combined"
        skill_frame_id = "skill_frame_from_context"
        nodes.extend(
            [
                _skill_node(
                    skill_set_background_id,
                    -1560,
                    -1240,
                    skill_id="freezone.set_selected_background",
                    display_name="设为当前背景",
                    extra_data={"scene_source_urls": _beat_scene_source_urls(context)},
                ),
                _skill_node(
                    skill_set_director_combined_id,
                    -1560,
                    -620,
                    skill_id="freezone.set_director_combined",
                    display_name="设为导演合成图",
                    extra_data={"scene_source_urls": _beat_scene_source_urls(context)},
                ),
                _skill_node(
                    skill_background_sketch_id,
                    -360,
                    -1040,
                    skill_id="freezone.sketch_from_context",
                    display_name="Sketch from selected background",
                    extra_data={"parameters": {"aspect_ratio": "2:3"}},
                ),
                _skill_node(
                    skill_director_sketch_id,
                    -360,
                    -360,
                    skill_id="freezone.sketch_from_director_combined",
                    display_name="从导演合成图生成草图",
                    extra_data={"parameters": {"aspect_ratio": "2:3"}},
                ),
            ]
        )
        if not is_sketch_primary:
            nodes.append(
                _skill_node(
                    skill_frame_id,
                    1020,
                    260,
                    skill_id="freezone.frame_from_context",
                    display_name="Frame from context",
                    extra_data={"parameters": {"quality": "medium"}},
                )
            )

        def _append_skill_input_edge(
            edge_id: str,
            source_id: str | None,
            target_id: str,
            *,
            role: str,
            label: str,
            target_handle: str | None = None,
            extra_data: dict[str, Any] | None = None,
        ) -> None:
            if not source_id:
                return
            edges.append(
                _skill_role_edge(
                    edge_id,
                    source_id,
                    target_id,
                    role=role,
                    label=label,
                    target_handle=target_handle,
                    extra_data=extra_data,
                )
            )

        context_skill_ids = [
            skill_set_background_id,
            skill_set_director_combined_id,
            skill_background_sketch_id,
            skill_director_sketch_id,
        ]
        if not is_sketch_primary:
            context_skill_ids.append(skill_frame_id)
        for skill_id in context_skill_ids:
            _append_skill_input_edge(
                f"edge_context_beat_to_{skill_id}_beat_context",
                "context_beat",
                skill_id,
                role="beat_context",
                label="Beat context",
            )
        if selected_background_id:
            edges.append(
                _edge(
                    f"edge_{skill_set_background_id}_to_{_safe_id(selected_background_id)}",
                    skill_set_background_id,
                    selected_background_id,
                    {
                        "edgeKind": "mainline_data",
                        "propagates": True,
                        "role": "selected_background",
                        "label": "当前背景",
                    },
                    source_handle="selected_background",
                )
            )
        if director_combined_id:
            edges.append(
                _edge(
                    f"edge_{skill_set_director_combined_id}_to_{_safe_id(director_combined_id)}",
                    skill_set_director_combined_id,
                    director_combined_id,
                    {
                        "edgeKind": "mainline_data",
                        "propagates": True,
                        "role": "director_combined",
                        "label": "导演合成图",
                    },
                    source_handle="director_combined",
                )
            )
        _append_skill_input_edge(
            "edge_selected_background_to_skill_sketch_background",
            selected_background_id,
            skill_background_sketch_id,
            role="background",
            label="Background",
        )
        if not is_sketch_primary:
            _append_skill_input_edge(
                "edge_selected_background_to_skill_frame_background",
                selected_background_id,
                skill_frame_id,
                role="background",
                label="Background",
            )
        _append_skill_input_edge(
            "edge_director_combined_to_skill_sketch_director_combined",
            director_combined_id,
            skill_director_sketch_id,
            role="director_combined",
            label="导演合成图",
        )
        if not is_sketch_primary:
            _append_skill_input_edge(
                "edge_current_sketch_to_skill_frame_sketch",
                current_sketch_id,
                skill_frame_id,
                role="sketch",
                label="Sketch",
            )
            for index, (identity_node_id, identity_id) in enumerate(identity_node_refs, start=1):
                _append_skill_input_edge(
                    f"edge_{_safe_id(identity_node_id)}_to_skill_frame_identity_{index}",
                    identity_node_id,
                    skill_frame_id,
                    role="identity",
                    label="Identity",
                    target_handle=f"identity:{identity_id}",
                    extra_data={
                        "reference_target": {
                            "kind": "identity",
                            "identity_id": identity_id,
                        }
                    },
                )
            for index, (prop_node_id, prop_id) in enumerate(prop_node_refs, start=1):
                _append_skill_input_edge(
                    f"edge_{_safe_id(prop_node_id)}_to_skill_frame_prop_{index}",
                    prop_node_id,
                    skill_frame_id,
                    role="prop",
                    label="Prop",
                    target_handle=f"prop:{prop_id}",
                    extra_data={
                        "reference_target": {
                            "kind": "prop",
                            "prop_id": prop_id,
                        }
                    },
                )

        if current_sketch_id:
            for sketch_skill_id in (skill_background_sketch_id, skill_director_sketch_id):
                edges.append(
                    _edge(
                        f"edge_{sketch_skill_id}_to_{_safe_id(current_sketch_id)}",
                        sketch_skill_id,
                        current_sketch_id,
                        {
                            "edgeKind": "mainline_data",
                            "propagates": True,
                            "role": "current_sketch_candidate",
                            "label": "Current sketch candidate",
                        },
                        source_handle="current_sketch_candidate",
                    )
                )
        if not is_sketch_primary and current_frame_id:
            edges.append(
                _edge(
                    f"edge_{skill_frame_id}_to_{_safe_id(current_frame_id)}",
                    skill_frame_id,
                    current_frame_id,
                    {
                        "edgeKind": "mainline_data",
                        "propagates": True,
                        "role": "current_frame_candidate",
                        "label": "Current frame candidate",
                    },
                    source_handle="current_frame_candidate",
                )
            )

        # Visualization edges for prompt / marker context. annotation kind so
        # they show the prompt provenance without participating in mainline
        # data propagation (backend builds the prompt itself from DB).
        if is_sketch_primary:
            edges.append(
                _edge(
                    "edge_sketch_marker_context_to_prompt",
                    "sketch_marker_context",
                    "prompt_beat_visual",
                    {"edgeKind": "annotation"},
                )
            )
        edges.append(
            _edge(
                "edge_beat_context_to_prompt",
                "context_beat",
                "prompt_beat_visual",
                {"edgeKind": "mainline_data", "propagates": True},
            )
        )

        # current_sketch / current_frame canonical slots are outputs of
        # SkillNodes. Runtime inputs stay on SkillNode role_binding edges so the
        # visible graph matches left-input → skill → right-output flow.
        _append_director_capture_workflow(
            edges=edges,
            ref_node_ids=ref_node_ids,
        )
        metadata = {
            "preset": {
                "preset_key": preset_key,
                "scope": context.get("scope"),
                "episode": context.get("episode"),
                "beat": context.get("beat"),
                "primary_slot": context.get("primary_slot"),
                "created_at": created_at or datetime.now().isoformat(),
            },
            "workbench": {"kind": "beat", "primary_slot": context.get("primary_slot")},
            "references": [ref for ref in refs if _is_asset_library_reference(ref)],
            "beat_context": context.get("beat_data"),
            "prop_menu": context.get("prop_menu") or [],
        }
        metadata["director_capture"] = {
            "episode": context.get("episode"),
            "beat": context.get("beat"),
            "scene_id": _normalize_scene_name(beat_data.get("scene_ref")),
            "node_id": "director_capture",
        }
        return {
            "nodes": nodes,
            "edges": edges,
            "viewport": {"x": 960, "y": 540, "zoom": 0.65},
            "metadata": metadata,
        }

    buckets = [
        ("sketch", 260, -340),
        ("frame", 620, -340),
        ("render", 980, -340),
        ("identity", 260, 140),
        ("scene", 620, 140),
        ("prop", 980, 140),
        ("director", 260, 620),
    ]
    role_counts: dict[str, int] = {}
    if context.get("scope") != "episode":
        for kind, base_x, base_y in buckets:
            kind_refs = [r for r in image_refs if r.get("kind") == kind]
            if not kind_refs:
                continue
            for idx, ref in enumerate(kind_refs[:12]):
                role = _safe_id(str(ref.get("role") or kind), fallback=kind)
                role_counts[role] = role_counts.get(role, 0) + 1
                node_id = f"ref_{role}_{role_counts[role]}"
                x = base_x + (idx % 2) * 420
                y = base_y + (idx // 2) * 360
                nodes.append(_ref_image_node(node_id, x, y, ref))
                ref_node_ids.append((ref, node_id))

    def _find_ref_node_id(
        *,
        role: str,
        kind: str | None = None,
        identity_id: str | None = None,
    ) -> str | None:
        for ref, node_id in ref_node_ids:
            if str(ref.get("role") or "") != role:
                continue
            if kind and str(ref.get("kind") or "") != kind:
                continue
            meta = ref.get("meta") or {}
            if identity_id is not None and str(meta.get("identity_id") or "") != identity_id:
                continue
            return node_id
        return None

    def _find_ref_node_ids(
        *,
        role: str,
        kind: str | None = None,
        identity_id: str | None = None,
    ) -> list[str]:
        node_ids: list[str] = []
        for ref, node_id in ref_node_ids:
            if str(ref.get("role") or "") != role:
                continue
            if kind and str(ref.get("kind") or "") != kind:
                continue
            meta = ref.get("meta") or {}
            if identity_id is not None and str(meta.get("identity_id") or "") != identity_id:
                continue
            node_ids.append(node_id)
        return node_ids

    def _set_node_position(node_id: str, x: int, y: int) -> None:
        for node in nodes:
            if node.get("id") == node_id:
                node["position"] = {"x": x, "y": y}
                return

    def _node_image_url(node_id: str | None) -> str | None:
        if not node_id:
            return None
        for node in nodes:
            if node.get("id") != node_id:
                continue
            data = node.get("data") or {}
            for key in ("imageUrl", "previewImageUrl", "referenceImageUrl"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    def _update_node_data(node_id: str | None, data: dict[str, Any]) -> None:
        if not node_id:
            return
        for node in nodes:
            if node.get("id") == node_id:
                node.setdefault("data", {}).update(data)
                return

    def _append_edge_if_missing(edge_id: str, source: str | None, target: str | None) -> None:
        if not source or not target:
            return
        for edge in edges:
            if edge.get("source") == source and edge.get("target") == target:
                return
        edges.append(_edge(edge_id, source, target))

    _append_scene_asset_workflow(nodes=nodes, edges=edges, ref_node_ids=ref_node_ids)

    if context.get("scope") == "asset":
        generation_context = context.get("generation_context") or {}
        asset_kind = str(context.get("asset_kind") or "").strip()
        character = str(context.get("character") or "").strip()
        character_profile = generation_context.get("character_profile") or {}
        if character_profile:
            nodes.append(
                _text_node(
                    "character_profile",
                    -1040,
                    -560,
                    "Character profile",
                    _character_profile_content(character_profile),
                    {
                        "__freezone_source": {
                            "kind": "character_profile",
                            "role": "character_context",
                            "label": f"{character} profile",
                            "meta": {"character": character},
                        }
                    },
                )
            )
        portrait_data = generation_context.get("portrait") or {}
        portrait_prompt = str(portrait_data.get("prompt") or "").strip()
        portrait_ref_id = _find_ref_node_id(role="character_portrait", kind="identity")
        portrait_flow_id = None
        portrait_sink_id = portrait_ref_id
        if portrait_prompt:
            portrait_prompt_id = "prompt_character_portrait"
            portrait_prompt_text = _display_prompt_text(
                portrait_prompt,
                fallback_note="（当前没有可复用的 Portrait 提示词）",
            )
            nodes.append(
                _prompt_text_node(
                    portrait_prompt_id,
                    -1040,
                    -220,
                    "Portrait prompt",
                    portrait_prompt_text,
                    source_meta={
                        "kind": "portrait_prompt",
                        "role": "character_portrait_prompt",
                        "label": f"{character} portrait prompt",
                        "meta": {"character": character},
                    },
                )
            )
            if portrait_ref_id:
                _set_node_position(portrait_ref_id, -560, -220)
                _append_edge_if_missing(
                    "edge_prompt_character_portrait_to_ref",
                    portrait_prompt_id,
                    portrait_ref_id,
                )
            else:
                portrait_flow_id = "flow_character_portrait"
                nodes.append(
                    _image_gen_node(
                        portrait_flow_id,
                        -560,
                        -220,
                        "Portrait generation",
                        portrait_prompt,
                        aspect_ratio="3:4",
                        source_meta={
                            "kind": "portrait",
                            "role": "character_portrait_workflow",
                            "label": f"{character} portrait",
                            "meta": {"character": character},
                        },
                    )
                )
                _append_edge_if_missing(
                    "edge_prompt_to_flow_character_portrait",
                    portrait_prompt_id,
                    portrait_flow_id,
                )
                portrait_sink_id = portrait_flow_id
        elif portrait_ref_id:
            _set_node_position(portrait_ref_id, -560, -220)
        # Portrait sink keeps commit metadata for candidate push/default target.
        if portrait_sink_id:
            existing_committed_url: Any = None
            for node in nodes:
                if node.get("id") == portrait_sink_id:
                    existing_committed_url = (node.get("data") or {}).get("imageUrl")
                    break
            _update_node_data(
                portrait_sink_id,
                {
                    "committed_slot_url": existing_committed_url,
                    "slot_target": {
                        "kind": "portrait",
                        "character": character,
                    },
                    "prompt": portrait_prompt,
                    "aspectRatio": "3:4",
                    "autoCommitOnGenerate": True,
                },
            )
        identity_contexts = generation_context.get("identities") or []
        if asset_kind == "portrait":
            identity_contexts = []
        usage_context_ids = [
            *_find_ref_node_ids(role="related_sketch", kind="sketch"),
            *_find_ref_node_ids(role="related_director_combined", kind="director"),
        ]
        workflow_asset_ids: list[str] = []
        for idx, identity_ctx in enumerate(identity_contexts):
            current_identity_id = str(identity_ctx.get("identity_id") or "").strip()
            if not current_identity_id:
                continue
            safe_identity_id = _safe_id(current_identity_id, fallback="identity")
            identity_name = str(identity_ctx.get("identity_name") or current_identity_id).strip()
            prompt = str(identity_ctx.get("prompt") or "").strip()
            prompt_node_id = f"prompt_identity_{safe_identity_id}"
            flow_node_id = f"flow_identity_{safe_identity_id}"
            lane_y = 260 + idx * 520
            prompt_text = _display_prompt_text(
                prompt,
                fallback_note="（当前生成主要依赖参考图，无额外提示词）",
            )
            nodes.append(
                _prompt_text_node(
                    prompt_node_id,
                    -1040,
                    lane_y,
                    f"{identity_name} prompt",
                    prompt_text,
                    source_meta={
                        "kind": "identity_prompt",
                        "role": "identity_generation_prompt",
                        "label": f"{identity_name} prompt",
                        "meta": {
                            "character": character,
                            "identity_id": current_identity_id,
                            "identity_name": identity_name,
                        },
                    },
                )
            )
            identity_ref_id = _find_ref_node_id(
                role="character_identity",
                kind="identity",
                identity_id=current_identity_id,
            )
            identity_portrait_ref_id = _find_ref_node_id(
                role="identity_portrait",
                kind="identity",
                identity_id=current_identity_id,
            )
            identity_costume_ref_id = _find_ref_node_id(
                role="identity_costume",
                kind="identity",
                identity_id=current_identity_id,
            )
            identity_target_id = identity_ref_id or flow_node_id
            if identity_ref_id:
                _set_node_position(identity_ref_id, 620, lane_y + 90)
                for node in nodes:
                    if node.get("id") == identity_ref_id:
                        node.setdefault("data", {})["prompt"] = prompt
                        break
            else:
                nodes.append(
                    _image_gen_node(
                        flow_node_id,
                        620,
                        lane_y + 90,
                        f"{identity_name} generation",
                        prompt,
                        aspect_ratio="3:4",
                        source_meta={
                            "kind": "identity",
                            "role": "identity_workflow",
                            "label": identity_name,
                            "meta": {
                                "character": character,
                                "identity_id": current_identity_id,
                                "identity_name": identity_name,
                            },
                        },
                    )
                )
            # Identity sink keeps commit metadata for candidate push/default target.
            existing_identity_committed_url: Any = None
            for node in nodes:
                if node.get("id") == identity_target_id:
                    existing_identity_committed_url = (node.get("data") or {}).get("imageUrl")
                    break
            _update_node_data(
                identity_target_id,
                {
                    "committed_slot_url": existing_identity_committed_url,
                    "slot_target": {
                        "kind": "identity",
                        "character": character,
                        "identity_id": current_identity_id,
                    },
                    "prompt": prompt,
                    "aspectRatio": "3:4",
                    "autoCommitOnGenerate": True,
                },
            )
            # Age variant identity 没自己 identity_portrait 时,不 fallback 主
            # character portrait —— 主 portrait 是 youth 形态,拿来当中年/老年
            # 变体的视觉参考会误导(用户以为这个 age 变体已经有 portrait)。
            # 没参考就走 prompt-only 生成(portrait_flow_id),让用户先把该
            # 变体的专属 portrait 生成出来。
            is_age_variant = bool(identity_ctx.get("is_age_variant"))
            portrait_source_id = identity_portrait_ref_id or (
                (portrait_ref_id if not is_age_variant else None) or portrait_flow_id
            )
            if identity_portrait_ref_id:
                _set_node_position(identity_portrait_ref_id, -560, lane_y)
                existing_portrait_committed_url: Any = None
                for node in nodes:
                    if node.get("id") == identity_portrait_ref_id:
                        existing_portrait_committed_url = (node.get("data") or {}).get("imageUrl")
                        break
                portrait_prompt = str(
                    identity_ctx.get("identity_portrait_prompt")
                    or identity_ctx.get("face_prompt")
                    or identity_ctx.get("appearance_details")
                    or identity_ctx.get("identity_prompt")
                    or ""
                ).strip()
                _update_node_data(
                    identity_portrait_ref_id,
                    {
                        "committed_slot_url": existing_portrait_committed_url,
                        "slot_target": {
                            "kind": "identity_portrait",
                            "character": character,
                            "identity_id": current_identity_id,
                        },
                        "prompt": portrait_prompt,
                        "aspectRatio": "3:4",
                        "autoCommitOnGenerate": True,
                    },
                )
                _append_edge_if_missing(
                    f"edge_ref_identity_portrait_{safe_identity_id}_to_identity",
                    identity_portrait_ref_id,
                    identity_target_id,
                )
            if identity_costume_ref_id:
                _set_node_position(identity_costume_ref_id, -560, lane_y + 260)
                existing_costume_committed_url: Any = None
                for node in nodes:
                    if node.get("id") == identity_costume_ref_id:
                        existing_costume_committed_url = (node.get("data") or {}).get("imageUrl")
                        break
                costume_prompt = prompt
                _update_node_data(
                    identity_costume_ref_id,
                    {
                        "committed_slot_url": existing_costume_committed_url,
                        "slot_target": {
                            "kind": "identity_costume",
                            "character": character,
                            "identity_id": current_identity_id,
                        },
                        "prompt": costume_prompt,
                        "aspectRatio": "3:4",
                        "autoCommitOnGenerate": True,
                    },
                )
                _append_edge_if_missing(
                    f"edge_ref_identity_costume_{safe_identity_id}_to_identity",
                    identity_costume_ref_id,
                    identity_target_id,
                )
            _append_edge_if_missing(
                f"edge_prompt_identity_{safe_identity_id}_to_identity",
                prompt_node_id,
                identity_target_id,
            )
            if portrait_source_id and portrait_source_id != identity_portrait_ref_id:
                _append_edge_if_missing(
                    f"edge_ref_portrait_to_identity_{safe_identity_id}",
                    portrait_source_id,
                    identity_target_id,
                )
            reference_url = _node_image_url(portrait_source_id)
            if reference_url:
                _update_node_data(identity_target_id, {"referenceImageUrl": reference_url})
            workflow_asset_ids.append(identity_target_id)

        usage_source_ids = workflow_asset_ids or ([portrait_ref_id] if portrait_ref_id else [])
        for idx, node_id in enumerate(usage_context_ids[:8]):
            paired_y = 260 + idx * 260
            _set_node_position(node_id, 1080, paired_y)
            for source_id in usage_source_ids[:3]:
                _append_edge_if_missing(
                    f"edge_asset_{source_id}_to_usage_{node_id}",
                    source_id,
                    node_id,
                )

        prop_context = generation_context.get("prop") or {}
        if asset_kind in {"prop", "prop_ref"} and prop_context:
            prop_id = str(prop_context.get("prop_id") or context.get("asset_id") or "").strip()
            prop_ref_id = _find_ref_node_id(role="prop_reference", kind="prop")
            if prop_ref_id:
                _set_node_position(prop_ref_id, -120, -120)
            profile = prop_context.get("profile") or {}
            if profile:
                nodes.append(
                    _text_node(
                        "prop_profile",
                        -1040,
                        -520,
                        "Prop profile",
                        _prop_profile_content(profile),
                        {
                            "__freezone_source": {
                                "kind": "prop_profile",
                                "role": "prop_context",
                                "label": f"{prop_id} profile",
                                "meta": {"prop_id": prop_id},
                            }
                        },
                    )
                )
            prompt = str(prop_context.get("prompt") or "").strip()
            prop_committed_url: Any = None
            if prop_ref_id:
                for node in nodes:
                    if node.get("id") == prop_ref_id:
                        prop_committed_url = (node.get("data") or {}).get("imageUrl")
                        break
                _update_node_data(
                    prop_ref_id,
                    {
                        "committed_slot_url": prop_committed_url,
                        "slot_target": {
                            "kind": "prop_ref",
                            "prop_id": prop_id,
                        },
                        "prompt": prompt,
                        "aspectRatio": "1:1",
                        "requestAspectRatio": "auto",
                        "autoCommitOnGenerate": True,
                    },
                )
            _append_edge_if_missing(
                "edge_prop_profile_to_reference",
                "prop_profile" if profile else None,
                prop_ref_id,
            )

        # Scene master/reverse slots keep commit metadata for candidate push/default target.
        # scene_director_pano_360 — 该 ref 由 _add_scene_refs 渲染成 pano360ViewerNode
        # (sphere viewer, 不是 imageGenNode),所以额外 emit 一个 imageGenNode
        # canonical source placeholder (workflow_scene_360)。两个节点共享同一 slot 文件:
        #   - pano360 viewer:展示当前 canonical 文件
        #   - workflow trigger: candidate preview + Push target metadata
        #
        # 注:旧 \"scene_360\" role 已 deprecate (presets/ref_collect.py:413-417 注释),asset
        # preset 实际只 emit scene_director_pano_360。所以这里只查后者。
        scene_master_node_id = _find_ref_node_id(role="scene_master", kind="scene")
        scene_reverse_node_id = _find_ref_node_id(role="scene_reverse_master", kind="scene")
        scene_pano_viewer_node_id = _find_ref_node_id(
            role="scene_director_pano_360",
            kind="scene",
        ) or _find_ref_node_id(role="scene_360", kind="scene")
        # scene_id 来源: asset_id (scene preset_key 把它放 asset_id);兼容老路径
        # 也接 identity_id / character (有些 fixture 没分清)。
        scene_name_for_target = str(
            context.get("asset_id") or context.get("identity_id") or context.get("character") or ""
        ).strip()
        if scene_master_node_id and scene_name_for_target:
            existing_master_url: Any = None
            for node in nodes:
                if node.get("id") == scene_master_node_id:
                    existing_master_url = (node.get("data") or {}).get("imageUrl")
                    break
            _update_node_data(
                scene_master_node_id,
                {
                    "committed_slot_url": existing_master_url,
                    "slot_target": {
                        "kind": "scene_master",
                        "scene_id": scene_name_for_target,
                    },
                    "aspectRatio": "16:9",
                    "autoCommitOnGenerate": True,
                },
            )
        if scene_reverse_node_id and scene_name_for_target:
            existing_reverse_url: Any = None
            for node in nodes:
                if node.get("id") == scene_reverse_node_id:
                    existing_reverse_url = (node.get("data") or {}).get("imageUrl")
                    break
            _update_node_data(
                scene_reverse_node_id,
                {
                    "committed_slot_url": existing_reverse_url,
                    "slot_target": {
                        "kind": "scene_reverse_master",
                        "scene_id": scene_name_for_target,
                    },
                    "aspectRatio": "16:9",
                    "autoCommitOnGenerate": True,
                },
            )
        if scene_pano_viewer_node_id and scene_name_for_target:
            # scene_director_pano_360 是复杂 workflow (master + reverse +
            # prompt → 360 panorama),应该由显式 SkillNode 表达组合技能:
            # master/reverse/prompt → SkillNode → readonly canonical viewer。
            pano_viewer_pos = (1480, 460)
            pano_viewer_url: Any = None
            for node in nodes:
                node_id = node.get("id")
                if node_id == scene_pano_viewer_node_id:
                    pano_viewer_url = (node.get("data") or {}).get("imageUrl")
                    pos = node.get("position") or {}
                    pano_viewer_pos = (
                        int(pos.get("x") or pano_viewer_pos[0]),
                        int(pos.get("y") or pano_viewer_pos[1]),
                    )
            skill_360_node_id = "skill_scene_360"
            nodes.append(
                _skill_node(
                    skill_360_node_id,
                    pano_viewer_pos[0] - 760,  # inputs → skill → output viewer
                    pano_viewer_pos[1],
                    skill_id="freezone.scene_360",
                    display_name=f"{scene_name_for_target} Scene 360",
                )
            )
            if scene_master_node_id:
                edges.append(
                    _skill_role_edge(
                        f"edge_{_safe_id(scene_master_node_id)}_to_skill_scene_360_scene_master",
                        scene_master_node_id,
                        skill_360_node_id,
                        role="scene_master",
                        label="Scene master",
                    )
                )
            if scene_reverse_node_id:
                edges.append(
                    _skill_role_edge(
                        f"edge_{_safe_id(scene_reverse_node_id)}_to_skill_scene_360_scene_reverse_master",
                        scene_reverse_node_id,
                        skill_360_node_id,
                        role="scene_reverse_master",
                        label="Scene reverse master",
                    )
                )
            # generic scene prompt → skill input. prompt_id pattern:
            # "prompt_scene_<safe_scene_id>".
            generic_scene_prompt_id = (
                f"prompt_scene_{_safe_id(scene_name_for_target, fallback='scene')}"
            )
            if any(node.get("id") == generic_scene_prompt_id for node in nodes):
                edges.append(
                    _skill_role_edge(
                        f"edge_{generic_scene_prompt_id}_to_{_safe_id(skill_360_node_id)}_scene",
                        generic_scene_prompt_id,
                        skill_360_node_id,
                        role="scene",
                        label="Scene prompt",
                    )
                )
            _update_node_data(
                scene_pano_viewer_node_id,
                {
                    "committed_slot_url": pano_viewer_url,
                    "slot_target": {
                        "kind": "scene_director_pano_360",
                        "scene_id": scene_name_for_target,
                    },
                },
            )
            edges.append(
                _edge(
                    f"edge_{_safe_id(skill_360_node_id)}_to_{_safe_id(scene_pano_viewer_node_id)}",
                    skill_360_node_id,
                    scene_pano_viewer_node_id,
                    {
                        "edgeKind": "mainline_data",
                        "propagates": True,
                        "role": "scene_360_canonical",
                        "label": "Scene 360",
                    },
                    source_handle="scene_360_candidate",
                )
            )

    metadata = {
        "preset": {
            "preset_key": preset_key,
            "scope": context.get("scope"),
            "episode": context.get("episode"),
            "beat": context.get("beat"),
            "primary_slot": context.get("primary_slot"),
            "created_at": created_at or datetime.now().isoformat(),
        },
        "default_push_target": default_push_target,
        "references": [ref for ref in refs if _is_asset_library_reference(ref)],
        "beat_context": context.get("beat_data"),
        "prop_menu": context.get("prop_menu") or [],
    }
    if context.get("scope") == "beat":
        beat_data = context.get("beat_data") or {}
        metadata["director_capture"] = {
            "episode": context.get("episode"),
            "beat": context.get("beat"),
            "scene_id": _normalize_scene_name(beat_data.get("scene_ref")),
            "node_id": "director_capture",
        }
    return {
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 960, "y": 540, "zoom": 0.65},
        "metadata": metadata,
        "episode": context.get("episode"),
        "beat": context.get("beat"),
    }
