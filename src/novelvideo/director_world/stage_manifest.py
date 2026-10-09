"""3GS stage asset manifest.

`output/<user>/<project>/director_worlds/<scene>/v1/stage_manifest.json`
records which 3GS assets exist for a scene. It's the shared truth between:

- scene_workspace UI (writes after each build step)
- DirectorWorldService.make_3gs_editor_url (reads to gate the 3GS entry)
- paths.scene_gaussian_splat_ply_path (reads for asset discovery)

Schema:
    {
      "schema_version": "stage_manifest_v1",
      "scene_id": "<original_scene_id>",
      "version": "v1",
      "source": "custom_scene" | "uploaded_360" | "uploaded_master" | "text_to_360"
                | "single_face_master" | "single_face_reverse",
      "pano_path": "pano_360.png" | null,
      "ply_path": "custom.sog" | "custom.ply" | "custom.splat" | "custom.ksplat"
                  | "pano_depth.sog" | "pano_sharp_merged.sog"
                  | "master_sharp.sog" | "reverse_sharp.sog" | null,
      "pano_ply_path": "pano_depth.sog" | "pano_sharp_merged.sog" | null,
      "pano_depth_ply_path": "pano_depth.sog" | null,
      "master_ply_path": "master_sharp.sog" | null,
      "reverse_ply_path": "reverse_sharp.sog" | null,
      "custom_scene_path": "custom.ply" | "custom.sog" | "custom.splat"
                           | "custom.ksplat" | null,
      "collision_glb_path": "scene.collision.glb" | null,
      "voxel_json_path": "scene.voxel.json" | null,
      "created_at": "<isoformat utc>",
      "updated_at": "<isoformat utc>",
      "pano_sharp_args": {...} | null,
      "pano_correction": {
        "front_yaw_deg": 0.0,
        "sphere_correction_deg": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
      } | null,
      "single_face_sharp_args": {...} | null,
      "master_sharp_args": {...} | null,
      "reverse_sharp_args": {...} | null,
      "scene_360_args": {...} | null,
      "splat_transform_args": {...} | null
    }
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .paths import safe_name

SCHEMA_VERSION = "stage_manifest_v1"


def _prefer_sog_sidecar(path: Path) -> Path | None:
    """Return the preferred loadable splat package for a manifest path.

    Historical manifest fields are still named `*_ply_path`, but the browser
    should load compressed SOG whenever it exists. A manifest value that names
    `master_sharp.ply` therefore resolves to `master_sharp.sog` first.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".ply":
        sog_path = path.with_suffix(".sog")
        if sog_path.exists():
            return sog_path
    if path.exists():
        return path
    return None


def stage_dir(project_dir: Path, scene_id: str, version: str = "v1") -> Path:
    return Path(project_dir) / "director_worlds" / safe_name(scene_id) / version


def manifest_path(project_dir: Path, scene_id: str, version: str = "v1") -> Path:
    return stage_dir(project_dir, scene_id, version) / "stage_manifest.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(
    project_dir: Path, scene_id: str, version: str = "v1"
) -> dict[str, Any] | None:
    path = manifest_path(project_dir, scene_id, version)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_manifest(
    project_dir: Path,
    scene_id: str,
    payload: dict[str, Any],
    *,
    version: str = "v1",
) -> Path:
    """Atomic write: tmp + rename."""
    path = manifest_path(project_dir, scene_id, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.setdefault("schema_version", SCHEMA_VERSION)
    body.setdefault("scene_id", scene_id)
    body.setdefault("version", version)
    body.setdefault("created_at", _utcnow_iso())
    body["updated_at"] = _utcnow_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def update_manifest(
    project_dir: Path,
    scene_id: str,
    *,
    version: str = "v1",
    clear_fields: Iterable[str] | None = None,
    **fields: Any,
) -> Path:
    """Merge fields into existing manifest (or create new). Atomic."""
    existing = load_manifest(project_dir, scene_id, version) or {}
    for field in clear_fields or ():
        existing[field] = None
    existing.update({k: v for k, v in fields.items() if v is not None})
    return save_manifest(project_dir, scene_id, existing, version=version)


def has_3gs_assets(
    project_dir: Path,
    scene_id: str,
    *,
    require_collision: bool = True,
    version: str = "v1",
) -> bool:
    """Return True if PLY exists (and optionally collision GLB)."""
    mani = load_manifest(project_dir, scene_id, version)
    if not mani:
        return False
    if resolve_ply_path(project_dir, scene_id, version=version) is None:
        return False
    if require_collision:
        base = stage_dir(project_dir, scene_id, version)
        glb_name = mani.get("collision_glb_path")
        if not glb_name or not (base / glb_name).exists():
            return False
    return True


def resolve_ply_path(
    project_dir: Path,
    scene_id: str,
    version: str = "v1",
    *,
    ply_kind: str = "active",
) -> Path | None:
    mani = load_manifest(project_dir, scene_id, version)
    if not mani:
        return None
    kind_to_field = {
        "active": "ply_path",
        "pano": "pano_ply_path",
        "360": "pano_ply_path",
        "pano_depth": "pano_depth_ply_path",
        "depth": "pano_depth_ply_path",
        "depth_debug": "pano_depth_ply_path",
        "pano_depth_debug": "pano_depth_ply_path",
        "debug": "pano_depth_ply_path",
        "master": "master_ply_path",
        "reverse": "reverse_ply_path",
        "custom": "custom_scene_path",
    }
    field = kind_to_field.get(str(ply_kind or "active").strip().lower())
    if not field:
        return None
    base = stage_dir(project_dir, scene_id, version)
    name = mani.get(field)
    if not name and field == "ply_path":
        # `ply_path` is the default entry used by the 3GS viewer. Older scene
        # packs may have a concrete pano/master/reverse PLY on disk while the
        # active field is null, so fall back to the available concrete source.
        for fallback_field in (
            "pano_ply_path",
            "pano_depth_ply_path",
            "master_ply_path",
            "reverse_ply_path",
            "custom_scene_path",
        ):
            fallback_name = mani.get(fallback_field)
            if not fallback_name:
                continue
            candidate = _prefer_sog_sidecar(base / fallback_name)
            if candidate is not None:
                return candidate
        for fallback_name in (
            "pano_sharp_merged.sog",
            "pano_depth.sog",
            "master_sharp.sog",
            "reverse_sharp.sog",
            "pano_sharp_merged.ply",
            "pano_depth.ply",
            "master_sharp.ply",
            "reverse_sharp.ply",
        ):
            candidate = _prefer_sog_sidecar(base / fallback_name)
            if candidate is not None:
                return candidate
        return None
    if not name and field == "pano_ply_path":
        if mani.get("source") in {"uploaded_360", "uploaded_master", "text_to_360"}:
            name = mani.get("ply_path")
    if not name and field == "custom_scene_path":
        if mani.get("source") == "custom_scene":
            name = mani.get("ply_path")
    if not name:
        fallback_by_field = {
            "pano_ply_path": (
                "pano_depth.sog",
                "pano_sharp_merged.sog",
                "pano_depth.ply",
            ),
            "pano_depth_ply_path": ("pano_depth.sog", "pano_depth.ply"),
            "master_ply_path": ("master_sharp.sog", "master_sharp.ply"),
            "reverse_ply_path": ("reverse_sharp.sog", "reverse_sharp.ply"),
        }
        for fallback_name in fallback_by_field.get(field, ()):
            fallback = _prefer_sog_sidecar(base / fallback_name)
            if fallback is not None:
                return fallback
    if not name:
        return None
    return _prefer_sog_sidecar(base / name)


def resolve_collision_glb_path(
    project_dir: Path, scene_id: str, version: str = "v1"
) -> Path | None:
    mani = load_manifest(project_dir, scene_id, version)
    if not mani:
        return None
    name = mani.get("collision_glb_path")
    if not name:
        return None
    candidate = stage_dir(project_dir, scene_id, version) / name
    return candidate if candidate.exists() else None


def resolve_pano_path(
    project_dir: Path, scene_id: str, version: str = "v1"
) -> Path | None:
    mani = load_manifest(project_dir, scene_id, version)
    if not mani:
        return None
    name = mani.get("pano_path")
    if not name:
        return None
    candidate = stage_dir(project_dir, scene_id, version) / name
    return candidate if candidate.exists() else None


def get_pano_correction(
    project_dir: Path,
    scene_id: str,
    version: str = "v1",
) -> dict[str, Any]:
    mani = load_manifest(project_dir, scene_id, version) or {}
    value = mani.get("pano_correction")
    return value if isinstance(value, dict) else {}


def set_pano_correction(
    project_dir: Path,
    scene_id: str,
    correction: dict[str, Any],
    version: str = "v1",
) -> Path:
    sphere = (
        correction.get("sphere_correction_deg") if isinstance(correction, dict) else {}
    )
    sphere = sphere if isinstance(sphere, dict) else {}
    payload = {
        "front_yaw_deg": float(correction.get("front_yaw_deg") or 0),
        "sphere_correction_deg": {
            "roll": float(sphere.get("roll") or 0),
            "pitch": float(sphere.get("pitch") or 0),
            "yaw": float(sphere.get("yaw") or 0),
        },
    }
    return update_manifest(
        project_dir, scene_id, pano_correction=payload, version=version
    )


def _canonical_scene_source_id(source_id: str) -> str:
    source_id = str(source_id or "").strip()
    if not source_id.startswith("legacy:"):
        return source_id
    parts = source_id.split(":", 3)
    if len(parts) != 4:
        return source_id
    url = parts[3].split("#", 1)[0].split("?", 1)[0]
    return ":".join([parts[0], parts[1], parts[2], url])


def _snapshot_for_source_id(snapshot: dict[str, Any], source_id: str) -> dict[str, Any]:
    next_snapshot = dict(snapshot)
    world = next_snapshot.get("world")
    world = dict(world) if isinstance(world, dict) else {}
    world["activeSourceId"] = source_id
    next_snapshot["world"] = world
    return next_snapshot


def _source_for_source_id(
    source: dict[str, Any] | None, source_id: str
) -> dict[str, Any]:
    source_payload = dict(source) if isinstance(source, dict) else {}
    if source_payload.get("id"):
        source_payload["id"] = source_id
    return source_payload


def _snapshot_saved_at(snapshot: dict[str, Any]) -> float:
    value = snapshot.get("savedAt")
    if isinstance(value, (int, float)):
        return float(value)
    return -1


def _canonical_scene_map(scenes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    exact_source_ids: dict[str, bool] = {}
    for source_id, snapshot in scenes.items():
        if not isinstance(snapshot, dict):
            continue
        raw_source_id = str(source_id)
        canonical_id = _canonical_scene_source_id(raw_source_id)
        existing = canonical.get(canonical_id)
        incoming_is_exact = raw_source_id == canonical_id
        if existing is not None:
            existing_saved_at = _snapshot_saved_at(existing)
            incoming_saved_at = _snapshot_saved_at(snapshot)
            if existing_saved_at > incoming_saved_at:
                continue
            if existing_saved_at == incoming_saved_at:
                existing_is_exact = exact_source_ids.get(canonical_id, False)
                if existing_is_exact and not incoming_is_exact:
                    continue
        canonical[canonical_id] = _snapshot_for_source_id(snapshot, canonical_id)
        exact_source_ids[canonical_id] = incoming_is_exact
    return canonical


def get_scene_director_world(
    project_dir: Path,
    scene_id: str,
    version: str = "v1",
) -> dict[str, Any]:
    mani = load_manifest(project_dir, scene_id, version) or {}
    scenes = mani.get("scenes_by_source_id")
    scenes = scenes if isinstance(scenes, dict) else {}
    scenes_by_source_id = _canonical_scene_map(scenes)
    active_source_id = _canonical_scene_source_id(
        str(mani.get("active_source_id") or "").strip()
    )
    scene = mani.get("scene")
    scene = (
        scene
        if isinstance(scene, dict)
        else (scenes_by_source_id.get(active_source_id) if active_source_id else None)
    )
    if isinstance(scene, dict) and active_source_id:
        scene = _snapshot_for_source_id(scene, active_source_id)
    active_source = mani.get("active_source")
    return {
        "active_source_id": active_source_id,
        "active_source": active_source if isinstance(active_source, dict) else {},
        "scene": scene if isinstance(scene, dict) else None,
        "scenes_by_source_id": scenes_by_source_id,
    }


def save_scene_director_world(
    project_dir: Path,
    scene_id: str,
    *,
    active_source_id: str,
    snapshot: dict[str, Any],
    active_source: dict[str, Any] | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    source_id = _canonical_scene_source_id(str(active_source_id or "").strip())
    if not source_id:
        world = snapshot.get("world") if isinstance(snapshot, dict) else {}
        source_id = _canonical_scene_source_id(
            str(
                (world if isinstance(world, dict) else {}).get("activeSourceId") or ""
            ).strip()
        )
    if not source_id:
        raise ValueError("active_source_id is required")
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    snapshot = _snapshot_for_source_id(snapshot, source_id)
    active_source_payload = _source_for_source_id(active_source, source_id)

    existing = get_scene_director_world(project_dir, scene_id, version)
    scenes_by_source_id = dict(existing["scenes_by_source_id"])
    scenes_by_source_id[source_id] = snapshot
    update_manifest(
        project_dir,
        scene_id,
        active_source_id=source_id,
        active_source=active_source_payload,
        scene=snapshot,
        scenes_by_source_id=scenes_by_source_id,
        version=version,
    )
    return {
        "active_source_id": source_id,
        "active_source": active_source_payload,
        "scene": snapshot,
        "scenes_by_source_id": scenes_by_source_id,
    }


def save_scene_director_world_source(
    project_dir: Path,
    scene_id: str,
    *,
    source_id: str,
    snapshot: dict[str, Any],
    source: dict[str, Any] | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    """Save one source's Director World state without changing other sources."""
    source_id = _canonical_scene_source_id(str(source_id or "").strip())
    if not source_id:
        raise ValueError("source_id is required")
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    snapshot = _snapshot_for_source_id(snapshot, source_id)

    existing = get_scene_director_world(project_dir, scene_id, version)
    active_source_id = str(existing["active_source_id"] or "").strip()
    scenes_by_source_id = dict(existing["scenes_by_source_id"])
    scenes_by_source_id[source_id] = snapshot
    next_active = active_source_id
    source_payload = _source_for_source_id(source, source_id)
    if next_active == source_id:
        next_scene = snapshot
        next_active_source = source_payload
    else:
        next_scene = existing["scene"]
        next_active_source = existing["active_source"]
    update_manifest(
        project_dir,
        scene_id,
        active_source_id=next_active,
        active_source=next_active_source,
        scene=next_scene,
        scenes_by_source_id=scenes_by_source_id,
        version=version,
    )
    return {
        "active_source_id": next_active,
        "active_source": next_active_source,
        "scene": next_scene,
        "scenes_by_source_id": scenes_by_source_id,
    }


def clear_scene_director_world(
    project_dir: Path,
    scene_id: str,
    *,
    active_source_id: str | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    existing = get_scene_director_world(project_dir, scene_id, version)
    scenes_by_source_id = dict(existing["scenes_by_source_id"])
    source_id = _canonical_scene_source_id(
        str(active_source_id or existing["active_source_id"] or "").strip()
    )
    if source_id:
        scenes_by_source_id.pop(source_id, None)
    else:
        scenes_by_source_id = {}
    next_active = next(iter(scenes_by_source_id), "")
    next_scene = scenes_by_source_id.get(next_active) if next_active else None
    active_source = existing["active_source"] if next_active else {}
    update_manifest(
        project_dir,
        scene_id,
        active_source_id=next_active,
        active_source=active_source,
        scene=next_scene or {},
        scenes_by_source_id=scenes_by_source_id,
        version=version,
    )
    return {
        "active_source_id": next_active,
        "active_source": active_source,
        "scene": next_scene,
        "scenes_by_source_id": scenes_by_source_id,
    }
