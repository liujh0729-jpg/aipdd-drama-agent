"""画布节点与边工厂（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.freezone.skill_registry import SKILL_SCHEMA_VERSION

from .constants import FREEZONE_PRESET_DEFAULT_IMAGE_MODEL
from .refs import _mainline_context_for_ref, _safe_id
from .aspect import (
    _nearest_supported_image_aspect_ratio,
    _normalize_supported_image_aspect_ratio,
)
from .roles import DIRECTOR_CAPTURE_IMAGE_ROLES


def _node_source_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": ref.get("kind"),
        "role": ref.get("role"),
        "label": ref.get("label"),
        "rel_path": ref.get("rel_path"),
        "meta": ref.get("meta") or {},
    }


def _node_mainline_context_from_ref(ref: dict[str, Any]) -> list[dict[str, Any]]:
    contexts = ref.get("mainline_context")
    if isinstance(contexts, list):
        return [ctx for ctx in contexts if isinstance(ctx, dict)]
    return _mainline_context_for_ref(ref)


def _beat_target_placeholder_ref(
    *,
    episode: Any,
    beat: Any,
    primary_slot: str,
    role: str,
    aspect_ratio: str = "2:3",
) -> dict[str, Any] | None:
    try:
        ep_num = int(episode)
        beat_num = int(beat)
    except (TypeError, ValueError):
        return None

    ep_dir = f"ep{ep_num:03d}"
    base_meta = {"episode": ep_num, "beat": beat_num, "primary_slot": primary_slot}
    output_aspect_ratio = _normalize_supported_image_aspect_ratio(aspect_ratio)
    if role == "current_sketch":
        return {
            "kind": "sketch",
            "role": role,
            "label": "当前草图",
            "rel_path": f"sketches/{ep_dir}/beat_{beat_num:02d}.png",
            "url": None,
            "exists": False,
            "media_type": "image",
            "aspect_ratio": output_aspect_ratio,
            "meta": base_meta,
        }
    if role == "current_frame":
        return {
            "kind": "frame",
            "role": role,
            "label": "当前分镜",
            "rel_path": f"frames/{ep_dir}/beat_{beat_num:02d}.png",
            "url": None,
            "exists": False,
            "media_type": "image",
            "aspect_ratio": output_aspect_ratio,
            "meta": base_meta,
        }
    if role == "selected_background":
        # selected_background 总是有站位 — 没文件时也画个 placeholder 节点,
        # 用户才知道往哪 commit 背景(否则 canvas 上完全找不到目标 slot)。
        # 16:9 跟导演阶段产出对齐,不走 beat sketch 的 2:3。
        beat_dir = f"beat_{beat_num:02d}"
        return {
            "kind": "director",
            "role": role,
            "label": f"当前背景 · Beat {beat_num}",
            "rel_path": f"director_control_frames/{ep_dir}/{beat_dir}/selected_background.png",
            "url": None,
            "exists": False,
            "media_type": "image",
            "aspect_ratio": "16:9",
            "meta": base_meta,
        }
    return None


def _text_node(
    node_id: str,
    x: int,
    y: int,
    label: str,
    content: str,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # preset_managed: True — every node returned by a `_*_node` factory in this
    # module is emitted by preset, so it must carry the explicit flag so frontend
    # nodeMainlineFlags() and backend _is_preset_managed_canvas_node() can rely
    # on a single source of truth (not heuristics over __freezone_source).
    return {
        "id": node_id,
        "type": "textAnnotationNode",
        "position": {"x": x, "y": y},
        "data": {
            "displayName": label,
            "content": content,
            "preset_managed": True,
            **(extra_data or {}),
        },
    }


def _skill_node(
    node_id: str,
    x: int,
    y: int,
    *,
    skill_id: str,
    display_name: str,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "skill_id": skill_id,
        "skill_schema_version": SKILL_SCHEMA_VERSION,
        "displayName": display_name,
        "preset_managed": True,
    }
    if extra_data:
        data.update(extra_data)
    return {
        "id": node_id,
        "type": "skillNode",
        "position": {"x": x, "y": y},
        "measured": {"width": 380, "height": 520},
        "data": data,
    }


def _beat_context_node(
    node_id: str,
    x: int,
    y: int,
    label: str,
    content: str,
    *,
    mainline_context: list[dict[str, Any]],
    edit_fields: dict[str, Any] | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    beat_ctx = next((ctx for ctx in mainline_context if ctx.get("kind") == "beat"), {})
    snapshot = {
        "visualDescription": beat_ctx.get("visualDescription") or "",
        "narrationSegment": beat_ctx.get("narrationSegment") or "",
        "sceneId": beat_ctx.get("sceneId") or "",
        "timeOfDay": (edit_fields or {}).get("time_of_day") or "",
        "detectedIdentities": beat_ctx.get("detectedIdentities") or [],
        "detectedProps": beat_ctx.get("detectedProps") or [],
        "sketchColors": beat_ctx.get("sketchColors") or {},
        "propMarkerColors": beat_ctx.get("propMarkerColors") or {},
        "selectedBackgroundExists": bool(beat_ctx.get("selectedBackgroundExists")),
        "currentSketchExists": bool(beat_ctx.get("currentSketchExists")),
        "currentFrameExists": bool(beat_ctx.get("currentFrameExists")),
    }
    data: dict[str, Any] = {
        "displayName": label,
        "content": content,
        "projectId": beat_ctx.get("projectId"),
        "episode": beat_ctx.get("episode"),
        "beat": beat_ctx.get("beat"),
        "snapshot": snapshot,
        "mainline_context": mainline_context,
        "beat_edit_fields": edit_fields or {},
        "preset_managed": True,
        **(extra_data or {}),
    }
    return {
        "id": node_id,
        "type": "beatContextNode",
        "position": {"x": x, "y": y},
        "data": data,
        # React Flow v12 uses measured size for the node wrapper. Without it,
        # BeatContextNode collapses to 0x0 and source handles/edges disappear.
        # Keep in sync with BeatContextNode.tsx DEFAULT_WIDTH/DEFAULT_HEIGHT.
        "measured": {"width": 420, "height": 560},
    }


def _beat_context_display_name(context: dict[str, Any]) -> str:
    episode = context.get("episode")
    beat = context.get("beat")
    primary_slot = str(context.get("primary_slot") or "").strip() or "render"
    ep_label = f"EP{episode}" if episode is not None else "EP?"
    beat_label = f"Beat {beat}" if beat is not None else "Beat ?"
    return f"{ep_label} / {beat_label} / {primary_slot}"


def _upload_node(node_id: str, x: int, y: int, ref: dict[str, Any]) -> dict[str, Any]:
    actual_ratio = str(ref.get("aspect_ratio") or "1:1")
    generation_ratio = _nearest_supported_image_aspect_ratio(actual_ratio)
    data: dict[str, Any] = {
        "displayName": ref.get("label") or ref.get("role") or "reference",
        "imageUrl": ref.get("url"),
        "previewImageUrl": ref.get("url"),
        "aspectRatio": generation_ratio,
        "__freezone_source": _node_source_from_ref(ref),
        "preset_managed": True,
    }
    contexts = _node_mainline_context_from_ref(ref)
    if contexts:
        data["mainline_context"] = contexts
    if actual_ratio and actual_ratio != generation_ratio:
        data["actualAspectRatio"] = actual_ratio
    return {
        "id": node_id,
        "type": "uploadNode",
        "position": {"x": x, "y": y},
        "data": data,
    }


def _pano_360_viewer_node(node_id: str, x: int, y: int, ref: dict[str, Any]) -> dict[str, Any]:
    url = ref.get("url")
    node = {
        "id": node_id,
        "type": "pano360ViewerNode",
        "position": {"x": x, "y": y},
        "data": {
            "displayName": ref.get("label") or ref.get("role") or "360 panorama",
            "panoUrl": url,
            "imageUrl": url,
            "previewImageUrl": url,
            "url": url,
            "aspectRatio": ref.get("aspect_ratio") or "2:1",
            "__freezone_source": _node_source_from_ref(ref),
            "preset_managed": True,
        },
    }
    contexts = _node_mainline_context_from_ref(ref)
    if contexts:
        node["data"]["mainline_context"] = contexts
    return node


def _three_d_world_node(node_id: str, x: int, y: int, ref: dict[str, Any]) -> dict[str, Any]:
    url = ref.get("url")
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    ply_kind = str(meta.get("ply_kind") or "").strip().lower()
    node = {
        "id": node_id,
        "type": "threeDWorldNode",
        "position": {"x": x, "y": y},
        "data": {
            "displayName": ref.get("label") or ref.get("role") or "3GS",
            "plyUrl": url,
            "modelUrl": url,
            "fileUrl": url,
            "url": url,
            "sourceFileName": Path(str(ref.get("rel_path") or "")).name or None,
            "__freezone_source": _node_source_from_ref(ref),
            "preset_managed": True,
        },
    }
    if ply_kind in {"master", "reverse", "pano"}:
        node["data"]["plyKind"] = ply_kind
    contexts = _node_mainline_context_from_ref(ref)
    if contexts:
        node["data"]["mainline_context"] = contexts
    return node


def _director_world_source_from_ref(ref: dict[str, Any], scene_id: str) -> dict[str, Any] | None:
    url = ref.get("url")
    if not isinstance(url, str) or not url:
        return None
    role = str(ref.get("role") or "")
    safe_scene = _safe_id(scene_id, fallback="scene")
    label = str(ref.get("label") or role or "Director World source")
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    if role in {"scene_director_pano_360", "scene_360"}:
        return {
            "id": f"scene-pano:{safe_scene}",
            "source_type": "pano360",
            "source_kind": "pano",
            "label": label,
            "url": url,
            "pano_url": url,
            "slot_kind": "scene_director_pano_360",
        }
    if role.startswith("scene_3gs_"):
        ply_kind = str(meta.get("ply_kind") or "").strip().lower()
        if not ply_kind:
            if role == "scene_3gs_master_ply":
                ply_kind = "master"
            elif role == "scene_3gs_reverse_ply":
                ply_kind = "reverse"
            elif role == "scene_3gs_pano_ply":
                ply_kind = "pano"
            else:
                ply_kind = "custom"
        return {
            "id": f"scene-sog:{ply_kind}:{safe_scene}",
            "source_type": "sog",
            "source_kind": ply_kind,
            "label": label,
            "ply_url": url,
            "url": url,
            "current": role == "scene_3gs_active_ply",
        }
    return None


def _director_world_node(
    node_id: str,
    x: int,
    y: int,
    *,
    scene_id: str,
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for ref in refs:
        source = _director_world_source_from_ref(ref, scene_id)
        if not source:
            continue
        source_url = str(source.get("pano_url") or source.get("ply_url") or source.get("url") or "")
        if source_url and source_url in seen_urls:
            continue
        if source_url:
            seen_urls.add(source_url)
        sources.append(source)

    active_source = (
        next((source for source in sources if source.get("current")), None)
        or next((source for source in sources if source.get("source_type") == "sog"), None)
        or (sources[0] if sources else None)
    )
    pano_source = next(
        (source for source in sources if source.get("source_type") == "pano360"),
        None,
    )
    preview_url = next(
        (
            ref.get("url")
            for ref in refs
            if str(ref.get("role") or "")
            in {"scene_master", "scene_director_pano_360", "scene_360"}
            and isinstance(ref.get("url"), str)
            and ref.get("url")
        ),
        None,
    )
    data: dict[str, Any] = {
        "displayName": f"{scene_id} 导演世界",
        "sources": sources,
        "activeSourceId": active_source.get("id") if active_source else None,
        "plyUrl": (
            active_source.get("ply_url")
            if active_source and active_source.get("source_type") == "sog"
            else None
        ),
        "panoUrl": pano_source.get("pano_url") if pano_source else None,
        "previewImageUrl": preview_url,
        "__freezone_source": {
            "kind": "scene",
            "role": "director_world",
            "label": f"{scene_id} director world",
            "meta": {"scene_id": scene_id},
        },
        "preset_managed": True,
    }
    contexts = _node_mainline_context_from_ref(refs[0]) if refs else []
    if contexts:
        data["mainline_context"] = contexts
    return {
        "id": node_id,
        "type": "threeDWorldNode",
        "position": {"x": x, "y": y},
        "data": data,
    }


def _asset_node_source_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    source = _node_source_from_ref(ref)
    role = str(ref.get("role") or "")
    if role == "character_portrait":
        source["kind"] = "portrait"
    elif role == "identity_portrait":
        source["kind"] = "identity_portrait"
    elif role == "identity_costume":
        source["kind"] = "identity_costume"
    return source


def _image_gen_node(
    node_id: str,
    x: int,
    y: int,
    label: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    actual_aspect_ratio: str | None = None,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actual_ratio = str(actual_aspect_ratio or aspect_ratio or "1:1")
    generation_ratio = _nearest_supported_image_aspect_ratio(actual_ratio)
    data: dict[str, Any] = {
        "displayName": label,
        "imageUrl": None,
        "previewImageUrl": None,
        "aspectRatio": generation_ratio,
        "isSizeManuallyAdjusted": False,
        "requestAspectRatio": "auto",
        "prompt": prompt,
        "model": FREEZONE_PRESET_DEFAULT_IMAGE_MODEL,
        "size": "2K",
        "count": 1,
        "styleTemplateId": None,
        "focusRegion": None,
        "cameraSelection": None,
        "marks": [],
        "isGenerating": False,
        "generationStartedAt": None,
        "generationDurationMs": 60000,
        "__freezone_source": source_meta or {},
        "preset_managed": True,
    }
    if actual_ratio and actual_ratio != generation_ratio:
        data["actualAspectRatio"] = actual_ratio
    return {
        "id": node_id,
        "type": "imageGenNode",
        "position": {"x": x, "y": y},
        "data": data,
    }


def _asset_image_node(node_id: str, x: int, y: int, ref: dict[str, Any]) -> dict[str, Any]:
    node = _image_gen_node(
        node_id,
        x,
        y,
        str(ref.get("label") or ref.get("role") or "asset"),
        "",
        aspect_ratio=str(ref.get("aspect_ratio") or "1:1"),
        actual_aspect_ratio=str(ref.get("aspect_ratio") or "1:1"),
        source_meta=_asset_node_source_from_ref(ref),
    )
    node["data"]["imageUrl"] = ref.get("url")
    node["data"]["previewImageUrl"] = ref.get("url")
    contexts = _node_mainline_context_from_ref(ref)
    if contexts:
        node["data"]["mainline_context"] = contexts
    return node


def _ref_image_node(node_id: str, x: int, y: int, ref: dict[str, Any]) -> dict[str, Any]:
    role = str(ref.get("role") or "")
    rel_path = str(ref.get("rel_path") or "")
    media_type = str(ref.get("media_type") or "")
    if role.startswith("scene_3gs_") or (
        media_type == "file" and rel_path.lower().endswith((".ply", ".sog", ".splat", ".ksplat"))
    ):
        return _three_d_world_node(node_id, x, y, ref)
    if role in {"scene_director_pano_360", "scene_360"}:
        return _pano_360_viewer_node(node_id, x, y, ref)
    if role in DIRECTOR_CAPTURE_IMAGE_ROLES:
        return _asset_image_node(node_id, x, y, ref)
    if str(ref.get("role") or "") in {
        "character_portrait",
        "character_identity",
        "identity_portrait",
        "identity_costume",
        "prop_reference",
        "scene_master",
        "scene_reverse_master",
    }:
        return _asset_image_node(node_id, x, y, ref)
    return _upload_node(node_id, x, y, ref)


def _edge(
    edge_id: str,
    source: str,
    target: str,
    data: dict[str, Any] | None = None,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> dict[str, Any]:
    edge_data = dict(data or {})
    edge_data.setdefault("preset_managed", True)
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "data": edge_data,
    }
    if source_handle:
        edge["sourceHandle"] = source_handle
    if target_handle:
        edge["targetHandle"] = target_handle
    return edge


def _candidate_binding_edge(edge_id: str, source: str, target: str, role: str) -> dict[str, Any]:
    edge = _edge(edge_id, target, source)
    edge["data"] = {
        "edgeKind": "role_binding",
        "propagates": True,
        "role": role,
        "sourceNodeId": source,
        "beatContextNodeId": target,
        "preset_managed": True,
    }
    return edge


def _skill_role_edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    role: str,
    label: str,
    source_handle: str | None = None,
    target_handle: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge = _edge(
        edge_id,
        source,
        target,
        {
            "edgeKind": "role_binding",
            "role": role,
            "label": label,
        },
    )
    if extra_data:
        edge["data"].update(extra_data)
    edge["targetHandle"] = target_handle or role
    if source_handle:
        edge["sourceHandle"] = source_handle
    return edge


def _reference_path_lookup_keys(raw_path: str, project_dir: Path) -> set[str]:
    """Return stable lookup keys for matching NovelVideo ref-plan paths to canvas refs."""
    value = str(raw_path or "").strip()
    if not value:
        return set()
    keys = {value}
    try:
        path = Path(value)
        absolute_path = path if path.is_absolute() else project_dir / path
        resolved = absolute_path.resolve()
        keys.add(str(resolved))
        keys.add(resolved.as_posix())
        try:
            keys.add(resolved.relative_to(project_dir.resolve()).as_posix())
        except Exception:
            pass
    except Exception:
        pass
    return {key for key in keys if key}


def _is_asset_library_reference(ref: dict[str, Any]) -> bool:
    rel_path = str(ref.get("rel_path") or "")
    role = str(ref.get("role") or "")
    if rel_path.startswith("freezone/"):
        return False
    if rel_path.startswith("director_control_frames/ep"):
        return False
    if role == "director_blocking" or rel_path.startswith("director_blockings/"):
        return False
    if str(ref.get("kind") or "") == "director" and rel_path.endswith(".json"):
        return False
    return True
