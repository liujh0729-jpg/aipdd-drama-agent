"""freezone.canvas_routes 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import CanvasPayload
from novelvideo.api.schemas import PresetCanvasRequest
from novelvideo.api.schemas import ProjectionPresetCanvasRequest
from novelvideo.api.schemas import ProjectionRemoveRequest
from novelvideo.api.schemas import ProjectionStatusRequest
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.freezone.canvas_media_scope import CanvasMediaScopeError
from novelvideo.freezone.canvas_media_scope import reject_new_foreign_media_refs
from novelvideo.freezone.canvas_media_scope import scan_foreign_media_refs
from novelvideo.freezone.canvas_static_urls import migrate_canvas_static_urls_in_memory
from novelvideo.freezone.canvas_static_urls import sanitize_project_local_paths_in_memory
from novelvideo.freezone.history import delete_generation_history_record
from novelvideo.freezone.history import read_canvas_generation_history
from novelvideo.freezone.paths import CANVAS_ID_RE
from novelvideo.freezone.presets import build_asset_preset_context
from novelvideo.freezone.presets import build_episode_preset_context
from novelvideo.freezone.presets import canvas_id_for_preset
from novelvideo.freezone.presets import preset_key_for_request

from ._common import TAG_FREEZONE_CANVAS

router = APIRouter()


@router.post("/projects/{project}/freezone/canvases:from-preset", tags=[TAG_FREEZONE_CANVAS])
async def create_canvas_from_preset(
    project: str,
    body: PresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    """根据项目上下文创建一个预填充画布。

    这不是会话资源，而是一个无状态工厂接口。
    如果项目里已有相同 preset 的画布，会复用最近更新的那张，避免同一主线入口
    不断生成副本。
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)

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

    canonical_canvas_id = canvas_id_for_preset(preset_key)
    overwrite_canvas_id = (
        str(body.canvas_id or "").strip()
        if body.overwrite_existing and str(body.canvas_id or "").strip()
        else ""
    )
    existing = None
    if not overwrite_canvas_id:
        existing = _freezone._canonical_preset_canvas(
            canvas_project_dir,
            preset_key=preset_key,
            canvas_id=canonical_canvas_id,
        )
        if existing is None:
            existing = _freezone._latest_preset_canvas(canvas_project_dir, preset_key)
    if existing:
        return {
            "ok": True,
            "data": {
                "canvas_id": existing,
                "reused": True,
                "url": f"/?p={project}&canvas={existing}",
            },
        }
    if overwrite_canvas_id:
        existing_payload = canvas_store.read_canvas(canvas_project_dir, overwrite_canvas_id)
        if not isinstance(existing_payload, dict):
            raise HTTPException(404, "canvas not found")
        existing_preset_key = _freezone._preset_key_from_canvas_metadata(
            existing_payload.get("metadata")
            if isinstance(existing_payload.get("metadata"), dict)
            else None
        )
        if existing_preset_key != preset_key:
            raise HTTPException(
                400,
                "canvas preset_key does not match requested preset",
            )

    if body.scope == "blank":
        payload = {
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
    elif body.scope == "episode":
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
        payload = _freezone.build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_freezone._default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    elif body.scope == "beat":
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
        payload = _freezone.build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_freezone._default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    elif body.scope == "asset":
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
            default_push_target=_freezone._default_push_target_for_preset(body),
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
    else:
        raise HTTPException(400, f"unsupported preset scope: {body.scope}")

    incoming_facts_signature = _freezone._preset_facts_signature(payload)
    _freezone._stamp_preset_facts_signature(payload, incoming_facts_signature)
    canvas_id = overwrite_canvas_id or canonical_canvas_id

    def build_payload(existing_payload: dict | None) -> dict:
        raw_payload = (
            _freezone._merge_restored_preset_canvas(payload, existing_payload)
            if overwrite_canvas_id
            else payload
        )
        _freezone._stamp_preset_facts_signature(raw_payload, incoming_facts_signature)
        prepared = _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _freezone._stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    # Plan §10 — replays of the same preset request (network retry, double
    # click) must not bump revision twice or duplicate history entries. Mint
    # a stable client_save_id + request_hash from the preset inputs so the
    # second call hits save_canvas's idempotency cache instead of producing
    # a revision_conflict. We deliberately exclude volatile fields like
    # ``metadata.preset.created_at`` — they're stamped per-call inside
    # ``build_canvas_payload_from_context`` and would otherwise defeat the
    # whole point of the key.
    preset_stable_hash = canvas_store.canvas_request_hash(
        {
            "scope": body.scope,
            "episode": body.episode,
            "beat": body.beat,
            "primary_slot": body.primary_slot,
            "asset_kind": body.asset_kind,
            "character": body.character,
            "identity_id": body.identity_id,
            "asset_id": body.asset_id,
            "canvas_id": overwrite_canvas_id,
            "base_revision": body.base_revision,
        }
    )
    preset_client_save_id = f"from-preset:{canvas_id}:{preset_stable_hash}"

    def skip_if_same_preset_facts(existing_payload: dict | None) -> dict | None:
        if not overwrite_canvas_id:
            return None
        if _freezone._preset_facts_signature_from_payload(existing_payload) != incoming_facts_signature:
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "preset_facts_unchanged",
        }

    try:
        saved_canvas = await canvas_store.save_canvas_async(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=preset_client_save_id,
            request_hash=preset_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_same_preset_facts,
            enforce_revision=True,
            save_source="from_preset",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _freezone._append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.preset_refresh.conflict",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "scope": body.scope,
                "preset_key": preset_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _freezone._raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    payload = saved_canvas.payload
    _freezone._append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.preset_emitted",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "scope": body.scope,
            "preset_key": preset_key,
            "revision": payload.get("revision"),
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "overwrote_existing": bool(overwrite_canvas_id),
            "backup_path": (
                canvas_store.relative_project_path(canvas_project_dir, saved_canvas.backup_path)
                if saved_canvas.backup_path
                else None
            ),
            "preset_facts_unchanged": (
                isinstance(saved_canvas.response_cache, dict)
                and saved_canvas.response_cache.get("noop_reason") == "preset_facts_unchanged"
            ),
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "reused": False,
            "url": f"/?p={project}&canvas={canvas_id}",
        },
    }


@router.post(
    "/projects/{project}/freezone/projections:build-from-preset",
    tags=[TAG_FREEZONE_CANVAS],
)
async def build_projection_from_preset(
    project: str,
    body: ProjectionPresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    payload, _preset_key, facts_signature = await _freezone._build_projection_payload_for_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
    )
    # 前端「同步更新」把这些节点直接写进内存画布，不经过读画布的补全；
    # beat 上下文缺 projectId 会被当成 standalone，这里和读画布保持一致。
    _freezone._stamp_canvas_mainline_context_project_id(payload, ctx.project_id)
    metadata = payload.get("metadata")
    return {
        "ok": True,
        "data": {
            "projection_key": body.projection_key,
            "facts_signature": facts_signature,
            "nodes": payload.get("nodes") or [],
            "edges": payload.get("edges") or [],
            "metadata": metadata if isinstance(metadata, dict) else None,
        },
    }


@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:from-preset",
    tags=[TAG_FREEZONE_CANVAS],
)
async def project_canvas_from_preset(
    project: str,
    canvas_id: str,
    body: ProjectionPresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)

    payload, preset_key, incoming_facts_signature = await _freezone._build_projection_payload_for_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
    )

    def skip_if_same_projection_facts(existing_payload: dict | None) -> dict | None:
        if body.force_refresh:
            return None
        if (
            _freezone._projection_facts_signature_from_payload(existing_payload, body.projection_key)
            != incoming_facts_signature
        ):
            return None
        # 事实没变但组散了：照样得重建，否则这张画布再也回不到成组状态。
        if not _freezone._projection_is_intact_in_payload(existing_payload, body.projection_key):
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "projection_facts_unchanged",
        }

    def build_payload(existing_payload: dict | None) -> dict:
        raw_payload = _freezone._merge_projected_preset_canvas(
            incoming_payload=payload,
            existing_payload=existing_payload,
            projection_key=body.projection_key,
        )
        _freezone._stamp_projection_metadata(
            raw_payload,
            projection_key=body.projection_key,
            preset_key=preset_key,
            body=body,
            facts_signature=incoming_facts_signature,
        )
        prepared = _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _freezone._stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    projection_stable_hash = canvas_store.canvas_request_hash(
        {
            "projection_key": body.projection_key,
            "scope": body.scope,
            "episode": body.episode,
            "beat": body.beat,
            "primary_slot": body.primary_slot,
            "asset_kind": body.asset_kind,
            "character": body.character,
            "identity_id": body.identity_id,
            "asset_id": body.asset_id,
            "canvas_id": canvas_id,
            "base_revision": body.base_revision,
            "force_refresh": body.force_refresh,
        }
    )
    projection_client_save_id = f"projection:{canvas_id}:{projection_stable_hash}"

    try:
        saved_canvas = await canvas_store.save_canvas_async(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=projection_client_save_id,
            request_hash=projection_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_same_projection_facts,
            enforce_revision=True,
            save_source="from_preset",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _freezone._append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.projection_refresh.conflict",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "scope": body.scope,
                "preset_key": preset_key,
                "projection_key": body.projection_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _freezone._raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)

    response_cache = (
        saved_canvas.response_cache if isinstance(saved_canvas.response_cache, dict) else {}
    )
    payload = saved_canvas.payload
    revision = payload.get("revision")
    no_op = response_cache.get("noop_reason") == "projection_facts_unchanged"
    saved = response_cache.get("saved")
    _freezone._append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.projection_emitted",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "scope": body.scope,
            "preset_key": preset_key,
            "projection_key": body.projection_key,
            "revision": revision,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "backup_path": (
                canvas_store.relative_project_path(canvas_project_dir, saved_canvas.backup_path)
                if saved_canvas.backup_path
                else None
            ),
            "projection_facts_unchanged": no_op,
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "projection_key": body.projection_key,
            "revision": revision if isinstance(revision, int) else None,
            "saved": bool(saved) if isinstance(saved, bool) else True,
            "no_op": no_op,
        },
    }


@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:remove",
    tags=[TAG_FREEZONE_CANVAS],
)
async def remove_canvas_projection(
    project: str,
    canvas_id: str,
    body: ProjectionRemoveRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)

    def skip_if_projection_missing(existing_payload: dict | None) -> dict | None:
        if not isinstance(existing_payload, dict):
            return None
        metadata = existing_payload.get("metadata") if isinstance(existing_payload, dict) else None
        projections = metadata.get("projections") if isinstance(metadata, dict) else None
        if isinstance(projections, dict) and body.projection_key in projections:
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "projection_missing",
        }

    def build_payload(existing_payload: dict | None) -> dict:
        if not isinstance(existing_payload, dict):
            raise HTTPException(404, "canvas not found")
        raw_payload = _freezone._remove_projected_preset_canvas(
            existing_payload=existing_payload,
            projection_key=body.projection_key,
        )
        prepared = _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _freezone._stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    remove_stable_hash = canvas_store.canvas_request_hash(
        {
            "projection_key": body.projection_key,
            "canvas_id": canvas_id,
            "base_revision": body.base_revision,
        }
    )
    remove_client_save_id = f"projection-remove:{canvas_id}:{remove_stable_hash}"

    try:
        saved_canvas = await canvas_store.save_canvas_async(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=remove_client_save_id,
            request_hash=remove_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_projection_missing,
            enforce_revision=True,
            save_source="projection_remove",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _freezone._append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.projection_remove.conflict",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "projection_key": body.projection_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _freezone._raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)

    payload = saved_canvas.payload
    response_cache = (
        saved_canvas.response_cache if isinstance(saved_canvas.response_cache, dict) else {}
    )
    revision = payload.get("revision")
    no_op = response_cache.get("noop_reason") == "projection_missing"
    _freezone._append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.projection_removed",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "projection_key": body.projection_key,
            "revision": revision,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "projection_missing": no_op,
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "projection_key": body.projection_key,
            "revision": revision if isinstance(revision, int) else None,
            "saved": not no_op,
            "no_op": no_op,
        },
    }


@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:status",
    tags=[TAG_FREEZONE_CANVAS],
)
async def projection_status(
    project: str,
    canvas_id: str,
    body: ProjectionStatusRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    existing = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    if not isinstance(existing, dict):
        raise HTTPException(404, "canvas not found")
    metadata = existing.get("metadata")
    projections = metadata.get("projections") if isinstance(metadata, dict) else None
    if not isinstance(projections, dict):
        return {
            "ok": True,
            "data": {
                "canvas_id": canvas_id,
                "revision": existing.get("revision"),
                "projections": [],
            },
        }

    requested_keys = set(body.projection_keys or [])
    keys = [
        key
        for key in sorted(projections.keys())
        if isinstance(key, str) and (not requested_keys or key in requested_keys)
    ]
    statuses: list[dict] = []
    for projection_key in keys:
        projection = projections.get(projection_key)
        if not isinstance(projection, dict):
            continue
        request = projection.get("request")
        if not isinstance(request, dict):
            continue
        try:
            request_body = ProjectionPresetCanvasRequest(
                **{**request, "projection_key": projection_key, "base_revision": 0}
            )
            preset_key = preset_key_for_request(
                scope=request_body.scope,
                episode=request_body.episode,
                beat=request_body.beat,
                primary_slot=request_body.primary_slot,
                asset_kind=request_body.asset_kind,
                character=request_body.character,
                identity_id=request_body.identity_id,
                asset_id=request_body.asset_id,
            )
            payload = await _freezone._build_canvas_payload_for_preset_request(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                body=request_body,
                preset_key=preset_key,
            )
            _freezone._stamp_projection_key(payload, projection_key)
            _freezone._wrap_projection_payload_in_group(
                payload,
                projection_key=projection_key,
                label=_freezone._projection_group_label(request_body),
            )
            current_signature = _freezone._preset_facts_signature(payload)
        except Exception as exc:
            statuses.append(
                {
                    "projection_key": projection_key,
                    "stale": False,
                    "error": str(exc),
                }
            )
            continue
        stored_signature = projection.get("facts_signature")
        stored_signature = stored_signature if isinstance(stored_signature, str) else ""
        # 组散了也算 stale：上游事实一致但画布结构坏了，同样需要一次 refresh 才能修。
        intact = _freezone._projection_is_intact_in_payload(existing, projection_key)
        statuses.append(
            {
                "projection_key": projection_key,
                "scope": request_body.scope,
                "episode": request_body.episode,
                "beat": request_body.beat,
                "asset_kind": request_body.asset_kind,
                "asset_id": request_body.asset_id,
                "stored_facts_signature": stored_signature,
                "current_facts_signature": current_signature,
                "stale": stored_signature != current_signature or not intact,
            }
        )

    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "revision": existing.get("revision"),
            "projections": statuses,
        },
    }


@router.get("/projects/{project}/freezone/canvases", tags=[TAG_FREEZONE_CANVAS])
async def list_canvases(project: str, user: dict = Depends(get_api_user)):
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        canvas_store.ensure_default_canvas(
            canvas_project_dir,
            project_id=ctx.project_id,
            actor_id=_freezone._canvas_actor_id(user),
        )
        return {"ok": True, "data": canvas_store.list_canvases(canvas_project_dir)}
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)


@router.get("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def get_canvas(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        if canvas_id == "default":
            canvas_store.ensure_default_canvas(
                canvas_project_dir,
                project_id=ctx.project_id,
                actor_id=_freezone._canvas_actor_id(user),
            )
        payload = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    if payload is None:
        return {
            "ok": True,
            "data": {"nodes": [], "edges": [], "viewport": None},
        }
    refreshed_payload = await _freezone._refresh_preset_canvas_payload_on_read(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        payload=payload,
    )
    migrated_payload = migrate_canvas_static_urls_in_memory(
        refreshed_payload or {"nodes": [], "edges": []},
        project_id=ctx.project_id,
        owner_username=ctx.owner_username,
        project_name=ctx.project_name,
        project_dir=project_dir,
    )
    response = {"ok": True, "data": migrated_payload or {"nodes": [], "edges": []}}
    # B2 §3.9 O2:最近有别人写过就带一句提示,零额外往返——判断只用刚读出来的
    # 那份载荷。字段挂在 `data` 之外,画布契约(`CanvasPayload`)一个字节不变(B2-6)。
    editing_by = canvas_store.canvas_editing_hint(
        payload, viewer_id=_freezone._canvas_actor_id(user)
    )
    if editing_by:
        response["editing_by"] = editing_by
    # 历史遗留的外项目引用不拦保存,只在读取期报出来:前端据此把节点显示成
    # 「素材属于其他项目」并给修复入口,不再只给一片裂图。同样挂在 `data` 之外。
    foreign_media = scan_foreign_media_refs(response["data"], project_id=ctx.project_id)
    if foreign_media:
        response["foreign_media"] = [ref.as_dict() for ref in foreign_media]
    return response


@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def list_canvas_history(
    project: str,
    canvas_id: str,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        return {"ok": True, "data": canvas_store.list_canvas_history(canvas_project_dir, canvas_id)}
    except canvas_store.CanvasStoreError as exc:
        _freezone._raise_canvas_store_http(exc)


@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/restore",
    tags=[TAG_FREEZONE_CANVAS],
)
async def restore_canvas_history(
    project: str,
    canvas_id: str,
    body: dict = Body(...),
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    history_id = str(body.get("history_id") or "").strip()
    base_revision = body.get("base_revision")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)

    def build_payload(existing: dict | None, history_payload: dict) -> dict:
        prepared = _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=history_payload,
            existing=existing,
            user=user,
        )
        _freezone._stamp_canvas_mainline_context_project_id(prepared, project)
        # 回滚也是一次写入：旧版本里的外项目引用不能借着「恢复历史」重新落库(SuperTale#192)。
        # 与 PUT 同一条判据——只拦相对当前版本新引入的，当前版本已有的照常放行。
        reject_new_foreign_media_refs(prepared, existing=existing, project_id=ctx.project_id)
        return prepared

    try:
        restored_canvas = canvas_store.restore_canvas_version(
            canvas_project_dir,
            canvas_id,
            history_id=history_id,
            base_revision=base_revision,
            build_payload=build_payload,
        )
    except CanvasMediaScopeError as exc:
        raise HTTPException(
            422,
            {
                "code": "canvas_media_scope_mismatch",
                "project_id": ctx.project_id,
                "refs": [ref.as_dict() for ref in exc.refs],
            },
        ) from exc
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    payload = restored_canvas.payload
    restored_from_revision = restored_canvas.history_payload.get("revision")
    _freezone._append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.restored",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "revision": payload.get("revision"),
            "base_revision": base_revision,
            "restored_from_revision": restored_from_revision,
            "history_id": history_id,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "backup_path": canvas_store.relative_project_path(
                canvas_project_dir,
                restored_canvas.backup_path,
            ),
        },
    )
    return {
        "ok": True,
        "data": {
            "restored": True,
            "revision": payload["revision"],
            "restored_from_revision": restored_from_revision,
        },
    }


@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/nodes/{node_id}/generation-history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def get_node_generation_history(
    project: str,
    canvas_id: str,
    node_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_api_user),
):
    """Return backend-side generation attempts recorded for one canvas node."""
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    try:
        records = _freezone.read_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id,
            node_id=node_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    records = [
        sanitize_project_local_paths_in_memory(
            migrate_canvas_static_urls_in_memory(
                record,
                project_id=ctx.project_id,
                owner_username=ctx.owner_username,
                project_name=ctx.project_name,
                project_dir=project_dir,
            )
            or record,
            project_id=ctx.project_id,
            project_dir=project_dir,
        )
        or record
        for record in records
    ]
    return {"ok": True, "data": {"records": records}}


@router.delete(
    "/projects/{project}/freezone/canvases/{canvas_id}/nodes/{node_id}/generation-history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def delete_node_generation_history_record(
    project: str,
    canvas_id: str,
    node_id: str,
    record_id: str = Query(..., min_length=1, max_length=512),
    user: dict = Depends(get_api_user),
):
    """Remove one entry from the history browser without deleting its media."""
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="editor",
        require_home_node=False,
    )
    try:
        deleted = delete_generation_history_record(
            project_dir=project_dir,
            canvas_id=canvas_id,
            node_id=node_id,
            record_id=record_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "generation history record not found")
    return {"ok": True, "data": {"deleted": True}}


@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/generation-history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def get_canvas_generation_history(
    project: str,
    canvas_id: str,
    limit: int = Query(500, ge=1, le=2000),
    user: dict = Depends(get_api_user),
):
    """Return every node's recorded generation attempts for a whole canvas.

    Aggregates across all nodes (newest first), including nodes that were deleted
    from the canvas — their history files persist, so their past attempts stay
    recoverable in the history browser.
    """
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        required_role="viewer",
        require_home_node=False,
    )
    try:
        records = read_canvas_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    records = [
        sanitize_project_local_paths_in_memory(
            migrate_canvas_static_urls_in_memory(
                record,
                project_id=ctx.project_id,
                owner_username=ctx.owner_username,
                project_name=ctx.project_name,
                project_dir=project_dir,
            )
            or record,
            project_id=ctx.project_id,
            project_dir=project_dir,
        )
        or record
        for record in records
    ]
    return {"ok": True, "data": {"records": records}}


@router.put("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def put_canvas(
    project: str,
    canvas_id: str,
    body: CanvasPayload,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)

    def build_payload(existing: dict | None) -> dict:
        prepared = _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=body,
            existing=existing,
            user=user,
        )
        _freezone._stamp_canvas_mainline_context_project_id(prepared, project)
        # 静态资源按 URL 里的项目 id 独立鉴权:画布存下别的项目的媒体地址,源项目成员
        # 看着一切正常,换个同样合法的本项目成员打开就是整片 403(SuperTale#192)。
        # 只拦本次新引入的,库里已有的历史脏引用照常放行——否则一个脏节点就能把整张
        # 老画布永久锁死,而那些画布正是本守卫要解决的问题的受害者。
        reject_new_foreign_media_refs(prepared, existing=existing, project_id=ctx.project_id)
        return prepared

    try:
        saved_canvas = await canvas_store.save_canvas_async(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            build_payload=build_payload,
            client_save_id=body.client_save_id,
            request_hash=canvas_store.canvas_request_hash(
                body.model_dump(
                    exclude={"client_save_id"},
                    exclude_none=True,
                )
            ),
            save_source=body.save_source,
            allow_empty_overwrite=body.allow_empty_overwrite,
        )
    except CanvasMediaScopeError as exc:
        # 前端据此调 assets/copy 把素材拷进本项目,改写节点后重试保存(自愈)。
        raise HTTPException(
            422,
            {
                "code": "canvas_media_scope_mismatch",
                "project_id": ctx.project_id,
                "refs": [ref.as_dict() for ref in exc.refs],
            },
        ) from exc
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    payload = saved_canvas.payload
    if not saved_canvas.idempotent:
        _freezone._append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.saved",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "revision": payload.get("revision"),
                "base_revision": body.base_revision,
                "node_count": len(payload.get("nodes") or []),
                "edge_count": len(payload.get("edges") or []),
                "client_save_id": body.client_save_id,
                "save_source": body.save_source,
                "backup_path": canvas_store.relative_project_path(
                    canvas_project_dir,
                    saved_canvas.backup_path,
                ),
            },
        )
    response_data = saved_canvas.response_cache or {
        "saved": True,
        "revision": payload.get("revision"),
        "updated_at": payload.get("updated_at"),
        "client_save_id": body.client_save_id,
    }
    return {"ok": True, "data": response_data}


@router.delete("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def delete_canvas(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project,
        user,
        require_home_node=False,
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        deleted_canvas = canvas_store.soft_delete_canvas(
            canvas_project_dir,
            canvas_id,
            deleted_by=_freezone._canvas_actor_id(user),
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    existing = deleted_canvas.existing
    _freezone._append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.deleted",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "revision": existing.get("revision") if isinstance(existing, dict) else None,
            "deleted_path": canvas_store.relative_project_path(
                canvas_project_dir,
                deleted_canvas.deleted_path,
            ),
        },
    )
    return {"ok": True, "data": {"deleted": True}}
