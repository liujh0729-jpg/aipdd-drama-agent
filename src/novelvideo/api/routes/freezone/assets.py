"""freezone.assets 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import CreateIdentityAssetRequest
from novelvideo.api.schemas import ImpactRequest
from novelvideo.api.schemas import PushRequest
from novelvideo.director_world import DirectorWorldService
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.freezone.canvas_static_urls import migrate_canvas_static_urls_in_memory
from novelvideo.freezone.paths import canvases_dir
from novelvideo.freezone.paths import freezone_root
from novelvideo.freezone.paths import uploads_dir
from novelvideo.freezone.slots import IdentityTarget
from novelvideo.freezone.slots import is_global_asset_slot
from novelvideo.freezone.slots import record_slot_stale_marks
from novelvideo.freezone.slots import slot_target_path
from novelvideo.models import CharacterIdentity
from novelvideo.models import beat_scene_id
from novelvideo.project_context import ProjectContext
from novelvideo.utils.path_resolver import canonical_beat_director_env_only_path
from novelvideo.utils.path_resolver import canonical_beat_selected_background_path
from novelvideo.utils.path_resolver import canonical_identity_costume_path
from novelvideo.utils.path_resolver import canonical_identity_path
from novelvideo.utils.path_resolver import canonical_identity_portrait_path
from novelvideo.utils.path_resolver import canonical_portrait_path
from novelvideo.utils.path_resolver import canonical_prop_reference_path
from novelvideo.utils.path_resolver import canonical_scene_master_path
from novelvideo.utils.path_resolver import canonical_scene_reverse_master_path

from ._common import (TAG_FREEZONE_ASSETS, TAG_FREEZONE_BOOTSTRAP, TAG_FREEZONE_COMMIT, logger)

router = APIRouter()


# ============================================================
# Commit 到 canonical slot（Freezone → Slot）
# ============================================================


def _asset_record_from_path(
    *,
    username: str,
    project: str,
    project_dir: Path,
    project_id: str,
    tab: str,
    kind: str,
    role: str,
    label: str,
    abs_path: Path,
    sublabel: str = "",
    aspect_ratio: str = "1:1",
    meta: dict | None = None,
) -> dict:
    rel_path = abs_path.relative_to(project_dir).as_posix()
    exists = abs_path.exists()
    suffix = abs_path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        media_type = "image"
    elif suffix in {".mp4", ".mov", ".webm"}:
        media_type = "video"
    elif suffix in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}:
        media_type = "audio"
    elif suffix in {".json", ".txt", ".md"}:
        media_type = "text"
    else:
        media_type = "file"
    if exists and not project_id:
        raise ValueError("project_id is required for freezone asset static URLs")
    url = _freezone.project_static_url(project_id, rel_path, local_path=abs_path) if exists else None
    slot_target = _slot_target_for_asset_record(kind=kind, role=role, meta=meta or {})
    record = {
        "id": f"{kind}:{role}:{rel_path}",
        "tab": tab,
        "kind": kind,
        "role": role,
        "label": label,
        "sublabel": sublabel,
        "rel_path": rel_path,
        "url": url,
        "exists": exists,
        "media_type": media_type,
        "aspect_ratio": aspect_ratio,
        "meta": meta or {},
    }
    if slot_target is not None:
        record["slot_target"] = slot_target
        record["pushable"] = bool(exists)
    director_control_bundle = _director_control_bundle_from_combined_ref(
        role=role,
        rel_path=rel_path,
        url=url,
    )
    if director_control_bundle is not None:
        record["director_control_bundle"] = director_control_bundle
    contexts = _mainline_context_for_asset_record(
        project_id=project_id,
        kind=kind,
        role=role,
        label=label,
        source_url=url,
        meta=meta or {},
    )
    if contexts:
        record["mainline_context"] = contexts
    history_links = _character_asset_history_links(project_id, role, meta or {})
    if history_links is not None:
        record.update(history_links)
    return record


def _asset_record_from_optional_project_path(
    *,
    username: str,
    project: str,
    project_dir: Path,
    project_id: str,
    tab: str,
    kind: str,
    role: str,
    label: str,
    stored_path: str,
    sublabel: str = "",
    aspect_ratio: str = "1:1",
    meta: dict | None = None,
) -> dict | None:
    raw = str(stored_path or "").strip()
    if not raw:
        return None
    abs_path = Path(raw)
    if not abs_path.is_absolute():
        abs_path = project_dir / raw
    try:
        abs_path.relative_to(project_dir)
    except ValueError:
        return None
    return _asset_record_from_path(
        username=username,
        project=project,
        project_dir=project_dir,
        project_id=project_id,
        tab=tab,
        kind=kind,
        role=role,
        label=label,
        sublabel=sublabel,
        abs_path=abs_path,
        aspect_ratio=aspect_ratio,
        meta=meta,
    )


def _character_asset_history_links(project_id: str, role: str, meta: dict) -> dict | None:
    character = str(meta.get("character") or "").strip()
    if not character:
        return None

    asset_kind = ""
    if role == "character_identity":
        asset_kind = "identity"
    elif role == "identity_costume":
        asset_kind = "identity_costume"
    elif role == "identity_portrait":
        asset_kind = "identity_portrait"
    elif role in {"character_portrait", "character_reference"}:
        asset_kind = "portrait"
    if not asset_kind:
        return None

    query = {"kind": asset_kind}
    identity_id = str(meta.get("identity_id") or "").strip()
    if asset_kind != "portrait":
        if not identity_id:
            return None
        query["identity_id"] = identity_id

    base = f"/api/v1/projects/{quote(project_id, safe='')}/characters/{quote(character, safe='')}"
    return {
        "history_url": f"{base}/asset-history?{urlencode(query)}",
        "restore_url": f"{base}/asset-history/restore",
    }


def _compact_mainline_context(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in (None, "", [])}


def _slot_target_for_asset_record(*, kind: str, role: str, meta: dict) -> dict | None:
    episode = meta.get("episode")
    beat = meta.get("beat")
    if role == "current_sketch" and episode and beat:
        return {"kind": "sketch", "episode": episode, "beat": beat}
    if role == "current_frame" and episode and beat:
        return {"kind": "frame", "episode": episode, "beat": beat}
    if role == "director_combined" and episode and beat:
        return {"kind": "director_render", "episode": episode, "beat": beat}
    if role == "current_video" and episode and beat:
        return {"kind": "video", "episode": episode, "beat": beat}
    if role == "current_audio" and episode and beat:
        return {"kind": "beat_audio", "episode": episode, "beat": beat}

    character = meta.get("character")
    identity_id = meta.get("identity_id")
    if role == "character_identity" and character and identity_id:
        return {"kind": "identity", "character": character, "identity_id": identity_id}
    if role == "identity_costume" and character and identity_id:
        return {"kind": "identity_costume", "character": character, "identity_id": identity_id}
    if role == "identity_portrait" and character and identity_id:
        return {"kind": "identity_portrait", "character": character, "identity_id": identity_id}
    if role in {"character_portrait", "character_reference"} and character:
        return {"kind": "portrait", "character": character}

    prop_id = meta.get("prop_id")
    if (kind == "prop" or role.startswith("prop_")) and prop_id:
        return {"kind": "prop_ref", "prop_id": prop_id}

    scene_id = meta.get("scene_id") or meta.get("scene")
    if (
        role
        in {
            "scene_master",
            "scene_360",
            "scene_reverse_master",
            "scene_spatial_layout",
            "scene_director_pano_360",
            "scene_3gs_active_ply",
            "scene_3gs_master_ply",
            "scene_3gs_reverse_ply",
            "scene_3gs_pano_ply",
            "scene_3gs_custom_scene",
            "scene_3gs_collision_glb",
        }
        and scene_id
    ):
        return {"kind": role, "scene_id": scene_id}

    return None


def _mainline_context_for_asset_record(
    *,
    project_id: str,
    kind: str,
    role: str,
    label: str,
    source_url: str | None,
    meta: dict,
) -> list[dict]:
    def base(context_kind: str, **extra) -> dict:
        return _compact_mainline_context(
            {
                "kind": context_kind,
                "projectId": project_id,
                "episode": meta.get("episode"),
                "beat": meta.get("beat"),
                "character": meta.get("character"),
                "identityId": meta.get("identity_id"),
                "sceneId": meta.get("scene_id") or meta.get("scene"),
                "propId": meta.get("prop_id"),
                "voiceId": meta.get("voice_id") or meta.get("slot"),
                "markerColor": meta.get("marker_color"),
                "visualDescription": meta.get("visual_description"),
                "narrationSegment": meta.get("narration_segment"),
                "detectedIdentities": meta.get("detected_identities"),
                "detectedProps": meta.get("detected_props"),
                "sketchColors": meta.get("sketch_colors"),
                "propMarkerColors": meta.get("prop_marker_colors"),
                "role": role,
                "label": label,
                "sourceUrl": source_url,
                **extra,
            }
        )

    if role in {
        "character_identity",
        "character_portrait",
        "identity_portrait",
        "identity_costume",
    }:
        return [base("identity")]
    if role in {"character_voice", "character_age_group_voice", "identity_voice"}:
        return [base("voice", audioRole="character_voice")]
    if kind == "scene" or role.startswith("scene_"):
        return [base("scene", plyKind=meta.get("ply_kind"))]
    if kind == "prop" or role.startswith("prop_"):
        return [base("prop")]
    if role == "current_sketch":
        return [base("sketch")]
    if role == "current_frame":
        return [base("frame")]
    if role == "current_video":
        return [base("video")]
    if role == "current_audio":
        return [base("audio", audioRole="beat_audio")]
    if role == "director_combined":
        return [base("director_combined")]
    if role == "selected_background":
        return [base("selected_background")]
    return []


def _tab_for_beat_context_ref(kind: str, role: str) -> str:
    if kind == "director":
        return "director"
    if kind in {"identity", "portrait"} or role.startswith("character_"):
        return "characters"
    if kind == "scene" or role.startswith("scene_"):
        return "scenes"
    if kind == "prop" or role.startswith("prop_"):
        return "props"
    return "beat"


def _is_beat_director_control_path(rel_path: str) -> bool:
    normalized = str(rel_path or "")
    return normalized.startswith("director_control_frames/ep") or normalized.startswith(
        "freezone/director_control_frames/ep"
    )


def _is_mainline_beat_director_control_ref(role: str, rel_path: str) -> bool:
    if role != "director_combined":
        return False
    normalized = str(rel_path or "")
    return _is_beat_director_control_path(normalized) and normalized.endswith("/combined.png")


def _director_control_bundle_from_combined_ref(
    *,
    role: str,
    rel_path: str | None,
    url: str | None,
) -> dict | None:
    if role != "director_combined":
        return None
    rel = str(rel_path or "").strip()
    combined_url = str(url or "").strip()
    combined_url_path = combined_url.split("?", 1)[0]
    if not rel.endswith("/combined.png") or not combined_url_path.endswith("/combined.png"):
        return None
    rel_base = rel[: -len("/combined.png")]
    url_base = combined_url_path[: -len("/combined.png")]
    return {
        "schema_version": "director_control_bundle_v1",
        "rel_paths": {
            "combined": f"{rel_base}/combined.png",
            "env_only": f"{rel_base}/env_only.png",
            "frame_meta": f"{rel_base}/frame_meta.json",
        },
        "urls": {
            "combined": f"{url_base}/combined.png",
            "env_only": f"{url_base}/env_only.png",
            "frame_meta": f"{url_base}/frame_meta.json",
        },
    }


def _is_mainline_beat_selected_background_ref(role: str, rel_path: str) -> bool:
    if role != "selected_background":
        return False
    normalized = str(rel_path or "")
    return _is_beat_director_control_path(normalized) and normalized.endswith(
        "/selected_background.png"
    )


def _is_beat_context_metadata_ref(kind: str, role: str, rel_path: str) -> bool:
    if role == "director_blocking":
        return True
    if role == "director_color_ref":
        return True
    if rel_path.startswith("director_blockings/"):
        return True
    return kind == "director" and rel_path.endswith(".json")


def _beat_context_asset_from_ref(
    *,
    ref: dict,
    project_id: str,
    episode: int,
    beat: int,
    beat_facts: dict | None = None,
) -> dict | None:
    rel_path = str(ref.get("rel_path") or "")
    kind = str(ref.get("kind") or "reference")
    role = str(ref.get("role") or "reference")
    if rel_path.startswith("freezone/") and not (
        _is_mainline_beat_director_control_ref(role, rel_path)
        or _is_mainline_beat_selected_background_ref(role, rel_path)
    ):
        return None
    if _is_beat_director_control_path(rel_path) and not (
        _is_mainline_beat_director_control_ref(role, rel_path)
        or _is_mainline_beat_selected_background_ref(role, rel_path)
    ):
        return None
    url = ref.get("url")
    exists = bool(ref.get("exists"))
    if _is_beat_context_metadata_ref(kind, role, rel_path):
        return None
    if role not in {
        "current_sketch",
        "current_frame",
        "current_video",
        "current_audio",
        "director_combined",
        "selected_background",
    }:
        return None
    label = str(ref.get("label") or role or kind)
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    merged_meta = {
        **meta,
        **(beat_facts or {}),
        "episode": int(episode),
        "beat": int(beat),
    }
    record = {
        "id": f"beat:{int(episode):03d}:{int(beat):03d}:{kind}:{role}:{rel_path or label}",
        "tab": _tab_for_beat_context_ref(kind, role),
        "kind": kind,
        "role": role,
        "label": label,
        "sublabel": f"EP{int(episode)} / Beat {int(beat)}",
        "rel_path": rel_path or None,
        "url": url if exists else None,
        "exists": exists,
        "media_type": str(ref.get("media_type") or "image"),
        "aspect_ratio": str(ref.get("aspect_ratio") or "1:1"),
        "meta": merged_meta,
    }
    slot_target = _slot_target_for_asset_record(kind=kind, role=role, meta=merged_meta)
    if slot_target is not None:
        record["slot_target"] = slot_target
        record["pushable"] = bool(exists)
    director_control_bundle = _director_control_bundle_from_combined_ref(
        role=role,
        rel_path=rel_path,
        url=url if exists else None,
    )
    if director_control_bundle is not None:
        record["director_control_bundle"] = director_control_bundle
    contexts = _mainline_context_for_asset_record(
        project_id=project_id,
        kind=kind,
        role=role,
        label=label,
        source_url=url if exists else None,
        meta=merged_meta,
    )
    if contexts:
        record["mainline_context"] = contexts
    return record


def _is_freezone_scene_library_role(role: str) -> bool:
    """Return whether a scene role should be exposed by /freezone/assets.

    This mirrors Assets > Scenes: concrete master/reverse/pano images and
    concrete 3D source packages are library assets. Deprecated sketch-pano
    slots, active aliases, and collision helpers stay internal.
    """

    return role in {
        "scene_master",
        "scene_reverse_master",
        "scene_director_pano_360",
        "scene_3gs_master_ply",
        "scene_3gs_reverse_ply",
        "scene_3gs_pano_ply",
        "scene_3gs_custom_scene",
    }


DIRECTOR_CAPTURE_FILES: tuple[tuple[str, str, str], ...] = (
    ("combined.png", "director_combined", "3GS 导演合成图"),
    ("selected_background.png", "selected_background", "selected background"),
    ("env_only.png", "director_env", "3GS environment plate"),
    ("env_actor_only.png", "director_env_actor", "3GS actor blocking plate"),
    ("actor_overlay_black.png", "actor_overlay", "actor overlay"),
    ("actor_mask.png", "actor_mask", "actor mask"),
    ("prop_staging_overlay.png", "prop_staging_overlay", "prop/staging overlay"),
    ("prop_staging_mask.png", "prop_staging_mask", "prop/staging mask"),
    ("frame_meta.json", "frame_meta", "3GS frame metadata"),
)


def _freezone_director_control_frames_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "director_control_frames"


def _freezone_director_capture_base(project_dir: Path, episode: int, beat: int) -> tuple[Path, str]:
    ep_dir = f"ep{int(episode):03d}"
    beat_dir = f"beat_{int(beat):02d}"
    base_dir = _freezone_director_control_frames_dir(project_dir) / ep_dir / beat_dir
    return base_dir, base_dir.relative_to(project_dir).as_posix()


def _director_capture_file_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode: int,
    beat: int,
) -> list[dict]:
    base_dir, base_rel = _freezone_director_capture_base(project_dir, episode, beat)
    out: list[dict] = []
    for filename, role, label in DIRECTOR_CAPTURE_FILES:
        rel_path = f"{base_rel}/{filename}"
        path = base_dir / filename
        exists = path.exists()
        out.append(
            {
                "filename": filename,
                "role": role,
                "label": label,
                "rel_path": rel_path,
                "exists": exists,
                "url": (
                    _freezone.make_static_url_for_context(ctx, rel_path, local_path=path) if exists else None
                ),
                "media_type": (
                    "image"
                    if Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                    else "json"
                ),
                "size": path.stat().st_size if exists else 0,
                "modified_at": (
                    canvas_store.timestamp_utc_iso(path.stat().st_mtime) if exists else None
                ),
            }
        )
    return out


async def _beat_for_capture(
    username: str,
    project: str,
    episode: int,
    beat: int,
    ctx: ProjectContext | None = None,
) -> dict:
    store = (
        await _freezone.make_sqlite_store_for_context(ctx)
        if ctx
        else await _freezone.make_sqlite_store(username, project)
    )
    try:
        beats = await store.get_beats_as_dicts(episode)
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    target = next((b for b in beats if int(b.get("beat_number") or -1) == int(beat)), None)
    if not target:
        raise HTTPException(404, f"beat not found: ep{episode} beat{beat}")
    return target


async def _persist_freezone_selected_background_scene_ref(
    *,
    ctx: ProjectContext,
    episode: int,
    beat: int,
) -> None:
    """Mark a Freezone-committed image as the Beat's render background slot."""
    store = await _freezone.make_sqlite_store_for_context(ctx)
    try:
        beats = await store.get_beats_as_dicts(int(episode))
        target = next(
            (item for item in beats if int(item.get("beat_number") or 0) == int(beat)),
            None,
        )
        if not target:
            raise HTTPException(404, f"beat not found: ep{episode} beat{beat}")

        scene_ref = dict(target.get("scene_ref") or {})
        scene_id = beat_scene_id(target)
        if scene_id:
            scene_ref["scene_id"] = scene_id
        scene_ref["render_anchor_id"] = "selected_background"
        scene_ref["render_anchor_source_id"] = "freezone_commit"
        scene_ref.pop("render_anchor_path", None)
        await store.update_beat_asset(
            episode_number=int(episode),
            beat_number=int(beat),
            scene_ref=scene_ref,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post("/projects/{project}/freezone/impact", tags=[TAG_FREEZONE_COMMIT])
async def freezone_impact(
    project: str,
    body: ImpactRequest,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, _project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    impacted = await _freezone.compute_slot_impact(username, project_name, body.target)
    return {
        "ok": True,
        "data": {
            "target": body.target.model_dump(),
            "affected_beats": impacted,
            "affected_count": len(impacted),
        },
    }


def _sync_env_only_to_selected_background(project_dir: Path, episode: int, beat: int) -> bool:
    """Lazy mirror env_only.png → selected_background.png if env_only is newer.

    Why mirror at all:
      PlayCanvas editor writes env_only.png / actor_overlay_black.png /
      actor_mask.png / combined.png directly via /@fs proxy when the user
      exports. There is no SuperTale write route to hook into, so we cannot
      guarantee selected_background.png is updated at write time without
      touching PlayCanvas.

    Trigger:
      Called from the explicit POST sync route (NOT GET manifest — GET must
      stay side-effect-free per REST hygiene + "Push is canonical write
      boundary" architectural rule).

    Cost: 2 stat() calls + (when stale) one file copy. Idempotent — if mtimes
    already match, no copy. Returns True when a copy actually happened.
    Failure is silent (logged) — never block the calling route.
    """
    try:
        env_only_path = canonical_beat_director_env_only_path(project_dir, int(episode), int(beat))
        if not env_only_path.is_file():
            return False
        selected_path = canonical_beat_selected_background_path(
            project_dir, int(episode), int(beat)
        )
        env_mtime = env_only_path.stat().st_mtime
        # If selected_background.png doesn't exist OR env_only is newer, mirror it.
        if not selected_path.exists() or env_mtime > selected_path.stat().st_mtime:
            selected_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(env_only_path, selected_path)
            # Preserve mtime so the next check sees them as in-sync.
            os.utime(selected_path, (env_mtime, env_mtime))
            logger.info(
                "[director-capture] mirrored env_only → selected_background ep=%s beat=%s",
                episode,
                beat,
            )
            return True
        return False
    except Exception as exc:  # noqa: BLE001 — never block calling route
        logger.warning("[director-capture] env_only mirror failed: %s", exc)
        return False


@router.get("/projects/{project}/freezone/director-capture", tags=[TAG_FREEZONE_ASSETS])
async def freezone_director_capture_manifest(
    project: str,
    episode: int,
    beat: int,
    canvas_id: Optional[str] = None,
    node_id: Optional[str] = None,
    user: dict = Depends(get_api_user),
):
    """返回某个 beat 当前的 3GS director capture 状态。

    这是 Freezone 和 PlayCanvas 3GS 导演台之间的桥接接口：
    调用方可以先打开 `editor_url`，在导演台里导出控制帧，
    然后再次调用这个接口，把导出的文件转成画布节点使用。

    Pure read — no side effects. Callers that need the env_only.png →
    selected_background.png mirror (e.g. frontend right after the user
    returns from director stage) should POST
    `/projects/{project}/freezone/director-capture/sync-background` first.
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    beat_data = await _freezone._beat_for_capture(
        username,
        project_name,
        int(episode),
        int(beat),
        ctx=ctx,
    )
    scene_name = beat_scene_id(beat_data)
    files = _director_capture_file_payload(
        ctx=ctx,
        project_dir=project_dir,
        episode=int(episode),
        beat=int(beat),
    )
    editor_url = None
    can_open_stage = False
    if scene_name:
        try:
            service = DirectorWorldService(project_dir)
            editor_url = service.make_3gs_editor_url(
                episode=int(episode),
                scene_id=scene_name,
                slate_beat=int(beat),
                user=username,
                project=project_name,
                control_frames_dir=_freezone_director_control_frames_dir(project_dir),
            )
            if editor_url:
                extra = {
                    "freezone_project": project,
                    "freezone_canvas": canvas_id or "",
                    "freezone_capture_node": node_id or "director_capture",
                    "return_to_freezone": "1",
                }
                separator = "&" if "?" in editor_url else "?"
                editor_url = f"{editor_url}{separator}{urlencode(extra)}"
                can_open_stage = True
        except Exception as exc:
            logger.warning("failed to build 3GS director stage url: %s", exc)

    return {
        "ok": True,
        "data": {
            "project": project,
            "episode": int(episode),
            "beat": int(beat),
            "scene_id": scene_name,
            "canvas_id": canvas_id,
            "node_id": node_id or "director_capture",
            "capture_dir": _freezone_director_capture_base(project_dir, int(episode), int(beat))[
                0
            ].as_posix(),
            "editor_url": editor_url,
            "can_open_stage": can_open_stage,
            "files": files,
            "existing_count": sum(1 for item in files if item.get("exists")),
        },
    }


@router.post(
    "/projects/{project}/freezone/director-capture/sync-background",
    tags=[TAG_FREEZONE_ASSETS],
)
async def freezone_director_capture_sync_background(
    project: str,
    episode: int,
    beat: int,
    user: dict = Depends(get_api_user),
):
    """Mirror env_only.png → selected_background.png (idempotent).

    Use this **after** the user returns from the 3GS director stage (where
    PlayCanvas writes env_only.png directly via /@fs proxy). Frontend should
    call this before re-rendering canvases that consume
    selected_background.png.

    Why POST + editor permission:
      The GET manifest route used to do this as a side effect; that
      violates the "Push / explicit action is canonical write boundary"
      architectural rule (GET should be safe + idempotent). Splitting the
      mirror into its own POST keeps reads pure and surfaces the write
      intent in the call.

    Idempotent — if env_only.png is missing OR already older-than /
    equal-to selected_background.png, no copy happens (returns synced=False).
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="editor"
    )
    synced = _sync_env_only_to_selected_background(project_dir, int(episode), int(beat))
    return {"ok": True, "data": {"synced": synced, "episode": int(episode), "beat": int(beat)}}


@router.get(
    "/projects/{project}/freezone/scene-assets-for-beat",
    tags=[TAG_FREEZONE_ASSETS],
)
async def freezone_scene_assets_for_beat(
    project: str,
    episode: int,
    beat: int,
    user: dict = Depends(get_api_user),
):
    """Lazy thumbnail/source pool for a beat's "selected_background" slot.

    Drag-in / popover use case: when the user spawns a `selected_background`
    slot node (via drag from another canvas, or by clicking "选源" on the
    node's popover), the frontend needs to enumerate the scene's available
    source assets (`scene_master.png` / `scene_reverse_master.png` /
    pano 360 / 3GS PLY) so the user can pick which one to crop into the
    canonical `selected_background.png`.

    Why lazy: we deliberately do NOT store these URLs in the slot node's
    `data` (that would denormalize + go stale when scene assets re-render).
    The node only carries `{scene_id, episode, beat}`; this route resolves
    those to current canonical URLs on demand.

    Each `*_url` may be null if the underlying canonical file doesn't exist
    yet (e.g. user hasn't run scene-master generation). Caller renders only
    the available sources.
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    beat_data = await _freezone._beat_for_capture(
        username,
        project_name,
        int(episode),
        int(beat),
        ctx=ctx,
    )
    scene_name = beat_scene_id(beat_data)

    def _resolve(path: Path | None) -> str | None:
        if path is None or not path.is_file():
            return None
        try:
            rel = path.relative_to(project_dir).as_posix()
        except ValueError:
            return None
        return _freezone.make_static_url_for_context(ctx, rel, local_path=path)

    master_url: str | None = None
    reverse_url: str | None = None
    director_env_only_url: str | None = None
    pano_360_url: str | None = None
    ply_url: str | None = None
    director_env_only_url = _resolve(
        canonical_beat_director_env_only_path(project_dir, int(episode), int(beat))
    )
    if scene_name:
        master_url = _resolve(canonical_scene_master_path(project_dir, scene_name))
        reverse_url = _resolve(canonical_scene_reverse_master_path(project_dir, scene_name))
        # scene_director_pano_360 lives under director_worlds/<scene>/v1 —
        # `stage_manifest.resolve_pano_path` already encodes that.
        try:
            from novelvideo.director_world import stage_manifest

            pano_360_url = _resolve(stage_manifest.resolve_pano_path(project_dir, scene_name))
            ply_url = _resolve(stage_manifest.resolve_ply_path(project_dir, scene_name))
        except Exception as exc:  # noqa: BLE001 — manifest issues should not 500 the listing
            logger.warning("scene-assets-for-beat: stage_manifest lookup failed: %s", exc)

    return {
        "ok": True,
        "data": {
            "project": project,
            "episode": int(episode),
            "beat": int(beat),
            "scene_id": scene_name,
            "master_url": master_url,
            "reverse_url": reverse_url,
            "director_env_only_url": director_env_only_url,
            "pano_360_url": pano_360_url,
            "ply_url": ply_url,
        },
    }


@router.post("/projects/{project}/freezone/push", tags=[TAG_FREEZONE_COMMIT])
async def freezone_push(project: str, body: PushRequest, user: dict = Depends(get_api_user)):
    """把 Freezone candidate 媒体写回主流程 canonical slot。

    源文件通常来自 `freezone/_outputs/`，也允许来自同项目作用域内的其他静态资源。
    写入前会自动备份已有目标文件。
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source file not found: {source_path}")
    target, target_url, backup, image_adaptation = await _freezone._copy_skill_output_to_slot(
        project_dir=project_dir,
        ctx=ctx,
        source_path=source_path,
        target=body.target,
    )
    if body.target.kind == "selected_background":
        await _persist_freezone_selected_background_scene_ref(
            ctx=ctx,
            episode=body.target.episode,
            beat=body.target.beat,
        )
    impacted: list[dict] = []
    stale_marked = 0
    if body.mark_stale and is_global_asset_slot(body.target):
        impacted = await _freezone.compute_slot_impact(username, project_name, body.target)
        stale_marked = record_slot_stale_marks(
            project_dir,
            target=body.target,
            impacted=impacted,
            source_url=body.source_url,
        )

    if body.target.kind in {"identity", "identity_costume", "identity_portrait"}:
        # F5 收尾逻辑：尽量提示 cognee_store 刷新 identity 记录。
        # 磁盘文件才是真正的数据源，这里只是 best-effort 同步。
        try:
            async with _freezone.freezone_cognee_store_for_context_scope(ctx) as store:
                character = body.target.character
                identity_id = body.target.identity_id
                if body.target.kind == "identity_costume":
                    try:
                        await store.update_character_identity(
                            character,
                            identity_id,
                            costume_image=str(target),
                        )
                    except AttributeError:
                        logger.info(
                            "cognee_store.update_character_identity not available; "
                            "skipping costume metadata sync (file is updated)"
                        )
                if body.target.kind == "identity_portrait":
                    try:
                        await store.update_character_identity(
                            character,
                            identity_id,
                            portrait_image=str(target),
                        )
                    except AttributeError:
                        logger.info(
                            "cognee_store.update_character_identity not available; "
                            "skipping identity portrait metadata sync (file is updated)"
                        )
                try:
                    await store.touch_identity(character, identity_id)  # type: ignore[attr-defined]
                except AttributeError:
                    logger.info(
                        "cognee_store.touch_identity not available; "
                        "skipping metadata sync (file is updated)"
                    )
        except Exception as exc:
            logger.warning("identity cognee sync best-effort failed: %s", exc)

    _freezone._append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=None,
        event_type="canvas.push_committed",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "source_url": body.source_url,
            "target": body.target.model_dump(mode="json"),
            "target_path": str(target),
            "target_url": target_url,
            "backup": str(backup) if backup else None,
            "stale_marked": stale_marked,
            "affected_count": len(impacted),
        },
    )
    return {
        "ok": True,
        "data": {
            "target_path": str(target),
            "target_url": target_url,
            "backup": str(backup) if backup else None,
            "image_adaptation": image_adaptation,
            "stale_marked": stale_marked,
            "affected_count": len(impacted),
        },
    }


@router.get("/projects/{project}/freezone/assets", tags=[TAG_FREEZONE_ASSETS])
async def list_freezone_assets(
    project: str,
    user: dict = Depends(get_api_user),
):
    """列出当前项目作用域下可供 Freezone 使用的 canonical assets。

    SQLiteStore 负责项目事实数据，PathResolver 负责磁盘路径解析。
    返回里同时保留 `exists` 和 `url`，这样调用方可以区分：
    “这是一个已知资产概念” 和 “这是一个当前可直接引用的文件”。
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    store = await _freezone.make_sqlite_store_for_context(ctx)

    assets: list[dict] = []

    try:
        for character in store.get_all_characters():
            portrait_path = canonical_portrait_path(project_dir, character.name)
            assets.append(
                _asset_record_from_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="characters",
                    kind="portrait",
                    role="character_portrait",
                    label=f"{character.name} / portrait",
                    sublabel=character.name,
                    abs_path=portrait_path,
                    aspect_ratio="1:1",
                    meta={"character": character.name},
                )
            )
            default_voice = _asset_record_from_optional_project_path(
                username=username,
                project=project_name,
                project_dir=project_dir,
                project_id=project,
                tab="characters",
                kind="audio",
                role="character_voice",
                label=f"{character.name} / 默认声线",
                sublabel=character.name,
                stored_path=str(getattr(character, "reference_audio_path", "") or ""),
                meta={
                    "character": character.name,
                    "scope": "character_default",
                    "slot": "default",
                    "age_group": str(getattr(character, "age_group", "") or ""),
                    "sha256": str(getattr(character, "reference_audio_sha256", "") or ""),
                    "updated_at": str(getattr(character, "reference_audio_updated_at", "") or ""),
                },
            )
            if default_voice is not None:
                assets.append(default_voice)

            voice_samples = getattr(character, "voice_samples_by_age_group", None) or {}
            if isinstance(voice_samples, dict):
                for slot, slot_label in _freezone.FREEZONE_AUDIO_AGE_GROUP_LABELS.items():
                    entry = voice_samples.get(slot)
                    if not isinstance(entry, dict):
                        continue
                    age_voice = _asset_record_from_optional_project_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="audio",
                        role="character_age_group_voice",
                        label=f"{character.name} / {slot_label}声线",
                        sublabel=character.name,
                        stored_path=str(entry.get("path", "") or ""),
                        meta={
                            "character": character.name,
                            "scope": "character_age_group",
                            "slot": slot,
                            "age_group": slot,
                            "sha256": str(entry.get("sha256", "") or ""),
                            "updated_at": str(entry.get("updated_at", "") or ""),
                        },
                    )
                    if age_voice is not None:
                        assets.append(age_voice)

            for identity in character.identities or []:
                identity_name = (
                    getattr(identity, "identity_name", "")
                    or getattr(identity, "identity_id", "")
                    or "identity"
                )
                identity_id = (
                    getattr(identity, "identity_id", "") or f"{character.name}_{identity_name}"
                )
                identity_path = canonical_identity_path(project_dir, character.name, identity_name)
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity",
                        role="character_identity",
                        label=f"{character.name} / {identity_name}",
                        sublabel=character.name,
                        abs_path=identity_path,
                        aspect_ratio="1:1",
                        meta={"character": character.name, "identity_id": identity_id},
                    )
                )
                identity_costume_path = canonical_identity_costume_path(
                    project_dir,
                    character.name,
                    identity_name,
                )
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity_costume",
                        role="identity_costume",
                        label=f"{character.name} / {identity_name} costume",
                        sublabel=character.name,
                        abs_path=identity_costume_path,
                        aspect_ratio="3:4",
                        meta={
                            "character": character.name,
                            "identity_id": identity_id,
                            "identity_name": identity_name,
                        },
                    )
                )
                identity_portrait_path = canonical_identity_portrait_path(
                    project_dir,
                    character.name,
                    identity_name,
                )
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity_portrait",
                        role="identity_portrait",
                        label=f"{character.name} / {identity_name} portrait",
                        sublabel=character.name,
                        abs_path=identity_portrait_path,
                        aspect_ratio="3:4",
                        meta={
                            "character": character.name,
                            "identity_id": identity_id,
                            "identity_name": identity_name,
                        },
                    )
                )
                identity_voice = _asset_record_from_optional_project_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="characters",
                    kind="audio",
                    role="identity_voice",
                    label=f"{character.name} / {identity_name}声线",
                    sublabel=character.name,
                    stored_path=str(getattr(identity, "reference_audio_path", "") or ""),
                    meta={
                        "character": character.name,
                        "identity_id": identity_id,
                        "identity_name": identity_name,
                        "scope": "identity",
                        "age_group": str(getattr(identity, "age_group", "") or ""),
                        "sha256": str(getattr(identity, "reference_audio_sha256", "") or ""),
                        "updated_at": str(
                            getattr(identity, "reference_audio_updated_at", "") or ""
                        ),
                    },
                )
                if identity_voice is not None:
                    assets.append(identity_voice)

        for scene in await store.list_scenes():
            scene_name = scene.name
            director_pano_path = None
            stage_manifest_module = None
            try:
                from novelvideo.director_world import stage_manifest

                stage_manifest_module = stage_manifest
                director_pano_path = stage_manifest_module.resolve_pano_path(
                    project_dir, scene_name
                )
            except Exception:
                director_pano_path = None
            for kind, role, label, path, aspect in [
                (
                    "scene",
                    "scene_master",
                    f"{scene_name} / master",
                    canonical_scene_master_path(project_dir, scene_name),
                    "16:9",
                ),
                (
                    "scene",
                    "scene_reverse_master",
                    f"{scene_name} / reverse master",
                    canonical_scene_reverse_master_path(project_dir, scene_name),
                    "16:9",
                ),
                (
                    "scene",
                    "scene_director_pano_360",
                    f"{scene_name} / director pano 360",
                    director_pano_path,
                    "2:1",
                ),
            ]:
                if path is None or not _is_freezone_scene_library_role(role):
                    continue
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="scenes",
                        kind=kind,
                        role=role,
                        label=label,
                        sublabel=scene_name,
                        abs_path=path,
                        aspect_ratio=aspect,
                        meta={
                            "scene": scene_name,
                            "scene_id": scene_name,
                            "scene_type": scene.scene_type,
                        },
                    )
                )
            if stage_manifest_module is not None:
                seen_stage_asset_paths: set[str] = set()
                for ply_kind, role, label in [
                    ("master", "scene_3gs_master_ply", f"{scene_name} / 3D 世界（正面）"),
                    ("reverse", "scene_3gs_reverse_ply", f"{scene_name} / 3D 世界（背面）"),
                    ("pano", "scene_3gs_pano_ply", f"{scene_name} / 3D 世界（360）"),
                    ("custom", "scene_3gs_custom_scene", f"{scene_name} / 3D 世界（自定义）"),
                ]:
                    ply_path = stage_manifest_module.resolve_ply_path(
                        project_dir,
                        scene_name,
                        ply_kind=ply_kind,
                    )
                    if ply_path is None or not _is_freezone_scene_library_role(role):
                        continue
                    rel = ply_path.relative_to(project_dir).as_posix()
                    if rel in seen_stage_asset_paths:
                        continue
                    seen_stage_asset_paths.add(rel)
                    assets.append(
                        _asset_record_from_path(
                            username=username,
                            project=project_name,
                            project_dir=project_dir,
                            project_id=project,
                            tab="scenes",
                            kind="scene",
                            role=role,
                            label=label,
                            sublabel=scene_name,
                            abs_path=ply_path,
                            aspect_ratio="1:1",
                            meta={
                                "scene": scene_name,
                                "scene_id": scene_name,
                                "scene_type": scene.scene_type,
                                "ply_kind": ply_kind,
                            },
                        )
                    )
        for prop in await store.list_props():
            prop_name = prop.name
            assets.append(
                _asset_record_from_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="props",
                    kind="prop",
                    role="prop_reference",
                    label=f"{prop_name} / reference",
                    sublabel=prop.prop_type or "object",
                    abs_path=canonical_prop_reference_path(project_dir, prop_name),
                    aspect_ratio="1:1",
                    meta={"prop_id": prop_name, "prop_type": prop.prop_type},
                )
            )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    return {"ok": True, "data": assets}


@router.get("/projects/{project}/freezone/assets/beat-context", tags=[TAG_FREEZONE_ASSETS])
async def list_freezone_beat_context_assets(
    project: str,
    episode: Optional[int] = None,
    beat: Optional[int] = None,
    user: dict = Depends(get_api_user),
):
    """列出 default/project 画布可用的 novelvideo Beat 上下文资产。

    这个接口只聚合 novelvideo canonical 产物，不扫描 `freezone/_uploads`
    或 `freezone/_outputs`。用于 default 画布展示全局 Beat 资源；具体 Beat
    预设画布仍可继续读取 canvas `metadata.references`。
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    store = await _freezone.make_sqlite_store_for_context(ctx)

    requested_episode = int(episode) if episode is not None else None
    requested_beat = int(beat) if beat is not None else None
    if requested_episode is None and requested_beat is not None:
        raise HTTPException(400, "episode is required when beat is provided")

    flat_assets: list[dict] = []
    episode_groups: list[dict] = []
    try:
        if requested_episode is not None:
            episode_numbers = [requested_episode]
        else:
            episode_numbers = sorted(
                {
                    int(getattr(ep, "number", 0) or 0)
                    for ep in getattr(store, "_episodes", {}).values()
                    if int(getattr(ep, "number", 0) or 0) > 0
                }
            )
            if not episode_numbers:
                try:
                    visual_beats = await store.list_visual_beats()
                except Exception:
                    visual_beats = []
                episode_numbers = sorted(
                    {
                        int(getattr(item, "episode_number", 0) or 0)
                        for item in visual_beats
                        if int(getattr(item, "episode_number", 0) or 0) > 0
                    }
                )

        for ep_num in episode_numbers:
            try:
                beats = await store.get_beats_as_dicts(ep_num)
            except Exception as exc:
                logger.warning("failed to load beats for asset context ep%s: %s", ep_num, exc)
                beats = []
            beat_numbers = sorted(
                {
                    int(item.get("beat_number") or 0)
                    for item in beats
                    if int(item.get("beat_number") or 0) > 0
                }
            )
            if requested_beat is not None:
                beat_numbers = [num for num in beat_numbers if num == requested_beat]

            beat_groups: list[dict] = []
            for beat_num in beat_numbers:
                try:
                    context = await _freezone.build_beat_preset_context(
                        project_id=ctx.project_id,
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        store=store,
                        episode=ep_num,
                        beat=beat_num,
                        primary_slot="render",
                    )
                    context = (
                        migrate_canvas_static_urls_in_memory(
                            context,
                            project_id=ctx.project_id,
                            owner_username=username,
                            project_name=project_name,
                            project_dir=project_dir,
                        )
                        or context
                    )
                except Exception as exc:
                    logger.warning(
                        "failed to build beat context assets for ep%s beat%s: %s",
                        ep_num,
                        beat_num,
                        exc,
                    )
                    continue

                beat_data = context.get("beat_data") if isinstance(context, dict) else {}
                refs = context.get("refs") if isinstance(context, dict) else []
                beat_facts = {
                    "visual_description": str((beat_data or {}).get("visual_description") or ""),
                    "narration_segment": str((beat_data or {}).get("narration_segment") or ""),
                    "scene_id": beat_scene_id(beat_data or {}),
                    "detected_identities": (beat_data or {}).get("detected_identities") or [],
                    "detected_props": (beat_data or {}).get("detected_props") or [],
                    "sketch_colors": (
                        (context.get("sketch_context") or {}).get("sketch_colors") or {}
                    ),
                    "prop_marker_colors": (
                        (context.get("sketch_context") or {}).get("prop_marker_colors") or {}
                    ),
                }
                assets = [
                    asset
                    for ref in refs
                    if isinstance(ref, dict)
                    for asset in [
                        _beat_context_asset_from_ref(
                            ref=ref,
                            project_id=project,
                            episode=ep_num,
                            beat=beat_num,
                            beat_facts=beat_facts,
                        )
                    ]
                    if asset is not None
                ]
                existing_assets = [
                    asset for asset in assets if asset.get("exists") and asset.get("url")
                ]
                flat_assets.extend(existing_assets)
                beat_groups.append(
                    {
                        "episode": ep_num,
                        "beat": beat_num,
                        "label": f"EP{ep_num} / Beat {beat_num}",
                        "scene_id": beat_facts["scene_id"],
                        "detected_identities": beat_facts["detected_identities"],
                        "detected_props": beat_facts["detected_props"],
                        "sketch_colors": beat_facts["sketch_colors"],
                        "prop_marker_colors": beat_facts["prop_marker_colors"],
                        "visual_description": str(
                            (beat_data or {}).get("visual_description") or ""
                        ),
                        "narration_segment": str((beat_data or {}).get("narration_segment") or ""),
                        "assets": assets,
                        "asset_count": len(existing_assets),
                    }
                )

            if beat_groups:
                episode_groups.append({"episode": ep_num, "beats": beat_groups})
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    return {
        "ok": True,
        "data": {
            "scope": {
                "episode": requested_episode,
                "beat": requested_beat,
            },
            "episodes": episode_groups,
            "assets": flat_assets,
        },
    }


@router.post("/projects/{project}/freezone/assets/identities", tags=[TAG_FREEZONE_ASSETS])
async def freezone_create_identity_asset(
    project: str,
    body: CreateIdentityAssetRequest,
    user: dict = Depends(get_api_user),
):
    """从选中的 Freezone 图片创建一个新的角色 identity。

    这个接口故意和 `/freezone/push` 分开：
    `push` 是覆盖已有 canonical slot，
    这里则是新建一个全新的 identity slot，并注册进项目存储。
    """
    ctx, username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    character = body.character.strip()
    identity_name = body.identity_name.strip()
    if not character:
        raise HTTPException(400, "character is required")
    if not identity_name:
        raise HTTPException(400, "identity_name is required")

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source file not found: {source_path}")

    store = await _freezone.make_sqlite_store_for_context(ctx)
    try:
        char = store.get_character(character)
        if not char:
            raise HTTPException(404, f"character not found: {character}")
        identity = CharacterIdentity(
            identity_id=f"{character}_{identity_name}",
            character_name=character,
            identity_name=identity_name,
            appearance_details=body.appearance_details.strip(),
            face_prompt=body.face_prompt.strip(),
            age_group=body.age_group.strip(),
            source="freezone",
        )
        if any(existing.identity_id == identity.identity_id for existing in char.identities):
            raise HTTPException(409, f"identity already exists: {identity.identity_id}")
        target = slot_target_path(
            project_dir,
            IdentityTarget(
                character=character,
                identity_id=identity.identity_id,
            ),
        )
        if target.exists():
            raise HTTPException(409, f"identity image already exists: {identity.identity_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image

            with Image.open(source_path) as img:
                img.convert("RGB").save(target, format="PNG")
        except Exception:
            shutil.copy2(source_path, target)
        try:
            await store.add_character_identity(character, identity)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                logger.warning("failed to rollback copied identity image: %s", target)
            raise
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    rel = target.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "data": {
            "character": character,
            "identity_id": identity.identity_id,
            "identity_name": identity.identity_name,
            "target_path": str(target),
            "target_url": _freezone.make_static_url_for_context(ctx, rel, local_path=target),
        },
    }


@router.post("/projects/{project}/freezone/init", tags=[TAG_FREEZONE_BOOTSTRAP])
async def init_freezone(project: str, user: dict = Depends(get_api_user)):
    """懒创建 Freezone 目录树，可重复调用且幂等。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    freezone_root(project_dir).mkdir(parents=True, exist_ok=True)
    uploads_dir(project_dir).mkdir(parents=True, exist_ok=True)
    canvases_dir(canvas_project_dir).mkdir(parents=True, exist_ok=True)
    try:
        default_canvas = canvas_store.ensure_default_canvas(
            canvas_project_dir,
            project_id=ctx.project_id,
            actor_id=_freezone._canvas_actor_id(user),
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    return {
        "ok": True,
        "data": {
            "freezone_dir": str(freezone_root(project_dir)),
            "default_canvas": {
                "canvas_id": "default",
                "created": default_canvas.created,
                "revision": default_canvas.payload.get("revision"),
            },
        },
    }
