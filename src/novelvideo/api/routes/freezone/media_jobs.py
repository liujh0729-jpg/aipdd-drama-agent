"""freezone.media_jobs 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from novelvideo.api.routes import freezone as _freezone
from novelvideo.freezone.model_selection import (
    find_unusable_chat_model,
    unusable_chat_model_detail,
)
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.project_context import ProjectContext


def _chat_model_rejection(*, task_type: str, payload: dict) -> str:
    """[aipdd fork delta] req39：入队前拦掉「实测已判死」的 chat 模型选型。

    返回给用户看的 400 文案，没有命中（含一切读不到缓存的异常）就返回空串。
    放在这里而不是各条路由里：所有 freezone 后台任务都经下面的漏斗入队，工具页
    与画布 smart-edit 节点走的是同一个收口点，口径不会漂。
    """

    try:
        hit = find_unusable_chat_model(task_type=task_type, payload=payload or {})
    except Exception:  # noqa: BLE001 - 校验本身出错绝不挡正常提交
        return ""
    if not hit:
        return ""
    provider, call_name, verdict = hit
    return unusable_chat_model_detail(call_name, verdict, provider=provider)



async def _enqueue_or_start_freezone_video_analysis(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    task_type: Literal["freezone_extract", "freezone_analyze", "freezone_video_story"],
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            payload=payload,
            queue_kind="ffmpeg" if task_type != "freezone_analyze" else "default",
        )

    _freezone._raise_project_context_required(task_type)


async def _enqueue_or_start_freezone_video_clone(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "video",
            str(
                payload.get("catalog_id")
                or payload.get("model_id")
                or payload.get("backend")
                or ""
            ),
            requester_user_id=ctx.requester_user_id,
        )
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_video_clone",
            job_id=job_id,
            payload=payload,
            queue_kind="video",
        )

    _freezone._raise_project_context_required("freezone_video_clone")


# [aipdd fork delta] 混剪短视频素材类型识别（图片/视频后缀与画布上传口径一致）。
_MIX_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MIX_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
# 单次混剪素材池上限（防御性：超大列表没有真实用例，且会拖慢探时长）。
_MIX_MAX_MATERIALS = 60


async def _enqueue_or_start_freezone_video_mix(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_video_mix",
            job_id=job_id,
            payload=payload,
            queue_kind="video",
        )

    _freezone._raise_project_context_required("freezone_video_mix")


async def _enqueue_or_start_freezone_whiteboard_animation(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_whiteboard_animation",
            job_id=job_id,
            payload=payload,
            queue_kind="video",
        )

    _freezone._raise_project_context_required("freezone_whiteboard_animation")


async def _enqueue_or_start_freezone_explainer_video(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    """[aipdd fork delta] 科普讲解视频：画面渲染是长任务，走 video 队列。"""
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_explainer_video",
            job_id=job_id,
            payload=payload,
            queue_kind="video",
        )

    _freezone._raise_project_context_required("freezone_explainer_video")


async def _enqueue_or_start_freezone_video_idea(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_video_idea",
            job_id=job_id,
            payload=payload,
            queue_kind="video",
        )

    _freezone._raise_project_context_required("freezone_video_idea")


async def _enqueue_or_start_freezone_douyin_copywrite(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_douyin_copywrite",
            job_id=job_id,
            payload=payload,
            queue_kind="default",
        )

    _freezone._raise_project_context_required("freezone_douyin_copywrite")


async def _enqueue_or_start_freezone_media_copywrite(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_media_copywrite",
            job_id=job_id,
            payload=payload,
            queue_kind="default",
        )

    _freezone._raise_project_context_required("freezone_media_copywrite")


async def _enqueue_or_start_freezone_media_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    task_type: Literal[
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_audio_separate",
        "freezone_video_compose",
        "freezone_audio_eleven_music",
        # [aipdd fork delta req46] 工具页「音乐创作」（MiniMax Music 3.0 直连）。
        "freezone_music_generate",
    ],
    job_id: str,
    payload: dict,
    queue_kind: str = "ffmpeg",
) -> dict:
    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            payload=payload,
            queue_kind=queue_kind,
        )

    return _accepted_job_response(
        task_type=task_type,
        username=username,
        project=project,
        job_id=job_id,
    )


async def _enqueue_freezone_background_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    task_type: str,
    job_id: str,
    payload: dict,
    queue_kind: str = "default",
) -> dict:
    rejection = _chat_model_rejection(task_type=task_type, payload=payload)
    if rejection:
        raise HTTPException(400, rejection)

    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind=queue_kind,
        episode=0,
        scope=job_id,
        payload={"job_id": job_id, "project_dir": str(project_dir), **payload},
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
    )
