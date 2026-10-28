"""freezone.jobs 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneVideoComposeRequest
from novelvideo.freezone.audio_node import freezone_audio_eleven_music_output_path
from novelvideo.freezone.audio_node import freezone_audio_speech_output_path
# [aipdd fork delta req46] 音乐创作产物目录（mp3/wav/pcm 由用户所选格式决定）。
from novelvideo.freezone.minimax_music import music_output_path_for_job
from novelvideo.freezone.canvas_static_urls import migrate_canvas_static_urls_in_memory
from novelvideo.freezone.paths import output_path_for_job
from novelvideo.freezone.paths import outputs_dir
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.freezone.skill_registry import SkillErrorEnvelope
from novelvideo.freezone.skill_registry import SkillRunOutput
from novelvideo.freezone.skill_registry import SkillRunResult
from novelvideo.i18n_message import log_lines_text

from ._common import (TAG_FREEZONE_JOBS, TAG_FREEZONE_SKILLS, TAG_FREEZONE_VIDEO)

router = APIRouter()


@router.post(
    "/projects/{project}/freezone/video/compose",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_compose(
    project: str,
    body: FreezoneVideoComposeRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：按时间线描述异步导出成片。

    当前为 MVP 版本：
    - 支持顺序视频片段裁剪与拼接
    - 支持时间线空隙自动补黑场
    - 支持附加音频轨混音
    - 暂不支持重叠视频轨、转场和复杂特效
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.tracks:
        raise HTTPException(400, "tracks is required")

    resolved_tracks: list[dict] = []
    has_video_item = False
    for track in body.tracks:
        if not track.items:
            continue

        resolved_items: list[dict] = []
        for item in track.items:
            if item.source_end <= item.source_start:
                raise HTTPException(
                    400,
                    (
                        f"compose item {item.item_id} has invalid source range: "
                        "source_end must be > source_start"
                    ),
                )
            try:
                source_path = _freezone.resolve_static_url_to_path(item.source_url, project_dir)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not source_path.exists():
                raise HTTPException(404, f"compose source not found: {source_path}")

            resolved_item = item.model_dump()
            resolved_item["source_path"] = str(source_path)
            resolved_items.append(resolved_item)

        if not resolved_items:
            continue

        if track.kind == "video":
            has_video_item = True
        resolved_track = track.model_dump()
        resolved_track["items"] = resolved_items
        resolved_tracks.append(resolved_track)

    if not resolved_tracks:
        raise HTTPException(400, "tracks must contain at least one media item")
    if not has_video_item:
        raise HTTPException(400, "video compose requires at least one video item")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_compose",
                job_id=job_id,
                payload={
                    "title": body.title,
                    "canvas_id": body.canvas_id,
                    "resolution": body.resolution,
                    "fps": body.fps,
                    "background_color": body.background_color,
                    "keep_original_audio": body.keep_original_audio,
                    "tracks": resolved_tracks,
                    # [aipdd fork delta] 产物入库标记：合成成片是最终产物，提交方
                    # （画布/工具/agent）带标记时 runner 把结果登记进资产库。
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                },
            )
        _freezone._start_freezone_video_compose_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            body=body,
            resolved_tracks=resolved_tracks,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video compose task", exc)
        raise HTTPException(503, f"failed to start freezone video compose task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_compose",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.get(
    "/projects/{project}/freezone/jobs/{task_type}/{job_id}/result", tags=[TAG_FREEZONE_JOBS]
)
async def freezone_job_result(
    project: str,
    task_type: Literal[
        "freezone_gen",
        "freezone_edit",
        "freezone_upscale",
        "freezone_extract",
        "freezone_analyze",
        "freezone_video_story",
        "freezone_video_clone",
        "freezone_video_mix",
        # [aipdd fork delta] 白板动画成片（逐笔揭示 MP4），同走通用产物解析。
        "freezone_whiteboard_animation",
        # [aipdd fork delta] 科普讲解视频成片（逐帧代码绘制的横屏 MG），同走通用产物解析。
        "freezone_explainer_video",
        # [aipdd fork delta] 动效大片成片（按案例契约逐帧截图的 MG），同走通用产物解析。
        "freezone_motion_render",
        # [aipdd fork delta] 动效大片拍点预检：产物是一目录静帧 PNG，前端按同一套 job
        # 结果轮询取清单（`stills`），所以也要在这个 Literal 里挂号。
        "freezone_motion_stills",
        # [aipdd fork delta] 动效大片交付：产物是一盘剪映工程目录（不是可播放媒体），
        # 但前端按同一套 job 结果轮询取进度与落盘路径，所以要在这个 Literal 里挂号。
        "freezone_motion_draft",
        # [aipdd fork delta] Phase 2B 渠道短视频成片（复用讲解视频链，渠道画幅 MP4）。
        "freezone_channel_short_video",
        # [aipdd fork delta] 口播智能剪辑：直出 MP4 / 剪映草稿产物，同走通用产物解析。
        "freezone_narration_smart_edit",
        "freezone_video_idea",
        "freezone_video_gen",
        "freezone_mask_edit",
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_audio_separate",
        "freezone_audio_speech",
        # [aipdd fork delta] 替换人声：产物是目标音色新声的纯音频。
        "freezone_voice_replace",
        "freezone_audio_eleven_music",
        # [aipdd fork delta req46] 音乐创作（MiniMax Music 3.0 直连）：产物 mp3/wav/pcm。
        "freezone_music_generate",
        "freezone_video_compose",
        "freezone_image_reverse_prompt",
        "freezone_image_to_3gs",
        "freezone_text_generate",
        "freezone_text_translate",
        "freezone_story_script",
        # [aipdd fork delta] 公众号文章编写与排版（产物 JSON + HTML）。
        "freezone_article_write",
        # [aipdd fork delta] 短视频口播稿创作（工具栏首项，产物 JSON + 净台词 txt）。
        "freezone_oral_script",
        # [aipdd fork delta] 讲解类视频台词草稿（两段式第一段，产物是可编辑的草稿 JSON）。
        "freezone_explainer_script",
    ],
    job_id: str,
    user: dict = Depends(get_api_user),
):
    """任务完成后，通过这个接口解析最终产物 URL。

    前端通过 `/projects/{project_id}/tasks/stream` 的 SSE 知道任务是否完成；
    这个接口只负责把 `(task_type, job_id)` 翻译成实际的 `/static/...` URL。
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    if ctx is not None:
        task = await run_in_threadpool(
            _freezone.get_task_manager().get_task_for_project,
            ctx,
            task_type,
            0,
            scope=job_id,
        )
    else:
        task = await run_in_threadpool(
            _freezone.get_task_manager().get_task,
            task_type,
            username,
            project_name,
            0,
            scope=job_id,
        )
    if task_type == "freezone_image_to_3gs":
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": log_lines_text(task.logs[-10:]),
                }
            if task.status in {"pending", "starting", "running"}:
                return {
                    "ok": False,
                    "info": "job result not yet available",
                    "status": task.status,
                    "current_task": task.current_task,
                }
            if isinstance(task.result, dict):
                data = (
                    migrate_canvas_static_urls_in_memory(
                        task.result,
                        project_id=ctx.project_id,
                        owner_username=ctx.owner_username,
                        project_name=ctx.project_name,
                        project_dir=project_dir,
                    )
                    or task.result
                )
                splat_url = (
                    data.get("splat_url")
                    or data.get("ply_url")
                    or data.get("output_url")
                    or data.get("url")
                )
                for key in ("ply_path", "sog_path"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith(str(project_dir)):
                        try:
                            rel = Path(value).relative_to(project_dir).as_posix()
                        except ValueError:
                            continue
                        splat_url = _freezone.make_static_url_for_context(ctx, rel, local_path=value)
                        data[key] = splat_url
                if splat_url:
                    data.setdefault("output_url", splat_url)
                    data.setdefault("url", splat_url)
                    data.setdefault("ply_url", splat_url)
                    data.setdefault("splat_url", splat_url)
                    data.setdefault("media_type", "file")
                return {"ok": True, "data": data}

        artifact_dir = outputs_dir(project_dir, "freezone_image_to_3gs") / job_id
        candidates = sorted(artifact_dir.glob("*.sog")) or sorted(artifact_dir.glob("*.ply"))
        if candidates:
            out = candidates[0]
            rel = out.relative_to(project_dir).as_posix()
            url = _freezone.make_static_url_for_context(ctx, rel, local_path=out)
            suffix = out.suffix.lower().lstrip(".")
            return {
                "ok": True,
                "data": {
                    "url": url,
                    "output_url": url,
                    "ply_url": url,
                    "splat_url": url,
                    "ply_path": url,
                    "splat_format": suffix if suffix in {"ply", "sog"} else "unknown",
                    "media_type": "file",
                    "size": out.stat().st_size,
                },
            }
        return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}

    out = output_path_for_job(project_dir, task_type, job_id)
    if task_type == "freezone_image_reverse_prompt":
        out = _freezone._image_reverse_prompt_output_path(project_dir, job_id)
    if task_type == "freezone_video_erase":
        out = _freezone._video_erase_output_path(project_dir, job_id)
    if task_type == "freezone_video_upscale":
        out = _freezone._video_upscale_output_path(project_dir, job_id)
    if task_type == "freezone_audio_separate":
        audio_out = _freezone._audio_separate_audio_output_path(project_dir, job_id)
        mute_video_out = _freezone._audio_separate_mute_video_output_path(project_dir, job_id)
        if not mute_video_out.exists():
            if task is not None:
                if task.status == "failed":
                    return {
                        "ok": False,
                        "error": task.error or "job failed",
                        "status": task.status,
                        "logs": log_lines_text(task.logs[-10:]),
                    }
                if task.status in {"pending", "starting", "running"}:
                    return {
                        "ok": False,
                        "info": "job result not yet on disk",
                        "status": task.status,
                        "current_task": task.current_task,
                    }
            return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
        audio_rel = audio_out.relative_to(project_dir).as_posix() if audio_out.exists() else None
        mute_rel = mute_video_out.relative_to(project_dir).as_posix()
        task_result = getattr(task, "result", None) if task is not None else None
        push_metadata = {}
        if isinstance(task_result, dict):
            if task_result.get("pushable"):
                push_metadata["pushable"] = True
            if isinstance(task_result.get("slot_target"), dict):
                push_metadata["slot_target"] = task_result["slot_target"]
        return {
            "ok": True,
            "data": {
                "audio_url": _freezone.make_static_url_for_context(ctx, audio_rel) if audio_rel else None,
                "audio_size": audio_out.stat().st_size if audio_out.exists() else 0,
                "mute_video_url": _freezone.make_static_url_for_context(ctx, mute_rel),
                "mute_video_size": mute_video_out.stat().st_size,
                **push_metadata,
            },
        }
    if task_type == "freezone_audio_speech":
        out = freezone_audio_speech_output_path(project_dir, job_id)
    if task_type == "freezone_voice_replace":
        # [aipdd fork delta] runner 把零样本合成新声搬到本任务专属产物目录。
        out = outputs_dir(project_dir, "freezone_voice_replace") / f"{job_id}.mp3"
    if task_type == "freezone_audio_eleven_music":
        out = freezone_audio_eleven_music_output_path(project_dir, job_id)
    if task_type == "freezone_music_generate":
        # [aipdd fork delta req46] 下面的后缀兜底循环只试图片/视频后缀，音乐得先换到
        # 自己的产物目录（用户可选 mp3/wav/pcm，落盘格式在叶里定）。
        out = music_output_path_for_job(project_dir, job_id)
    if task_type == "freezone_video_compose":
        out = _freezone._video_compose_output_path(project_dir, job_id)
    if task_type == "freezone_text_translate":
        out = _freezone._text_translate_output_path(project_dir, job_id)
    if task_type == "freezone_text_generate":
        out = _freezone._text_generate_output_path(project_dir, job_id)
    if task_type == "freezone_story_script":
        out = _freezone._story_script_output_path(project_dir, job_id)
    if task_type == "freezone_article_write":
        out = _freezone._article_write_output_path(project_dir, job_id)
    if task_type == "freezone_oral_script":
        out = _freezone._oral_script_output_path(project_dir, job_id)
    if task_type == "freezone_explainer_script":
        out = _freezone._explainer_script_output_path(project_dir, job_id)
    if task_type in {"freezone_analyze", "freezone_video_story"}:
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": log_lines_text(task.logs[-10:]),
                }
            if task.status != "completed":
                return {
                    "ok": False,
                    "info": "job result not yet available",
                    "status": task.status,
                    "current_task": task.current_task,
                }
        task_result = getattr(task, "result", None) if task is not None else None
        if isinstance(task_result, dict):
            if task_type == "freezone_video_story":
                task_result = _freezone._public_freezone_video_story_result(task_result)
            return {"ok": True, "data": task_result}
        analysis_out = outputs_dir(project_dir, "freezone_analyze") / job_id / "analysis.json"
        if analysis_out.exists():
            data = json.loads(analysis_out.read_text(encoding="utf-8"))
            if task_type == "freezone_video_story" and isinstance(data, dict):
                data = _freezone._public_freezone_video_story_result(data)
            return {"ok": True, "data": data}
    if not out.exists():
        for suffix in (".webp", ".mp4", ".mov", ".webm"):
            candidate = out.with_suffix(suffix)
            if candidate.exists():
                out = candidate
                break
    if not out.exists():
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": log_lines_text(task.logs[-10:]),
                }
            if task.status in {"pending", "starting", "running"}:
                return {
                    "ok": False,
                    "info": "job result not yet on disk",
                    "status": task.status,
                    "current_task": task.current_task,
                }
        return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
    if task_type in {
        "freezone_image_reverse_prompt",
        "freezone_text_generate",
        "freezone_text_translate",
        "freezone_story_script",
        "freezone_article_write",
        "freezone_oral_script",
        # 草稿 JSON 直接当结果读：它没有 Url 产物，渲染链只认草稿本体。
        "freezone_explainer_script",
    }:
        return {"ok": True, "data": json.loads(out.read_text(encoding="utf-8"))}
    rel = out.relative_to(project_dir).as_posix()
    task_result = getattr(task, "result", None) if task is not None else None
    push_metadata = {}
    if isinstance(task_result, dict):
        if task_result.get("pushable"):
            push_metadata["pushable"] = True
        if isinstance(task_result.get("slot_target"), dict):
            push_metadata["slot_target"] = task_result["slot_target"]
    return {
        "ok": True,
        "data": {
            "url": _freezone.make_static_url_for_context(ctx, rel, local_path=out),
            "size": out.stat().st_size,
            **push_metadata,
        },
    }


@router.get(
    "/projects/{project}/freezone/skills/runs/{run_id}/result",
    response_model=SkillRunResult,
    tags=[TAG_FREEZONE_SKILLS],
)
async def freezone_skill_run_result(
    project: str,
    run_id: str,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    metadata = _freezone._read_skill_run_metadata(project_dir, run_id)
    if isinstance(metadata.get("outputs"), list):
        return SkillRunResult(
            run_id=run_id,
            status="done" if metadata.get("status") == "completed" else str(metadata.get("status")),
            outputs=[SkillRunOutput(**item) for item in metadata["outputs"]],
            task_key=metadata.get("task_key"),
            task_type=metadata.get("task_type"),
            job_id=metadata.get("job_id"),
        )

    task_type = str(metadata.get("task_type") or "")
    job_id = str(metadata.get("job_id") or "")
    if not task_type or not job_id:
        _freezone._raise_skill_error(
            500,
            code="skill_run_metadata_incomplete",
            category="runtime",
            message="skill run metadata missing task_type/job_id",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect stored run metadata.",
        )
    try:
        task_episode = int(metadata.get("task_episode") or 0)
    except (TypeError, ValueError):
        task_episode = 0
    task_scope = str(metadata.get("task_scope") or job_id)
    task_beat_num_raw = metadata.get("task_beat_num")
    try:
        task_beat_num = int(task_beat_num_raw) if task_beat_num_raw is not None else None
    except (TypeError, ValueError):
        task_beat_num = None
    if ctx is not None:
        task = await run_in_threadpool(
            _freezone.get_task_manager().get_task_for_project,
            ctx,
            task_type,
            task_episode,
            beat_num=task_beat_num,
            scope=task_scope,
        )
    else:
        task = await run_in_threadpool(
            _freezone.get_task_manager().get_task,
            task_type,
            username,
            project_name,
            task_episode,
            beat_num=task_beat_num,
            scope=task_scope,
        )
    task_status = getattr(task, "status", None)
    if task is not None and task_status == "failed":
        return SkillRunResult(
            run_id=run_id,
            status="failed",
            outputs=[],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
            error=SkillErrorEnvelope(
                code="skill_run_failed",
                category="runtime",
                message=task.error or "job failed",
                retryable=False,
                user_action_hint="Review the failed job logs before retrying.",
            ),
        )
    if task_status != "completed":
        return SkillRunResult(
            run_id=run_id,
            status=_freezone._skill_run_status_from_task_status(task_status),
            outputs=[],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    task_result = getattr(task, "result", None) if task is not None else None
    output_metadata = dict(metadata.get("output") or {})
    nested_outputs = _freezone._normalize_task_result_outputs(
        task_result=task_result,
        output_metadata=output_metadata,
        project_dir=project_dir,
        ctx=ctx,
        username=username,
        project_name=project_name,
    )
    if nested_outputs:
        finalized_outputs = await _freezone._finalize_skill_run_outputs(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            metadata=metadata,
            outputs=[item.model_dump(mode="json") for item in nested_outputs],
            user=user,
        )
        return SkillRunResult(
            run_id=run_id,
            status="done",
            outputs=[SkillRunOutput(**item) for item in finalized_outputs],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    image_url = _freezone._extract_result_image_url(task_result)
    if not image_url:
        image_url = _freezone._static_url_for_task_result_path(
            task_result=task_result,
            project_dir=project_dir,
            ctx=ctx,
            username=username,
            project_name=project_name,
        )
    if not image_url and getattr(task, "status", None) == "completed":
        image_url = _freezone._static_url_for_skill_slot_target(
            output_metadata=output_metadata,
            project_dir=project_dir,
            ctx=ctx,
        )
    out = _freezone._skill_output_path_for_job(project_dir, task_type, job_id)
    if not image_url and out is not None:
        rel = out.relative_to(project_dir).as_posix()
        image_url = _freezone.make_static_url_for_context(ctx, rel, local_path=out)
    if image_url:
        finalized_outputs = await _freezone._finalize_skill_run_outputs(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            metadata=metadata,
            outputs=[{**output_metadata, "image_url": image_url}],
            user=user,
        )
        return SkillRunResult(
            run_id=run_id,
            status="done",
            outputs=[SkillRunOutput(**item) for item in finalized_outputs],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    return SkillRunResult(
        run_id=run_id,
        status=_freezone._skill_run_status_from_task_status(task_status),
        outputs=[],
        task_key=metadata.get("task_key"),
        task_type=task_type,
        job_id=job_id,
    )
