"""generation.video_ops 子模块（自 api/routes/generation.py 拆分）。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import SingleVideoRequest
from novelvideo.task_identity import project_task_state_key

router = APIRouter()


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/video")
async def generate_single_video(
    project: str,
    episode_num: int,
    beat_num: int,
    body: SingleVideoRequest,
    user: dict = Depends(get_api_user),
):
    """单 Beat 视频再生。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir

    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        # 加载 beat 数据
        beats = await store.get_beats_as_dicts(episode_num)
        beat = next((b for b in beats if b.get("beat_number") == beat_num), None)
        if not beat:
            return {"ok": False, "error": f"Beat {beat_num} not found"}
        backend_error = _generation._validate_seedance_pro_dialogue_only([beat], body.video_backend)
        if backend_error:
            return {"ok": False, "error": backend_error}
        is_seedance2 = _generation._is_seedance2_backend(body.video_backend)
        is_happyhorse = _generation._is_happyhorse_backend(body.video_backend)
        is_grok_video = _generation._is_grok_video_backend(body.video_backend)

        # 首帧路径
        from novelvideo.utils.path_resolver import PathResolver

        paths = PathResolver(output_dir, episode_num)
        frame_path = paths.first_frame_for_video(
            beat_num,
            use_director_render=bool(body.use_director_render),
        )
        if not frame_path.exists():
            return {"ok": False, "error": f"Beat {beat_num} 首帧不存在，请先生成预览"}

        # 视频模式与提示词
        video_mode = beat.get("video_mode", "first_frame")
        prompt = _generation._legacy_video_prompt_for_mode(beat, video_mode)

        # 音频时长
        from novelvideo.manual_shots import resolve_target_video_duration

        audio_duration = await _generation._api_audio_duration_seconds(output_dir, episode_num, beat_num)
        video_duration = resolve_target_video_duration(beat, audio_duration)

        # 尾帧路径 (keyframe 模式)
        last_frame_path = None
        if video_mode == "keyframe":
            next_frame = paths.first_frame_for_video(
                beat_num + 1,
                use_director_render=bool(body.use_director_render),
            )
            if next_frame.exists():
                last_frame_path = str(next_frame)
            else:
                video_mode = "first_frame"  # 回退
                prompt = _generation._legacy_video_prompt_for_mode(beat, video_mode)

        seedance2_config_json = None
        single_video_resolution: str | None = None
        happyhorse_references: list[dict[str, str]] = []
        happyhorse_ratio: str | None = None
        grok_video_references: list[dict[str, str]] = []
        grok_video_ratio: str | None = None
        if is_seedance2:
            try:
                request_config_json = _generation._merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_generation._seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _generation._episode_from_store_or_none(store, episode_num)
                prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
                prepared = await _generation._prepare_seedance2_api_beat(
                    store=store,
                    output_dir=output_dir,
                    episode=episode_num,
                    beat=beat,
                    all_beats=beats,
                    index=beat_index,
                    video_backend=body.video_backend,
                    resolution=body.resolution if "resolution" in body.model_fields_set else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            prompt = prepared.prompt
            video_duration = prepared.duration
            frame_path = Path(prepared.image_path) if prepared.image_path else frame_path
            last_frame_path = prepared.last_frame_path
            seedance2_config_json = prepared.seedance2_config_json
            from novelvideo.seedance2_i2v.models import parse_seedance2_config

            single_video_resolution = parse_seedance2_config(
                seedance2_config_json
            ).resolution
            video_mode = "keyframe" if prepared.last_frame_path else "first_frame"
        elif is_happyhorse:
            try:
                request_config_json = _generation._merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_generation._seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _generation._episode_from_store_or_none(store, episode_num)
                prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
                prepared = await _generation._prepare_happyhorse_api_beat(
                    output_dir=output_dir,
                    state_dir=Path(store.state_dir),
                    episode=episode_num,
                    beat=beat,
                    next_beat=beats[beat_index + 1] if beat_index + 1 < len(beats) else None,
                    frame_path=frame_path,
                    video_mode=video_mode,
                    prompt=prompt,
                    duration=video_duration,
                    resolution=body.resolution if "resolution" in body.model_fields_set else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                )
                if prepared["config_json"] and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=str(prepared["config_json"]),
                    )
                prompt = str(prepared["prompt"])
                video_duration = float(prepared["duration"])
                frame_path = Path(str(prepared["image_path"])) if prepared["image_path"] else None
                last_frame_path = None
                seedance2_config_json = str(prepared["config_json"])
                single_video_resolution = str(prepared["resolution"])
                happyhorse_ratio = str(prepared["ratio"])
                happyhorse_references = list(prepared.get("references") or [])
                video_mode = "first_frame"
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        elif is_grok_video:
            try:
                request_config_json = _generation._merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_generation._seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _generation._episode_from_store_or_none(store, episode_num)
                prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
                prepared = await _generation._prepare_grok_video_api_beat(
                    output_dir=output_dir,
                    state_dir=Path(store.state_dir),
                    episode=episode_num,
                    beat=beat,
                    next_beat=beats[beat_index + 1] if beat_index + 1 < len(beats) else None,
                    frame_path=frame_path,
                    video_mode=video_mode,
                    prompt=prompt,
                    duration=video_duration,
                    resolution=body.resolution if "resolution" in body.model_fields_set else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                )
                if prepared["config_json"] and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=str(prepared["config_json"]),
                    )
                prompt = str(prepared["prompt"])
                video_duration = float(prepared["duration"])
                frame_path = Path(str(prepared["image_path"])) if prepared["image_path"] else None
                last_frame_path = None
                seedance2_config_json = str(prepared["config_json"])
                single_video_resolution = str(prepared["resolution"])
                grok_video_ratio = str(prepared["ratio"])
                grok_video_references = list(prepared.get("references") or [])
                video_mode = "first_frame"
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            if not prompt.strip():
                return {"ok": False, "error": _generation._missing_video_prompt_error(beat_num)}
            # 非 seedance2 后端（含 seedance-1.5-pro）：透传用户选择的时长/清晰度，
            # 并保证视频时长不短于音频（与 1.0 的 duration_floor 行为一致；
            # 生成器侧再按模型上限 4-12 夹紧并向上取整）。
            import math

            if body.duration is not None:
                try:
                    video_duration = float(body.duration)
                except (TypeError, ValueError):
                    pass
            if audio_duration:
                video_duration = max(float(video_duration), float(math.ceil(float(audio_duration))))
            if "resolution" in body.model_fields_set:
                # [aipdd fork delta] 通用后端不再按写死的 seedance2 名称表夹紧，
                # 前端控件只下发目录声明的档位，这里只做像素形式→p 形式的归一。
                clean_resolution = str(body.resolution or "").strip()
                if clean_resolution:
                    single_video_resolution = (
                        clean_resolution
                        if clean_resolution in {"480p", "720p", "1080p"}
                        else _generation._seedance2_api_resolution(clean_resolution)
                    )

    from novelvideo.usage.video_duration import normalize_video_duration_for_backend

    video_duration = normalize_video_duration_for_backend(
        body.video_backend,
        video_duration,
    )

    config = {
        "beat": dict(beat),
        "frame_path": str(frame_path) if frame_path else None,
        "video_mode": video_mode,
        "prompt": prompt,
        "video_duration": video_duration,
        "video_backend": body.video_backend,
        "use_director_render": bool(body.use_director_render),
        "last_frame_path": last_frame_path,
        "cognee_store_project": f"{username}/{project_name}",
    }
    if seedance2_config_json:
        config["seedance2_config"] = seedance2_config_json
    if single_video_resolution:
        config["resolution"] = single_video_resolution
    if is_happyhorse:
        config["ratio"] = _generation._happyhorse_ratio_for_backend(happyhorse_ratio)
        config["references"] = happyhorse_references
        if body.audio_setting is not None:
            config["audio_setting"] = body.audio_setting
    if is_grok_video:
        config["ratio"] = _generation._grok_video_ratio_for_backend(grok_video_ratio)
        config["references"] = grok_video_references
    # [aipdd fork delta] 通用后端（非 seedance2/happyhorse/grok）也要把比例透传给渲染 runner：
    # runner 的通用路径走 ``_resolve_video_aspect_ratio(config.get("ratio"), ...)``，此前因路由
    # 不塞 ratio 而只能落在 adaptive；前端目录控件选出的比例需据此下发。
    if (
        not is_seedance2
        and not is_happyhorse
        and not is_grok_video
        and "ratio" in body.model_fields_set
        and str(body.ratio or "").strip()
    ):
        config["ratio"] = str(body.ratio).strip()


    if ctx is not None:
        queued = await _generation.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="mainline",
            task_type="single_video",
            queue_kind="video",
            episode=episode_num,
            beat_num=beat_num,
            payload={"config": config, "output_dir": output_dir},
        )
        return {
            "ok": True,
            "task_type": "single_video",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "single_video",
                ctx.project_id,
                episode_num,
                beat_num=beat_num,
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"第 {episode_num} 集 Beat {beat_num} 视频生成已入队",
        }

    return {"ok": False, "error": "单条视频生成需要 project context"}
