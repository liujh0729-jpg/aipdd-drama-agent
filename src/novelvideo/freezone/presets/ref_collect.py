"""预设引用收集（角色 / 道具 / 场景 / 文件引用与标记工具）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from novelvideo.models import NO_CHARACTER_MARKER, NO_PROP_MARKER
from novelvideo.utils.path_resolver import (
    canonical_beat_director_env_only_path,
    canonical_identity_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
)

from .refs import PresetRef, _make_url, _rel
from .aspect import _media_type_for_path, _aspect_ratio_for_ref


def _add_file_ref(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    kind: str,
    role: str,
    label: str,
    rel_path: str,
    required: bool = False,
    placeholder_aspect_ratio: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    path = project_dir / rel_path
    exists = path.exists()
    if not exists and not required:
        return
    refs.append(
        PresetRef(
            kind=kind,
            role=role,
            label=label,
            rel_path=rel_path,
            url=_make_url(project_id, project_dir, rel_path) if exists else None,
            exists=exists,
            media_type=_media_type_for_path(path),
            aspect_ratio=(
                _aspect_ratio_for_ref(path)
                if exists
                else (placeholder_aspect_ratio or _aspect_ratio_for_ref(path))
            ),
            meta=meta or {},
        )
    )


def _latest_matching(directory: Path, patterns: Iterable[str]) -> list[Path]:
    if not directory.exists():
        return []
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for p in directory.glob(pattern):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _normalize_scene_name(scene_ref: Any) -> str:
    if isinstance(scene_ref, dict):
        for key in ("scene_id", "name", "scene_name", "id", "title"):
            value = str(scene_ref.get(key) or "").strip()
            if value:
                return value
    if scene_ref:
        return str(scene_ref).strip()
    return ""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


_MARKER_RE = re.compile(r"\{\{([^{}]+)\}\}|\[\[([^\[\]]+)\]\]")


def _visual_markers(text: str) -> tuple[list[str], list[str]]:
    identities: list[str] = []
    props: list[str] = []
    for match in _MARKER_RE.finditer(text or ""):
        identity = (match.group(1) or "").strip()
        prop = (match.group(2) or "").strip()
        if identity:
            identities.append(identity)
        if prop:
            props.append(prop)
    return identities, props


def _identity_character(identity_id: str, known_characters: Iterable[str]) -> str:
    for name in sorted((n for n in known_characters if n), key=len, reverse=True):
        if identity_id == name or identity_id.startswith(f"{name}_"):
            return name
    if "_" in identity_id:
        return identity_id.split("_", 1)[0]
    return identity_id


def _identity_name(identity_id: str, character: str) -> str:
    prefix = f"{character}_"
    return identity_id[len(prefix) :] if identity_id.startswith(prefix) else identity_id


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def _prop_id_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("prop_id") or item.get("base_id") or item.get("id") or "").strip()
    return str(item or "").strip()


def _identity_id_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("identity_id") or item.get("id") or item.get("name") or "").strip()
    return str(item or "").strip()


def _real_identity_ids(values: Iterable[str]) -> list[str]:
    return [value for value in values if value and value != NO_CHARACTER_MARKER]


def _real_prop_ids(values: Iterable[str]) -> list[str]:
    return [value for value in values if value and value != NO_PROP_MARKER]


def _add_character_refs(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    character: str,
    identity_id: str | None,
) -> None:
    if not character:
        return
    if identity_id:
        _add_file_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            kind="identity",
            role="character_identity",
            label=identity_id,
            rel_path=_rel(
                project_dir,
                canonical_identity_path(
                    project_dir,
                    character,
                    _identity_name(identity_id, character),
                ),
            ),
            required=True,
            meta={"character": character, "identity_id": identity_id},
        )
    _add_file_ref(
        refs,
        project_id=project_id,
        username=username,
        project=project,
        project_dir=project_dir,
        kind="identity",
        role="character_portrait",
        label=f"{character} portrait",
        rel_path=_rel(project_dir, canonical_portrait_path(project_dir, character)),
        meta={"character": character},
    )


def _add_character_identity_ref(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    character: str,
    identity_id: str,
) -> None:
    if not character or not identity_id:
        return
    _add_file_ref(
        refs,
        project_id=project_id,
        username=username,
        project=project,
        project_dir=project_dir,
        kind="identity",
        role="character_identity",
        label=identity_id,
        rel_path=_rel(
            project_dir,
            canonical_identity_path(
                project_dir,
                character,
                _identity_name(identity_id, character),
            ),
        ),
        required=True,
        meta={"character": character, "identity_id": identity_id},
    )


def _add_mainline_identity_ref(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    character: str,
    identity_id: str,
    include_portrait_fallback: bool = True,
) -> None:
    """Add the single identity image consumed by EP/Beat mainline canvases.

    Asset canvases may expose portrait/identity as separate production
    steps. EP/Beat canvases only consume the selected identity concept; if the
    canonical identity file is missing, a portrait file can visually stand in
    for that identity without changing the semantic role.

    `include_portrait_fallback`: when False, no fallback to portrait /
    reference images — if the canonical identity image is missing, no node is
    emitted. EP-scope canvas uses this to avoid surfacing portrait nodes for
    identities that haven't been generated yet (users found portrait stand-ins
    confusing at the EP scope). Beat workbench keeps the fallback on so the
    workflow always has *some* image to anchor against.
    """
    if not character or not identity_id:
        return
    canonical_path = canonical_identity_path(
        project_dir,
        character,
        _identity_name(identity_id, character),
    )
    candidates: list[tuple[Path, str]] = [(canonical_path, "character_identity")]
    if include_portrait_fallback:
        candidates.append((canonical_portrait_path(project_dir, character), "character_portrait"))
    for path, source_role in candidates:
        if not path.exists():
            continue
        _add_file_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            kind="identity",
            role="character_identity",
            label=identity_id,
            rel_path=_rel(project_dir, path),
            meta={
                "character": character,
                "identity_id": identity_id,
                "source_role": source_role,
            },
        )
        return


def _add_prop_refs(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    prop_id: str,
    meta: dict[str, Any],
) -> None:
    if not prop_id:
        return
    canonical = canonical_prop_reference_path(project_dir, prop_id)
    _add_file_ref(
        refs,
        project_id=project_id,
        username=username,
        project=project,
        project_dir=project_dir,
        kind="prop",
        role="prop_reference",
        label=prop_id,
        rel_path=_rel(project_dir, canonical),
        required=True,
        placeholder_aspect_ratio="1:1",
        meta=meta,
    )


def _add_selected_background_ref(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    episode: int,
    beat: int,
    scene_id: str = "",
) -> None:
    ep_dir = f"ep{episode:03d}"
    beat_dir = f"beat_{beat:02d}"
    _add_file_ref(
        refs,
        project_id=project_id,
        username=username,
        project=project,
        project_dir=project_dir,
        kind="director",
        role="selected_background",
        label=f"当前背景 · Beat {beat}",
        rel_path=f"director_control_frames/{ep_dir}/{beat_dir}/selected_background.png",
        meta={"episode": episode, "beat": beat, "scene_id": scene_id},
    )


def _add_scene_refs(
    refs: list[PresetRef],
    *,
    project_id: str,
    username: str,
    project: str,
    project_dir: Path,
    scene_name: str,
    scene_info: dict[str, Any] | None = None,
    include_derived_assets: bool = True,
) -> None:
    if not scene_name:
        return
    scene_info = scene_info or {}
    effective_environment_prompt = str(
        scene_info.get("effective_environment_prompt") or scene_info.get("environment_prompt") or ""
    ).strip()
    scene_meta = {
        "scene": scene_name,
        "environment_prompt": effective_environment_prompt,
        "raw_environment_prompt": str(scene_info.get("environment_prompt") or "").strip(),
        "variant_prompt": str(scene_info.get("variant_prompt") or "").strip(),
        "base_scene_id": str(scene_info.get("base_scene_id") or "").strip(),
        "base_master_url": str(scene_info.get("base_master_url") or "").strip(),
        "base_master_rel_path": str(scene_info.get("base_master_rel_path") or "").strip(),
        "base_environment_prompt": str(scene_info.get("base_environment_prompt") or "").strip(),
        "base_description": str(scene_info.get("base_description") or "").strip(),
        "base_scene_type": str(scene_info.get("base_scene_type") or "").strip(),
        "variant_id": str(scene_info.get("variant_id") or "").strip(),
        "time_of_day": str(scene_info.get("time_of_day") or "").strip(),
        "description": str(scene_info.get("description") or "").strip(),
        "scene_type": str(scene_info.get("scene_type") or "").strip(),
        "style_name": str(scene_info.get("style_name") or "").strip(),
        "style_prompt": str(scene_info.get("style_prompt") or "").strip(),
        "avoid_instructions": str(scene_info.get("avoid_instructions") or "").strip(),
    }
    director_pano_path: Path | None = None
    director_ply_paths: list[tuple[Path, str, str, str]] = []
    if include_derived_assets:
        try:
            from novelvideo.director_world import stage_manifest

            director_pano_path = stage_manifest.resolve_pano_path(
                project_dir,
                scene_name,
            ) or (stage_manifest.stage_dir(project_dir, scene_name) / "pano_360.png")
            seen_ply_paths: set[str] = set()
            for ply_kind, role, label in [
                ("master", "scene_3gs_master_ply", f"{scene_name} 3D 世界（正面）"),
                ("reverse", "scene_3gs_reverse_ply", f"{scene_name} 3D 世界（背面）"),
                ("pano", "scene_3gs_pano_ply", f"{scene_name} 3D 世界（360）"),
            ]:
                path = stage_manifest.resolve_ply_path(project_dir, scene_name, ply_kind=ply_kind)
                if path is None:
                    continue
                rel = _rel(project_dir, path)
                if rel in seen_ply_paths:
                    continue
                seen_ply_paths.add(rel)
                director_ply_paths.append((path, role, label, ply_kind))
        except Exception:
            director_pano_path = None
            director_ply_paths = []
    # scene_360 is the direct panorama workflow/slot. Assets > Scenes uses
    # scene_director_pano_360 as the canonical director-world pano, so the
    # preset intentionally does not emit scene_360 as a mainline scene asset.
    # The direct-360 workflow itself remains available through freezone.scene_360
    # and scene_360_candidate outputs.
    for path, role, label, required, placeholder_aspect_ratio in [
        (
            canonical_scene_master_path(project_dir, scene_name),
            "scene_master",
            f"{scene_name} master",
            True,
            "16:9",
        ),
        (
            canonical_scene_reverse_master_path(project_dir, scene_name),
            "scene_reverse_master",
            f"{scene_name} reverse master",
            True,
            "16:9",
        ),
        (
            director_pano_path,
            "scene_director_pano_360",
            f"{scene_name} director pano 360",
            True,
            "2:1",
        ),
    ]:
        if path is None:
            continue
        if not include_derived_assets and role == "scene_director_pano_360":
            continue
        _add_file_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            kind="scene",
            role=role,
            label=label,
            rel_path=_rel(project_dir, path),
            required=required,
            placeholder_aspect_ratio=placeholder_aspect_ratio,
            meta={**scene_meta, "scene_id": scene_name},
        )
    for path, role, label, ply_kind in director_ply_paths:
        _add_file_ref(
            refs,
            project_id=project_id,
            username=username,
            project=project,
            project_dir=project_dir,
            kind="scene",
            role=role,
            label=label,
            rel_path=_rel(project_dir, path),
            meta={**scene_meta, "scene_id": scene_name, "ply_kind": ply_kind},
        )


def _beat_scene_source_urls(context: dict[str, Any]) -> dict[str, Any]:
    refs = context.get("refs") if isinstance(context.get("refs"), list) else []
    by_role: dict[str, dict[str, Any]] = {}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        role = str(ref.get("role") or "")
        if role:
            by_role[role] = ref
    scene_ref = (context.get("beat_data") or {}).get("scene_ref")
    scene_id = _normalize_scene_name(scene_ref)
    project_id = str(context.get("project_id") or "")

    def _maybe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    episode = _maybe_int(context.get("episode"))
    beat = _maybe_int(context.get("beat"))
    project_dir_raw = context.get("project_dir")
    project_dir = (
        Path(project_dir_raw) if isinstance(project_dir_raw, str) and project_dir_raw else None
    )

    def url_for(role: str) -> str | None:
        ref = by_role.get(role) or {}
        url = ref.get("url")
        return str(url).strip() if isinstance(url, str) and url.strip() else None

    def url_for_path(path: Path | None) -> str | None:
        if not path or not project_dir or not project_id or not path.exists():
            return None
        return _make_url(project_id, project_dir, _rel(project_dir, path))

    direct_master_url = direct_reverse_url = direct_pano_url = direct_env_only_url = None
    direct_has_3gs = False
    if project_dir and scene_id:
        direct_master_url = url_for_path(canonical_scene_master_path(project_dir, scene_id))
        direct_reverse_url = url_for_path(
            canonical_scene_reverse_master_path(project_dir, scene_id)
        )
        try:
            from novelvideo.director_world import stage_manifest

            direct_pano_url = url_for_path(stage_manifest.resolve_pano_path(project_dir, scene_id))
            direct_has_3gs = any(
                stage_manifest.resolve_ply_path(project_dir, scene_id, ply_kind=ply_kind)
                for ply_kind in ("master", "reverse", "pano")
            )
        except Exception:
            direct_pano_url = None
            direct_has_3gs = False
    if project_dir and episode is not None and beat is not None:
        direct_env_only_url = url_for_path(
            canonical_beat_director_env_only_path(project_dir, episode, beat)
        )

    return {
        "scene_id": scene_id,
        "master_url": direct_master_url or url_for("scene_master"),
        "reverse_url": direct_reverse_url or url_for("scene_reverse_master"),
        "director_env_only_url": direct_env_only_url,
        "pano_360_url": direct_pano_url or url_for("scene_director_pano_360"),
        "has_3gs": direct_has_3gs
        or any(
            role in by_role
            for role in (
                "scene_3gs_master_ply",
                "scene_3gs_reverse_ply",
                "scene_3gs_pano_ply",
            )
        ),
    }
