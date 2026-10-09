"""scenes.custom_routes 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import scenes as _scenes
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.api.upload_workers import scene_ply_upload_limiter
from novelvideo.api.upload_workers import scene_upload_lock
from novelvideo.utils.async_ops import metadata_io_limiter
from novelvideo.utils.upload_safety import MAX_PROJECT_UPLOAD_BYTES
from novelvideo.utils.upload_safety import UploadTooLargeError

router = APIRouter()


@router.post("/projects/{project}/scenes/{name}/custom/upload")
async def upload_scene_custom_package(
    project: str,
    name: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        suffix = Path(str(file.filename or "")).suffix.lower()
        if suffix not in {".ply", ".sog", ".splat", ".ksplat"}:
            return {
                "ok": False,
                "error": "Custom scene package must be .ply, .sog, .splat, or .ksplat",
            }

        try:
            async with scene_upload_lock(_scenes._scene_upload_lock_key(project_dir, scene.name)):
                await run_asset_upload_operation(
                    _scenes._persist_custom_scene_upload,
                    file,
                    suffix=suffix,
                    project_dir=project_dir,
                    scene_name=scene.name,
                    worker_limiter=(
                        scene_ply_upload_limiter() if suffix == ".ply" else None
                    ),
                )
        except _scenes._EmptyScenePackageError as exc:
            return {"ok": False, "error": str(exc)}
        except UploadTooLargeError:
            return {
                "ok": False,
                "error": (
                    "Custom scene package exceeds "
                    f"{MAX_PROJECT_UPLOAD_BYTES // (1024 * 1024)}MB limit"
                ),
            }

        return {
            "ok": True,
            "data": _scenes._scene_payload(
                scene,
                ctx=ctx,
                project_dir=project_dir,
            ),
        }


@router.post("/projects/{project}/scenes/{name}/custom/delete")
async def delete_scene_custom_package(
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
                _scenes._delete_scene_custom_files,
                project_dir,
                scene.name,
                worker_limiter=metadata_io_limiter(),
            )

        return {"ok": True, "data": {"deleted": deleted}}
