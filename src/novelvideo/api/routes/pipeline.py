"""流水线聚合状态端点。"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_sqlite_store, resolve_project_scope
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_state import get_task_manager
from novelvideo.utils.path_resolver import compute_identity_path, compute_portrait_path

router = APIRouter()


class PlanRequest(BaseModel):
    """自动化流水线计划请求：只决策下一步（execute=false）或顺带发射（execute=true）。

    ``mode`` 仅为消费方（MCP / 前端 driver）传递语义用，本端点每次只产出「一个未完成
    阶段」的动作；循环由消费方驱动（发射→轮询空闲→再调用），令服务端保持无状态、单一事实源。
    """

    mode: str = "checkpoint"
    execute: bool = False
    include_story_version: bool = False
    # [aipdd fork delta] 是否把该集「场景规划 / 道具规划」也当前置自动补齐（分镜页
    # 「自动化生成分镜脚本」与故事页「全自动」传 true；「自动化生成镜头草图」不传，
    # 按用户口径草图只补到「分镜脚本 + 故事版」）。
    plan_episode_assets: bool = False
    episode: Optional[int] = None
    story_text: Optional[str] = None
    aspect_ratio: str = "2:3"
    model: Optional[str] = None
    style: Optional[str] = None
    episode_count: Optional[int] = None
    target_beats: Optional[int] = None
    seconds_per_episode: Optional[float] = None
    total_seconds: Optional[float] = None
    story_style: Optional[str] = None


_STEP_MAP = {
    "ingest": ("ingest_fast", "小说摄入"),
    "configure": (None, "配置项目"),
    "characters": ("build_characters", "角色提取"),
    "episodes": ("build_episodes", "分集规划"),
    "portraits": (None, "肖像生成"),
    "identity_plan": (None, "身份规划"),
    "identity_images": (None, "身份图生成"),
    "script": ("script_writer", "脚本生成"),
    "sketches": ("sketch_generation", "草图生成"),
    "coloring": (None, "配色+身份/道具检测"),
    "global_optimize": ("global_optimize_video", "全局视频优化"),
    "first_frames": ("selected_regen", "首帧生成"),
    "tts": (None, "TTS 配音"),
    "video": ("single_video", "视频生成"),
    "compose": ("compose_episode", "合成导出"),
    "done": (None, "全部完成"),
}


def _all_or_empty(items: list[bool]) -> bool:
    return bool(items) and all(items)


def _user_has_configured(username: str, project: str) -> bool:
    from novelvideo.project_config import load_project_config

    config = load_project_config(username, project)
    return bool(config.get("ethnicity") or config.get("narration_style"))


def _file_series_complete(directory: Path, suffix: str, count: int) -> bool:
    if count <= 0:
        return False
    return all((directory / f"beat_{i + 1:02d}.{suffix}").exists() for i in range(count))


def _beat_file_series_complete(directory: Path, suffix: str, beats: list[dict]) -> bool:
    beat_numbers = [
        int(beat.get("beat_number", 0) or 0)
        for beat in beats
        if int(beat.get("beat_number", 0) or 0) > 0
    ]
    if not beat_numbers:
        return False
    return all((directory / f"beat_{beat_num:02d}.{suffix}").exists() for beat_num in beat_numbers)


def _beat_has_script_content(beat: dict) -> bool:
    return bool(
        str(beat.get("narration_segment") or "").strip()
        or str(beat.get("narration") or "").strip()
        or str(beat.get("visual_description") or "").strip()
    )


@router.get("/projects/{project}/pipeline/status")
async def pipeline_status(
    project: str,
    episode: Optional[int] = Query(None, description="指定集数，不传则自动检测最新活跃集"),
    user: dict = Depends(get_api_user),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    mgr = get_task_manager()

    characters = store.get_all_characters()
    episodes = store.get_all_episodes()
    main_chars = [c for c in characters if getattr(c, "is_main", False)]

    ingest_task = (
        await run_in_threadpool(
            mgr.get_task_for_project,
            resolved.ctx,
            "ingest_fast",
            0,
        )
        if resolved.ctx
        else await run_in_threadpool(
            mgr.get_task,
            "ingest_fast",
            username,
            project_name,
            0,
        )
    )
    ingested = (
        bool(ingest_task and ingest_task.status == "completed")
        or bool(characters)
        or bool(episodes)
    )
    configured = _user_has_configured(username, project_name)
    portraits_done = bool(main_chars) and all(
        bool(compute_portrait_path(project_dir, c.name)) for c in main_chars
    )

    global_status = {
        "ingested": ingested,
        "configured": configured,
        "characters": len(characters),
        "episodes": len(episodes),
        "portraits_done": portraits_done,
    }

    if not (ingested and configured and characters and episodes and portraits_done):
        if not ingested:
            next_step = "ingest"
        elif not configured:
            next_step = "configure"
        elif not characters:
            next_step = "characters"
        elif not episodes:
            next_step = "episodes"
        else:
            next_step = "portraits"
        task_type, step_name = _STEP_MAP[next_step]
        return {
            "ok": True,
            "data": {
                "project": project,
                "global": global_status,
                "current_episode": None,
                "episode_status": None,
                "next_step": task_type or next_step,
                "next_step_name": step_name,
            },
        }

    target_ep = episode
    if target_ep is None:
        unfinished = [
            ep.number
            for ep in sorted(episodes, key=lambda item: getattr(item, "number", 0))
            if not (project_dir / "videos" / "episodes" / f"ep{ep.number:03d}_final.mp4").exists()
        ]
        target_ep = unfinished[0] if unfinished else max((ep.number for ep in episodes), default=1)

    target_episode = store.get_episode(target_ep)
    identity_ids = set(getattr(target_episode, "identity_ids", []) or [])
    has_identity_plan = bool(identity_ids)
    has_identity_images = has_identity_plan
    if has_identity_plan:
        for char in main_chars:
            for ident in getattr(char, "identities", []) or []:
                if ident.identity_id not in identity_ids:
                    continue
                if not compute_identity_path(project_dir, char.name, ident.identity_name):
                    has_identity_images = False

    beats = await store.get_beats_as_dicts(target_ep)
    has_script = _all_or_empty([_beat_has_script_content(b) for b in beats])

    grids_dir = project_dir / "grids" / f"ep{target_ep:03d}"
    sketches_dir = project_dir / "sketches" / f"ep{target_ep:03d}"
    has_sketches = bool(list(grids_dir.glob("*.png"))) or bool(list(sketches_dir.glob("*.png")))

    has_coloring = has_sketches and _all_or_empty(
        [
            b.get("detected_identities") is not None or b.get("detected_props") is not None
            for b in beats
        ]
    )
    has_global_optimize = _all_or_empty(
        [bool(b.get("video_mode")) and bool(b.get("video_prompt")) for b in beats]
    )

    episode_status = {
        "identity_plan": has_identity_plan,
        "identity_images": has_identity_images,
        "script": has_script,
        "sketches": has_sketches,
        "coloring": has_coloring,
        "global_optimize": has_global_optimize,
        "first_frames": _beat_file_series_complete(
            project_dir / "frames" / f"ep{target_ep:03d}", "png", beats
        ),
        "tts": _beat_file_series_complete(
            project_dir / "audio" / f"ep{target_ep:03d}", "mp3", beats
        ),
        "video": _beat_file_series_complete(
            project_dir / "videos" / "beats" / f"ep{target_ep:03d}", "mp4", beats
        ),
    }

    next_step = "done"
    for key in (
        "identity_plan",
        "identity_images",
        "script",
        "sketches",
        "coloring",
        "global_optimize",
        "first_frames",
        "tts",
        "video",
    ):
        if not episode_status[key]:
            next_step = key
            break
    if (
        next_step == "done"
        and not (project_dir / "videos" / "episodes" / f"ep{target_ep:03d}_final.mp4").exists()
    ):
        next_step = "compose"

    task_type, step_name = _STEP_MAP[next_step]
    return {
        "ok": True,
        "data": {
            "project": project,
            "global": global_status,
            "current_episode": target_ep,
            "episode_status": episode_status,
            "next_step": task_type or next_step,
            "next_step_name": step_name,
        },
    }


@router.post("/projects/{project}/pipeline/plan")
async def pipeline_plan(
    project: str,
    body: PlanRequest,
    request: Request,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 权威流水线计划器端点（单一逻辑源）。

    默认 ``execute=false`` 只返回「下一个未完成阶段」的动作清单（检查点预览、零副作用）；
    ``execute=true`` 经进程内 ASGI 自调用把这些动作发射到既有细粒度端点（转发调用方
    Authorization/Cookie，与外部调用同鉴权）。循环推进由消费方（MCP / 前端 driver）驱动。
    """
    from novelvideo.pipeline.planner import plan_next, run_actions
    from novelvideo.pipeline.transport import asgi_caller

    await resolve_project_scope(project, user, required_role="viewer")
    caller = asgi_caller(request.app, request.headers)
    opts = body.model_dump(exclude_none=True)
    try:
        plan = await plan_next(caller, project, opts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.execute and plan.get("actions"):
        fired = await run_actions(caller, plan["actions"])
        plan["fired"] = fired
        # 发射失败必须回写状态：MCP auto 循环与任何 execute=true 客户端都据此判停，
        # 否则一个恒定失败的动作会被当作「仍在推进」空耗到预算/guard 上限。类型化
        # 故事版步（deferred）由客户端自跑，不计入服务端发射失败。
        failure = next(
            (f for f in fired if f.get("ok") is False and not f.get("deferred")),
            None,
        )
        if failure is not None:
            plan["status"] = "failed"
            plan["error"] = failure.get("error") or (
                f"{failure.get('method', '')} {failure.get('path', '')} 发射失败".strip()
            )
    return {"ok": True, "data": plan}
