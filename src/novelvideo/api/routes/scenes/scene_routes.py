"""scenes.scene_routes 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Query
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import may_run_asset_repair
from novelvideo.api.routes import scenes as _scenes
from novelvideo.api.schemas import SceneCreate
from novelvideo.api.schemas import SceneUpdate
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.api.upload_workers import scene_upload_lock
from novelvideo.models import NovelScene
from novelvideo.models import resolve_scene_plate_from_records
from novelvideo.novel_source import novel_import_required_response
from novelvideo.scene_prerequisites import SceneBuildNotApplicableError
from novelvideo.scene_prerequisites import ScenePlanningRunningError
from novelvideo.scene_prerequisites import running_scene_planner
from novelvideo.scene_prerequisites import scene_prerequisite_response
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.asset_names import path_safe_asset_name
from novelvideo.utils.path_resolver import canonical_scene_master_path
from novelvideo.utils.path_resolver import compute_scene_master_path

router = APIRouter()


@router.get("/projects/{project}/scenes")
async def list_scenes(
    project: str,
    summary: bool = False,
    names: Annotated[list[str] | None, Query()] = None,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user, required_role="viewer") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        if may_run_asset_repair(ctx):
            await _scenes._heal_path_unsafe_scene_names(store, project_dir)
        scenes = await store.list_scenes()
        scenes_by_name = {
            scene.name: scene for scene in scenes if str(scene.name or "").strip()
        }
        requested_names = {
            str(name or "").strip() for name in (names or []) if str(name or "").strip()
        }
        if requested_names:
            scenes = [scene for scene in scenes if scene.name in requested_names]
        if summary:
            return {
                "ok": True,
                "data": [
                    _scenes._scene_summary_payload(
                        scene,
                        ctx=ctx,
                        project_dir=project_dir,
                        project_id=project,
                        base_scene=scenes_by_name.get(
                            str(getattr(scene, "base_scene_id", "") or "")
                        ),
                    )
                    for scene in scenes
                ],
            }
        data = await asyncio.to_thread(
            lambda: [
                _scenes._scene_payload(
                    scene,
                    ctx=ctx,
                    project_dir=project_dir,
                    derived_from_scene=str(
                        getattr(scene, "base_scene_id", "") or ""
                    ).strip(),
                    base_scene=scenes_by_name.get(
                        str(getattr(scene, "base_scene_id", "") or "")
                    ),
                )
                for scene in scenes
            ]
        )
        return {"ok": True, "data": data}


@router.get("/projects/{project}/scenes/plate-preview")
async def preview_scene_plate(
    project: str,
    scene_id: str = Query(default=""),
    variant_id: str = Query(default=""),
    time_of_day: str = Query(default=""),
    user: dict = Depends(get_api_user),
):
    scene_id = scene_id if isinstance(scene_id, str) else ""
    variant_id = variant_id if isinstance(variant_id, str) else ""
    time_of_day = time_of_day if isinstance(time_of_day, str) else ""
    async with _scenes._scene_project_scope(project, user, required_role="viewer") as (_ctx, _username, _project_name, project_dir, _output_dir, store):
        scene_records = await store.list_scenes()
        resolved_scene_name, time_baked = resolve_scene_plate_from_records(
            scene_id,
            variant_id,
            time_of_day,
            scene_records,
        )
        planned_scene_name = ""
        if time_baked:
            resolved_master_path = compute_scene_master_path(
                project_dir, resolved_scene_name
            )
            if not resolved_master_path:
                planned_scene_name = resolved_scene_name
                resolved_scene_name, _unused_time_baked = resolve_scene_plate_from_records(
                    scene_id,
                    variant_id,
                    "",
                    scene_records,
                )
                time_baked = False
        return {
            "ok": True,
            "data": _scenes._scene_plate_preview_payload(
                scene_id=scene_id,
                variant_id=variant_id,
                time_of_day=time_of_day,
                resolved_scene_name=resolved_scene_name,
                time_baked=time_baked,
                planned_scene_name=planned_scene_name,
            ),
        }


@router.post("/projects/{project}/scenes")
async def create_scene(
    project: str,
    body: SceneCreate,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        # 在查重之前消毒：留到 add_scene 再改名的话，两个只差斜杠的名字会双双通过查重，
        # 后写的那个把先写的静默覆盖掉。
        name = path_safe_asset_name(
            _scenes._compose_scene_asset_name(
                body.name,
                body.base_scene_id,
                body.variant_id,
                body.time_of_day,
            )
        )
        if not name:
            return {"ok": False, "error": "Scene name is required"}
        existing = await store.get_scene(name)
        if existing is not None:
            return {"ok": False, "error": f"Scene '{name}' already exists"}

        scene = NovelScene(
            name=name,
            aliases=body.aliases,
            scene_type=body.scene_type,
            base_scene_id=body.base_scene_id.strip(),
            variant_id=body.variant_id.strip(),
            time_of_day=body.time_of_day.strip(),
            environment_prompt=body.environment_prompt,
            variant_prompt=body.variant_prompt,
            description=body.description,
            spatial_layout_image=body.spatial_layout_image,
            notes=body.notes,
        )
        await store.add_scene(scene)
        return {
            "ok": True,
            "data": _scenes._scene_payload(
                scene,
                ctx=ctx,
                project_dir=project_dir,
            ),
        }


@router.patch("/projects/{project}/scenes/{name}")
async def update_scene(
    project: str,
    name: str,
    body: SceneUpdate,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        requested_name = str(updates.pop("name", "") or "").strip()
        next_base = str(
            updates.get("base_scene_id", getattr(scene, "base_scene_id", "")) or ""
        ).strip()
        next_variant = str(
            updates.get("variant_id", getattr(scene, "variant_id", "")) or ""
        ).strip()
        next_time = str(
            updates.get("time_of_day", getattr(scene, "time_of_day", "")) or ""
        ).strip()
        structured_name = _scenes._compose_scene_asset_name(
            requested_name or scene.name, next_base, next_variant, next_time
        )
        if next_base:
            requested_name = structured_name
        # 在挪目录之前消毒：store.rename_scene 里也会消毒，但目录迁移先于它执行，
        # 不在这里统一就会出现「库里 a_b、盘上 a/b」的错位。
        requested_name = path_safe_asset_name(str(requested_name or "").strip())
        if requested_name and requested_name != scene.name:
            guard_error = await _scenes._derived_scene_guard_error(store, scene.name)
            if guard_error:
                return {"ok": False, "error": guard_error}
            if await store.get_scene(requested_name) is not None:
                return {"ok": False, "error": f"Scene '{requested_name}' already exists"}
            try:
                _scenes._rename_scene_asset_dirs(project_dir, scene.name, requested_name)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            renamed = await store.rename_scene(scene.name, requested_name)
            if not renamed:
                return {"ok": False, "error": f"Scene '{scene.name}' rename failed"}
            scene = await _scenes._require_scene(store, requested_name) or scene
        if updates:
            await store.update_scene(scene.name, **updates)
            scene = await _scenes._require_scene(store, scene.name) or scene

        return {
            "ok": True,
            "data": _scenes._scene_payload(
                scene,
                ctx=ctx,
                project_dir=project_dir,
            ),
        }


@router.post("/projects/{project}/scenes/{name}/delete")
async def delete_scene(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        guard_error = await _scenes._derived_scene_guard_error(store, scene.name)
        if guard_error:
            return {"ok": False, "error": guard_error}
        deleted = await store.delete_scene(scene.name)
        return {"ok": True, "data": {"deleted": deleted}}


@router.post("/projects/{project}/scenes/build")
async def build_scenes(project: str, user: dict = Depends(get_api_user)):
    async with _scenes._scene_project_scope(
        project,
        user,
        required_role="editor",
    ) as (ctx, username, project_name, project_dir, output_dir, store):
        if ctx is not None:
            if not _scenes.has_imported_novel(project_dir):
                return novel_import_required_response()
            # Prerequisites are answered before the queue, not inside it: a narrated
            # project must not spend a generation slot on a build that would make no
            # model call and produce no scene.
            config = _scenes.load_project_config_file_from_state_dir(ctx.state_dir)
            if not _scenes.scene_build_applies(
                str(ctx.state_dir), str(config.get("spine_template") or "drama")
            ):
                return scene_prerequisite_response(SceneBuildNotApplicableError())
            # The other half of the exclusion. Both write the scenes table, so a
            # build landing on top of a running planner leaves a catalogue whose
            # contents depend on which writer got there first.
            tasks = await run_in_threadpool(
                _scenes.get_task_manager().list_tasks_for_project,
                ctx,
            )
            if running_scene_planner(tasks):
                return scene_prerequisite_response(ScenePlanningRunningError())
            queued = await _scenes.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="build_scenes",
                queue_kind="default",
                episode=0,
                payload={"output_dir": output_dir},
            )
            return {
                "ok": True,
                "task_type": "build_scenes",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key("build_scenes", ctx.project_id, 0),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": "场景补充任务已进入队列",
                "message_code": "tasks.toast.scenesBuildQueued",
            }

        return {"ok": False, "error": "场景补充需要 project context"}


@router.post("/projects/{project}/scenes/{name}/master/upload")
async def upload_scene_master(
    project: str,
    name: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}

        master_path = canonical_scene_master_path(project_dir, scene.name)

        async def touch_master(_result):
            await store.touch_scene_asset(scene.name)
            return await _scenes._require_scene(store, scene.name) or scene

        try:
            async with scene_upload_lock(
                _scenes._scene_upload_lock_key(project_dir, scene.name)
            ):
                scene = await run_asset_upload_operation(
                    _scenes._persist_scene_master_upload,
                    file,
                    master_path,
                    finalize=touch_master,
                )
        except _scenes._InvalidSceneImageError as exc:
            return {"ok": False, "error": f"Invalid image file: {exc}"}

        return {
            "ok": True,
            "data": _scenes._scene_payload(
                scene,
                ctx=ctx,
                project_dir=project_dir,
            ),
        }


@router.post("/projects/{project}/scenes/{name}/master/delete")
async def delete_scene_master(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    async with _scenes._scene_project_scope(project, user) as (_ctx, _username, _project_name, project_dir, _output_dir, store):
        scene = await _scenes._require_scene(store, name)
        if scene is None:
            return {"ok": False, "error": f"Scene '{name}' not found"}
        async with scene_upload_lock(_scenes._scene_upload_lock_key(project_dir, scene.name)):
            master_path = compute_scene_master_path(project_dir, scene.name)
            deleted = False
            if master_path:
                Path(master_path).unlink(missing_ok=True)
                deleted = True
                await store.touch_scene_asset(scene.name)
        return {"ok": True, "data": {"deleted": deleted}}
