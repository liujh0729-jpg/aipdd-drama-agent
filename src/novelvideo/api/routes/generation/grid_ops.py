"""generation.grid_ops 子模块（自 api/routes/generation.py 拆分）。"""

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import GlobalOptimizeRequest
from novelvideo.api.schemas import GridRegenerateRequest
from novelvideo.manual_shots import pick_beats_by_number
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.source_language import detect_episode_asset_language

router = APIRouter()


# ── 视频优化 ──────────────────────────────────────────────────────────────────


def _global_optimize_pending_beat_numbers(
    *, output_dir: str, episode_num: int, beats: list[dict]
) -> list[int]:
    """Return Beat numbers with a canonical sketch, matching the worker path."""
    from novelvideo.utils.path_resolver import PathResolver

    sketches_dir = PathResolver(output_dir, episode_num).sketches_dir()
    pending: list[int] = []
    for beat in beats:
        beat_num = int(beat.get("beat_number") or 0)
        if beat_num <= 0:
            continue
        if any(
            (sketches_dir / f"beat_{beat_num:02d}.{ext}").exists()
            for ext in ("png", "jpg")
        ):
            pending.append(beat_num)
    return pending


@router.post("/projects/{project}/episodes/{episode_num}/optimize/video-global")
async def global_optimize_video(
    project: str,
    episode_num: int,
    body: GlobalOptimizeRequest = GlobalOptimizeRequest(),
    user: dict = Depends(get_api_user),
):
    """全局视频提示词优化（草图 → AI 自由决策每个 beat 的 i2v/k2v 模式）。

    输出语言由作者保存的当前剧本文本决定，不受界面语言影响。
    """
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir

    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        language = await detect_episode_asset_language(
            store,
            episode_num,
            fallback_text="\n".join(
                str(beat.get(field) or "")
                for beat in beats
                for field in ("narration_segment", "dialogue", "visual_description")
            ),
        )

        # [aipdd fork delta] WP-D: 报价环节已随计费体系拆除；这里守的仍是「预检与入队同一统计口径」。
        pending_beat_numbers = _global_optimize_pending_beat_numbers(
            output_dir=output_dir,
            episode_num=episode_num,
            beats=beats,
        )
        pending_beat_count = len(pending_beat_numbers)
        if pending_beat_count <= 0:
            return {"ok": False, "error": "没有草图，请先生成草图再执行全局优化"}

        characters = store.get_all_characters()
        char_list = [
            {
                "name": c.name,
                "gender": c.gender,
                "body_type": getattr(c, "body_type", ""),
                "role": c.role,
                "is_main": getattr(c, "is_main", False),
                "face_prompt": c.face_prompt,
            }
            for c in characters
        ]

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="global_optimize_video",
                queue_kind="default",
                episode=episode_num,
                payload={
                    "episode": episode_num,
                    "beats": beats,
                    "characters": char_list,
                    "output_dir": output_dir,
                    "language": language,
                    "beat_numbers": pending_beat_numbers,
                },
            )
            return {
                "ok": True,
                "task_type": "global_optimize_video",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "global_optimize_video", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集全局视频优化已进入队列",
            }

        return {"ok": False, "error": "全局视频优化需要 project context"}


# ── 再生 ──────────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/regenerate")
async def regenerate_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """重新生成单个网格。"""
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

        # 验证 grid_index 范围
        character_map = await _generation._build_character_map(
            store,
            beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )

        if body.character_grouping:
            from novelvideo.generators.grid_generation import character_grid_split

            char_plan = character_grid_split(beats, character_map)
            max_grids = len(char_plan)
            if grid_index < 0 or grid_index >= max_grids:
                grid_labels = " + ".join(
                    f'{e["rows"]}x{e["cols"]}(comp={e.get("composite_count","?")})' for e in char_plan
                )
                return {
                    "ok": False,
                    "error": (
                        f"grid_index={grid_index} 超出范围。"
                        f"角色分组方案: {grid_labels}，"
                        f"有效 grid_index: 0~{max_grids - 1}"
                    ),
                }
            selected_beat_numbers = [int(beat) for beat in char_plan[grid_index].get("beat_numbers", [])]
        elif body.scene_grouping:
            from novelvideo.generators.grid_generation import scene_grid_split

            loc_plan = scene_grid_split(beats, character_map=character_map)
            max_grids = len(loc_plan)
            if grid_index < 0 or grid_index >= max_grids:
                grid_labels = " + ".join(f'{e["rows"]}x{e["cols"]}({e["scene_id"]})' for e in loc_plan)
                return {
                    "ok": False,
                    "error": (
                        f"grid_index={grid_index} 超出范围。"
                        f"场景分组方案: {grid_labels}，"
                        f"有效 grid_index: 0~{max_grids - 1}"
                    ),
                }
            selected_beat_numbers = [int(beat) for beat in loc_plan[grid_index].get("beat_numbers", [])]
        else:
            from novelvideo.generators.grid_generation import (
                perfect_grid_split,
                REGEN_MODE_CONFIGS as _RMC,
            )

            grid_plan = perfect_grid_split(len(beats))
            if grid_index < 0 or grid_index >= len(grid_plan):
                grid_labels = " + ".join(f'{_RMC[mk]["rows"]}x{_RMC[mk]["cols"]}' for mk in grid_plan)
                return {
                    "ok": False,
                    "error": (
                        f"grid_index={grid_index} 超出范围。"
                        f"共 {len(beats)} 个 beats，分割方案: {grid_labels}，"
                        f"有效 grid_index: 0~{len(grid_plan) - 1}"
                    ),
                }
            start_offset = sum(_RMC[mk]["capacity"] for mk in grid_plan[:grid_index])
            capacity = _RMC[grid_plan[grid_index]]["capacity"]
            selected_beat_numbers = [
                int(beat.get("beat_number", index + 1))
                for index, beat in enumerate(beats[start_offset : start_offset + capacity], start_offset)
            ]

        selected_beats = pick_beats_by_number(beats, selected_beat_numbers)
        detection_error = _generation.render_ai_detection_error(selected_beats)
        if detection_error:
            return {"ok": False, "error": detection_error}

        config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "model": body.model,
            "image_generation_selection": render_image_selection,
            "render_mode": "Render",
            "scene_grouping": body.scene_grouping,
            "character_grouping": body.character_grouping,
            "sketch_aspect_padding": _generation._resolve_render_bool_setting(
                proj_config,
                "sketch_aspect_padding",
                body.sketch_aspect_padding,
                True,
            ),
        }

        scope = f"grid_{grid_index}"
        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="grid_regenerate",
                queue_kind="default",
                episode=episode_num,
                scope=scope,
                payload={
                    "episode": episode_num,
                    "grid_index": grid_index,
                    "output_dir": output_dir,
                    "config": config,
                },
            )
            return {
                "ok": True,
                "task_type": "grid_regenerate",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "grid_regenerate", ctx.project_id, episode_num, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集网格 {grid_index} 重新生成已进入队列",
            }

        return {"ok": False, "error": "网格重新生成需要 project context"}
