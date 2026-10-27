# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""freezone.explainer_script_ops 子模块：讲解类视频「台词草稿」。

两段式出片的第一段。科普讲解与白板动画原本都是「一次提交跑到底」：文本、配音、整片
渲染全在一条任务里，台词里有一句不顺就得整片重跑。这里把文本阶段单独拿出来做成一条
**只跑文本**的短任务，产物是可逐镜编辑的纯 JSON；用户在面板里改完，再把整份草稿交给
成片任务的 ``locked_script``，成片因此不再调文本模型。

只开一个端点、用 ``kind`` 区分两族草稿：科普讲解要两次文本调用（分章解说词 + 受限
分镜），白板动画只要一次逐幕切分，任务壳与产物契约完全相同（见
``freezone/explainer_script.py``），拆成两条路由只会多一份要同步维护的入参校验。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneExplainerScriptRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.freezone import explainer_script as _draft
from novelvideo.freezone.explainer import resolve_reference_content
from novelvideo.freezone.paths import outputs_dir

from ._common import TAG_FREEZONE_TEXT

router = APIRouter()

#: 白板文案长度闸门：与成片端点、锁定稿校验共用 ``explainer_script.MAX_SCRIPT_CHARS``
#: 这一份常量，避免两处各写一个数字后漂移——入口放行、锁定稿却拒收会把用户挡在最后一步。
MAX_WHITEBOARD_SCRIPT_CHARS = _draft.MAX_SCRIPT_CHARS


def _explainer_script_output_path(project_dir: Path, job_id: str) -> Path:
    """草稿 JSON 的落盘位置（jobs 子模块回读产物时用同一份口径）。"""
    return outputs_dir(project_dir, "freezone_explainer_script") / f"{job_id}.json"


@router.post(
    "/projects/{project}/freezone/explainer-script",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_explainer_script(
    project: str,
    body: FreezoneExplainerScriptRequest,
    user: dict = Depends(get_api_user),
):
    """讲解类视频台词草稿：参考资料 / 口播文案 → 逐镜台词 JSON（异步任务）。

    草稿只跑文本、不配音不渲染，所以它比成片快一个数量级：用户先看台词，再决定
    值不值得花配音与渲染的时间。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    kind = str(body.kind or _draft.EXPLAINER_DRAFT_KIND).strip().lower()
    if kind not in (_draft.EXPLAINER_DRAFT_KIND, _draft.WHITEBOARD_DRAFT_KIND):
        raise HTTPException(
            400, f"kind must be one of: {_draft.EXPLAINER_DRAFT_KIND}, {_draft.WHITEBOARD_DRAFT_KIND}"
        )

    reference = ""
    script = ""
    if kind == _draft.WHITEBOARD_DRAFT_KIND:
        script = body.script.strip()
        if not script:
            raise HTTPException(400, "script is required for whiteboard draft")
        if len(script) > MAX_WHITEBOARD_SCRIPT_CHARS:
            raise HTTPException(
                400, f"script must be <= {MAX_WHITEBOARD_SCRIPT_CHARS} characters"
            )
    else:
        # 清洗与长度闸门复用成片那一份：草稿放行的资料，成片也一定放行，不会出现
        # 「草稿过了、确认出片才说资料太长」这种把用户挡在最后一步的情况。
        try:
            reference = resolve_reference_content(body.reference_content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    job_id = _freezone._new_job_id()
    payload = {
        "kind": kind,
        "reference_content": reference,
        "script": script,
        "title": body.title.strip(),
        "duration_s": body.duration_s,
        "max_scenes": body.max_scenes,
        "model_text": body.model_text.strip(),
        "source_tool": body.source_tool,
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_explainer_script",
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_explainer_script(
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
            "failed to start freezone explainer script task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone explainer script task: {exc}"
        ) from exc
