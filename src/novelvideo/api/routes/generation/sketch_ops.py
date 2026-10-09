"""generation.sketch_ops 子模块（自 api/routes/generation.py 拆分）。"""

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation

from ._common import _FANOUT_ACTIVE_LIMIT_ERRORS

router = APIRouter()


@router.get("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor")
async def get_sketch_pose_editor(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-compatible pose editor payload for a canonical sketch."""
    from PIL import Image
    from novelvideo.services.sketch_pose_service import (
        POSE_PRESETS,
        SKELETON_EDGES,
        build_all_episode_candidates,
        build_pose_candidates,
        _heuristic_pose_from_bbox,
    )

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    sketch_path = _generation._canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)
        beat = next((b for b in beats if int(b.get("beat_number", 0) or 0) == beat_num), None)
        if beat is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"Beat {beat_num} 不存在"},
            )

        sketch_colors = store.get_sketch_colors(episode_num) or {}
        candidates = build_pose_candidates(beat, sketch_colors)
        if not candidates:
            candidates = build_all_episode_candidates(sketch_colors)
        if not candidates:
            return {"ok": False, "error": "本集没有分配颜色的身份，请先重新配色"}

        with Image.open(sketch_path) as image:
            width, height = image.size

        skeletons: list[dict[str, Any]] = []
        total = len(candidates)
        margin = width * 0.15
        spacing = (width - 2 * margin) / max(1, total - 1) if total > 1 else 0
        for idx, candidate in enumerate(candidates):
            cx = int(margin + idx * spacing) if total > 1 else width // 2
            bw = max(40, int(width * 0.15))
            bh = max(80, int(height * 0.65))
            cy = int(height * 0.1)
            bbox = (cx - bw // 2, cy, cx + bw // 2, cy + bh)
            pose_data = _heuristic_pose_from_bbox(bbox, (width, height))
            skeletons.append(
                {
                    "identityId": candidate.identity_id,
                    "colorHex": candidate.color_hex,
                    "colorName": candidate.color_name,
                    "joints": pose_data["joints"],
                    "lineWidth": pose_data.get("line_width", 3),
                    "headRadius": pose_data.get("head_radius", 12),
                    "visible": False,
                    "active": idx == 0,
                }
            )

        return {
            "ok": True,
            "data": {
                "beat_num": beat_num,
                "sketch_url": _generation._canonical_sketch_url(resolved.ctx, project_dir, episode_num, beat_num),
                "width": width,
                "height": height,
                "candidates": [
                    {
                        "identity_id": candidate.identity_id,
                        "color_hex": candidate.color_hex,
                        "color_name": candidate.color_name,
                    }
                    for candidate in candidates
                ],
                "skeleton_edges": SKELETON_EDGES,
                "pose_presets": POSE_PRESETS,
                "skeletons": skeletons,
            },
        }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor")
async def save_sketch_pose_editor(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Persist pose editor strokes/skeletons back to the canonical sketch."""
    from novelvideo.services.sketch_pose_service import save_pose_editor_state

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    sketch_path = _generation._canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    try:
        save_pose_editor_state(str(sketch_path), body)
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"保存草图编辑失败: {exc}"},
        )

    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "sketch_url": _generation._canonical_sketch_url(resolved.ctx, project_dir, episode_num, beat_num),
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/crop")
async def crop_current_sketch(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Crop and overwrite the canonical sketch, matching NiceGUI current-image crop."""
    from PIL import Image

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    sketch_path = _generation._canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    try:
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
        width = int(body.get("width", 0))
        height = int(body.get("height", 0))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "裁剪参数无效"},
        )
    if width <= 0 or height <= 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "裁剪宽高必须大于 0"},
        )

    with Image.open(sketch_path).convert("RGBA") as image:
        crop_x = max(0, min(x, image.width - 1))
        crop_y = max(0, min(y, image.height - 1))
        right = min(crop_x + width, image.width)
        bottom = min(crop_y + height, image.height)
        cropped = image.crop((crop_x, crop_y, right, bottom))
        cropped.save(sketch_path, format="PNG")

    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "sketch_url": _generation._canonical_sketch_url(resolved.ctx, project_dir, episode_num, beat_num),
            "width": cropped.width,
            "height": cropped.height,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/sketches/generate-missing-manual")
async def generate_missing_manual_sketches(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Dispatch sketch regen only for manually inserted beats missing sketches.

    This scans `is_manual_shot=True` beats whose canonical sketch file does not
    exist, groups adjacent missing manual beats by scene, and dispatches one
    `sketch_regen` task per group. Normal beats are never regenerated here.
    """
    from novelvideo.manual_shots import (
        choose_manual_sketch_mode_key,
        missing_manual_shot_segments,
        storyboard_beats_for_manual_sketches,
    )
    from novelvideo.task_identity import selection_scope

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    output_dir = resolved.output_dir
    sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"

    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"第 {episode_num} 集没有 beats"}

        storyboard_beats = storyboard_beats_for_manual_sketches(beats)
        segments = missing_manual_shot_segments(storyboard_beats, sketches_dir)
        if not segments:
            return {
                "ok": True,
                "data": {"dispatched": 0, "scopes": [], "segments": []},
                "message": "没有缺草图的手工分镜",
            }

        proj_config = _generation.load_project_config(username, project_name)
        style = proj_config.get("visual_style", "chinese_period_drama")
        sketch_image_selection = _generation._resolve_sketch_image_selection(proj_config)
        character_map = await _generation._build_character_map(
            store,
            beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=False,
        )
        sketch_colors = store.get_sketch_colors(episode_num) or {}

        dispatched_scopes: list[str] = []
        dispatched_segments: list[list[int]] = []
        rejected: list[dict[str, Any]] = []
        for position, beat_numbers in enumerate(segments):
            beat_indices = [int(n) for n in beat_numbers]
            mode_key = choose_manual_sketch_mode_key(len(beat_indices))
            config = {
                "beats": beats,
                "character_map": character_map,
                "style": style,
                "model": None,
                "image_generation_selection": sketch_image_selection,
                "selected_beat_numbers": beat_indices,
                "composite_key": f"{mode_key}:sketch",
                "sketch_colors": sketch_colors,
            }
            scope = selection_scope(mode_key, beat_indices)
            if ctx is not None:
                try:
                    await _generation.get_task_backend().enqueue_project_task(
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
                except _FANOUT_ACTIVE_LIMIT_ERRORS as exc:
                    if not dispatched_scopes:
                        # k == 0：裸抛交 handler 渲染 429（M8 不变量 7）。
                        raise
                    _generation._log_partial_dispatch_rejection(exc)
                    # 只投这一次，但把未投的尾段逐条如实上报（N−k 条，M8 :722 / :755）。
                    for pending in segments[position:]:
                        pending_beats = [int(n) for n in pending]
                        pending_mode_key = choose_manual_sketch_mode_key(len(pending_beats))
                        rejected.append(
                            _generation._task_limit_rejection(
                                selection_scope(pending_mode_key, pending_beats), exc
                            )
                        )
                    break
                dispatched_scopes.append(scope)
                dispatched_segments.append(beat_indices)
                continue

            return {
                "ok": False,
                "error": f"分段 {beat_indices} 派发失败: 需要 project context",
                "data": {
                    "dispatched": len(dispatched_scopes),
                    "scopes": dispatched_scopes,
                    "segments": dispatched_segments,
                },
            }

        return {
            "ok": True,
            "task_type": "sketch_regen",
            "data": {
                "dispatched": len(dispatched_segments),
                "scopes": dispatched_scopes,
                "segments": dispatched_segments,
                "rejected": rejected,
            },
            "message": f"已启动 {len(dispatched_segments)} 组新增分镜草图生成",
        }
