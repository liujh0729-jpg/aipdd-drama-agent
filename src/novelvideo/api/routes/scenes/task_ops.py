"""scenes.task_ops 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.api.routes import scenes as _scenes
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_scopes import stage_asset_scope



async def _start_or_enqueue_stage_asset(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    scene_name: str,
    step: str,
    params: dict[str, Any],
) -> tuple[str, dict | None]:
    scope = stage_asset_scope(scene_name, step)
    if ctx is not None:
        queued = await _scenes.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="mainline",
            task_type="stage_asset",
            queue_kind="world",
            episode=0,
            scope=scope,
            payload={
                "scene_name": scene_name,
                "step": step,
                "params": params,
                "project_dir": str(project_dir),
            },
        )
        return scope, {
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "stage_asset", ctx.project_id, 0, scope=scope
            ),
            "backend": queued.backend,
            "queue": queued.queue,
        }

    raise RuntimeError("片场资产生成需要 project context")


async def _start_or_enqueue_scene_pano(
    *,
    ctx: ProjectContext | None,
    project_dir: Path,
    scene_name: str,
    step: str,
    params: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if ctx is None:
        raise RuntimeError("场景全景生成需要 project context")

    scope = stage_asset_scope(scene_name, step)
    queued = await _scenes.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="mainline",
        task_type="scene_pano_generation",
        queue_kind="world",
        episode=0,
        scope=scope,
        payload={
            "scene_name": scene_name,
            "step": step,
            "params": params,
            "project_dir": str(project_dir),
        },
    )
    return scope, {
        "task_id": queued.task_state.task_id,
        "task_key": project_task_state_key(
            "scene_pano_generation", ctx.project_id, 0, scope=scope
        ),
        "backend": queued.backend,
        "queue": queued.queue,
    }
