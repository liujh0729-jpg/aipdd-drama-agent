# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""freezone.article_ops 子模块：公众号「文章编写与排版 + 可选发布」。

写作任务是异步后台任务（照讲解视频/故事脚本的注册模式），产物是结果 JSON + 一份
公众号合规 HTML；发布是同步动作（几次官方 API 调用），凭证缺失即返回配置指引、
**不阻塞排版**。出网口径与讲解视频一致：文本叶经既有网关分发，发布是用户自带凭证
的公开 API 直连。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneArticlePublishRequest
from novelvideo.api.schemas import FreezoneArticleWriteRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.freezone.explainer import resolve_reference_content
from novelvideo.freezone.paths import outputs_dir
from novelvideo.gzh import publish as gzh_publish

from ._common import TAG_FREEZONE_TEXT

router = APIRouter()


def _article_write_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_article_write") / f"{job_id}.json"


def _article_html_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_article_write") / f"{job_id}.html"


@router.post(
    "/projects/{project}/freezone/article/write",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_article_write(
    project: str,
    body: FreezoneArticleWriteRequest,
    user: dict = Depends(get_api_user),
):
    """公众号文章编写与排版：主题/素材 → 风格化 Markdown → 合规 HTML（异步任务）。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    topic = body.topic.strip()
    material = body.material.strip()
    if not topic and not material:
        raise HTTPException(400, "topic or material is required")
    if material:
        try:
            material = resolve_reference_content(material)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    job_id = _freezone._new_job_id()
    payload = {
        "topic": topic,
        "material": material,
        "style": body.style.strip(),
        "theme": body.theme.strip(),
        "target_words": body.target_words,
        "audience": body.audience.strip(),
        "tone": body.tone.strip(),
        "intro_quote": body.intro_quote.strip(),
        "author": body.author.strip(),
        "model_text": body.model_text.strip(),
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_article_write",
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_article_write(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone article write task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone article write task: {exc}"
        ) from exc


@router.post(
    "/projects/{project}/freezone/article/publish",
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_article_publish(
    project: str,
    body: FreezoneArticlePublishRequest,
    user: dict = Depends(get_api_user),
):
    """把已排版的公众号文章发布到草稿箱 / 可选直发（同步；凭证缺失返回配置指引）。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    # 定位待发布 HTML：显式 html_url 优先，其次 article_job_id 的排版产物。
    html_path: Path | None = None
    html_url = body.html_url.strip()
    if html_url:
        try:
            html_path = _freezone.resolve_static_url_to_path(html_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        job_id = body.article_job_id.strip()
        if not job_id:
            raise HTTPException(400, "html_url or article_job_id is required")
        html_path = _article_html_path(project_dir, job_id)
    if not html_path or not html_path.is_file():
        raise HTTPException(404, f"article html not found: {html_path}")

    cover_path: Path | None = None
    cover_url = body.cover_url.strip()
    if cover_url:
        try:
            cover_path = _freezone.resolve_static_url_to_path(cover_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not cover_path.exists():
            raise HTTPException(404, f"cover not found: {cover_path}")

    html = html_path.read_text(encoding="utf-8")
    outcome = await gzh_publish.publish_article(
        html,
        title=body.title.strip(),
        author=body.author.strip(),
        digest=body.digest.strip(),
        thumb_path=cover_path,
        project_dir=project_dir,
        do_publish=body.publish_now,
    )
    # 统一走标准 {ok, data} 信封：这里的 ok 表示「发布调用本身跑通了」，
    # 真正的发布结果（含 needs_config / dry_run / error 降级）都放在 data.status，
    # 前端据此内联展示配置指引而不是当作 HTTP 错误抛出。
    return {
        "ok": True,
        "data": {
            "status": outcome.status,
            "published_ok": outcome.status in {"published", "draft", "dry_run"},
            "message": outcome.message,
            "draft_media_id": outcome.draft_media_id,
            "publish_id": outcome.publish_id,
            "article_url": outcome.article_url,
            "guidance": outcome.guidance,
            "error_code": outcome.error_code,
        },
    }
