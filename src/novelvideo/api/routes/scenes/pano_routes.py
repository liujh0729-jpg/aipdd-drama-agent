"""scenes.pano_routes 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import scenes as _scenes
from novelvideo.api.schemas import PanoViewerCorrection
from novelvideo.api.schemas import ScenePanoGenerateRequest
from novelvideo.api.task_start_errors import handle_task_start_runtime_error
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.api.upload_workers import scene_upload_lock
from novelvideo.director_world import stage_manifest
from novelvideo.utils.async_ops import metadata_io_limiter
from novelvideo.utils.path_resolver import compute_scene_master_path

from ._common import logger

router = APIRouter()


@router.get("/projects/{project}/scenes/{name}/pano/manifest")
async def get_scene_pano_manifest(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="viewer") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        manifest = _scenes.build_pano_viewer_manifest(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
            mode="scene",
        )
        if manifest is None:
            return {"ok": False, "error": "当前场景没有 360 全景资产"}
        return {"ok": True, "data": manifest.model_dump(exclude_none=True)}


@router.patch("/projects/{project}/scenes/{name}/pano/correction")
async def update_scene_pano_correction(
    project: str,
    name: str,
    correction: PanoViewerCorrection,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="editor") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        stage_manifest.set_pano_correction(
            project_dir,
            scene.name,
            correction.model_dump(),
        )
        manifest = _scenes.build_pano_viewer_manifest(
            ctx=ctx,
            project_dir=project_dir,
            scene_name=scene.name,
            mode="scene",
        )
        if manifest is None:
            return {"ok": False, "error": "当前场景没有 360 全景资产"}
        return {"ok": True, "data": manifest.model_dump(exclude_none=True)}


@router.post("/projects/{project}/scenes/{name}/pano/upload")
async def upload_scene_pano(
    project: str,
    name: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        try:
            async with scene_upload_lock(
                _scenes._scene_upload_lock_key(project_dir, scene.name)
            ):
                await run_asset_upload_operation(
                    _scenes._persist_scene_pano_upload,
                    file,
                    project_dir=project_dir,
                    scene_name=scene.name,
                )
        except _scenes._InvalidSceneImageError as exc:
            return {"ok": False, "error": f"Invalid image file: {exc}"}
        except _scenes._InvalidScenePanoRatioError as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "data": _scenes._scene_payload(
                scene,
                ctx=ctx,
                project_dir=project_dir,
            ),
        }


@router.post("/projects/{project}/scenes/{name}/pano/delete")
async def delete_scene_pano(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (_ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        async with scene_upload_lock(_scenes._scene_upload_lock_key(project_dir, scene.name)):
            deleted = await run_asset_upload_operation(
                _scenes._delete_scene_pano_files,
                project_dir,
                scene.name,
                worker_limiter=metadata_io_limiter(),
            )
        return {"ok": True, "data": {"deleted": deleted}}


@router.post("/projects/{project}/scenes/{name}/pano/generate-async")
async def generate_scene_pano(
    project: str,
    name: str,
    body: ScenePanoGenerateRequest,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        source = body.source
        if source == "master" and not compute_scene_master_path(project_dir, scene.name):
            source = "text"
        step = f"pano_from_{source}"

        params: dict[str, Any] = {
            "description": _scenes._scene_360_description(scene),
            "style": _scenes._project_style(username, project_name),
            "timeout_seconds": 1800,
        }

        try:
            scope, queued = await _scenes._start_or_enqueue_scene_pano(
                ctx=ctx,
                project_dir=project_dir,
                scene_name=scene.name,
                step=step,
                params=params,
            )
        except RuntimeError as exc:
            handle_task_start_runtime_error(
                logger,
                "failed to start scene pano generation task",
                exc,
            )
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "task_type": "scene_pano_generation",
            "scope": scope,
            "source": source,
            **(queued or {}),
            "message": f"场景「{scene.name}」360 全景生成任务已启动",
        }
