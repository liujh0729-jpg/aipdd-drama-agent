# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""freezone.oral_script_ops 子模块：短视频口播稿创作（工具栏首项）。

一次提交出一条**可以直接念**的完整口播逐字稿：使用者的处境（是谁、想吸引谁、在手
里能拍什么）压进单一写作指令，只走一次网关文本调用；随后本地跑一遍去 AI 味机械预
检，命中假对比这类硬伤时自动追加**一轮**定向重写（原方法论就限定「最多一轮有依据
的修改」，不做无限重写）。产物是结果 JSON + 一份净台词 txt（提词器 / 配音可直接
用）。出网口径与公众号文章任务一致：文本叶经既有网关分发，检查与解析纯本地。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneOralScriptRequest
from novelvideo.freezone.oral_script.prompts import clamp_duration
from novelvideo.freezone.oral_script.prompts import clamp_title_count
from novelvideo.freezone.oral_script.prompts import normalize_formula
from novelvideo.freezone.paths import outputs_dir

from ._common import TAG_FREEZONE_TEXT

router = APIRouter()

#: 素材长度闸门（与写作层的截断上限同源，超限直接 400 而不是静默截断）。
MAX_MATERIAL_CHARS = 12000


def _oral_script_output_path(project_dir: Path, job_id: str) -> Path:
    """结果 JSON 的落盘位置（jobs 子模块回读产物时用同一份口径）。

    净台词 txt 的路径由 runner 自己拼（它与 JSON 写在同一目录），不在这里再留一份
    没人调用的镜像函数。
    """
    return outputs_dir(project_dir, "freezone_oral_script") / f"{job_id}.json"


@router.post(
    "/projects/{project}/freezone/oral-script",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_oral_script(
    project: str,
    body: FreezoneOralScriptRequest,
    user: dict = Depends(get_api_user),
):
    """短视频口播稿创作：处境 + 素材 → 能直接念的逐字稿（异步任务）。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    topic = body.topic.strip()
    persona = body.persona.strip()
    material = body.material.strip()
    if not topic and not material:
        raise HTTPException(400, "topic or material is required")
    if len(material) > MAX_MATERIAL_CHARS:
        raise HTTPException(
            400, f"material must be <= {MAX_MATERIAL_CHARS} characters"
        )

    job_id = _freezone._new_job_id()
    payload = {
        "topic": topic,
        "persona": persona,
        "material": material,
        "audience": body.audience.strip(),
        "scene": body.scene.strip(),
        "tone": body.tone.strip(),
        # 归一化在路由层就做一次，任务记录与前端回显看到的是同一个口径（与文章主题/风格同例）。
        "formula": normalize_formula(body.formula),
        "duration_s": clamp_duration(body.duration_s),
        "title_count": clamp_title_count(body.title_count),
        "rewrite_hard_hits": bool(body.rewrite_hard_hits),
        "model_text": body.model_text.strip(),
        "source_tool": body.source_tool,
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_oral_script",
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_oral_script(
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
            "failed to start freezone oral script task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone oral script task: {exc}"
        ) from exc
