"""freezone.skill_runs 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import unquote
from urllib.parse import urlsplit

from fastapi import HTTPException

from novelvideo.api.deps import sqlite_store_for_context_scope
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import PushRequest
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import CANVAS_ID_RE
from novelvideo.freezone.paths import freezone_root
from novelvideo.freezone.paths import output_path_for_job
from novelvideo.freezone.skill_registry import ResolvedSkillInput
from novelvideo.freezone.skill_registry import SkillDefinition
from novelvideo.freezone.skill_registry import SkillErrorEnvelope
from novelvideo.freezone.skill_registry import SkillInputAcceptSpec
from novelvideo.freezone.skill_registry import SkillRunOutput
from novelvideo.freezone.skill_registry import SkillRunRequest
from novelvideo.freezone.skill_registry import SkillRunResponse
from novelvideo.freezone.slots import PushTarget
from novelvideo.freezone.slots import SCENE_ASSET_KINDS
from novelvideo.freezone.slots import backup_slot_if_exists
from novelvideo.freezone.slots import slot_target_path
from novelvideo.freezone.slots import sync_slot_after_write
from novelvideo.freezone.slots import validate_source_for_slot
from novelvideo.models import beat_scene_id
from novelvideo.project_context import ProjectContext
from novelvideo.utils.background_anchor import copy_to_beat_selected_background
from novelvideo.utils.path_resolver import PathResolver

from ._common import (_SKILL_RUN_ID_RE, logger)



def _skill_runs_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "_skill_runs"


def _skill_run_metadata_path(project_dir: Path, run_id: str) -> Path:
    if not _SKILL_RUN_ID_RE.match(run_id):
        raise HTTPException(404, "skill run not found")
    return _skill_runs_dir(project_dir) / f"{run_id}.json"


def _write_skill_run_metadata(project_dir: Path, run_id: str, metadata: dict) -> None:
    path = _skill_run_metadata_path(project_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_skill_run_metadata(project_dir: Path, run_id: str) -> dict:
    path = _skill_run_metadata_path(project_dir, run_id)
    if not path.exists():
        raise HTTPException(404, "skill run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _skill_error_envelope(
    *,
    code: str,
    category: str,
    message: str,
    retryable: bool = False,
    user_action_hint: str | None = None,
) -> dict:
    return SkillErrorEnvelope(
        code=code,
        category=category,
        message=message,
        retryable=retryable,
        user_action_hint=user_action_hint,
    ).model_dump(mode="json")


def _raise_skill_error(
    status_code: int,
    *,
    code: str,
    category: str,
    message: str,
    retryable: bool = False,
    user_action_hint: str | None = None,
) -> None:
    raise HTTPException(
        status_code,
        _skill_error_envelope(
            code=code,
            category=category,
            message=message,
            retryable=retryable,
            user_action_hint=user_action_hint,
        ),
    )


def _skill_run_idempotency_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "_skill_run_idempotency"


def _skill_run_idempotency_record_path(
    project_dir: Path,
    skill_id: str,
    idempotency_key: str,
) -> Path:
    digest = hashlib.sha256(f"{skill_id}\0{idempotency_key}".encode("utf-8")).hexdigest()
    return _skill_run_idempotency_dir(project_dir) / f"{digest}.json"


def _skill_run_request_hash(body: SkillRunRequest) -> str:
    payload = body.model_dump(
        mode="json",
        exclude={"idempotency_key"},
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_skill_run_idempotency_record(
    project_dir: Path,
    skill_id: str,
    idempotency_key: str,
) -> dict | None:
    path = _skill_run_idempotency_record_path(project_dir, skill_id, idempotency_key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_skill_run_idempotency_record(
    project_dir: Path,
    skill_id: str,
    idempotency_key: str,
    request_hash: str,
    response: SkillRunResponse,
) -> None:
    path = _skill_run_idempotency_record_path(project_dir, skill_id, idempotency_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "skill_id": skill_id,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "response": response.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _idempotent_skill_run_response(
    project_dir: Path,
    skill_id: str,
    body: SkillRunRequest,
) -> tuple[str | None, SkillRunResponse | None]:
    idempotency_key = (body.idempotency_key or "").strip()
    if not idempotency_key:
        return None, None
    request_hash = _skill_run_request_hash(body)
    record = _read_skill_run_idempotency_record(project_dir, skill_id, idempotency_key)
    if record is None:
        return request_hash, None
    if record.get("request_hash") != request_hash:
        _raise_skill_error(
            409,
            code="skill_run_idempotency_conflict",
            category="conflict",
            message="idempotency key reused with different skill run request",
            user_action_hint="Retry with a new idempotency key for a changed request.",
        )
    response = record.get("response")
    if not isinstance(response, dict):
        _raise_skill_error(
            500,
            code="skill_run_idempotency_record_invalid",
            category="runtime",
            message="invalid skill run idempotency record",
            retryable=True,
            user_action_hint="Retry the skill run or contact support if this repeats.",
        )
    return request_hash, SkillRunResponse(**response)


def _persist_skill_run_idempotency_response(
    project_dir: Path,
    skill_id: str,
    body: SkillRunRequest,
    request_hash: str | None,
    response: SkillRunResponse,
) -> None:
    idempotency_key = (body.idempotency_key or "").strip()
    if not idempotency_key or not request_hash:
        return
    _write_skill_run_idempotency_record(
        project_dir,
        skill_id,
        idempotency_key,
        request_hash,
        response,
    )


def _input_extra(input_item: ResolvedSkillInput, field: str):
    return getattr(input_item, field, None) or input_item.model_extra.get(field)


def _dict_extra(input_item: ResolvedSkillInput, field: str) -> dict:
    value = _input_extra(input_item, field)
    return value if isinstance(value, dict) else {}


def _input_mainline_contexts(input_item: ResolvedSkillInput) -> list[dict]:
    contexts = _input_extra(input_item, "mainline_context")
    if not isinstance(contexts, list):
        return []
    return [context for context in contexts if isinstance(context, dict)]


def _first_text_value(source: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return ""


def _inferred_slot_target_from_input(input_item: ResolvedSkillInput) -> dict | None:
    if input_item.slot_target:
        return input_item.slot_target
    for context in _input_mainline_contexts(input_item):
        kind = str(context.get("kind") or "").strip()
        role = str(context.get("role") or "").strip()
        scene_id = _first_text_value(context, ("sceneId", "scene_id", "scene"))
        if kind == "scene" and scene_id and role in {"scene_master", "scene_reverse_master"}:
            return {"kind": role, "scene_id": scene_id}
        identity_id = _first_text_value(
            context,
            ("identityId", "identity_id", "character"),
        )
        if kind == "identity" and identity_id:
            return {
                "kind": "portrait" if role == "portrait" else "identity",
                "identity_id": identity_id,
            }
        prop_id = _first_text_value(context, ("propId", "prop_id"))
        if kind == "prop" and prop_id:
            return {"kind": "prop", "prop_id": prop_id}
        if kind in {"sketch", "frame", "selected_background", "director_combined"}:
            try:
                episode = int(context.get("episode") or 0)
                beat = int(context.get("beat") or 0)
            except (TypeError, ValueError):
                episode = 0
                beat = 0
            if episode > 0 and beat > 0:
                return {"kind": kind, "episode": episode, "beat": beat}

    source = _dict_extra(input_item, "freezone_source") or _dict_extra(
        input_item,
        "__freezone_source",
    )
    role = str(source.get("role") or "").strip()
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    scene_id = _first_text_value(meta, ("scene_id", "scene", "scene_name", "name"))
    if scene_id and role in {"scene_master", "scene_reverse_master"}:
        return {"kind": role, "scene_id": scene_id}
    identity_id = _first_text_value(meta, ("identity_id", "identityId", "character"))
    if identity_id and role in {"identity", "portrait"}:
        return {"kind": role, "identity_id": identity_id}
    prop_id = _first_text_value(meta, ("prop_id", "propId"))
    if prop_id and role == "prop":
        return {"kind": "prop", "prop_id": prop_id}
    return None


def _reference_target_for_input(input_item: ResolvedSkillInput | None) -> dict | None:
    if input_item is None:
        return None
    reference_target = _dict_extra(input_item, "reference_target")
    if reference_target:
        return reference_target
    return _slot_target_for_input(input_item)


def _slot_target_for_input(input_item: ResolvedSkillInput | None) -> dict | None:
    if input_item is None:
        return None
    inferred = _inferred_slot_target_from_input(input_item)
    if inferred and not input_item.slot_target:
        input_item.slot_target = inferred
    return inferred


def _canvas_reference_from_input(input_item: ResolvedSkillInput, role: str) -> dict:
    reference_target = _reference_target_for_input(input_item) or {}
    return {
        "role": role,
        "image_url": _required_image_url(input_item, role),
        "slot_kind": str(reference_target.get("kind") or ""),
        "identity_id": str(reference_target.get("identity_id") or "").strip(),
        "prop_id": str(reference_target.get("prop_id") or "").strip(),
    }


def _canvas_references_from_inputs(
    grouped: dict[str, list[ResolvedSkillInput]],
    role: str,
) -> list[dict]:
    return [
        _canvas_reference_from_input(input_item, role) for input_item in grouped.get(role) or []
    ]


def _string_id_set(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    elif value is None:
        return set()
    else:
        items = [value]
    out: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            text = (
                item.get("identity_id")
                or item.get("identityId")
                or item.get("prop_id")
                or item.get("propId")
                or item.get("id")
            )
        else:
            text = item
        text = str(text or "").strip()
        if text:
            out.add(text)
    return out


def _detected_reference_ids_from_beat_context_data(data: dict, role: str) -> set[str] | None:
    if role == "identity":
        snake_key = "detected_identities"
        camel_key = "detectedIdentities"
    elif role == "prop":
        snake_key = "detected_props"
        camel_key = "detectedProps"
    else:
        return None

    edit_fields = data.get("beat_edit_fields")
    if isinstance(edit_fields, dict) and snake_key in edit_fields:
        return _string_id_set(edit_fields.get(snake_key))

    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict) and camel_key in snapshot:
        return _string_id_set(snapshot.get(camel_key))

    for key in (snake_key, camel_key):
        if key in data:
            return _string_id_set(data.get(key))

    contexts = data.get("mainline_context")
    if isinstance(contexts, list):
        for item in contexts:
            if isinstance(item, dict) and item.get("kind") == "beat" and camel_key in item:
                return _string_id_set(item.get(camel_key))
    return None


def _detected_reference_ids_from_skill_input(
    input_item: ResolvedSkillInput | None,
    role: str,
) -> set[str] | None:
    beat_context = (input_item.beat_context if input_item else None) or {}
    if not isinstance(beat_context, dict):
        return None
    return _detected_reference_ids_from_beat_context_data(beat_context, role)


def _reference_id_from_edge(edge: dict, role: str) -> str:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    target = data.get("reference_target")
    if isinstance(target, dict):
        if role == "identity":
            value = target.get("identity_id") or target.get("identityId")
        else:
            value = target.get("prop_id") or target.get("propId")
        value = str(value or "").strip()
        if value:
            return value
    handle = str(edge.get("targetHandle") or "")
    prefix = f"{role}:"
    if handle.startswith(prefix):
        return handle[len(prefix) :].strip()
    return ""


def _reference_id_from_canvas_reference(item: dict, role: str) -> str:
    if role == "identity":
        return str(item.get("identity_id") or "").strip()
    if role == "prop":
        return str(item.get("prop_id") or "").strip()
    return ""


def _reference_id_from_node(node: dict, role: str) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    source = data.get("__freezone_source")
    meta = (
        source.get("meta")
        if isinstance(source, dict) and isinstance(source.get("meta"), dict)
        else {}
    )
    if role == "identity":
        value = _first_text_value(meta, ("identity_id", "identityId", "character"))
    elif role == "prop":
        value = _first_text_value(meta, ("prop_id", "propId"))
    else:
        return ""
    if value:
        return value

    contexts = data.get("mainline_context")
    if isinstance(contexts, list):
        for context in contexts:
            if not isinstance(context, dict):
                continue
            kind = str(context.get("kind") or "").strip()
            if role == "identity" and kind == "identity":
                value = _first_text_value(context, ("identityId", "identity_id", "character"))
            elif role == "prop" and kind == "prop":
                value = _first_text_value(context, ("propId", "prop_id"))
            else:
                value = ""
            if value:
                return value
    return ""


def _reference_target_for_role(role: str, ref_id: str) -> dict:
    if role == "identity":
        return {"kind": "identity", "identity_id": ref_id}
    return {"kind": "prop", "prop_id": ref_id}


def _synced_reference_edge_id(
    *,
    source_id: str,
    target_id: str,
    role: str,
    ref_id: str,
    existing_ids: set[str],
) -> str:
    digest = hashlib.sha256(f"{source_id}\0{target_id}\0{role}\0{ref_id}".encode("utf-8"))
    base_id = f"edge_{role}_{digest.hexdigest()[:16]}"
    edge_id = base_id
    suffix = 2
    while edge_id in existing_ids:
        edge_id = f"{base_id}_{suffix}"
        suffix += 1
    existing_ids.add(edge_id)
    return edge_id


def _synced_reference_edge(
    *,
    source_id: str,
    target_id: str,
    role: str,
    ref_id: str,
    existing_ids: set[str],
) -> dict:
    label = "Identity" if role == "identity" else "Prop"
    return {
        "id": _synced_reference_edge_id(
            source_id=source_id,
            target_id=target_id,
            role=role,
            ref_id=ref_id,
            existing_ids=existing_ids,
        ),
        "source": source_id,
        "target": target_id,
        "targetHandle": f"{role}:{ref_id}",
        "data": {
            "edgeKind": "role_binding",
            "role": role,
            "label": label,
            "reference_target": _reference_target_for_role(role, ref_id),
        },
    }


def _filter_canvas_references_by_beat_context(
    items: list[dict],
    beat_input: ResolvedSkillInput | None,
    role: str,
) -> list[dict]:
    allowed = _detected_reference_ids_from_skill_input(beat_input, role)
    if allowed is None:
        return items
    return [
        item
        for item in items
        if not (ref_id := _reference_id_from_canvas_reference(item, role)) or ref_id in allowed
    ]


def _sync_frame_context_reference_edges(payload: dict) -> None:
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    edges = [edge for edge in payload.get("edges") or [] if isinstance(edge, dict)]
    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id")}
    frame_skill_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if ((node.get("data") if isinstance(node.get("data"), dict) else {}) or {}).get("skill_id")
        == "freezone.frame_from_context"
    }
    if not frame_skill_ids:
        return

    allowed_by_skill: dict[str, dict[str, set[str] | None]] = {}
    for edge in edges:
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        if data.get("role") != "beat_context":
            continue
        skill_id = str(edge.get("target") or "")
        if skill_id not in frame_skill_ids:
            continue
        context_node = node_by_id.get(str(edge.get("source") or ""))
        context_data = (
            context_node.get("data")
            if context_node and isinstance(context_node.get("data"), dict)
            else {}
        )
        allowed_by_skill[skill_id] = {
            "identity": _detected_reference_ids_from_beat_context_data(context_data, "identity"),
            "prop": _detected_reference_ids_from_beat_context_data(context_data, "prop"),
        }
    if not allowed_by_skill:
        return

    pruned_edges: list[dict] = []
    for edge in edges:
        target = str(edge.get("target") or "")
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        role = str(data.get("role") or "")
        allowed = allowed_by_skill.get(target, {}).get(role)
        if role in {"identity", "prop"} and allowed is not None:
            ref_id = _reference_id_from_edge(edge, role)
            if ref_id and ref_id not in allowed:
                continue
        pruned_edges.append(edge)

    source_by_role_ref: dict[str, dict[str, str]] = {"identity": {}, "prop": {}}
    for node in nodes:
        source_id = str(node.get("id") or "").strip()
        if not source_id:
            continue
        for role in ("identity", "prop"):
            ref_id = _reference_id_from_node(node, role)
            if ref_id:
                source_by_role_ref[role].setdefault(ref_id, source_id)

    existing_ids = {str(edge.get("id") or "") for edge in pruned_edges if edge.get("id")}
    existing_refs_by_skill_role: dict[tuple[str, str], set[str]] = {}
    for edge in pruned_edges:
        target = str(edge.get("target") or "")
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        role = str(data.get("role") or "")
        if role not in {"identity", "prop"}:
            continue
        ref_id = _reference_id_from_edge(edge, role)
        if ref_id:
            existing_refs_by_skill_role.setdefault((target, role), set()).add(ref_id)

    for skill_id, allowed_by_role in allowed_by_skill.items():
        for role in ("identity", "prop"):
            allowed = allowed_by_role.get(role)
            if allowed is None:
                continue
            existing_refs = existing_refs_by_skill_role.setdefault((skill_id, role), set())
            for ref_id in sorted(allowed):
                if ref_id in existing_refs:
                    continue
                source_id = source_by_role_ref[role].get(ref_id)
                if not source_id:
                    continue
                pruned_edges.append(
                    _synced_reference_edge(
                        source_id=source_id,
                        target_id=skill_id,
                        role=role,
                        ref_id=ref_id,
                        existing_ids=existing_ids,
                    )
                )
                existing_refs.add(ref_id)
    payload["edges"] = pruned_edges


def _input_media_kind(input_item: ResolvedSkillInput) -> str:
    media_kind = str(input_item.media_kind or "").strip()
    if media_kind:
        return media_kind
    if input_item.image_url:
        return "image"
    if input_item.text:
        return "text"
    return ""


def _validate_skill_input_accepts(
    *,
    input_item: ResolvedSkillInput,
    input_spec_role: str,
    accepts: SkillInputAcceptSpec,
) -> None:
    if accepts.node_types and input_item.node_type not in accepts.node_types:
        _raise_skill_error(
            422,
            code="skill_input_node_type_rejected",
            category="validation",
            message=(
                f"input role {input_spec_role!r} does not accept "
                f"node_type {input_item.node_type!r}"
            ),
            user_action_hint="Connect a node type accepted by this skill input.",
        )
    for field in accepts.has_field:
        if _input_extra(input_item, field) in (None, "", [], {}):
            _raise_skill_error(
                422,
                code="skill_input_missing_field",
                category="validation",
                message=f"input role {input_spec_role!r} missing field {field!r}",
                user_action_hint="Use a source node that includes the required field.",
            )
    if accepts.media_kinds:
        media_kind = _input_media_kind(input_item)
        if media_kind not in accepts.media_kinds:
            _raise_skill_error(
                422,
                code="skill_input_media_kind_rejected",
                category="validation",
                message=f"input role {input_spec_role!r} does not accept media kind {media_kind!r}",
                user_action_hint="Connect media whose type matches this skill input.",
            )
    provenance_required = bool(accepts.canonical_slot_kinds or accepts.candidate_origin_skill_ids)
    if provenance_required:
        slot_target = _slot_target_for_input(input_item) or {}
        candidate_origin = input_item.candidate_origin or {}
        slot_kind = str(slot_target.get("kind") or "")
        origin_skill_id = str(candidate_origin.get("skill_id") or "")
        has_slot_match = bool(
            accepts.canonical_slot_kinds and slot_kind in accepts.canonical_slot_kinds
        )
        has_candidate_match = bool(
            accepts.candidate_origin_skill_ids
            and origin_skill_id in accepts.candidate_origin_skill_ids
        )
        has_plain_media_match = bool(accepts.media_kinds and _input_media_kind(input_item))
        if not (has_slot_match or has_candidate_match or has_plain_media_match):
            _raise_skill_error(
                422,
                code="skill_input_origin_rejected",
                category="validation",
                message=(
                    f"input role {input_spec_role!r} does not match "
                    "accepted slot/candidate origins"
                ),
                user_action_hint="Connect a canonical slot or candidate produced by an accepted skill.",
            )


def _normalize_skill_input_url_scope(
    input_item: ResolvedSkillInput,
    *,
    project: str,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
) -> None:
    image_url = (input_item.image_url or "").strip()
    if not image_url:
        return
    parsed = urlsplit(image_url)
    path = parsed.path or image_url
    if parsed.scheme in {"http", "https"}:
        allowed_hosts = {"static.local", "localhost", "127.0.0.1"}
        if parsed.hostname not in allowed_hosts:
            _raise_skill_error(
                422,
                code="skill_input_external_url_rejected",
                category="validation",
                message="external image URLs are not accepted for skill runs",
                user_action_hint="Use media stored in the current project before running the skill.",
            )
        if not (path.startswith("/static/") or path.startswith("/api/v1/projects/")):
            _raise_skill_error(
                422,
                code="skill_input_external_url_rejected",
                category="validation",
                message="external image URLs are not accepted for skill runs",
                user_action_hint="Use media stored in the current project before running the skill.",
            )
    elif parsed.scheme:
        _raise_skill_error(
            422,
            code="skill_input_external_url_rejected",
            category="validation",
            message="external image URLs are not accepted for skill runs",
            user_action_hint="Use media stored in the current project before running the skill.",
        )
    if path.startswith("/api/v1/projects/"):
        parts = path.split("/", 6)
        if len(parts) < 7 or parts[5] != "media":
            _raise_skill_error(
                422,
                code="skill_input_media_url_unsupported",
                category="validation",
                message="unsupported project API media URL",
                user_action_hint="Use a project media URL returned by the SuperTale API.",
            )
        url_project = unquote(parts[4])
        if url_project != project:
            _raise_skill_error(
                422,
                code="skill_input_wrong_project_url",
                category="validation",
                message="project media URL does not match current project",
                user_action_hint="Use media from the same project as the canvas.",
            )
        media_path = unquote(parts[6]).lstrip("/")
        if not media_path:
            _raise_skill_error(
                422,
                code="skill_input_media_path_missing",
                category="validation",
                message="project media URL missing media path",
                user_action_hint="Use a complete project media URL.",
            )
        input_item.image_url = f"/{media_path}"
        return
    if path.startswith("/static/"):
        parts = path.split("/", 4)
        if len(parts) < 5:
            _raise_skill_error(
                422,
                code="skill_input_static_url_unsupported",
                category="validation",
                message="unsupported static URL",
                user_action_hint="Use a static URL generated for this project.",
            )
        if parts[2] == "projects":
            static_project = unquote(parts[3])
            if static_project != project:
                _raise_skill_error(
                    422,
                    code="skill_input_wrong_project_url",
                    category="validation",
                    message="project static URL does not match current project",
                    user_action_hint="Use media from the same project as the canvas.",
                )
            return
        static_owner = unquote(parts[2])
        static_project = unquote(parts[3])
        expected_owner = ctx.owner_username if ctx is not None else username
        expected_project = ctx.project_name if ctx is not None else project_name
        if static_owner != expected_owner or static_project != expected_project:
            _raise_skill_error(
                422,
                code="skill_input_wrong_project_url",
                category="validation",
                message="static URL does not match current project",
                user_action_hint="Use media from the same project as the canvas.",
            )
        return
    if path.startswith("/api/"):
        _raise_skill_error(
            422,
            code="skill_input_media_url_unsupported",
            category="validation",
            message="unsupported project API media URL",
            user_action_hint="Use a project media URL returned by the SuperTale API.",
        )


def _group_and_validate_skill_inputs(
    skill: SkillDefinition,
    resolved_inputs: list[ResolvedSkillInput],
    *,
    project: str,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
) -> dict[str, list[ResolvedSkillInput]]:
    specs_by_role = {item.role: item for item in skill.inputs}
    grouped: dict[str, list[ResolvedSkillInput]] = {}
    for input_item in resolved_inputs:
        spec = specs_by_role.get(input_item.role)
        if spec is None:
            _raise_skill_error(
                422,
                code="skill_input_unknown_role",
                category="validation",
                message=f"unknown input role {input_item.role!r}",
                user_action_hint="Reconnect the input to one of the skill node's listed handles.",
            )
        _normalize_skill_input_url_scope(
            input_item,
            project=project,
            ctx=ctx,
            username=username,
            project_name=project_name,
        )
        _validate_skill_input_accepts(
            input_item=input_item,
            input_spec_role=spec.role,
            accepts=spec.accepts,
        )
        if input_item.role == "beat_context" and not _freezone._is_standalone_beat_context_input(input_item):
            _episode_and_beat_from_input(input_item)
        grouped.setdefault(input_item.role, []).append(input_item)
    for spec in skill.inputs:
        items = grouped.get(spec.role, [])
        if spec.required and not items:
            _raise_skill_error(
                422,
                code="skill_input_missing_required",
                category="validation",
                message=f"missing required input role {spec.role!r}",
                user_action_hint="Connect the missing input role before running the skill.",
            )
        if spec.cardinality == "single" and len(items) > 1:
            _raise_skill_error(
                422,
                code="skill_input_cardinality_exceeded",
                category="validation",
                message=f"input role {spec.role!r} accepts only one value",
                user_action_hint="Remove extra edges from this single-value input role.",
            )
    return grouped


def _single_input(
    grouped: dict[str, list[ResolvedSkillInput]], role: str
) -> ResolvedSkillInput | None:
    items = grouped.get(role) or []
    return items[0] if items else None


def _required_input(grouped: dict[str, list[ResolvedSkillInput]], role: str) -> ResolvedSkillInput:
    input_item = _single_input(grouped, role)
    if input_item is None:
        _raise_skill_error(
            422,
            code="skill_input_missing_required",
            category="validation",
            message=f"missing required input role {role!r}",
            user_action_hint="Connect the missing input role before running the skill.",
        )
    return input_item


def _required_image_url(input_item: ResolvedSkillInput, role: str) -> str:
    image_url = (input_item.image_url or "").strip()
    if not image_url:
        _raise_skill_error(
            422,
            code="skill_input_missing_field",
            category="validation",
            message=f"input role {role!r} missing field 'image_url'",
            user_action_hint="Connect an image node that has a concrete project media URL.",
        )
    return image_url


def _input_image_urls(grouped: dict[str, list[ResolvedSkillInput]], role: str) -> list[str]:
    urls: list[str] = []
    for input_item in grouped.get(role) or []:
        urls.append(_required_image_url(input_item, role))
    return urls


def _episode_and_beat_from_input(input_item: ResolvedSkillInput | None) -> tuple[int, int]:
    beat_context = (input_item.beat_context if input_item else None) or {}
    try:
        episode = int(beat_context.get("episode") or beat_context.get("episode_number") or 0)
        beat = int(beat_context.get("beat") or beat_context.get("beat_number") or 0)
    except (TypeError, ValueError):
        _raise_skill_error(
            422,
            code="skill_input_beat_context_invalid",
            category="validation",
            message="beat_context must include numeric episode and beat",
            user_action_hint="Connect a Beat Context node with episode and beat values.",
        )
    if episode <= 0 or beat <= 0:
        _raise_skill_error(
            422,
            code="skill_input_beat_context_invalid",
            category="validation",
            message="beat_context must include positive episode and beat",
            user_action_hint="Connect a Beat Context node with positive episode and beat values.",
        )
    return episode, beat


def _slot_target_from_inputs(grouped: dict[str, list[ResolvedSkillInput]]) -> dict | None:
    beat_item = _single_input(grouped, "beat_context")
    beat_target = _slot_target_for_input(beat_item)
    if beat_target:
        return beat_target
    if beat_item and not _freezone._is_standalone_beat_context_input(beat_item):
        episode, beat = _episode_and_beat_from_input(beat_item)
        return {"episode": episode, "beat": beat}
    for role in ("sketch", "frame", "scene_master", "background"):
        item = _single_input(grouped, role)
        slot_target = _slot_target_for_input(item)
        if slot_target:
            return slot_target
    return None


def _skill_output_slot_target(
    _skill_id: str,
    output_role: str,
    grouped: dict[str, list[ResolvedSkillInput]],
) -> dict | None:
    beat_item = _single_input(grouped, "beat_context")
    if _freezone._is_standalone_beat_context_input(beat_item):
        return None
    if output_role == "current_sketch_candidate":
        if beat_item:
            episode, beat = _episode_and_beat_from_input(beat_item)
            return {"kind": "sketch", "episode": episode, "beat": beat}
    if output_role == "current_frame_candidate":
        if beat_item:
            episode, beat = _episode_and_beat_from_input(beat_item)
            return {"kind": "frame", "episode": episode, "beat": beat}
    if output_role == "selected_background":
        if beat_item:
            episode, beat = _episode_and_beat_from_input(beat_item)
            return {"kind": "selected_background", "episode": episode, "beat": beat}
    if output_role == "director_combined":
        if beat_item:
            episode, beat = _episode_and_beat_from_input(beat_item)
            return {"kind": "director_render", "episode": episode, "beat": beat}
    if output_role == "scene_360_candidate":
        scene_master = _single_input(grouped, "scene_master")
        scene_master_slot = _slot_target_for_input(scene_master)
        scene_id = (
            scene_master_slot.get("scene_id") if isinstance(scene_master_slot, dict) else None
        )
        if scene_id:
            return {"kind": "scene_director_pano_360", "scene_id": scene_id}
    return _slot_target_from_inputs(grouped)


def _skill_node_is_preset_managed(
    *,
    project_dir: Path,
    ctx: ProjectContext | None,
    canvas_id: str | None,
    skill_node_id: str | None,
) -> bool:
    canvas = (canvas_id or "").strip()
    node_id = (skill_node_id or "").strip()
    if not canvas or not node_id or not CANVAS_ID_RE.match(canvas):
        return False
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        payload = canvas_store.read_canvas(canvas_project_dir, canvas)
    except Exception:
        logger.exception("failed to inspect canvas node for skill auto-commit")
        return False
    if not isinstance(payload, dict):
        return False
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id") or "") == node_id:
            return _freezone._is_preset_managed_canvas_node(node)
    return False


def _skill_output_metadata(
    skill: SkillDefinition,
    grouped: dict[str, list[ResolvedSkillInput]],
    *,
    auto_commit: bool = False,
) -> dict:
    output = skill.outputs[0]
    slot_target = _skill_output_slot_target(skill.id, output.role, grouped)
    return {
        "role": output.role,
        "media_type": output.media_type,
        "node_type": output.node_type,
        "pushable": output.pushable,
        "slot_target": slot_target,
        "auto_commit": bool(auto_commit),
    }


def _skill_run_status_from_task_status(status: str | None) -> str:
    if status == "completed":
        return "done"
    if status in {"failed", "cancelled"}:
        return status
    return status or "unknown"


def _project_path_from_rel(project_dir: Path, rel_path: str) -> Path | None:
    rel = str(rel_path or "").strip().lstrip("/")
    if not rel:
        return None
    candidate = (project_dir / rel).resolve()
    try:
        candidate.relative_to(project_dir.resolve())
    except ValueError:
        return None
    return candidate


def _director_control_bundle_from_input(input_item: ResolvedSkillInput | None) -> dict:
    if not input_item:
        return {}
    return _dict_extra(input_item, "director_control_bundle")


def _copy_director_control_bundle_to_mainline(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    bundle: dict,
    fallback_combined_path: Path,
    target_dir: Path,
) -> dict | None:
    rel_paths = bundle.get("rel_paths")
    rel_paths = rel_paths if isinstance(rel_paths, dict) else {}
    source_paths = {
        "combined": _project_path_from_rel(project_dir, str(rel_paths.get("combined") or ""))
        or fallback_combined_path,
        "env_only": _project_path_from_rel(project_dir, str(rel_paths.get("env_only") or "")),
        "frame_meta": _project_path_from_rel(project_dir, str(rel_paths.get("frame_meta") or "")),
    }
    if not all(path and path.exists() and path.is_file() for path in source_paths.values()):
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    filenames = {
        "combined": "combined.png",
        "env_only": "env_only.png",
        "frame_meta": "frame_meta.json",
    }
    paths: dict[str, str] = {}
    next_rel_paths: dict[str, str] = {}
    urls: dict[str, str] = {}
    for kind, filename in filenames.items():
        source_path = source_paths[kind]
        if not source_path:
            return None
        target_path = target_dir / filename
        if source_path.resolve() != target_path.resolve():
            shutil.copyfile(source_path, target_path)
        rel = target_path.relative_to(project_dir).as_posix()
        paths[kind] = target_path.as_posix()
        next_rel_paths[kind] = rel
        urls[kind] = _freezone.make_static_url_for_context(ctx, rel, local_path=target_path)

    frame_meta_value = bundle.get("frame_meta")
    if not isinstance(frame_meta_value, dict):
        try:
            frame_meta_value = json.loads(
                (target_dir / "frame_meta.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            frame_meta_value = None

    next_bundle = {
        "schema_version": "director_control_bundle_v1",
        "dir": str(target_dir),
        "paths": paths,
        "rel_paths": next_rel_paths,
        "urls": urls,
    }
    if isinstance(bundle.get("source"), dict):
        next_bundle["source"] = bundle["source"]
    if isinstance(frame_meta_value, dict):
        next_bundle["frame_meta"] = frame_meta_value
    return next_bundle


def _extract_result_image_url(result: dict | None) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("image_url", "output_url", "url"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _static_url_for_skill_output_path(
    *,
    output_path: str,
    project_dir: Path,
    ctx: ProjectContext,
    username: str,
    project_name: str,
) -> str | None:
    raw_path = str(output_path or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir / raw_path.lstrip("/")
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return _freezone.make_static_url_for_context(ctx, rel, local_path=resolved)


def _static_url_for_task_result_path(
    *,
    task_result: dict | None,
    project_dir: Path,
    ctx: ProjectContext,
    username: str,
    project_name: str,
) -> str | None:
    if not isinstance(task_result, dict):
        return None
    for key in ("output_path", "pano_path"):
        image_url = _static_url_for_skill_output_path(
            output_path=str(task_result.get(key) or ""),
            project_dir=project_dir,
            ctx=ctx,
            username=username,
            project_name=project_name,
        )
        if image_url:
            return image_url
    return None


def _static_url_for_skill_slot_target(
    *,
    output_metadata: dict,
    project_dir: Path,
    ctx: ProjectContext,
) -> str | None:
    target = _parse_skill_output_push_target(output_metadata)
    if target is None:
        return None
    target_path = slot_target_path(project_dir, target)
    if not target_path.exists() or not target_path.is_file():
        return None
    rel = target_path.relative_to(project_dir).as_posix()
    return _freezone.make_static_url_for_context(ctx, rel, local_path=target_path)


def _normalize_task_result_outputs(
    *,
    task_result: dict | None,
    output_metadata: dict,
    project_dir: Path,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
) -> list[SkillRunOutput]:
    if not isinstance(task_result, dict):
        return []
    raw_outputs = task_result.get("outputs")
    if not isinstance(raw_outputs, list):
        return []
    outputs: list[SkillRunOutput] = []
    for raw_output in raw_outputs:
        if not isinstance(raw_output, dict):
            continue
        item = {**output_metadata, **raw_output}
        output_path = str(item.get("output_path") or "").strip()
        if output_path and not item.get("image_url"):
            image_url = _static_url_for_skill_output_path(
                output_path=output_path,
                project_dir=project_dir,
                ctx=ctx,
                username=username,
                project_name=project_name,
            )
            if image_url:
                item["image_url"] = image_url
        outputs.append(SkillRunOutput(**item))
    return outputs


def _skill_output_path_for_job(project_dir: Path, task_type: str, job_id: str) -> Path | None:
    out = output_path_for_job(project_dir, task_type, job_id)
    if out.exists():
        return out
    for suffix in (".webp", ".mp4", ".mov", ".webm"):
        candidate = out.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def _scene_id_from_scene_master_input(scene_master: ResolvedSkillInput | None) -> str:
    scene_master_slot = _slot_target_for_input(scene_master)
    scene_id = scene_master_slot.get("scene_id") if isinstance(scene_master_slot, dict) else None
    if scene_id:
        return str(scene_id)
    _raise_skill_error(
        422,
        code="skill_scene_master_missing_scene_id",
        category="validation",
        message="scene_master input must include slot_target.scene_id",
        user_action_hint="Connect a scene master node that belongs to a mainline scene.",
    )


def _scene_prompt_from_input(scene_input: ResolvedSkillInput | None) -> str:
    if scene_input is None:
        return ""
    return str(scene_input.text or _input_extra(scene_input, "content") or "").strip()


async def _run_set_selected_background_skill(
    *,
    project: str,
    project_dir: Path,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    skill: SkillDefinition,
    grouped: dict[str, list[ResolvedSkillInput]],
    body: SkillRunRequest,
    user: dict,
    idempotency_request_hash: str,
    auto_commit: bool,
) -> SkillRunResponse:
    beat_input = _single_input(grouped, "beat_context")
    source = _single_input(grouped, "source_image")
    is_standalone_beat_context = _freezone._is_standalone_beat_context_input(beat_input)
    if is_standalone_beat_context:
        auto_commit = False
        episode = beat = None
    else:
        episode, beat = _episode_and_beat_from_input(beat_input)
    source_url = (source.image_url if source else "") or ""
    if not source_url:
        _raise_skill_error(
            422,
            code="skill_input_missing_field",
            category="validation",
            message="source_image must include image_url",
            user_action_hint="Connect an image source before running the skill.",
        )
    try:
        source_path = _freezone.resolve_static_url_to_path(source_url, project_dir)
    except ValueError as exc:
        _raise_skill_error(
            422,
            code="skill_input_media_url_unsupported",
            category="validation",
            message=str(exc),
            user_action_hint="Use media stored in the current project.",
        )
    if not source_path.exists() or not source_path.is_file():
        _raise_skill_error(
            404,
            code="skill_input_media_missing",
            category="not_found",
            message="source image file not found",
            user_action_hint="Refresh the canvas or choose an existing image.",
        )

    committed = False
    if auto_commit:
        selected_path = copy_to_beat_selected_background(
            project_dir,
            int(episode or 0),
            int(beat or 0),
            source_path,
        )
        store = await _freezone.make_sqlite_store_for_context(ctx)
        try:
            beats = await store.get_beats_as_dicts(int(episode))
            target = next(
                (item for item in beats if int(item.get("beat_number") or 0) == int(beat)),
                None,
            )
            if not target:
                _raise_skill_error(
                    404,
                    code="skill_beat_not_found",
                    category="not_found",
                    message=f"beat not found: ep{episode} beat{beat}",
                    user_action_hint="Reconnect a valid Beat Context node.",
                )
            scene_ref = dict(target.get("scene_ref") or {})
            scene_id = beat_scene_id(target)
            if scene_id:
                scene_ref["scene_id"] = scene_id
            scene_ref["render_anchor_id"] = "selected_background"
            scene_ref["render_anchor_source_id"] = "skill_source_image"
            scene_ref.pop("render_anchor_path", None)
            await store.update_beat_asset(
                episode_number=int(episode or 0),
                beat_number=int(beat or 0),
                scene_ref=scene_ref,
            )
            committed = True
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        rel = selected_path.relative_to(project_dir).as_posix()
        image_url = _freezone.make_static_url_for_context(ctx, rel, local_path=selected_path)
    else:
        rel = source_path.relative_to(project_dir).as_posix()
        image_url = _freezone.make_static_url_for_context(ctx, rel, local_path=source_path)
    output = _skill_output_metadata(skill, grouped, auto_commit=auto_commit)
    output["image_url"] = image_url
    if not auto_commit:
        output["pushable"] = True
    if committed:
        output["pushable"] = False
        output["committed"] = True
        output["committed_slot_url"] = image_url
    run_id = f"freezone.set_selected_background:{_freezone._new_job_id()}"
    _write_skill_run_metadata(
        project_dir,
        run_id,
        {
            "run_id": run_id,
            "skill_id": skill.id,
            "status": "completed",
            "outputs": [output],
            "canvas_id": body.canvas_id,
            "skill_node_id": body.skill_node_id,
        },
    )
    response = SkillRunResponse(run_id=run_id, status="completed")
    _freezone._append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=body.canvas_id,
        event_type="skill.run_completed",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "skill_id": skill.id,
            "skill_node_id": body.skill_node_id,
            "run_id": run_id,
            "status": response.status,
            "output_count": 1,
        },
    )
    _persist_skill_run_idempotency_response(
        project_dir,
        skill.id,
        body,
        idempotency_request_hash,
        response,
    )
    return response


async def _run_set_director_combined_skill(
    *,
    project: str,
    project_dir: Path,
    ctx: ProjectContext,
    skill: SkillDefinition,
    grouped: dict[str, list[ResolvedSkillInput]],
    body: SkillRunRequest,
    user: dict,
    idempotency_request_hash: str,
    auto_commit: bool,
) -> SkillRunResponse:
    beat_input = _single_input(grouped, "beat_context")
    source = _single_input(grouped, "source_image")
    is_standalone_beat_context = _freezone._is_standalone_beat_context_input(beat_input)
    if is_standalone_beat_context:
        auto_commit = False
        episode = beat = None
    else:
        episode, beat = _episode_and_beat_from_input(beat_input)
    source_url = (source.image_url if source else "") or ""
    if not source_url:
        _raise_skill_error(
            422,
            code="skill_input_missing_field",
            category="validation",
            message="source_image must include image_url",
            user_action_hint="Connect an image source before running the skill, or use 3GS capture.",
        )
    try:
        source_path = _freezone.resolve_static_url_to_path(source_url, project_dir)
    except ValueError as exc:
        _raise_skill_error(
            422,
            code="skill_input_media_url_unsupported",
            category="validation",
            message=str(exc),
            user_action_hint="Use media stored in the current project.",
        )
    if not source_path.exists() or not source_path.is_file():
        _raise_skill_error(
            404,
            code="skill_input_media_missing",
            category="not_found",
            message="source image file not found",
            user_action_hint="Refresh the canvas or choose an existing image.",
        )

    committed = False
    director_bundle: dict | None = None
    if auto_commit:
        target_path = PathResolver(str(project_dir), int(episode or 0)).director_render(
            int(beat or 0)
        )
        source_bundle = _director_control_bundle_from_input(source)
        if source_bundle:
            director_bundle = _copy_director_control_bundle_to_mainline(
                ctx=ctx,
                project_dir=project_dir,
                bundle=source_bundle,
                fallback_combined_path=source_path,
                target_dir=target_path.parent,
            )
        if not director_bundle:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.resolve() != target_path.resolve():
                shutil.copyfile(source_path, target_path)
        committed = True
        rel = target_path.relative_to(project_dir).as_posix()
        image_url = _freezone.make_static_url_for_context(ctx, rel, local_path=target_path)
    else:
        rel = source_path.relative_to(project_dir).as_posix()
        image_url = _freezone.make_static_url_for_context(ctx, rel, local_path=source_path)
        source_bundle = _director_control_bundle_from_input(source)
        if source_bundle:
            director_bundle = source_bundle

    output = _skill_output_metadata(skill, grouped, auto_commit=auto_commit)
    output["image_url"] = image_url
    output["label"] = skill.outputs[0].label
    if director_bundle:
        output["director_control_bundle"] = director_bundle
    if not is_standalone_beat_context:
        output["mainline_context"] = [
            {
                "kind": "director_combined",
                "episode": int(episode or 0),
                "beat": int(beat or 0),
                "role": "director_combined",
                "sourceUrl": image_url,
            }
        ]
    if committed:
        output["pushable"] = False
        output["committed"] = True
        output["committed_slot_url"] = image_url
    run_id = f"freezone.set_director_combined:{_freezone._new_job_id()}"
    _write_skill_run_metadata(
        project_dir,
        run_id,
        {
            "run_id": run_id,
            "skill_id": skill.id,
            "status": "completed",
            "outputs": [output],
            "canvas_id": body.canvas_id,
            "skill_node_id": body.skill_node_id,
        },
    )
    response = SkillRunResponse(run_id=run_id, status="completed")
    _freezone._append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=body.canvas_id,
        event_type="skill.run_completed",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "skill_id": skill.id,
            "skill_node_id": body.skill_node_id,
            "run_id": run_id,
            "status": response.status,
            "output_count": 1,
        },
    )
    _persist_skill_run_idempotency_response(
        project_dir,
        skill.id,
        body,
        idempotency_request_hash,
        response,
    )
    return response


def _parse_skill_output_push_target(output: dict) -> PushTarget | None:
    if output.get("pushable") is not True:
        return None
    if output.get("auto_commit") is not True:
        return None
    slot_target = output.get("slot_target")
    if not isinstance(slot_target, dict) or not slot_target.get("kind"):
        return None
    try:
        return PushRequest(source_url="skill-output", target=slot_target).target
    except Exception as exc:
        logger.warning("invalid skill output slot_target ignored: %s", exc)
        return None


async def _touch_canonical_slot_revision(
    ctx: ProjectContext,
    target: PushTarget,
) -> None:
    """Advance the owning entity revision after a canonical asset write.

    Canonical paths remain a PathResolver convention. The database timestamp
    is only the cache-busting revision used by lightweight asset summaries.
    Keeping this beside the shared Freezone slot writer prevents push and skill
    auto-commit paths from publishing new bytes under an unchanged URL.
    """

    if target.kind not in {"portrait", "prop_ref", *SCENE_ASSET_KINDS}:
        return
    async with sqlite_store_for_context_scope(ctx, load_graph_state=False) as store:
        if target.kind == "portrait":
            await store.touch_character_asset(target.character)
        elif target.kind == "prop_ref":
            await store.touch_prop_asset(target.prop_id)
        else:
            await store.touch_scene_asset(target.scene_id)


async def _copy_skill_output_to_slot(
    *,
    project_dir: Path,
    ctx: ProjectContext,
    source_path: Path,
    target: PushTarget,
) -> tuple[Path, str, Path | None, dict]:
    validate_source_for_slot(source_path, target)
    target_path = slot_target_path(project_dir, target)
    if target.kind == "scene_3gs_custom_scene":
        target_path = target_path.with_suffix(source_path.suffix.lower())
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        same_file = source_path.resolve() == target_path.resolve()
    except OSError:
        same_file = False

    should_match_existing_size = (
        target_path.exists()
        and not same_file
        and target.kind in {"frame", "sketch", "director_render"}
        and source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        and target_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    backup = None if same_file else backup_slot_if_exists(target_path)
    if same_file:
        image_adaptation = {"adapted": False, "same_file": True}
    elif should_match_existing_size:
        image_adaptation = _freezone._copy_image_matching_existing_target(source_path, target_path)
    else:
        image_adaptation = {"adapted": False}
        shutil.copy2(source_path, target_path)
    sync_slot_after_write(project_dir, target, target_path)
    await _touch_canonical_slot_revision(ctx, target)
    rel = target_path.relative_to(project_dir).as_posix()
    return (
        target_path,
        _freezone.make_static_url_for_context(ctx, rel, local_path=target_path),
        backup,
        image_adaptation,
    )


async def _finalize_skill_run_outputs(
    *,
    project: str,
    project_dir: Path,
    ctx: ProjectContext,
    metadata: dict,
    outputs: list[dict],
    user: dict,
) -> list[dict]:
    finalized: list[dict] = []
    changed = False
    for output in outputs:
        item = dict(output)
        target = _parse_skill_output_push_target(item)
        image_url = str(item.get("image_url") or "").strip()
        if target is not None and image_url:
            try:
                source_path = _freezone.resolve_static_url_to_path(image_url, project_dir)
                if not source_path.exists() or not source_path.is_file():
                    raise FileNotFoundError(source_path)
                target_path, target_url, backup, image_adaptation = await _copy_skill_output_to_slot(
                    project_dir=project_dir,
                    ctx=ctx,
                    source_path=source_path,
                    target=target,
                )
                item["image_url"] = target_url
                item["pushable"] = False
                item["committed"] = True
                item["committed_slot_url"] = target_url
                item["target_path"] = target_path.as_posix()
                item["backup"] = str(backup) if backup else None
                item["image_adaptation"] = image_adaptation
                changed = True
                _freezone._append_canvas_event(
                    project_dir=project_dir,
                    project_id=project,
                    canvas_id=metadata.get("canvas_id"),
                    event_type="skill.output_committed",
                    actor=_freezone._canvas_event_actor(user),
                    payload={
                        "skill_id": metadata.get("skill_id"),
                        "skill_node_id": metadata.get("skill_node_id"),
                        "run_id": metadata.get("run_id"),
                        "role": item.get("role"),
                        "target": target.model_dump(mode="json"),
                        "target_url": target_url,
                    },
                )
            except Exception as exc:
                logger.exception("skill output auto-commit failed")
                _raise_skill_error(
                    500,
                    code="skill_output_auto_commit_failed",
                    category="runtime",
                    message=f"skill output auto-commit failed: {exc}",
                    retryable=True,
                    user_action_hint="Retry the skill run. If this repeats, inspect the canonical slot target.",
                )
        finalized.append(item)

    if outputs and (changed or metadata.get("status") != "completed"):
        metadata["status"] = "completed"
        metadata["outputs"] = finalized
        _write_skill_run_metadata(project_dir, str(metadata.get("run_id") or ""), metadata)
        _freezone._append_canvas_event(
            project_dir=project_dir,
            project_id=project,
            canvas_id=metadata.get("canvas_id"),
            event_type="skill.run_completed",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "skill_id": metadata.get("skill_id"),
                "skill_node_id": metadata.get("skill_node_id"),
                "run_id": metadata.get("run_id"),
                "status": "completed",
                "output_count": len(finalized),
            },
        )
    return finalized


def _deterministic_frame_review(
    body: SkillRunRequest, grouped: dict[str, list[ResolvedSkillInput]]
) -> str:
    beat_input = _single_input(grouped, "beat_context")
    if _freezone._is_standalone_beat_context_input(beat_input):
        episode = beat = None
    else:
        episode, beat = _episode_and_beat_from_input(beat_input)
    frame = _single_input(grouped, "frame")
    frame_label = frame.node_id if frame else "frame"
    target_label = (
        "Canvas Beat Context"
        if episode is None or beat is None
        else f"Episode {episode}, Beat {beat}"
    )
    return (
        f"{target_label} frame review for {frame_label}: "
        "deterministic backend check completed. Verify composition, continuity, "
        "identity consistency, and whether visible details match the beat context."
    )


def _build_frame_review_prompt(
    body: SkillRunRequest, grouped: dict[str, list[ResolvedSkillInput]]
) -> str:
    beat_input = _single_input(grouped, "beat_context")
    if _freezone._is_standalone_beat_context_input(beat_input):
        episode = beat = None
    else:
        episode, beat = _episode_and_beat_from_input(beat_input)
    beat_context = (beat_input.beat_context if beat_input else None) or {}
    frame = _single_input(grouped, "frame")
    frame_details = {
        "node_id": frame.node_id if frame else "",
        "node_type": frame.node_type if frame else "",
        "image_url": frame.image_url if frame else "",
        "slot_target": frame.slot_target if frame else None,
        "candidate_origin": frame.candidate_origin if frame else None,
    }
    return "\n".join(
        [
            "Review this generated frame against the beat context.",
            "Return a concise production note covering composition, continuity, "
            "identity consistency, and mismatches.",
            f"Episode: {json.dumps(episode)}",
            f"Beat: {json.dumps(beat)}",
            f"Skill node: {body.skill_node_id}",
            f"Canvas: {body.canvas_id}",
            f"Beat context: {json.dumps(beat_context, ensure_ascii=False, sort_keys=True)}",
            f"Frame: {json.dumps(frame_details, ensure_ascii=False, sort_keys=True)}",
        ]
    )


async def _review_frame_text(
    body: SkillRunRequest, grouped: dict[str, list[ResolvedSkillInput]]
) -> str:
    reviewer = _freezone._agent_review_frame_reviewer
    if reviewer is None:
        return _deterministic_frame_review(body, grouped)

    prompt = _build_frame_review_prompt(body, grouped)
    try:
        review = reviewer(prompt)
        if hasattr(review, "__await__"):
            review = await review
    except Exception:
        logger.exception("agent.review_frame reviewer failed; using deterministic fallback")
        return _deterministic_frame_review(body, grouped)

    if isinstance(review, str) and review.strip():
        return review.strip()
    return _deterministic_frame_review(body, grouped)
