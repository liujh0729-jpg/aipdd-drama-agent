"""scenes.gen_routes 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import scenes as _scenes
from novelvideo.api.schemas import SceneReferenceGenerateRequest
from novelvideo.api.task_start_errors import handle_task_start_runtime_error
from novelvideo.director_world import stage_manifest
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_scopes import scene_reference_asset_scope
from novelvideo.utils.path_resolver import compute_scene_master_path
from novelvideo.utils.path_resolver import compute_scene_reverse_master_path

from ._common import logger

router = APIRouter()


@router.post("/projects/{project}/scenes/{name}/master/generate-async")
async def generate_scene_master(
    project: str,
    name: str,
    body: SceneReferenceGenerateRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _start_scene_reference_task(
        project=project,
        name=name,
        kind="master",
        model=body.model if body else None,
        user=user,
    )


@router.post("/projects/{project}/scenes/{name}/reverse/generate-async")
async def generate_scene_reverse_master(
    project: str,
    name: str,
    body: SceneReferenceGenerateRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _start_scene_reference_task(
        project=project,
        name=name,
        kind="reverse_master",
        model=body.model if body else None,
        user=user,
    )


async def _start_scene_reference_task(
    *,
    project: str,
    name: str,
    kind: str,
    model: str | None = None,
    user: dict,
    store: SQLiteStore | None = None,
):
    async with _scenes._scene_project_scope(project, user) as (
        ctx,
        username,
        project_name,
        project_dir,
        output_dir,
        resolved_store,
    ):
        store = store or resolved_store
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        scope = scene_reference_asset_scope(scene.name, kind)
        if ctx is not None:
            model_selection = str(model or "").strip()
            queued = await _scenes.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="scene_reference_asset",
                queue_kind="default",
                episode=0,
                scope=scope,
                payload={
                    "scene_name": scene.name,
                    "kind": kind,
                    "model": model_selection,
                    "style": _scenes._project_style(username, project_name),
                    "output_dir": output_dir,
                },
            )
            return {
                "ok": True,
                "task_type": "scene_reference_asset",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "scene_reference_asset", ctx.project_id, 0, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"场景「{scene.name}」{kind} 生成任务已进入队列",
            }

        return {"ok": False, "error": "场景参考图生成需要 project context"}


async def _start_3gs_single_face_task(
    *,
    project: str,
    name: str,
    source_kind: str,
    user: dict,
    store: SQLiteStore,
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        source_kind = str(source_kind or "master").strip().lower()
        if source_kind == "reverse":
            if not compute_scene_reverse_master_path(project_dir, scene.name):
                return {
                    "ok": False,
                    "error": "缺少 reverse_master.png，请先生成 reverse master",
                }
        elif not compute_scene_master_path(project_dir, scene.name):
            return {"ok": False, "error": "缺少 master.png，请先上传或生成场景源图"}

        params = {
            "source_kind": source_kind,
            "face_name": "front",
            "depth_meters": 8.0,
            "device": "auto",
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
        }
        try:
            scope, queued = await _scenes._start_or_enqueue_stage_asset(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=output_dir,
                scene_name=scene.name,
                step="single_face_sharp",
                params=params,
            )
        except RuntimeError as exc:
            handle_task_start_runtime_error(
                logger,
                "failed to start single-face stage asset task",
                exc,
            )
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "task_type": "stage_asset",
            "scope": scope,
            "source": source_kind,
            **(queued or {}),
            "message": f"场景「{scene.name}」{source_kind} → SOG 任务已启动",
        }


@router.post("/projects/{project}/scenes/{name}/3gs/master-ply/generate-async")
async def generate_scene_3gs_master_ply(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    return await _start_3gs_single_face_task(
        project=project,
        name=name,
        source_kind="master",
        user=user,
        store=None,
    )


@router.post("/projects/{project}/scenes/{name}/3gs/reverse-ply/generate-async")
async def generate_scene_3gs_reverse_ply(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    return await _start_3gs_single_face_task(
        project=project,
        name=name,
        source_kind="reverse",
        user=user,
        store=None,
    )


@router.post("/projects/{project}/scenes/{name}/3gs/pano-ply/generate-async")
async def generate_scene_3gs_pano_ply(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        if stage_manifest.resolve_pano_path(project_dir, scene.name) is None:
            return {"ok": False, "error": "缺少 pano_360.png，请先上传或生成 360 全景"}

        params = {
            "geometry_mode": "pano-depth",
            "depth_source": "da2",
            "depth_device": "auto",
            "device": "auto",
            "pano_depth_width": 2048,
            "pano_depth_point_scale": 0.72,
            "pano_depth_min_scale": 0.0008,
            "pano_depth_max_scale": 0.045,
            "pano_depth_opacity": 0.96,
            "pano_depth_radius_scale": 1.0,
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
        }
        try:
            scope, queued = await _scenes._start_or_enqueue_stage_asset(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=output_dir,
                scene_name=scene.name,
                step="pano_sharp",
                params=params,
            )
        except RuntimeError as exc:
            handle_task_start_runtime_error(
                logger,
                "failed to start pano stage asset task",
                exc,
            )
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "task_type": "stage_asset",
            "scope": scope,
            "source": "pano",
            **(queued or {}),
            "message": f"场景「{scene.name}」360 → SOG 任务已启动",
        }
