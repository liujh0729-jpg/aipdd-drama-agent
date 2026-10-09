"""scenes.payloads 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from novelvideo.api.asset_metadata import newest_updated_at
from novelvideo.api.asset_metadata import tree_updated_at
from novelvideo.api.routes import scenes as _scenes
from novelvideo.director_world import stage_manifest
from novelvideo.models import NovelScene
from novelvideo.models import build_scene_effective_prompt
from novelvideo.project_context import ProjectContext
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.derived_scenes import compose_derived_scene_name
from novelvideo.utils.path_resolver import canonical_scene_master_path
from novelvideo.utils.path_resolver import compute_scene_master_path
from novelvideo.utils.path_resolver import compute_scene_reverse_master_path
from novelvideo.utils.static_urls import project_static_url

from ._common import _SCENE_TIME_TOKENS



def _scene_360_description(scene: NovelScene) -> str:
    environment_prompt = str(
        scene.environment_prompt or scene.description or scene.name
    ).strip()
    return "\n".join(
        [
            f"场景名称：{scene.name}",
            f"场景类型：{scene.scene_type}",
            "环境描述是完整场景空间合同：应说明正面、背面、左侧、右侧、天花/天空、地面和固定物件关系。",
            "master 图代表正面半区：正面中心 + 左侧一半 + 右侧一半，并提供视觉风格锚点。",
            "reverse 图应代表背面半区：背面中心 + 左侧另一半 + 右侧另一半。",
            "360 需要把 environment_prompt 的四向空间合同展开成完整连续空间。",
            "如果某些方向没有明确写出，请基于场景类型和 master 视觉风格合理补全，"
            "但不要把正面物件机械复制到每个方向。",
            "环境描述：",
            environment_prompt,
        ]
    )


def _stage_file_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    path: Path | None,
) -> dict[str, Any]:
    if path is None:
        return {"ready": False, "path": "", "url": "", "size_bytes": 0, "size_mb": 0.0}
    size_bytes = path.stat().st_size if path.exists() else 0
    try:
        display_path = path.relative_to(project_dir).as_posix()
    except ValueError:
        display_path = path.name
    return {
        "ready": path.exists(),
        "path": display_path,
        "url": _scenes._asset_url(ctx, project_dir, path),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 1) if size_bytes else 0.0,
    }


def _stage_3gs_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_name: str,
) -> dict[str, Any]:
    stage_dir = stage_manifest.stage_dir(project_dir, scene_name)
    manifest = stage_manifest.load_manifest(project_dir, scene_name) or {}
    saved_world = stage_manifest.get_scene_director_world(project_dir, scene_name)
    saved_source_id = str(saved_world.get("active_source_id") or "").strip()
    saved_source = saved_world.get("active_source")
    saved_source = saved_source if isinstance(saved_source, dict) else {}
    kind_paths = {
        kind: stage_manifest.resolve_ply_path(project_dir, scene_name, ply_kind=kind)
        for kind in ("custom", "master", "reverse", "pano")
    }
    active_path = stage_manifest.resolve_ply_path(project_dir, scene_name)
    active_source = ""
    if saved_source_id:
        saved_source_type = str(saved_source.get("source_type") or "").strip()
        saved_kind = str(
            saved_source.get("source_kind")
            or saved_source.get("kind")
            or saved_source.get("label")
            or saved_source_id
        ).lower()
        if (
            saved_source_type == "pano360"
            or "360" in saved_kind
            or "pano" in saved_kind
        ):
            active_path = stage_manifest.resolve_pano_path(project_dir, scene_name)
            active_source = "360"
        elif "master" in saved_kind:
            active_path = kind_paths.get("master")
            active_source = "master"
        elif "reverse" in saved_kind:
            active_path = kind_paths.get("reverse")
            active_source = "reverse"
        elif "custom" in saved_kind:
            active_path = kind_paths.get("custom")
            active_source = "custom"
    if active_path is not None and not active_source:
        for kind, label in (
            ("custom", "custom"),
            ("pano", "360"),
            ("master", "master"),
            ("reverse", "reverse"),
        ):
            kind_path = kind_paths.get(kind)
            if kind_path is not None and kind_path.resolve() == active_path.resolve():
                active_source = label
                break

    try:
        stage_dir_display = stage_dir.relative_to(project_dir).as_posix()
    except ValueError:
        stage_dir_display = stage_dir.name

    return {
        "stage_dir": stage_dir_display,
        "manifest_ready": bool(manifest),
        "source": str(manifest.get("source") or ""),
        "active_source": active_source,
        "active": _stage_file_payload(
            ctx=ctx,
            project_dir=project_dir,
            path=active_path,
        ),
        "custom": _stage_file_payload(
            ctx=ctx,
            project_dir=project_dir,
            path=kind_paths["custom"],
        ),
        "master": _stage_file_payload(
            ctx=ctx,
            project_dir=project_dir,
            path=kind_paths["master"],
        ),
        "reverse": _stage_file_payload(
            ctx=ctx,
            project_dir=project_dir,
            path=kind_paths["reverse"],
        ),
        "pano": _stage_file_payload(
            ctx=ctx,
            project_dir=project_dir,
            path=kind_paths["pano"],
        ),
    }


def _scene_payload(
    scene: NovelScene,
    *,
    ctx: ProjectContext,
    project_dir: Path,
    derived_from_scene: str = "",
    base_scene: NovelScene | None = None,
) -> dict[str, Any]:
    base_scene_id = str(
        getattr(scene, "base_scene_id", "") or derived_from_scene or ""
    ).strip()
    variant_id = str(getattr(scene, "variant_id", "") or "").strip()
    time_of_day = str(getattr(scene, "time_of_day", "") or "").strip()
    if base_scene_id and not (variant_id or time_of_day):
        prefix = f"{base_scene_id}_"
        if scene.name.startswith(prefix):
            suffix = scene.name[len(prefix) :].strip()
            if suffix:
                variant_candidate, sep, time_candidate = suffix.rpartition("_")
                if sep and time_candidate in _SCENE_TIME_TOKENS:
                    variant_id = variant_candidate
                    time_of_day = time_candidate
                elif suffix in _SCENE_TIME_TOKENS:
                    time_of_day = suffix
                else:
                    variant_id = suffix
    master_path = compute_scene_master_path(project_dir, scene.name)
    reverse_master_path = compute_scene_reverse_master_path(project_dir, scene.name)
    pano_path = stage_manifest.resolve_pano_path(project_dir, scene.name)
    custom_scene_path = stage_manifest.resolve_ply_path(
        project_dir, scene.name, ply_kind="custom"
    )
    pano_url = _scenes._asset_url(ctx, project_dir, pano_path) if pano_path is not None else ""

    return {
        "name": scene.name,
        "aliases": scene.aliases,
        "scene_type": scene.scene_type,
        "base_scene_id": base_scene_id,
        "variant_id": variant_id,
        "time_of_day": time_of_day,
        "environment_prompt": scene.environment_prompt,
        "variant_prompt": getattr(scene, "variant_prompt", ""),
        "effective_environment_prompt": build_scene_effective_prompt(
            scene, base_scene
        ),
        "description": scene.description,
        "derived_from_scene": derived_from_scene,
        "spatial_layout_image": scene.spatial_layout_image,
        "notes": scene.notes,
        "updated_at": newest_updated_at(
            getattr(scene, "updated_at", ""),
            tree_updated_at(project_dir / "assets" / "scenes" / scene.name),
            tree_updated_at(stage_manifest.stage_dir(project_dir, scene.name)),
        ),
        "master_path": master_path,
        "master_url": _scenes._asset_url(ctx, project_dir, master_path) if master_path else "",
        "reverse_master_path": reverse_master_path,
        "reverse_master_url": (
            _scenes._asset_url(ctx, project_dir, reverse_master_path)
            if reverse_master_path
            else ""
        ),
        "pano_path": str(pano_path) if pano_path is not None else "",
        "pano_url": pano_url,
        "custom_scene_path": (
            str(custom_scene_path) if custom_scene_path is not None else ""
        ),
        "custom_scene_url": (
            _scenes._asset_url(ctx, project_dir, custom_scene_path)
            if custom_scene_path is not None
            else ""
        ),
        "stage_3gs": _stage_3gs_payload(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
        ),
    }


def _scene_summary_payload(
    scene: NovelScene,
    *,
    ctx: ProjectContext | None,
    project_dir: Path,
    project_id: str = "",
    base_scene: NovelScene | None = None,
) -> dict[str, Any]:
    """Return the SQLite-only fields needed to group/search the scene list.

    The full payload probes master/reverse/pano files, loads several manifests,
    and recursively walks asset directories. Doing that for every scene before
    the first paint makes list latency scale with OSSFS round trips rather than
    the tiny scenes table. Full payloads are fetched for the selected group.
    """

    base_scene_id = str(getattr(scene, "base_scene_id", "") or "").strip()
    updated_at = str(getattr(scene, "updated_at", "") or "")
    master_path = canonical_scene_master_path(project_dir, scene.name)
    master_rel = master_path.relative_to(project_dir).as_posix()
    asset_project = str(getattr(ctx, "project_id", "") or project_id).strip()
    master_url = project_static_url(asset_project, master_rel)
    if updated_at:
        master_url = f"{master_url}?v={quote(updated_at, safe='')}"
    return {
        "name": scene.name,
        "aliases": scene.aliases,
        "scene_type": scene.scene_type,
        "base_scene_id": base_scene_id,
        "variant_id": str(getattr(scene, "variant_id", "") or "").strip(),
        "time_of_day": str(getattr(scene, "time_of_day", "") or "").strip(),
        "environment_prompt": scene.environment_prompt,
        "variant_prompt": getattr(scene, "variant_prompt", ""),
        "effective_environment_prompt": build_scene_effective_prompt(scene, base_scene),
        "description": scene.description,
        "derived_from_scene": base_scene_id,
        "spatial_layout_image": scene.spatial_layout_image,
        "notes": scene.notes,
        "updated_at": updated_at,
        # The canonical preview slot is a projection, not proof that the file
        # exists. The browser loads it lazily; authoritative state remains in
        # the selected-scene detail response.
        "master_path": str(master_path),
        "master_url": master_url,
    }


async def _require_scene(store: SQLiteStore, name: str) -> NovelScene | None:
    return await store.get_scene(name)


async def _derived_scene_names_for(store: SQLiteStore, scene_name: str) -> list[str]:
    scenes = await store.list_scenes()
    return sorted(
        str(scene.name).strip()
        for scene in scenes
        if str(scene.name or "").strip()
        and str(getattr(scene, "base_scene_id", "") or "").strip() == scene_name
    )


async def _is_derived_scene(store: SQLiteStore, scene_name: str) -> bool:
    scene = await store.get_scene(scene_name)
    return bool(scene and str(getattr(scene, "base_scene_id", "") or "").strip())


async def _derived_scene_guard_error(store: SQLiteStore, scene_name: str) -> str:
    derived_names = await _derived_scene_names_for(store, scene_name)
    if derived_names:
        preview = "、".join(derived_names[:5])
        suffix = "…" if len(derived_names) > 5 else ""
        return f"场景「{scene_name}」存在派生场景：{preview}{suffix}；请先处理派生场景"
    return ""


def _scene_plate_preview_payload(
    *,
    scene_id: str,
    variant_id: str,
    time_of_day: str,
    resolved_scene_name: str,
    time_baked: bool,
    planned_scene_name: str = "",
) -> dict[str, Any]:
    has_time = bool(str(time_of_day or "").strip())
    if not has_time:
        render_status = "no_time"
        render_relight = False
        render_label = f"Render：将使用 {resolved_scene_name}，锁图光"
        seedance_label = f"Seedance2：将喂入 {resolved_scene_name}，提示词时间：无"
    elif planned_scene_name and planned_scene_name != resolved_scene_name:
        render_status = "planned_missing"
        render_relight = True
        render_label = (
            f"Render：已规划 {planned_scene_name} 但暂无图，将使用 "
            f"{resolved_scene_name}，relight 到 {time_of_day}"
        )
        seedance_label = (
            f"Seedance2：将喂入 {resolved_scene_name}，提示词时间：{time_of_day}"
        )
    elif time_baked:
        render_status = "time_baked"
        render_relight = False
        render_label = f"Render：将使用 {resolved_scene_name}，锁图光"
        seedance_label = (
            f"Seedance2：将喂入 {resolved_scene_name}，提示词时间：{time_of_day}"
        )
    else:
        render_status = "relight"
        render_relight = True
        render_label = f"Render：将使用 {resolved_scene_name}，relight 到 {time_of_day}"
        seedance_label = (
            f"Seedance2：将喂入 {resolved_scene_name}，提示词时间：{time_of_day}"
        )

    return {
        "scene_id": scene_id,
        "variant_id": variant_id,
        "time_of_day": time_of_day,
        "resolved_scene_name": resolved_scene_name,
        "planned_scene_name": planned_scene_name,
        "time_baked": time_baked,
        "render": {
            "resolved_scene_name": resolved_scene_name,
            "planned_scene_name": planned_scene_name,
            "relight": render_relight,
            "status": render_status,
            "label": render_label,
        },
        "seedance2": {
            "resolved_scene_name": resolved_scene_name,
            "prompt_time_of_day": time_of_day,
            "label": seedance_label,
        },
    }


def _compose_scene_asset_name(
    name: str,
    base_scene_id: str = "",
    variant_id: str = "",
    time_of_day: str = "",
) -> str:
    base = str(base_scene_id or "").strip()
    variant = str(variant_id or "").strip()
    scene_time = str(time_of_day or "").strip()
    if not base:
        return str(name or "").strip()
    scene_name = base
    if variant:
        scene_name = compose_derived_scene_name(scene_name, variant)
    if scene_time:
        scene_name = compose_derived_scene_name(scene_name, scene_time)
    return scene_name
