"""freezone._common 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Awaitable
from typing import Callable

from fastapi import HTTPException

from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.task_start_errors import handle_task_start_runtime_error
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import CANVAS_ID_RE
from novelvideo.freezone.paths import freezone_root
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key



async def _resolve_freezone_project(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
    require_home_node: bool = True,
) -> tuple[ProjectContext, str, str, Path, str]:
    """解析 freezone 项目上下文。

    `require_home_node=False` 只给画布那 13 条路由用（B2 步 11，按 `TCP-P60` 收窄）。
    这一道守卫是**全部 72 条 freezone 路由**共用的，删掉它等于连另外 58 条读写
    `Path(ctx.output_dir)` 本地文件、既无租约也无共享存储交代的路由一起放开，
    与 B2 §6.3「逐个撤、不批量撤」冲突；故默认值保持 `True`，逐个调用点撤。
    """
    ctx = await _freezone.resolve_project_context(
        user=user,
        project_id=project,
        required_role=required_role,
    )
    if require_home_node:
        _freezone.require_project_home_node(ctx, operation="access freezone project files")
    return ctx, ctx.owner_username, ctx.project_name, Path(ctx.output_dir), str(ctx.output_dir)


def _raise_project_context_required(task_type: str) -> None:
    raise HTTPException(
        503,
        f"Project context required for {task_type}.",
    )


def _handle_task_start_runtime_error(message: str, exc: RuntimeError) -> None:
    handle_task_start_runtime_error(logger, message, exc)


def _project_job_response(
    *,
    task_type: str,
    ctx: ProjectContext,
    job_id: str,
    backend: str,
    queue: str | None,
    task_id: str | None,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
) -> dict:
    task_scope = scope or job_id
    data = {
        "task_type": task_type,
        "job_id": job_id,
        "task_key": project_task_state_key(
            task_type,
            ctx.project_id,
            int(episode),
            beat_num=beat_num,
            scope=task_scope,
        ),
        "task_episode": int(episode),
        "task_scope": task_scope,
        "backend": backend,
        "queue": queue,
    }
    if beat_num is not None:
        data["task_beat_num"] = int(beat_num)
    if task_id:
        data["task_id"] = task_id
    return {"ok": True, "data": data}


logger = logging.getLogger("novelvideo.api.freezone")

FrameReviewReviewer = Callable[[str], str | Awaitable[str]]

TAG_FREEZONE_BOOTSTRAP = "freezone-bootstrap"
TAG_FREEZONE_MEDIA = "freezone-media"
TAG_FREEZONE_AUDIO = "freezone-audio"
# [aipdd fork delta req46] 音乐创作（工具页「音乐创作」与候选目录）单独成组：
# 它与配音不同链路（厂商直连 music_generation），Swagger 里混在 audio 下会误导。
TAG_FREEZONE_MUSIC = "freezone-music"
TAG_FREEZONE_IMAGE = "freezone-image"
TAG_FREEZONE_VIDEO = "freezone-video"
TAG_FREEZONE_TEXT = "freezone-text"
TAG_FREEZONE_CANVAS = "freezone-canvas"
TAG_FREEZONE_ASSETS = "freezone-assets"

TAG_FREEZONE_COMMIT = "freezone-commit"
TAG_FREEZONE_JOBS = "freezone-jobs"
TAG_FREEZONE_SKILLS = "freezone-skills"

CANVAS_EVENT_SCHEMA_VERSION = "canvas_event.v1"
MAINLINE_SKETCH_IMAGE_SIZE = "1K"
MAINLINE_SKETCH_IMAGE_QUALITY = "low"
MAINLINE_FRAME_IMAGE_SIZE = "1K"
MAINLINE_SCENE_360_IMAGE_SIZE = "2K"
_IMAGE_SIZE_TIERS = ("512", "1K", "2K", "3K", "4K")
_IMAGE_SIZE_TIER_BY_CASEFOLD = {tier.casefold(): tier for tier in _IMAGE_SIZE_TIERS}
_SKILL_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_.:\-]{1,128}$")


def _canvas_events_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "_canvas_events"


def _canvas_event_log_path(project_dir: Path, canvas_id: str | None) -> Path:
    event_canvas_id = (canvas_id or "").strip() or "_project"
    if not CANVAS_ID_RE.match(event_canvas_id):
        digest = hashlib.sha256(event_canvas_id.encode("utf-8")).hexdigest()[:16]
        event_canvas_id = f"canvas_{digest}"
    return _canvas_events_dir(project_dir) / f"{event_canvas_id}.jsonl"


def _canvas_event_actor(user: dict) -> dict:
    return {
        "kind": "user",
        "id": str(user.get("id") or user.get("username") or "unknown"),
        "username": str(user.get("username") or ""),
    }


def _append_canvas_event(
    *,
    project_dir: Path,
    project_id: str,
    canvas_id: str | None,
    event_type: str,
    actor: dict,
    payload: dict,
) -> None:
    path = _canvas_event_log_path(project_dir, canvas_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": CANVAS_EVENT_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "project_id": project_id,
        "canvas_id": (canvas_id or "").strip() or "_project",
        "event_type": event_type,
        "actor": actor,
        "created_at": canvas_store.utc_now_iso(),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
