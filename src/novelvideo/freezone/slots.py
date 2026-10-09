"""Canonical SuperTale asset slots used by Freezone commits.

A slot is not a canvas node and not a recipe. It is the controlled canonical
write target shared by the main pipeline and Freezone's Commit to Asset flow.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional, Union

from fastapi import HTTPException
from pydantic import BaseModel

from novelvideo.api.deps import make_sqlite_store
from novelvideo.freezone.paths import freezone_root
from novelvideo.utils.path_resolver import (
    PathResolver,
    canonical_beat_selected_background_path,
    canonical_identity_costume_path,
    canonical_identity_path,
    canonical_identity_portrait_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_360_path,
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
    canonical_scene_spatial_layout_path,
)


class FrameTarget(BaseModel):
    kind: Literal["frame"] = "frame"
    episode: int
    beat: int


class SketchTarget(BaseModel):
    kind: Literal["sketch"] = "sketch"
    episode: int
    beat: int


class DirectorRenderTarget(BaseModel):
    kind: Literal["director_render"] = "director_render"
    episode: int
    beat: int


class SelectedBackgroundTarget(BaseModel):
    kind: Literal["selected_background"] = "selected_background"
    episode: int
    beat: int


class IdentityTarget(BaseModel):
    kind: Literal["identity"] = "identity"
    character: str
    identity_id: str


class IdentityCostumeTarget(BaseModel):
    kind: Literal["identity_costume"] = "identity_costume"
    character: str
    identity_id: str


class IdentityPortraitTarget(BaseModel):
    kind: Literal["identity_portrait"] = "identity_portrait"
    character: str
    identity_id: str


class PortraitTarget(BaseModel):
    kind: Literal["portrait"] = "portrait"
    character: str


class SceneMasterTarget(BaseModel):
    kind: Literal["scene_master"] = "scene_master"
    scene_id: str


# Direct 360 panorama slot. It is kept for legacy/experimental direct-pano
# workflows, but the user-facing mainline scene asset and default commit target
# for director-world panoramas is scene_director_pano_360.
class Scene360Target(BaseModel):
    kind: Literal["scene_360"] = "scene_360"
    scene_id: str


class SceneReverseMasterTarget(BaseModel):
    kind: Literal["scene_reverse_master"] = "scene_reverse_master"
    scene_id: str


class SceneSpatialLayoutTarget(BaseModel):
    kind: Literal["scene_spatial_layout"] = "scene_spatial_layout"
    scene_id: str


class SceneDirectorPano360Target(BaseModel):
    kind: Literal["scene_director_pano_360"] = "scene_director_pano_360"
    scene_id: str


class Scene3gsActivePlyTarget(BaseModel):
    kind: Literal["scene_3gs_active_ply"] = "scene_3gs_active_ply"
    scene_id: str


class Scene3gsMasterPlyTarget(BaseModel):
    kind: Literal["scene_3gs_master_ply"] = "scene_3gs_master_ply"
    scene_id: str


class Scene3gsReversePlyTarget(BaseModel):
    kind: Literal["scene_3gs_reverse_ply"] = "scene_3gs_reverse_ply"
    scene_id: str


class Scene3gsPanoPlyTarget(BaseModel):
    kind: Literal["scene_3gs_pano_ply"] = "scene_3gs_pano_ply"
    scene_id: str


class Scene3gsCustomSceneTarget(BaseModel):
    kind: Literal["scene_3gs_custom_scene"] = "scene_3gs_custom_scene"
    scene_id: str


class Scene3gsCollisionGlbTarget(BaseModel):
    kind: Literal["scene_3gs_collision_glb"] = "scene_3gs_collision_glb"
    scene_id: str


class PropRefTarget(BaseModel):
    kind: Literal["prop_ref"] = "prop_ref"
    prop_id: str


class VideoTarget(BaseModel):
    kind: Literal["video"] = "video"
    episode: int
    beat: int


class BeatAudioTarget(BaseModel):
    kind: Literal["beat_audio"] = "beat_audio"
    episode: int
    beat: int


SlotTarget = Union[
    FrameTarget,
    SketchTarget,
    DirectorRenderTarget,
    SelectedBackgroundTarget,
    IdentityTarget,
    IdentityCostumeTarget,
    IdentityPortraitTarget,
    PortraitTarget,
    SceneMasterTarget,
    Scene360Target,
    SceneReverseMasterTarget,
    SceneSpatialLayoutTarget,
    SceneDirectorPano360Target,
    Scene3gsActivePlyTarget,
    Scene3gsMasterPlyTarget,
    Scene3gsReversePlyTarget,
    Scene3gsPanoPlyTarget,
    Scene3gsCustomSceneTarget,
    Scene3gsCollisionGlbTarget,
    PropRefTarget,
    VideoTarget,
    BeatAudioTarget,
]

# Backwards-compatible alias for API route models.
PushTarget = SlotTarget


SCENE_ASSET_KINDS = {
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
GLOBAL_ASSET_KINDS = {
    "identity",
    "identity_costume",
    "identity_portrait",
    "portrait",
    "prop_ref",
    *SCENE_ASSET_KINDS,
}
BEAT_SCOPED_KINDS = {
    "frame",
    "sketch",
    "director_render",
    "selected_background",
    "video",
    "beat_audio",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm", ".flac", ".opus"}
SPLAT_PACKAGE_SUFFIXES = {".ply", ".sog", ".splat", ".ksplat"}
SCENE_PACKAGE_SUFFIXES = {".ply", ".sog", ".splat", ".ksplat"}
GLB_SUFFIXES = {".glb"}


SCENE_3GS_PLY_TARGETS = {
    "scene_3gs_active_ply": ("active", "active.sog"),
    "scene_3gs_master_ply": ("master", "master_sharp.sog"),
    "scene_3gs_reverse_ply": ("reverse", "reverse_sharp.sog"),
    "scene_3gs_pano_ply": ("pano", "pano_depth.sog"),
    "scene_3gs_custom_scene": ("custom", "custom.sog"),
}


def is_global_asset_slot(target: SlotTarget) -> bool:
    return target.kind in GLOBAL_ASSET_KINDS


def is_beat_scoped_slot(target: SlotTarget) -> bool:
    return target.kind in BEAT_SCOPED_KINDS


def slot_target_path(project_dir: Path, target: SlotTarget) -> Path:
    """Return the canonical filesystem path for a slot target."""
    if target.kind == "frame":
        return PathResolver(str(project_dir), target.episode).frame(target.beat)
    if target.kind == "sketch":
        return PathResolver(str(project_dir), target.episode).sketch(target.beat)
    if target.kind == "director_render":
        return PathResolver(str(project_dir), target.episode).director_render(target.beat)
    if target.kind == "selected_background":
        return canonical_beat_selected_background_path(project_dir, target.episode, target.beat)
    if target.kind == "identity":
        return canonical_identity_path(project_dir, target.character, target.identity_id)
    if target.kind == "identity_costume":
        return canonical_identity_costume_path(project_dir, target.character, target.identity_id)
    if target.kind == "identity_portrait":
        return canonical_identity_portrait_path(project_dir, target.character, target.identity_id)
    if target.kind == "portrait":
        return canonical_portrait_path(project_dir, target.character)
    if target.kind == "scene_master":
        return canonical_scene_master_path(project_dir, target.scene_id)
    if target.kind == "scene_360":
        return canonical_scene_360_path(project_dir, target.scene_id)
    if target.kind == "scene_reverse_master":
        return canonical_scene_reverse_master_path(project_dir, target.scene_id)
    if target.kind == "scene_spatial_layout":
        return canonical_scene_spatial_layout_path(project_dir, target.scene_id)
    if target.kind == "scene_director_pano_360":
        from novelvideo.director_world import stage_manifest

        existing = stage_manifest.resolve_pano_path(project_dir, target.scene_id)
        return existing or (stage_manifest.stage_dir(project_dir, target.scene_id) / "pano_360.png")
    if target.kind in SCENE_3GS_PLY_TARGETS:
        from novelvideo.director_world import stage_manifest

        ply_kind, default_name = SCENE_3GS_PLY_TARGETS[target.kind]
        existing = stage_manifest.resolve_ply_path(
            project_dir,
            target.scene_id,
            ply_kind=ply_kind,
        )
        return existing or (stage_manifest.stage_dir(project_dir, target.scene_id) / default_name)
    if target.kind == "scene_3gs_collision_glb":
        from novelvideo.director_world import stage_manifest

        existing = stage_manifest.resolve_collision_glb_path(project_dir, target.scene_id)
        return existing or (
            stage_manifest.stage_dir(project_dir, target.scene_id) / "scene.collision.glb"
        )
    if target.kind == "prop_ref":
        return canonical_prop_reference_path(project_dir, target.prop_id)
    if target.kind == "video":
        return PathResolver(str(project_dir), target.episode).video(target.beat)
    if target.kind == "beat_audio":
        return PathResolver(str(project_dir), target.episode).audio(target.beat)
    raise HTTPException(400, f"unknown slot target kind: {target}")


def validate_source_for_slot(source_path: Path, target: SlotTarget) -> None:
    """Reject obvious media-kind mistakes before writing a canonical slot."""
    suffix = source_path.suffix.lower()
    if target.kind == "video":
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(400, "video slot requires a video source file")
        return
    if target.kind == "beat_audio":
        if suffix not in AUDIO_SUFFIXES:
            raise HTTPException(400, "beat_audio slot requires an audio source file")
        return
    if target.kind in SCENE_3GS_PLY_TARGETS:
        if target.kind == "scene_3gs_custom_scene":
            if suffix not in SCENE_PACKAGE_SUFFIXES:
                raise HTTPException(400, f"{target.kind} slot requires a 3GS package source file")
            return
        if suffix not in SPLAT_PACKAGE_SUFFIXES:
            raise HTTPException(400, f"{target.kind} slot requires a 3GS package source file")
        return
    if target.kind == "scene_3gs_collision_glb":
        if suffix not in GLB_SUFFIXES:
            raise HTTPException(400, "scene_3gs_collision_glb slot requires a GLB source file")
        return
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"{target.kind} slot requires an image source file")


def backup_slot_if_exists(target: Path) -> Optional[Path]:
    if not target.exists():
        return None
    backup_dir = target.parent / "_history"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"{target.name}.{ts}.bak"
    shutil.copy2(target, backup)
    return backup


def slot_asset_key(target: SlotTarget) -> str | None:
    if target.kind == "identity":
        return f"identity:{target.character}:{target.identity_id}"
    if target.kind == "identity_costume":
        return f"identity_costume:{target.character}:{target.identity_id}"
    if target.kind == "identity_portrait":
        return f"identity_portrait:{target.character}:{target.identity_id}"
    if target.kind == "portrait":
        return f"portrait:{target.character}"
    if target.kind == "scene_master":
        return f"scene_master:{target.scene_id}"
    if target.kind == "scene_360":
        return f"scene_360:{target.scene_id}"
    if target.kind in SCENE_ASSET_KINDS:
        return f"{target.kind}:{target.scene_id}"
    if target.kind == "prop_ref":
        return f"prop_ref:{target.prop_id}"
    return None


def sync_slot_after_write(project_dir: Path, target: SlotTarget, target_path: Path) -> None:
    """Update sidecar manifests for canonical slots outside assets/*."""
    if (
        target.kind != "scene_director_pano_360"
        and target.kind not in SCENE_3GS_PLY_TARGETS
        and target.kind != "scene_3gs_collision_glb"
    ):
        return

    from novelvideo.director_world import stage_manifest

    rel_name = target_path.name
    if target.kind == "scene_director_pano_360":
        stage_manifest.update_manifest(
            project_dir,
            target.scene_id,
            source="freezone_commit",
            pano_path=rel_name,
        )
        return
    if target.kind == "scene_3gs_collision_glb":
        stage_manifest.update_manifest(
            project_dir,
            target.scene_id,
            source="freezone_commit",
            collision_glb_path=rel_name,
        )
        return

    ply_kind, _default_name = SCENE_3GS_PLY_TARGETS[target.kind]
    field_by_kind = {
        "active": "ply_path",
        "master": "master_ply_path",
        "reverse": "reverse_ply_path",
        "pano": "pano_ply_path",
        "custom": "custom_scene_path",
    }
    field = field_by_kind[ply_kind]
    updates = {"source": "freezone_commit", field: rel_name}
    if ply_kind == "active":
        updates["ply_path"] = rel_name
    stage_manifest.update_manifest(project_dir, target.scene_id, **updates)


async def compute_slot_impact(username: str, project: str, target: SlotTarget) -> list[dict]:
    """Return beats affected by committing a global asset slot."""
    if not is_global_asset_slot(target):
        return []
    store = await make_sqlite_store(username, project)
    try:
        beats = await store.list_visual_beats()
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    impacted: list[dict] = []
    for beat in beats:
        visual = str(getattr(beat, "visual_description", "") or "")
        try:
            detected = json.loads(getattr(beat, "detected_identities_json", "[]") or "[]")
        except Exception:
            detected = []
        detected = [str(x) for x in detected if x]
        hit = False
        if target.kind in {"identity", "identity_costume", "identity_portrait"}:
            hit = target.identity_id in visual or target.identity_id in detected
        elif target.kind == "portrait":
            hit = (
                f"{{{{{target.character}_" in visual
                or f"{{{{{target.character}}}}}" in visual
                or any(
                    x == target.character or x.startswith(f"{target.character}_") for x in detected
                )
            )
        elif target.kind in SCENE_ASSET_KINDS:
            hit = getattr(beat, "scene_id", "") == target.scene_id or target.scene_id in visual
        elif target.kind == "prop_ref":
            try:
                detected_props = json.loads(getattr(beat, "detected_props_json", "[]") or "[]")
            except Exception:
                detected_props = []
            detected_props = [str(x) for x in detected_props if x]
            hit = target.prop_id in visual or target.prop_id in detected_props
        if not hit:
            continue
        impacted.append(
            {
                "episode": int(getattr(beat, "episode_number", 0)),
                "beat": int(getattr(beat, "beat_number", 0)),
                "visual_description": visual,
            }
        )
    impacted.sort(key=lambda x: (x["episode"], x["beat"]))
    return impacted


def stale_marks_path(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "stale_marks.json"


def record_slot_stale_marks(
    project_dir: Path,
    *,
    target: SlotTarget,
    impacted: list[dict],
    source_url: str,
) -> int:
    if not impacted:
        return 0
    path = stale_marks_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        existing = {}
    marks = existing.get("marks") if isinstance(existing, dict) else None
    if not isinstance(marks, list):
        marks = []
    asset_key = slot_asset_key(target) or target.kind
    ts = datetime.now().isoformat()
    before = {
        (m.get("asset_key"), m.get("episode"), m.get("beat")) for m in marks if isinstance(m, dict)
    }
    added = 0
    for item in impacted:
        key = (asset_key, item.get("episode"), item.get("beat"))
        payload = {
            "asset_key": asset_key,
            "target": target.model_dump(),
            "episode": item.get("episode"),
            "beat": item.get("beat"),
            "reason": f"{asset_key} changed from Freezone",
            "source_url": source_url,
            "created_at": ts,
        }
        if key in before:
            for idx, mark in enumerate(marks):
                if (
                    isinstance(mark, dict)
                    and mark.get("asset_key") == key[0]
                    and mark.get("episode") == key[1]
                    and mark.get("beat") == key[2]
                ):
                    marks[idx] = payload
                    break
        else:
            marks.append(payload)
            added += 1
    path.write_text(
        json.dumps({"updated_at": ts, "marks": marks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return added
