"""scenes.director_routes 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import scenes as _scenes
from novelvideo.director_world import stage_manifest

router = APIRouter()


@router.get("/projects/{project}/scenes/{name}/director-stage/manifest")
async def get_scene_director_stage_manifest(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="viewer") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        manifest = _scenes.build_director_stage_manifest(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
            mode="scene",
        )
        if manifest is None:
            return {"ok": False, "error": "当前场景没有 3GS 资产"}
        return {"ok": True, "data": manifest.model_dump(exclude_none=True)}


@router.post("/projects/{project}/scenes/{name}/director-stage/world")
async def save_scene_director_world(
    project: str,
    name: str,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="editor") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            return {"ok": False, "error": "snapshot is required"}
        source_id = str(body.get("active_source_id") or "").strip()
        try:
            active_source = body.get("active_source")
            saved = stage_manifest.save_scene_director_world(
                project_dir,
                scene.name,
                active_source_id=source_id,
                snapshot=snapshot,
                active_source=active_source if isinstance(active_source, dict) else None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        manifest = _scenes.build_director_stage_manifest(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
            mode="scene",
        )
        return {
            "ok": True,
            "data": {
                **saved,
                "manifest": (
                    manifest.model_dump(exclude_none=True) if manifest is not None else None
                ),
            },
        }


@router.post("/projects/{project}/scenes/{name}/director-stage/world/source")
async def save_scene_director_world_source(
    project: str,
    name: str,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="editor") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            return {"ok": False, "error": "snapshot is required"}
        source_id = str(body.get("source_id") or "").strip()
        try:
            source = body.get("source")
            saved = stage_manifest.save_scene_director_world_source(
                project_dir,
                scene.name,
                source_id=source_id,
                snapshot=snapshot,
                source=source if isinstance(source, dict) else None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        manifest = _scenes.build_director_stage_manifest(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
            mode="scene",
        )
        return {
            "ok": True,
            "data": {
                **saved,
                "manifest": (
                    manifest.model_dump(exclude_none=True) if manifest is not None else None
                ),
            },
        }


@router.post("/projects/{project}/scenes/{name}/director-stage/world/clear")
async def clear_scene_director_world(
    project: str,
    name: str,
    body: dict[str, Any] | None = None,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="editor") as (_ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        body = body or {}
        saved = stage_manifest.clear_scene_director_world(
            project_dir,
            scene.name,
            active_source_id=str(body.get("active_source_id") or "").strip() or None,
        )
        return {"ok": True, "data": saved}
