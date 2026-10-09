"""generation.render 子模块（自 api/routes/generation.py 拆分）。"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import BeatsRegenerateRequest
from novelvideo.api.schemas import PlanEntryOut
from novelvideo.api.schemas import RenderPlanExecuteRequest
from novelvideo.api.schemas import RenderPlanExecuteResponse
from novelvideo.api.schemas import RenderPlanRequest
from novelvideo.api.schemas import RenderPlanResponse
from novelvideo.api.schemas import SketchRegenerateRequest
from novelvideo.generators.grid_generation import build_regen_plan
from novelvideo.generators.grid_generation import compute_input_fingerprint
from novelvideo.generators.grid_generation import hash_plan
from novelvideo.manual_shots import pick_beats_by_number
from novelvideo.render_plan.ref_image_hash import RefImageHasher
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.path_resolver import PathResolver

from ._common import _FANOUT_ACTIVE_LIMIT_ERRORS

router = APIRouter()


@router.post("/projects/{project}/episodes/{episode_num}/render/plan")
async def render_plan(
    project: str,
    episode_num: int,
    body: RenderPlanRequest,
    user: dict = Depends(get_api_user),
):
    """Return the server-authoritative render plan for selected beats."""
    if _generation._render_plan_feature_disabled():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "feature_disabled",
                "data": {"reason": "DISABLE_RENDER_PLAN_V2 is set"},
            },
        )

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        all_beats = await store.get_beats_as_dicts(episode_num)
        if not all_beats:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "no_beats", "data": {"episode": episode_num}},
            )

        beat_indices = _generation.normalize_beat_indices(body.beat_indices)
        invalid = _generation.validate_beat_indices(all_beats, beat_indices)
        if invalid:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_beats", "data": {"invalid": invalid}},
            )
        selected_beats = pick_beats_by_number(all_beats, beat_indices)

        detection_error = _generation.render_ai_detection_error(selected_beats)
        if detection_error:
            return JSONResponse(status_code=400, content={"ok": False, "error": detection_error})

        character_map = await _generation._build_character_map(
            store,
            selected_beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )
        sketch_colors = store.get_sketch_colors(episode_num) or {}
        project_config = _generation.load_project_config(username, project_name)
        render_image_selection = _generation._resolve_render_image_selection(
            project_config,
            body.image_generation_selection,
        )
        plan = build_regen_plan(
            selected_beats=selected_beats,
            strategy=body.strategy,
            aspect_mode=body.aspect_mode,
            character_map=character_map,
            force_one_by_one=body.force_one_by_one,
            image_generation_selection=render_image_selection,
        )

        hasher = RefImageHasher(Path(output_dir) / ".render_plan_cache")
        try:
            fingerprint = compute_input_fingerprint(
                beats=selected_beats,
                character_map=character_map,
                sketch_colors=sketch_colors,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                force_one_by_one=body.force_one_by_one,
                ref_image_hasher=hasher.hash,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"reason": f"missing ref image: {exc}"},
                },
            )

        return {
            "ok": True,
            "data": RenderPlanResponse(
                plan=[PlanEntryOut(**entry) for entry in _generation._plan_to_dicts(plan)],
                plan_hash=hash_plan(plan),
                input_fingerprint=fingerprint,
                strategy=body.strategy,
                total_beats=len(selected_beats),
                total_grids=len(plan),
            ).model_dump(),
        }


@router.post("/projects/{project}/episodes/{episode_num}/render/execute")
async def render_execute(
    project: str,
    episode_num: int,
    body: RenderPlanExecuteRequest,
    user: dict = Depends(get_api_user),
):
    """Validate and dispatch a render plan through the current selected-regen task path."""
    if _generation._render_plan_feature_disabled():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "feature_disabled",
                "data": {"reason": "DISABLE_RENDER_PLAN_V2 is set"},
            },
        )

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        all_beats = await store.get_beats_as_dicts(episode_num)
        if not all_beats:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "no_beats", "data": {"episode": episode_num}},
            )

        beat_indices = _generation.normalize_beat_indices(body.beat_indices)
        invalid = _generation.validate_beat_indices(all_beats, beat_indices)
        if invalid:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_beats", "data": {"invalid": invalid}},
            )
        selected_beats = pick_beats_by_number(all_beats, beat_indices)

        detection_error = _generation.render_ai_detection_error(selected_beats)
        if detection_error:
            return JSONResponse(status_code=400, content={"ok": False, "error": detection_error})

        character_map = await _generation._build_character_map(
            store,
            selected_beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )
        sketch_colors = store.get_sketch_colors(episode_num) or {}
        project_config = _generation.load_project_config(username, project_name)
        render_image_selection = _generation._resolve_render_image_selection(
            project_config,
            body.image_generation_selection,
        )
        hasher = RefImageHasher(Path(output_dir) / ".render_plan_cache")
        try:
            new_fingerprint = compute_input_fingerprint(
                beats=selected_beats,
                character_map=character_map,
                sketch_colors=sketch_colors,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                force_one_by_one=body.force_one_by_one,
                ref_image_hasher=hasher.hash,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"reason": f"missing ref image: {exc}"},
                },
            )

        if new_fingerprint != body.input_fingerprint:
            new_plan = build_regen_plan(
                selected_beats=selected_beats,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                character_map=character_map,
                force_one_by_one=body.force_one_by_one,
                image_generation_selection=render_image_selection,
            )
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": "input_stale",
                    "data": {
                        "new_plan": _generation._plan_to_dicts(new_plan),
                        "new_plan_hash": hash_plan(new_plan),
                        "new_input_fingerprint": new_fingerprint,
                    },
                },
            )

        if body.custom_plan:
            custom_error = _generation._custom_render_plan_error(body.plan, beat_indices)
            if custom_error:
                return JSONResponse(
                    status_code=400,
                    content={
                        "ok": False,
                        "error": "invalid_custom_plan",
                        "data": {"reason": custom_error},
                    },
                )
            execution_plan = body.plan
            execution_hash = hash_plan(execution_plan)
            dispatch_strategy = "custom"
        else:
            recomputed = build_regen_plan(
                selected_beats=selected_beats,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                character_map=character_map,
                force_one_by_one=body.force_one_by_one,
                image_generation_selection=render_image_selection,
            )
            recomputed_hash = hash_plan(recomputed)
            if recomputed_hash != body.plan_hash:
                return JSONResponse(
                    status_code=409,
                    content={
                        "ok": False,
                        "error": "plan_stale",
                        "data": {
                            "new_plan": _generation._plan_to_dicts(recomputed),
                            "new_plan_hash": recomputed_hash,
                            "new_input_fingerprint": new_fingerprint,
                        },
                    },
                )
            execution_plan = recomputed
            execution_hash = recomputed_hash
            dispatch_strategy = body.strategy

        from novelvideo.task_identity import selection_scope

        style = project_config.get("visual_style") or "chinese_period_drama"
        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, all_beats)
        base_config = {
            "beats": all_beats,
            "character_map": character_map,
            "style": style,
            "model": "nanobanana",
            "image_generation_selection": render_image_selection,
            "sketch_colors": sketch_colors,
            "prop_menu": prop_menu,
            "sketch_aspect_padding": _generation._resolve_render_bool_setting(
                project_config,
                "sketch_aspect_padding",
                body.sketch_aspect_padding,
                True,
            ),
        }
        scope = f"{dispatch_strategy}__{execution_hash}"
        dispatched_task_ids: list[str] = []
        rejected: list[dict[str, Any]] = []

        if ctx is not None:
            for position, entry in enumerate(execution_plan):
                entry_beats = [int(beat) for beat in entry.beat_numbers]
                entry_scope = selection_scope(entry.mode_key, entry_beats)
                try:
                    queued = await _generation.get_task_backend().enqueue_project_task(
                        ctx,
                        product_surface="mainline",
                        task_type="selected_regen",
                        queue_kind="default",
                        episode=episode_num,
                        scope=entry_scope,
                        payload={
                            "episode": episode_num,
                            "mode_key": entry.mode_key,
                            "output_dir": output_dir,
                            "config": {
                                **base_config,
                                "mode_key": entry.mode_key,
                                "selected_beat_numbers": entry_beats,
                            },
                        },
                    )
                except _FANOUT_ACTIVE_LIMIT_ERRORS as exc:
                    if not dispatched_task_ids:
                        # k == 0：裸抛交 handler 渲染 429（M8 不变量 7）。
                        raise
                    _generation._log_partial_dispatch_rejection(exc)
                    # 只投这一次，但把未投的尾段逐条如实上报（N−k 条，M8 :722 / :755）。
                    for pending in execution_plan[position:]:
                        pending_beats = [int(beat) for beat in pending.beat_numbers]
                        rejected.append(
                            _generation._task_limit_rejection(
                                selection_scope(pending.mode_key, pending_beats), exc
                            )
                        )
                    break
                dispatched_task_ids.append(queued.task_state.task_id)
        else:
            return {
                "ok": False,
                "error": "渲染计划执行需要 project context",
                "data": RenderPlanExecuteResponse(
                    task_type="render_plan",
                    message="渲染计划未启动",
                    scope=scope,
                    resolved_grids=[PlanEntryOut(**entry) for entry in _generation._plan_to_dicts(execution_plan)],
                ).model_dump(),
            }

        return {
            "ok": True,
            "data": RenderPlanExecuteResponse(
                task_type="render_plan",
                message=f"渲染已启动 ({len(execution_plan)} 个网格)",
                scope=scope,
                resolved_grids=[PlanEntryOut(**entry) for entry in _generation._plan_to_dicts(execution_plan)],
            ).model_dump()
            | ({"task_ids": dispatched_task_ids} if dispatched_task_ids else {})
            | ({"rejected": rejected} if rejected else {}),
        }


@router.post("/projects/{project}/episodes/{episode_num}/beats/regenerate")
async def regenerate_beats(
    project: str,
    episode_num: int,
    body: BeatsRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """选中 Beats 再生画面。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    proj_config = _generation.load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")
    render_image_selection = _generation._resolve_render_image_selection(
        proj_config,
        body.image_generation_selection,
    )

    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        # 验证 beat_indices
        if not body.beat_indices:
            return {"ok": False, "error": "beat_indices 不能为空"}
        total_beats = len(beats)
        invalid = [i for i in body.beat_indices if i < 1 or i > total_beats]
        if invalid:
            return {
                "ok": False,
                "error": f"beat_indices {invalid} 超出范围（共 {total_beats} 个 beats，有效: 1~{total_beats}）",
            }

        selected_beats = pick_beats_by_number(beats, body.beat_indices)
        detection_error = _generation.render_ai_detection_error(selected_beats)
        if detection_error:
            return {"ok": False, "error": detection_error}

        sketch_paths = PathResolver(output_dir, episode_num)
        missing_sketches = [
            beat_num
            for beat_num in body.beat_indices
            if not sketch_paths.sketch(beat_num).is_file()
        ]
        if missing_sketches:
            missing_labels = ", ".join(f"#{beat_num}" for beat_num in missing_sketches)
            raise HTTPException(
                422,
                f"Render 前请先为 beat {missing_labels} 生成或上传草图。",
            )

        character_map = await _generation._build_character_map(
            store,
            selected_beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )

        # The selected sketch is the source of truth for a single Render. The
        # client mode remains a compatibility fallback for missing legacy assets.
        mode_key = (
            _generation._single_render_mode_from_sketch(output_dir, episode_num, body.beat_indices)
            or body.mode_key
        )
        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "model": body.model,
            "image_generation_selection": render_image_selection,
            "selected_beat_numbers": body.beat_indices,
            "sketch_colors": store.get_sketch_colors(episode_num) or {},
            "prop_menu": prop_menu,
            "sketch_aspect_padding": _generation._resolve_render_bool_setting(
                proj_config,
                "sketch_aspect_padding",
                body.sketch_aspect_padding,
                True,
            ),
        }

        from novelvideo.task_identity import selection_scope

        scope = selection_scope(mode_key, body.beat_indices)

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="selected_regen",
                queue_kind="default",
                episode=episode_num,
                scope=scope,
                payload={
                    "episode": episode_num,
                    "mode_key": mode_key,
                    "output_dir": output_dir,
                    "config": {**config, "mode_key": mode_key},
                },
            )
            return {
                "ok": True,
                "task_type": "selected_regen",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "selected_regen", ctx.project_id, episode_num, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集选中 Beats 画面再生已进入队列",
            }

        return {"ok": False, "error": "选中 Beats 画面再生需要 project context"}


@router.post("/projects/{project}/episodes/{episode_num}/sketches/regenerate")
async def regenerate_sketches(
    project: str,
    episode_num: int,
    body: SketchRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """选中 Beats 再生草图。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    proj_config = _generation.load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")

    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        # 验证 beat_indices
        if not body.beat_indices:
            return {"ok": False, "error": "beat_indices 不能为空"}
        total_beats = len(beats)
        invalid = [i for i in body.beat_indices if i < 1 or i > total_beats]
        if invalid:
            return {
                "ok": False,
                "error": f"beat_indices {invalid} 超出范围（共 {total_beats} 个 beats，有效: 1~{total_beats}）",
            }

        character_map = await _generation._build_character_map(
            store,
            beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=False,
        )

        mode_key = body.mode_key
        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        sketch_image_selection = _generation._resolve_sketch_image_selection(
            proj_config,
            body.image_generation_selection,
        )
        config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "model": body.model,
            "image_generation_selection": sketch_image_selection,
            "selected_beat_numbers": body.beat_indices,
            "sketch_colors": store.get_sketch_colors(episode_num) or {},
            "prop_menu": prop_menu,
        }

        from novelvideo.task_identity import selection_scope

        scope = selection_scope(mode_key, body.beat_indices)

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="sketch_regen",
                queue_kind="default",
                episode=episode_num,
                scope=scope,
                payload={
                    "episode": episode_num,
                    "mode_key": mode_key,
                    "output_dir": output_dir,
                    "config": {**config, "mode_key": mode_key},
                },
            )
            return {
                "ok": True,
                "task_type": "sketch_regen",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "sketch_regen", ctx.project_id, episode_num, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集选中 Beats 草图再生已进入队列",
            }

        return {"ok": False, "error": "选中 Beats 草图再生需要 project context"}
