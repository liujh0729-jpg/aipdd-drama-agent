"""generation.episode_media 子模块（自 api/routes/generation.py 拆分）。"""

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.auth import require_scope
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import OperatorPasswordVerifyRequest
from novelvideo.api.schemas import SketchGenerateRequest
from novelvideo.api.schemas import SketchRegenQueueUpdate
from novelvideo.api.schemas import TTSGenerateRequest
from novelvideo.api.schemas import TTSPreviewRequest
from novelvideo.api.schemas import VideoComposeRequest
from novelvideo.task_identity import project_task_state_key

from ._common import _FANOUT_ACTIVE_LIMIT_ERRORS

router = APIRouter()


def _sketch_regen_queue_key(episode_num: int) -> str:
    return f"ep{int(episode_num):03d}"


def _is_react_sketch_regen_queue_items(items: object) -> bool:
    return (
        isinstance(items, list)
        and bool(items)
        and all(isinstance(item, dict) and "beatNumbers" in item for item in items)
    )


def _react_sketch_regen_queues(config: dict) -> tuple[dict, dict, bool]:
    queues = config.get("react_sketch_regen_queue")
    if not isinstance(queues, dict):
        queues = {}
    else:
        queues = dict(queues)

    legacy_queues = config.get("sketch_regen_queue")
    cleaned_legacy = dict(legacy_queues) if isinstance(legacy_queues, dict) else {}
    legacy_changed = False
    if isinstance(legacy_queues, dict):
        for key, items in legacy_queues.items():
            if (
                isinstance(key, str)
                and key.startswith("ep")
                and _is_react_sketch_regen_queue_items(items)
            ):
                queues.setdefault(key, list(items))
                cleaned_legacy.pop(key, None)
                legacy_changed = True

    return queues, cleaned_legacy, legacy_changed


def _sketch_regen_queue_payload(username: str, project: str, episode_num: int) -> dict:
    config = _generation.load_project_config(username, project)
    queues, _cleaned_legacy, _legacy_changed = _react_sketch_regen_queues(config)
    items = queues.get(_sketch_regen_queue_key(episode_num))
    return {"items": items if isinstance(items, list) else []}


@router.get("/projects/{project}/episodes/{episode_num}/sketch-regen-queue")
async def get_sketch_regen_queue(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Return the persisted React sketch regeneration dispatch queue."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _sketch_regen_queue_payload(
            resolved.username,
            resolved.project_name,
            episode_num,
        ),
    }


@router.put("/projects/{project}/episodes/{episode_num}/sketch-regen-queue")
async def update_sketch_regen_queue(
    project: str,
    episode_num: int,
    body: SketchRegenQueueUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist the React sketch regeneration dispatch queue per episode."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    config = _generation.load_project_config(username, project_name)
    queues, cleaned_legacy, legacy_changed = _react_sketch_regen_queues(config)
    queues[_sketch_regen_queue_key(episode_num)] = [item.model_dump() for item in body.items]
    updates = {"react_sketch_regen_queue": queues}
    if legacy_changed:
        updates["sketch_regen_queue"] = cleaned_legacy
    _generation.save_project_config(username, project_name, config=updates)
    return {"ok": True, "data": _sketch_regen_queue_payload(username, project_name, episode_num)}


@router.get("/projects/{project}/episodes/{episode_num}/sketch-image-usage")
async def get_sketch_image_usage(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-style Sketch image request usage summary."""
    from novelvideo.usage.image_request_usage import get_image_usage_summary

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    summary = get_image_usage_summary(
        project_output_dir=project_dir,
        task_types=("sketch_grid",),
        episode=episode_num,
    )
    return {"ok": True, "data": summary}


def _image_generation_guard_payload(attempt_count: int, subject: str) -> dict:
    next_attempt = attempt_count + 1
    if next_attempt >= 5:
        level = "locked"
        message = f"{subject} 已连续生成 {next_attempt} 次，请输入管理员密码继续本次生成。"
    elif next_attempt >= 3:
        level = "confirm"
        message = f"{subject} 已连续生成 {next_attempt} 次，确认继续生成吗？"
    else:
        level = "none"
        message = ""
    return {
        "attempt_count": attempt_count,
        "next_attempt": next_attempt,
        "level": level,
        "message": message,
    }


@router.get("/projects/{project}/episodes/{episode_num}/image-generation-guard")
async def get_image_generation_guard(
    project: str,
    episode_num: int,
    task_type: str = Query(...),
    scope: str = Query(...),
    subject: str = Query("当前生成任务"),
    user: dict = Depends(get_api_user),
):
    """Return per-scope image generation guard status used before dispatch."""
    from novelvideo.usage.image_request_usage import count_image_scope_attempts

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    attempt_count = count_image_scope_attempts(
        project_output_dir=project_dir,
        task_type=task_type,
        scope=scope,
        episode=episode_num,
    )
    return {"ok": True, "data": _image_generation_guard_payload(attempt_count, subject)}


@router.post("/projects/{project}/episodes/{episode_num}/image-generation-guard/verify-password")
async def verify_image_generation_guard_password(
    project: str,
    episode_num: int,
    body: OperatorPasswordVerifyRequest,
    user: dict = Depends(get_api_user),
):
    """Verify the same operator password NiceGUI requires after repeated image attempts."""
    from novelvideo.security.operator_auth import get_prompt_export_password

    _ = (project, episode_num, user)
    configured = get_prompt_export_password()
    verified = bool(configured) and (body.password or "") == configured
    return {
        "ok": True,
        "data": {"verified": verified},
    }


@router.post("/projects/{project}/episodes/{episode_num}/videos/compose")
async def compose_video(
    project: str,
    episode_num: int,
    body: VideoComposeRequest,
    user: dict = Depends(get_api_user),
):
    """合成成片。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        config = {
            "beats": beats,
            "add_subtitles": body.add_subtitles,
            "add_bgm": body.add_bgm,
        }

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="compose_episode",
                queue_kind="ffmpeg",
                episode=episode_num,
                payload={
                    **config,
                    "episode": episode_num,
                    "output_dir": output_dir,
                    "resolution": getattr(body, "resolution", "720x1280"),
                },
            )
            return {
                "ok": True,
                "task_type": "compose_episode",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key("compose_episode", ctx.project_id, episode_num),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集成片合成已进入队列",
            }

        return {"ok": False, "error": "成片合成需要 project context"}


@router.get("/projects/{project}/episodes/{episode_num}/final")
async def get_final_video(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """读取 episode 成片状态，供 supertale-fe compose 页刷新 hydration。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    filename = f"ep{episode_num:03d}_final.mp4"
    final_path = project_dir / "videos" / "episodes" / filename
    data = {"exists": final_path.exists(), "filename": filename}
    if final_path.exists():
        data["video_url"] = _generation.make_static_url_for_context(
            resolved.ctx,
            f"videos/episodes/{filename}",
            local_path=final_path,
        )
    return {"ok": True, "data": data}


# ── TTS 语音 ──────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/tts/generate")
async def generate_tts(
    project: str,
    episode_num: int,
    body: TTSGenerateRequest,
    user: dict = Depends(get_api_user),
):
    """Legacy TTS endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Legacy /tts/generate was removed. Use "
            f"/projects/{project}/episodes/{episode_num}/audio/generate for IndexTTS2."
        ),
    )


@router.post("/projects/{project}/tts/preview")
async def preview_tts(
    project: str,
    body: TTSPreviewRequest,
    user: dict = Depends(get_api_user),
):
    """Legacy TTS preview endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy /tts/preview was removed. IndexTTS2 uses configured reference voices.",
    )


@router.get("/projects/{project}/tts/voices")
async def list_tts_voices(project: str, user: dict = Depends(get_api_user)):
    """Legacy voice listing endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy /tts/voices was removed. IndexTTS2 voice options are project assets.",
    )


# ── 草图 ──────────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/sketches/generate")
async def generate_sketches(
    project: str,
    episode_num: int,
    body: SketchGenerateRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    """生成草图。"""
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

        # 提前验证 grid_index，避免异步任务内才报错
        from novelvideo.generators.grid_generation import (
            sketch_grid_split,
            sketch_scene_grid_split,
        )

        use_scene_grouping = body.sketch_scene_grouping
        if use_scene_grouping:
            loc_plan = sketch_scene_grid_split(beats, aspect_ratio=body.aspect_ratio)
            grid_plan = [(p["rows"], p["cols"]) for p in loc_plan]
        else:
            loc_plan = None
            grid_plan = sketch_grid_split(len(beats))

        generate_all_grids = body.grid_index == -1
        if body.grid_index < -1 or body.grid_index >= len(grid_plan):
            grid_labels = " + ".join(f"{r}x{c}" for r, c in grid_plan)
            return {
                "ok": False,
                "error": (
                    f"grid_index={body.grid_index} 超出范围。"
                    f"共 {len(beats)} 个 beats，分割方案: {grid_labels}，"
                    f"有效 grid_index: 0~{len(grid_plan) - 1}"
                ),
            }

        character_map = await _generation._build_character_map(
            store,
            beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=False,
        )
        has_colors = any(
            info.get("identity_sketch_colors") or info.get("sketch_color")
            for info in character_map.values()
        )
        if not has_colors:
            return {"ok": False, "error": "未检测到颜色分配，请先调用 assign-colors 接口"}

        from novelvideo.utils.path_resolver import PathResolver

        await run_in_threadpool(PathResolver(output_dir, episode_num).clean_sketches)

        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        sketch_image_selection = _generation._resolve_sketch_image_selection(
            proj_config,
            body.image_generation_selection,
        )
        base_config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "model": body.model,
            "sketch_scene_grouping": use_scene_grouping,
            "aspect_ratio": body.aspect_ratio,
            "image_generation_selection": sketch_image_selection,
            "sketch_colors": store.get_sketch_colors(episode_num) or {},
            "prop_menu": prop_menu,
        }

        dispatch_grid_indices = list(range(len(grid_plan))) if generate_all_grids else [body.grid_index]
        if ctx is not None:
            queued_tasks = []
            rejected: list[dict[str, Any]] = []
            for position, grid_index in enumerate(dispatch_grid_indices):
                scope = f"grid_{grid_index}"
                try:
                    queued = await _generation.get_task_backend().enqueue_project_task(
                        ctx,
                        product_surface="mainline",
                        task_type="sketch_grid_generation",
                        queue_kind="default",
                        episode=episode_num,
                        scope=scope,
                        payload={
                            "episode": episode_num,
                            "output_dir": output_dir,
                            "config": {**base_config, "grid_index": grid_index},
                        },
                    )
                except _FANOUT_ACTIVE_LIMIT_ERRORS as exc:
                    if not queued_tasks:
                        # 一个都没投出去＝纯粹超限：裸抛，交 api/app.py 的 handler
                        # 渲染 429 ＋ 正确的 limit_scope（M8 不变量 7）。
                        raise
                    _generation._log_partial_dispatch_rejection(exc)
                    # 闸是全局的，后面每一个都必然被拒 —— 所以**只投这一次**（break），
                    # 但要把「当前这条 ＋ 后面还没尝试的」全部如实记进 rejected：
                    # 契约要 N−k 条（M8 :722 / :755），下游按尾段长度对齐
                    # （render-plan-dialog.tsx:265 断言 entries.length == rejected.length）。
                    for pending_index in dispatch_grid_indices[position:]:
                        rejected.append(
                            _generation._task_limit_rejection(f"grid_{pending_index}", exc)
                        )
                    break
                queued_tasks.append(
                    {
                        "grid_index": grid_index,
                        "scope": scope,
                        "task_id": queued.task_state.task_id,
                        "task_key": project_task_state_key(
                            "sketch_grid_generation",
                            ctx.project_id,
                            episode_num,
                            scope=scope,
                        ),
                        "backend": queued.backend,
                        "queue": queued.queue,
                    }
                )
            if generate_all_grids:
                grid_labels = " + ".join(f"{r}x{c}" for r, c in grid_plan)
                return {
                    "ok": True,
                    "task_type": "sketch_grid_generation",
                    "backend": queued_tasks[0]["backend"] if queued_tasks else "inline",
                    "data": {
                        # 实投数，不是意图数（M8 不变量 17）。
                        "dispatched": len(queued_tasks),
                        "tasks": queued_tasks,
                        "scopes": [item["scope"] for item in queued_tasks],
                        "rejected": rejected,
                    },
                    "message": f"第 {episode_num} 集全集草图生成已进入队列 ({grid_labels})",
                }

            return {
                "ok": True,
                "task_type": "sketch_grid_generation",
                "backend": queued_tasks[0]["backend"],
                "task_id": queued_tasks[0]["task_id"],
                "task_key": queued_tasks[0]["task_key"],
                "queue": queued_tasks[0]["queue"],
                "message": f"第 {episode_num} 集草图生成已进入队列 (网格 {body.grid_index})",
            }

        return {"ok": False, "error": "草图生成需要 project context"}


# ── 语音生成 ──────────────────────────────────────────────────────────────────


async def _audio_generation_plan(
    *,
    store,
    username: str,
    project: str,
    episode: int,
    beat_numbers,
    mode: str,
) -> tuple[list[int], list[str]]:
    from novelvideo.audio.indextts2_beat_audio_task import (
        build_indextts2_audio_generation_plan,
    )

    try:
        plan = await build_indextts2_audio_generation_plan(
            store=store,
            username=username,
            project=project,
            episode=episode,
            beat_numbers=beat_numbers,
            mode=mode,
        )
        return list(plan.beat_numbers), list(plan.errors)
    except AttributeError:
        # Narrow route-test stores do not expose the voice and audio-state
        # surfaces used by the production planner.
        beats = await store.get_beats_as_dicts(episode)
        selected = {int(value) for value in beat_numbers or [] if int(value) > 0}
        planned = [
            int(beat.get("beat_number") or 0)
            for beat in beats
            if int(beat.get("beat_number") or 0) > 0
            and (not selected or int(beat.get("beat_number") or 0) in selected)
        ]
        return planned, []


def _voice_prereq_error_response(errors: list[str]) -> dict:
    preview = "；".join(errors[:5])
    suffix = " ..." if len(errors) > 5 else ""
    return {
        "ok": False,
        "code": "voice_prereq_required",
        "error": f"{preview}{suffix}",
    }


@router.post("/projects/{project}/episodes/{episode_num}/audio/generate")
async def generate_audio(
    project: str,
    episode_num: int,
    body: TTSGenerateRequest = TTSGenerateRequest(),
    user: dict = Depends(get_api_user),
):
    """批量生成语音（IndexTTS2）。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    state_dir = str(ctx.state_dir) if ctx else _generation.get_state_dir(username, project_name)
    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        mode = body.mode or "sync_changed"
        pending_beat_numbers, missing_voice = await _generation._audio_generation_plan(
            store=store,
            username=username,
            project=project_name,
            episode=episode_num,
            beat_numbers=body.beat_numbers,
            mode=mode,
        )
        if missing_voice:
            return _voice_prereq_error_response(missing_voice)
        if not pending_beat_numbers:
            return {
                "ok": False,
                "code": "audio_generation_not_required",
                "error": "没有需要生成的音频",
            }

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="audio_generation_indextts2",
                queue_kind="default",
                episode=episode_num,
                payload={
                    "episode": episode_num,
                    "mode": mode,
                    "beat_numbers": pending_beat_numbers,
                    "output_dir": output_dir,
                    "state_dir": state_dir,
                },
            )
            return {
                "ok": True,
                "task_type": "audio_generation_indextts2",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "audio_generation_indextts2", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集语音批量生成已进入队列",
            }

        return {
            "ok": False,
            "error": "音频生成需要 project context",
        }
