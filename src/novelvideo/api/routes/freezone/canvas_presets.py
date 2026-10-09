"""freezone.canvas_presets 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import HTTPException

from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import CanvasPayload
from novelvideo.api.schemas import PresetCanvasRequest
from novelvideo.api.schemas import ProjectionPresetCanvasRequest
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.freezone.presets import build_asset_preset_context
from novelvideo.freezone.presets import build_episode_preset_context
from novelvideo.freezone.presets import preset_key_for_request
from novelvideo.project_context import ProjectContext

from ._common import logger



# ============================================================
# 画布（F5）
# ============================================================


def _default_push_target_for_preset(body: PresetCanvasRequest) -> dict:
    if body.scope == "episode":
        return {"kind": "manual", "episode": body.episode}
    if body.scope == "beat":
        slot = body.primary_slot or "render"
        kind = "director_render" if slot == "render" else slot
        return {
            "kind": kind,
            "episode": body.episode,
            "beat": body.beat,
        }
    if body.scope == "asset" and body.asset_kind in {"identity", "portrait", "character"}:
        if body.asset_kind in {"portrait", "character"}:
            return {"kind": "portrait", "character": body.character}
        return {
            "kind": "identity",
            "character": body.character,
            "identity_id": body.identity_id,
        }
    if body.scope == "asset" and body.asset_kind in {
        "scene",
        "scene_master",
        "scene_reverse_master",
        "scene_spatial_layout",
        "scene_360",
        "scene_director_pano_360",
        "scene_3gs_active_ply",
        "scene_3gs_master_ply",
        "scene_3gs_reverse_ply",
        "scene_3gs_pano_ply",
        "scene_3gs_custom_scene",
        "scene_3gs_collision_glb",
    }:
        scene_id = body.asset_id or body.identity_id or body.character
        scene_kind = "scene_master" if body.asset_kind == "scene" else body.asset_kind
        return {"kind": scene_kind, "scene_id": scene_id}
    if body.scope == "asset" and body.asset_kind in {"prop", "prop_ref"}:
        prop_id = body.asset_id or body.identity_id or body.character
        return {"kind": "prop_ref", "prop_id": prop_id}
    return {"kind": "manual"}


def _latest_preset_canvas(project_dir: Path, preset_key: str) -> str | None:
    return canvas_store.latest_preset_canvas(project_dir, preset_key)


def _canonical_preset_canvas(
    project_dir: Path,
    *,
    preset_key: str,
    canvas_id: str,
) -> str | None:
    payload = canvas_store.read_canvas(project_dir, canvas_id)
    if not isinstance(payload, dict):
        return None
    preset = (payload.get("metadata") or {}).get("preset")
    if isinstance(preset, dict) and preset.get("preset_key") == preset_key:
        return canvas_id
    return None


def _preset_key_from_canvas_metadata(metadata: dict | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    preset = metadata.get("preset")
    if not isinstance(preset, dict):
        return None
    existing = preset.get("preset_key")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    scope = preset.get("scope")
    if not isinstance(scope, str) or not scope:
        return None
    try:
        return preset_key_for_request(
            scope=scope,
            episode=preset.get("episode") if isinstance(preset.get("episode"), int) else None,
            beat=preset.get("beat") if isinstance(preset.get("beat"), int) else None,
            primary_slot=(
                preset.get("primary_slot") if isinstance(preset.get("primary_slot"), str) else None
            ),
            asset_kind=(
                preset.get("asset_kind") if isinstance(preset.get("asset_kind"), str) else None
            ),
            character=(
                preset.get("character") if isinstance(preset.get("character"), str) else None
            ),
            identity_id=(
                preset.get("identity_id") if isinstance(preset.get("identity_id"), str) else None
            ),
            asset_id=(preset.get("asset_id") if isinstance(preset.get("asset_id"), str) else None),
        )
    except ValueError:
        return None


def _canvas_state_project_dir(ctx: ProjectContext | None, output_project_dir: Path) -> Path:
    if ctx is not None:
        return Path(ctx.state_dir)
    return output_project_dir


def _canvas_actor_id(user: dict) -> str:
    return str(user.get("id") or user.get("user_id") or user.get("username") or "")


def _canvas_scope_from_payload(canvas_id: str, payload: dict) -> str:
    raw_scope = payload.get("canvas_scope")
    if raw_scope in {"default", "episode", "beat", "asset"}:
        return raw_scope
    preset = (payload.get("metadata") or {}).get("preset") if isinstance(payload, dict) else None
    preset_scope = preset.get("scope") if isinstance(preset, dict) else None
    if preset_scope in {"episode", "beat", "asset"}:
        return preset_scope
    return "default"


def _merge_canvas_metadata(existing: dict | None, incoming: dict) -> None:
    existing_meta = existing.get("metadata") if isinstance(existing, dict) else None
    incoming_meta = incoming.get("metadata")
    if isinstance(existing_meta, dict) and isinstance(incoming_meta, dict):
        incoming["metadata"] = {**existing_meta, **incoming_meta}
    elif isinstance(existing_meta, dict) and incoming_meta is None:
        incoming["metadata"] = existing_meta


def _prepare_canvas_payload_for_write(
    *,
    project_id: str,
    canvas_id: str,
    body: CanvasPayload | None,
    raw_payload: dict | None = None,
    existing: dict | None = None,
    user: dict,
) -> dict:
    now = canvas_store.utc_now_iso()
    actor_id = _canvas_actor_id(user)
    payload = (
        body.model_dump(
            exclude={"base_revision", "client_save_id", "allow_empty_overwrite"},
            exclude_none=True,
        )
        if body is not None
        else dict(raw_payload or {})
    )
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    payload.setdefault("viewport", None)
    if "metadata" not in payload:
        payload["metadata"] = None
    _merge_canvas_metadata(existing, payload)
    _freezone._sync_frame_context_reference_edges(payload)

    current_revision = existing.get("revision") if isinstance(existing, dict) else None
    if not isinstance(current_revision, int):
        current_revision = None
    payload["schema_version"] = 2
    payload["canvas_id"] = canvas_id
    payload["project_id"] = project_id
    payload["canvas_scope"] = _canvas_scope_from_payload(canvas_id, payload)
    payload["owner_principal_type"] = (
        payload.get("owner_principal_type")
        or (existing or {}).get("owner_principal_type")
        or "user"
    )
    payload["owner_principal_id"] = (
        payload.get("owner_principal_id") or (existing or {}).get("owner_principal_id") or actor_id
    )
    payload["access_model"] = (
        payload.get("access_model") or (existing or {}).get("access_model") or "project_role"
    )
    payload["min_project_role"] = (
        payload.get("min_project_role") or (existing or {}).get("min_project_role") or "editor"
    )
    payload["created_by"] = (
        (existing or {}).get("created_by") or payload.get("created_by") or actor_id
    )
    payload["created_at"] = (existing or {}).get("created_at") or payload.get("created_at") or now
    payload["updated_by"] = actor_id
    payload["updated_at"] = now
    payload["revision"] = (current_revision + 1) if current_revision is not None else 1
    payload.pop("base_revision", None)
    return payload


async def _refresh_preset_canvas_payload_on_read(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    payload: dict,
) -> dict:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    preset = metadata.get("preset") if isinstance(metadata.get("preset"), dict) else {}
    if preset.get("scope") != "beat":
        return payload

    try:
        episode = int(preset.get("episode") or 0)
        beat = int(preset.get("beat") or 0)
    except (TypeError, ValueError):
        return payload
    if episode <= 0 or beat <= 0:
        return payload

    primary_slot = str(preset.get("primary_slot") or "").strip() or "render"
    store = await _freezone.make_sqlite_store_for_context(ctx)
    try:
        context = await _freezone.build_beat_preset_context(
            project_id=ctx.project_id,
            username=username,
            project=project_name,
            project_dir=project_dir,
            store=store,
            episode=episode,
            beat=beat,
            primary_slot=primary_slot,
        )
    except Exception as exc:  # noqa: BLE001 - stale canvas is better than failed read
        logger.warning(
            "failed to refresh beat preset canvas from mainline: ep=%s beat=%s: %s",
            episode,
            beat,
            exc,
        )
        return payload
    finally:
        close = getattr(store, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    fresh_payload = _freezone.build_canvas_payload_from_context(
        context=context,
        preset_key=str(preset.get("preset_key") or ""),
        default_push_target={
            "kind": "sketch" if primary_slot == "sketch" else "frame",
            "episode": episode,
            "beat": beat,
        },
        created_at=str(preset.get("created_at") or canvas_store.utc_now_iso()),
    )
    merged = _merge_restored_preset_canvas(fresh_payload, payload)
    for key in (
        "schema_version",
        "canvas_id",
        "project_id",
        "canvas_scope",
        "owner_principal_type",
        "owner_principal_id",
        "access_model",
        "min_project_role",
        "created_by",
        "created_at",
        "updated_by",
        "updated_at",
        "revision",
    ):
        if key in payload:
            merged[key] = payload[key]
    _stamp_canvas_mainline_context_project_id(merged, ctx.project_id)
    _freezone._sync_frame_context_reference_edges(merged)
    return merged


def _stamp_canvas_mainline_context_project_id(payload: dict, project_id: str) -> None:
    def stamp_contexts(value) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("kind") and not item.get("projectId"):
                    item["projectId"] = project_id

    stamp_contexts(payload.get("mainline_context"))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for ref in metadata.get("references") or []:
            if isinstance(ref, dict):
                stamp_contexts(ref.get("mainline_context"))
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if isinstance(data, dict):
            stamp_contexts(data.get("mainline_context"))


def _raise_canvas_store_http(exc: Exception) -> None:
    if isinstance(exc, canvas_store.CanvasCorruptError):
        raise HTTPException(500, str(exc)) from exc
    if isinstance(exc, canvas_store.CanvasBaseRevisionRequired):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, canvas_store.CanvasRevisionConflict):
        raise HTTPException(
            409,
            {
                "code": "canvas_revision_conflict",
                "error": "canvas revision conflict",
                "current_revision": exc.current_revision,
                "base_revision": exc.base_revision,
            },
        ) from exc
    if isinstance(exc, canvas_store.CanvasIdempotencyConflict):
        raise HTTPException(
            409,
            {
                "code": "canvas_idempotency_conflict",
                "client_save_id": exc.client_save_id,
            },
        ) from exc
    if isinstance(exc, canvas_store.CanvasInvalidHistoryId):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, canvas_store.CanvasHistoryNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, canvas_store.DangerousEmptyCanvasOverwrite):
        raise HTTPException(
            400,
            {
                "code": "dangerous_empty_canvas_overwrite",
                "old_nodes": exc.old_nodes,
                "new_nodes": exc.new_nodes,
                "save_source": exc.save_source,
            },
        ) from exc
    if isinstance(exc, CanvasLockBusy):
        raise HTTPException(
            503,
            {"code": "canvas_lock_busy", "canvas_id": exc.canvas_id},
            headers={"Retry-After": "1"},
        ) from exc
    raise exc


def _merge_restored_preset_canvas(new_payload: dict, existing_payload: dict | None) -> dict:
    """Restore preset-managed graph while preserving user experiment nodes.

    Preset restore should refresh protected mainline context/workflow/artifact
    nodes from current DB facts, but it must not discard free side experiments
    or already-produced candidates on the same canvas.
    """
    if not isinstance(existing_payload, dict):
        return new_payload

    new_nodes = [n for n in new_payload.get("nodes") or [] if isinstance(n, dict)]
    new_edges = [e for e in new_payload.get("edges") or [] if isinstance(e, dict)]
    new_node_ids = {str(n.get("id")) for n in new_nodes if n.get("id")}
    new_edge_ids = {str(e.get("id")) for e in new_edges if e.get("id")}

    preserved_nodes: list[dict] = []
    for node in existing_payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            preserved_nodes.append(node)
            continue
        if node_id in new_node_ids:
            continue
        if _is_preset_managed_canvas_node(node):
            continue
        preserved_nodes.append(node)

    final_node_ids = new_node_ids | {str(n.get("id")) for n in preserved_nodes if n.get("id")}
    # preset-managed 节点之间的 edge 归 preset 管 — 旧 preset emit 过、新 preset
    # 不 emit 了的(比如 edge 方向反转、删了 workflow trigger 等)就该消失。
    # 不然旧 edge 会跟新 edge 共存,画布出现重复/交叉连线 (X 形)。
    preset_managed_node_ids = {
        str(n.get("id"))
        for n in [*new_nodes, *preserved_nodes]
        if n.get("id") and _is_preset_managed_canvas_node(n)
    }
    preserved_edges: list[dict] = []
    for edge in existing_payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or "")
        if edge_id and edge_id in new_edge_ids:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        if source not in final_node_ids or target not in final_node_ids:
            continue
        edge_data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        # Edges between two preset-managed nodes normally belong to the preset
        # layer, including legacy edges emitted before explicit edge flags
        # existed. User-created role-binding edges are the exception: they carry
        # edgeKind=role_binding and must survive refresh.
        if (
            source in preset_managed_node_ids
            and target in preset_managed_node_ids
            and isinstance(edge_data, dict)
            and edge_data.get("edgeKind") != "role_binding"
        ):
            continue
        preserved_edges.append(edge)

    new_payload["nodes"] = [*new_nodes, *preserved_nodes]
    new_payload["edges"] = [*new_edges, *preserved_edges]
    new_payload["viewport"] = existing_payload.get("viewport") or new_payload.get("viewport")
    return new_payload


def _node_projection_key(node: dict) -> str | None:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    value = data.get("projection_key")
    return value if isinstance(value, str) and value else None


def _edge_projection_key(edge: dict) -> str | None:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    value = data.get("projection_key")
    return value if isinstance(value, str) and value else None


def _is_replaceable_projection_node(node: dict, projection_key: str) -> bool:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if data.get("user_spawned") is True:
        return False
    return data.get("preset_managed") is True and data.get("projection_key") == projection_key


def _projection_is_intact_in_payload(existing_payload: dict | None, projection_key: str) -> bool:
    """画布里这个投影是不是还「成组」的。

    facts_signature 只描述上游事实（角色/分镜的内容），完全看不到画布自己的结构。
    组节点一旦丢了——历史 id 撞车被别人的组顶掉、迁移后成员脱离 parent——上游事实又
    没变，refresh 就会一路 noop，散落的成员永远收不回组里。所以 noop 之前还得确认
    结构是完好的。

    判据：该 projection_key 的每个成员节点都挂在同一个属于自己的组节点下。没有成员
    时视为完好（没有可修的东西）。
    """
    if not isinstance(existing_payload, dict):
        return True
    nodes = [node for node in existing_payload.get("nodes") or [] if isinstance(node, dict)]
    own_group_ids = {
        str(node.get("id"))
        for node in nodes
        if node.get("type") == "groupNode"
        and node.get("id")
        and _is_replaceable_projection_node(node, projection_key)
    }
    members = [
        node
        for node in nodes
        if node.get("type") != "groupNode" and _is_replaceable_projection_node(node, projection_key)
    ]
    if not members:
        return True
    return all(str(node.get("parentId") or "") in own_group_ids for node in members)


def _is_replaceable_projection_edge(edge: dict, projection_key: str) -> bool:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    if data.get("user_spawned") is True:
        return False
    return data.get("preset_managed") is True and data.get("projection_key") == projection_key


def _archive_projection_node(node: dict) -> dict:
    archived = dict(node)
    data = dict(archived.get("data") if isinstance(archived.get("data"), dict) else {})
    projection_key = data.get("projection_key")
    data.pop("preset_managed", None)
    data.pop("projection_key", None)
    if isinstance(projection_key, str) and projection_key:
        data["source_projection_key"] = projection_key
    data["projection_archived"] = True
    data["user_spawned"] = True
    archived["data"] = data
    return archived


def _user_owned_projection_node(node: dict) -> dict:
    """Return a user-owned node with projection management fields removed."""
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if not isinstance(data, dict) or data.get("user_spawned") is not True:
        return node
    projection_key = data.get("projection_key")
    if not projection_key and data.get("preset_managed") is not True:
        return node
    cleaned = dict(node)
    next_data = dict(data)
    next_data.pop("preset_managed", None)
    next_data.pop("projection_key", None)
    if isinstance(projection_key, str) and projection_key:
        next_data.setdefault("source_projection_key", projection_key)
    cleaned["data"] = next_data
    return cleaned


def _merge_projected_preset_canvas(
    *,
    incoming_payload: dict,
    existing_payload: dict | None,
    projection_key: str,
) -> dict:
    """Refresh one projected preset subgraph without deleting user work.

    Only backend-owned nodes/edges matching ``projection_key`` are replaceable.
    User-spawned nodes, ordinary nodes, other projections, and user edges are
    preserved. If a user edge still points at an old preset node that the new
    projection no longer emits, the old node is archived into user-owned data
    instead of leaving a dangling edge.
    """
    if not isinstance(existing_payload, dict):
        return incoming_payload

    incoming_nodes = [
        node for node in incoming_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    incoming_edges = [
        edge for edge in incoming_payload.get("edges") or [] if isinstance(edge, dict)
    ]
    incoming_node_ids = {
        node.get("id") for node in incoming_nodes if isinstance(node.get("id"), str)
    }

    existing_nodes = [
        node for node in existing_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    existing_edges = [
        edge for edge in existing_payload.get("edges") or [] if isinstance(edge, dict)
    ]

    user_edge_endpoints: set[str] = set()
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str):
            user_edge_endpoints.add(source)
        if isinstance(target, str):
            user_edge_endpoints.add(target)

    merged_nodes: list[dict] = []
    existing_replaceable_nodes_by_id = {
        node.get("id"): node
        for node in existing_nodes
        if isinstance(node.get("id"), str) and _is_replaceable_projection_node(node, projection_key)
    }
    next_incoming_nodes: list[dict] = []
    for node in incoming_nodes:
        node_id = node.get("id")
        existing_node = existing_replaceable_nodes_by_id.get(node_id)
        if isinstance(existing_node, dict) and node.get("type") == existing_node.get("type"):
            updated_node = dict(node)
            for layout_key in ("position", "style", "width", "height", "parentId", "extent"):
                if layout_key in existing_node:
                    value = existing_node[layout_key]
                    updated_node[layout_key] = dict(value) if isinstance(value, dict) else value
            next_incoming_nodes.append(updated_node)
            continue
        next_incoming_nodes.append(node)

    for node in existing_nodes:
        node_id = node.get("id")
        if not _is_replaceable_projection_node(node, projection_key):
            merged_nodes.append(_user_owned_projection_node(node))
            continue
        if node_id in incoming_node_ids:
            continue
        if isinstance(node_id, str) and node_id in user_edge_endpoints:
            merged_nodes.append(_archive_projection_node(node))
    merged_nodes.extend(next_incoming_nodes)

    final_node_ids = {node.get("id") for node in merged_nodes if isinstance(node.get("id"), str)}
    merged_edges: list[dict] = []
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and source not in final_node_ids:
            continue
        if isinstance(target, str) and target not in final_node_ids:
            continue
        merged_edges.append(edge)
    merged_edges.extend(incoming_edges)

    merged = dict(existing_payload)
    merged["nodes"] = merged_nodes
    merged["edges"] = merged_edges
    metadata = dict(
        existing_payload.get("metadata")
        if isinstance(existing_payload.get("metadata"), dict)
        else {}
    )
    incoming_metadata = (
        incoming_payload.get("metadata")
        if isinstance(incoming_payload.get("metadata"), dict)
        else {}
    )
    projections = dict(
        metadata.get("projections") if isinstance(metadata.get("projections"), dict) else {}
    )
    incoming_projections = (
        incoming_metadata.get("projections")
        if isinstance(incoming_metadata.get("projections"), dict)
        else {}
    )
    if projection_key in incoming_projections:
        projections[projection_key] = incoming_projections[projection_key]
    metadata["projections"] = projections
    metadata["last_projection_key"] = projection_key
    merged["metadata"] = metadata
    return merged


def _remove_projected_preset_canvas(
    *,
    existing_payload: dict,
    projection_key: str,
) -> dict:
    """Remove one projected preset subgraph while preserving user work.

    Matching preset-managed projection nodes/edges are removed. User-spawned
    nodes are preserved even if they carry the same projection_key as
    provenance; edges dangling after projection removal are dropped.
    """
    if not isinstance(existing_payload, dict):
        return existing_payload

    existing_nodes = [
        node for node in existing_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    existing_edges = [
        edge for edge in existing_payload.get("edges") or [] if isinstance(edge, dict)
    ]

    kept_nodes = [
        _user_owned_projection_node(node)
        for node in existing_nodes
        if not _is_replaceable_projection_node(node, projection_key)
    ]
    kept_node_ids = {node.get("id") for node in kept_nodes if isinstance(node.get("id"), str)}

    kept_edges: list[dict] = []
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and source not in kept_node_ids:
            continue
        if isinstance(target, str) and target not in kept_node_ids:
            continue
        kept_edges.append(edge)

    merged = dict(existing_payload)
    merged["nodes"] = kept_nodes
    merged["edges"] = kept_edges
    metadata = dict(
        existing_payload.get("metadata")
        if isinstance(existing_payload.get("metadata"), dict)
        else {}
    )
    projections = dict(
        metadata.get("projections") if isinstance(metadata.get("projections"), dict) else {}
    )
    projections.pop(projection_key, None)
    metadata["projections"] = projections
    if metadata.get("last_projection_key") == projection_key:
        metadata.pop("last_projection_key", None)
    merged["metadata"] = metadata
    return merged


def _is_preset_managed_canvas_node(node: dict) -> bool:
    """Decide whether a restored canvas node is preset-managed.

    Current protocol is intentionally strict: only explicit
    `data.preset_managed === True` gives preset ownership. Pre-release
    heuristic fields such as workflow_kind, __freezone_source, mainline_role,
    artifact_role, or mainline_context are treated as user data unless the
    explicit ownership flag is present.
    """
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if not isinstance(data, dict):
        return False
    return data.get("preset_managed") is True


_PRESET_FACTS_SIGNATURE_OMIT_KEYS = {
    "created_at",
    "createdAt",
    "dragging",
    "measured",
    "position",
    "revision",
    "resizing",
    "selected",
    "updated_at",
    "updatedAt",
}


def _canonical_preset_facts_value(value):
    if isinstance(value, dict):
        return {
            key: _canonical_preset_facts_value(raw_value)
            for key, raw_value in sorted(value.items())
            if key not in _PRESET_FACTS_SIGNATURE_OMIT_KEYS
            and not (isinstance(key, str) and key.startswith("__runtime"))
        }
    if isinstance(value, list):
        return [_canonical_preset_facts_value(item) for item in value]
    return value


def _preset_facts_signature(payload: dict) -> str:
    nodes = [
        node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and _is_preset_managed_canvas_node(node)
    ]
    preset_node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    edges: list[dict] = []
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if (
            data.get("preset_managed") is True
            or source in preset_node_ids
            or target in preset_node_ids
        ):
            edges.append(edge)
    canonical = {
        "nodes": sorted(
            (_canonical_preset_facts_value(node) for node in nodes),
            key=lambda node: str(node.get("id") or "") if isinstance(node, dict) else "",
        ),
        "edges": sorted(
            (_canonical_preset_facts_value(edge) for edge in edges),
            key=lambda edge: (
                str(edge.get("source") or "") if isinstance(edge, dict) else "",
                str(edge.get("target") or "") if isinstance(edge, dict) else "",
                str(edge.get("id") or "") if isinstance(edge, dict) else "",
            ),
        ),
    }
    return canvas_store.canvas_request_hash(canonical)


def _stamp_preset_facts_signature(payload: dict, signature: str) -> None:
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    preset = metadata.setdefault("preset", {})
    if not isinstance(preset, dict):
        preset = {}
        metadata["preset"] = preset
    preset["facts_signature"] = signature


def _stamp_projection_key(payload: dict, projection_key: str) -> None:
    nodes = [
        node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and _is_preset_managed_canvas_node(node)
    ]
    preset_node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    for node in nodes:
        data = node.setdefault("data", {})
        if isinstance(data, dict):
            data["preset_managed"] = True
            data["projection_key"] = projection_key
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        data = edge.setdefault("data", {})
        if not isinstance(data, dict):
            data = {}
            edge["data"] = data
        if (
            data.get("preset_managed") is True
            or source in preset_node_ids
            or target in preset_node_ids
        ):
            data["preset_managed"] = True
            data["projection_key"] = projection_key


def _projection_group_id(projection_key: str) -> str:
    """一个 projection_key 一个组 id。

    slug 只是给人看的可读部分，是**有损**的：中文角色名会被整段换成 ``_`` 再 strip
    掉，``asset:character:林小满`` 和 ``asset:character:顾南城`` 都会塌成
    ``asset_character``——两个角色于是共用一个组 id，第二个人进画布时组名被顶掉、
    两个人的节点挤进同一个组。唯一性一律交给 key 的稳定摘要来保证。
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", projection_key).strip("_").lower()[:35]
    digest = canvas_store.canvas_request_hash({"projection_key": projection_key})[:12]
    if not slug:
        return f"projection_group_{digest}"
    return f"projection_group_{slug}_{digest}"


def _projection_group_label(body: ProjectionPresetCanvasRequest) -> str:
    if body.scope == "beat" and body.episode is not None and body.beat is not None:
        return f"EP{body.episode}/B{body.beat}"
    if body.scope == "episode" and body.episode is not None:
        return f"EP{body.episode}"
    if body.scope == "asset":
        if body.character:
            return str(body.character)
        if body.asset_id:
            return str(body.asset_id)
        if body.identity_id:
            return str(body.identity_id)
        if body.asset_kind:
            return str(body.asset_kind)
    return body.projection_key


# 各节点类型的实际渲染尺寸（前端 CSS 里的设计宽高）。preset 产出的节点大多不带
# width/height/style，兜底值 320x180 比真实渲染小一大截，组框按它算出来就会把成员
# 夹成一堆（成员带 extent:"parent"，React Flow 会把它们钳回小框里）。
_NODE_DESIGN_SIZES: dict[str, tuple[float, float]] = {
    "textAnnotationNode": (440.0, 320.0),
    "uploadNode": (320.0, 350.0),
    "imageNode": (640.0, 520.0),
    "imageGenNode": (580.0, 360.0),
    "exportImageNode": (580.0, 360.0),
    "beatContextNode": (420.0, 560.0),
    "skillNode": (380.0, 520.0),
    "videoNode": (580.0, 380.0),
    "audioNode": (480.0, 210.0),
    "scriptNode": (480.0, 320.0),
    # [aipdd fork delta] 画布「剧本」节点，尺寸与前端 ScreenplayNode 一致。
    "screenplayNode": (480.0, 320.0),
    "videoStoryNode": (720.0, 360.0),
    "pano360ViewerNode": (900.0, 480.0),
    "threeDWorldNode": (480.0, 360.0),
}
_DEFAULT_NODE_DESIGN_SIZE = (320.0, 200.0)


def _node_display_size(node: dict) -> tuple[float, float]:
    """节点的渲染尺寸，优先取前端量出来的真值。

    measured 是前端把节点渲染出来后回写的实际盒子，最准；其次是显式 width/height，
    再次是 style，最后按节点类型取设计尺寸。
    """
    style = node.get("style") if isinstance(node.get("style"), dict) else {}
    measured = node.get("measured") if isinstance(node.get("measured"), dict) else {}
    design_width, design_height = _NODE_DESIGN_SIZES.get(
        str(node.get("type") or ""), _DEFAULT_NODE_DESIGN_SIZE
    )

    def _pick(*candidates, fallback: float) -> float:
        for candidate in candidates:
            try:
                value = float(candidate)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return fallback

    width = _pick(
        measured.get("width"),
        node.get("width"),
        style.get("width"),
        fallback=design_width,
    )
    height = _pick(
        measured.get("height"),
        node.get("height"),
        style.get("height"),
        fallback=design_height,
    )
    return max(1.0, width), max(1.0, height)


def _wrap_projection_payload_in_group(
    payload: dict,
    *,
    projection_key: str,
    label: str,
) -> dict:
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    child_nodes = [
        node
        for node in nodes
        if node.get("type") != "groupNode" and _is_replaceable_projection_node(node, projection_key)
    ]
    if not child_nodes:
        return payload

    bounds = {
        "min_x": float("inf"),
        "min_y": float("inf"),
        "max_x": float("-inf"),
        "max_y": float("-inf"),
    }
    for node in child_nodes:
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        try:
            x = float(position.get("x") or 0)
        except (TypeError, ValueError):
            x = 0.0
        try:
            y = float(position.get("y") or 0)
        except (TypeError, ValueError):
            y = 0.0
        width, height = _node_display_size(node)
        bounds["min_x"] = min(bounds["min_x"], x)
        bounds["min_y"] = min(bounds["min_y"], y)
        bounds["max_x"] = max(bounds["max_x"], x + width)
        bounds["max_y"] = max(bounds["max_y"], y + height)

    if not all(
        map(lambda value: value != float("inf") and value != float("-inf"), bounds.values())
    ):
        return payload

    side_padding = 20
    top_padding = 34
    bottom_padding = 20
    group_x = round(bounds["min_x"] - side_padding)
    group_y = round(bounds["min_y"] - top_padding)
    group_width = round(max(220, bounds["max_x"] - bounds["min_x"] + side_padding * 2))
    group_height = round(max(140, bounds["max_y"] - bounds["min_y"] + top_padding + bottom_padding))
    group_id = _projection_group_id(projection_key)
    group_node = {
        "id": group_id,
        "type": "groupNode",
        "position": {"x": group_x, "y": group_y},
        "style": {"width": group_width, "height": group_height},
        "data": {
            "label": label,
            "displayName": label,
            "preset_managed": True,
            "projection_key": projection_key,
        },
    }

    child_ids = {str(node.get("id")) for node in child_nodes if node.get("id")}
    next_nodes: list[dict] = []
    inserted_group = False
    for node in nodes:
        if not inserted_group and str(node.get("id") or "") in child_ids:
            next_nodes.append(group_node)
            inserted_group = True
        if str(node.get("id") or "") not in child_ids:
            if node.get("id") != group_id:
                next_nodes.append(node)
            continue
        updated = dict(node)
        position = updated.get("position") if isinstance(updated.get("position"), dict) else {}
        try:
            x = float(position.get("x") or 0)
        except (TypeError, ValueError):
            x = 0.0
        try:
            y = float(position.get("y") or 0)
        except (TypeError, ValueError):
            y = 0.0
        updated["parentId"] = group_id
        updated["extent"] = "parent"
        updated["position"] = {
            "x": round(x - group_x),
            "y": round(y - group_y),
        }
        next_nodes.append(updated)

    if not inserted_group:
        next_nodes.insert(0, group_node)
    payload["nodes"] = next_nodes
    return payload


def _stamp_projection_metadata(
    payload: dict,
    *,
    projection_key: str,
    preset_key: str,
    body: ProjectionPresetCanvasRequest,
    facts_signature: str,
) -> None:
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    metadata.pop("preset", None)
    projections = metadata.setdefault("projections", {})
    if not isinstance(projections, dict):
        projections = {}
        metadata["projections"] = projections
    projections[projection_key] = {
        "projection_key": projection_key,
        "preset_key": preset_key,
        "scope": body.scope,
        "request": body.model_dump(
            exclude={"base_revision", "force_refresh"},
            exclude_none=True,
        ),
        "facts_signature": facts_signature,
        "last_synced_at": canvas_store.utc_now_iso(),
    }
    metadata["last_projection_key"] = projection_key


def _projection_facts_signature_from_payload(
    payload: dict | None,
    projection_key: str,
) -> str:
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    projections = metadata.get("projections")
    if not isinstance(projections, dict):
        return ""
    projection = projections.get(projection_key)
    if not isinstance(projection, dict):
        return ""
    signature = projection.get("facts_signature")
    return signature if isinstance(signature, str) else ""


def _preset_facts_signature_from_payload(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    preset = (payload.get("metadata") or {}).get("preset")
    if not isinstance(preset, dict):
        return ""
    signature = preset.get("facts_signature")
    return signature if isinstance(signature, str) else ""


async def _build_canvas_payload_for_preset_request(
    *,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
    project_dir: Path,
    body: PresetCanvasRequest | ProjectionPresetCanvasRequest,
    preset_key: str,
) -> dict:
    if body.scope == "blank":
        return {
            "nodes": [],
            "edges": [],
            "viewport": None,
            "metadata": {
                "preset": {
                    "preset_key": preset_key,
                    "scope": "blank",
                    "created_at": canvas_store.utc_now_iso(),
                }
            },
        }
    if body.scope == "episode":
        if body.episode is None:
            raise HTTPException(400, "episode preset requires episode")
        store = (
            await _freezone.make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await _freezone.make_sqlite_store(username, project_name)
        )
        try:
            context = await build_episode_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                episode=body.episode,
            )
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        return _freezone.build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    if body.scope == "beat":
        if body.episode is None or body.beat is None:
            raise HTTPException(400, "beat preset requires episode and beat")
        store = (
            await _freezone.make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await _freezone.make_sqlite_store(username, project_name)
        )
        try:
            context = await _freezone.build_beat_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                episode=body.episode,
                beat=body.beat,
                primary_slot=body.primary_slot,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        return _freezone.build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    if body.scope == "asset":
        if not body.asset_kind:
            raise HTTPException(400, "asset preset requires asset_kind")
        store = (
            await _freezone.make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await _freezone.make_sqlite_store(username, project_name)
        )
        try:
            context = await build_asset_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                asset_kind=body.asset_kind,
                character=body.character,
                identity_id=body.identity_id,
                asset_id=body.asset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        payload = _freezone.build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
        preset_meta = payload.setdefault("metadata", {}).setdefault("preset", {})
        preset_meta.update(
            {
                "asset_kind": body.asset_kind,
                "character": body.character,
                "identity_id": body.identity_id,
                "asset_id": body.asset_id,
            }
        )
        return payload
    raise HTTPException(400, f"unsupported preset scope: {body.scope}")


async def _build_projection_payload_for_request(
    *,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
    project_dir: Path,
    body: ProjectionPresetCanvasRequest,
) -> tuple[dict, str, str]:
    try:
        preset_key = preset_key_for_request(
            scope=body.scope,
            episode=body.episode,
            beat=body.beat,
            primary_slot=body.primary_slot,
            asset_kind=body.asset_kind,
            character=body.character,
            identity_id=body.identity_id,
            asset_id=body.asset_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    payload = await _freezone._build_canvas_payload_for_preset_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
        preset_key=preset_key,
    )
    _stamp_projection_key(payload, body.projection_key)
    _wrap_projection_payload_in_group(
        payload,
        projection_key=body.projection_key,
        label=_projection_group_label(body),
    )
    incoming_facts_signature = _preset_facts_signature(payload)
    _stamp_projection_metadata(
        payload,
        projection_key=body.projection_key,
        preset_key=preset_key,
        body=body,
        facts_signature=incoming_facts_signature,
    )
    return payload, preset_key, incoming_facts_signature
