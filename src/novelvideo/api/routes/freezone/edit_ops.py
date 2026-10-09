"""freezone.edit_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneEditRequest
from novelvideo.api.schemas import FreezoneImageCameraConfig
from novelvideo.api.schemas import FreezoneImageStyleConfig
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.freezone.route_helpers import FREEZONE_DEFAULT_IMAGE_MODEL
from novelvideo.freezone.route_helpers import merge_prompt_with_style_and_camera as _merge_prompt_with_style_and_camera
from novelvideo.freezone.route_helpers import resolve_freezone_image_provider as _resolve_freezone_image_provider
from novelvideo.freezone.route_helpers import resolve_outpaint_aspect_ratio as _resolve_outpaint_aspect_ratio
from novelvideo.freezone.route_helpers import split_provider_and_model as _split_provider_and_model
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key

from ._common import TAG_FREEZONE_IMAGE

router = APIRouter()


async def _start_or_enqueue_freezone_edit_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    prompt: str,
    base_url: str,
    extra_reference_urls: list[str],
    aspect_ratio: str,
    image_size: str,
    camera: FreezoneImageCameraConfig | None,
    style: FreezoneImageStyleConfig | None,
    provider: str | None,
    model: str | None,
    quality: str | None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    catalog_id: str | None = None,
    gen_mode: str | None = None,
    model_params: dict[str, Any] | None = None,
    request_schema: dict[str, Any] | None = None,
    task_display: dict[str, str] | None = None,
    archive_to_library: bool = False,
    source_tool: str = "",
) -> dict:
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "image",
            catalog_id or model_id or model or FREEZONE_DEFAULT_IMAGE_MODEL,
            requester_user_id=ctx.requester_user_id,
        )
    base_paths = _freezone._resolve_url_list(project_dir, [base_url])
    if not base_paths:
        raise HTTPException(400, "base_url is required")
    for path_text in base_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"base file not found: {path_text}")
    extra_paths = _freezone._resolve_url_list(project_dir, extra_reference_urls)
    for path_text in extra_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    resolved_aspect_ratio = (
        _resolve_outpaint_aspect_ratio(Path(base_paths[0]), "original")
        if str(aspect_ratio or "").strip().lower() == "original"
        else aspect_ratio
    )
    job_id = _freezone._new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(provider, model)
    normalized_provider = _resolve_freezone_image_provider(resolved_provider)
    prompt_text = _merge_prompt_with_style_and_camera(prompt, style, camera)
    display_payload = {
        "task_family": "freezone_canvas",
        "task_label": "编辑图片",
        "display_name": "编辑图片",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await _freezone.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="freezone",
            task_type="freezone_edit",
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "prompt": prompt_text,
                "base_path": base_paths[0],
                "extra_reference_paths": extra_paths,
                "aspect_ratio": resolved_aspect_ratio,
                "image_size": image_size,
                "provider": normalized_provider,
                "model": resolved_model,
                "quality": quality,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or "",
                "catalog_id": catalog_id or "",
                "gen_mode": gen_mode or "",
                # 目录动态参数与它的 schema 一起下发给 runner；schema 是 runner
                # 把参数写进网关请求体（requestPath）的唯一依据，只带值没有用。
                **({"model_params": model_params} if model_params else {}),
                **({"request_schema": request_schema} if request_schema else {}),
                # [aipdd fork delta] 工具页批量任务标记：产物完成后 runner 会把它
                # 登记进资产库「待分类资产」；画布等其它入口不带该键。
                **({"archive_to_library": True} if archive_to_library else {}),
                # [aipdd fork delta] 工具页任务归属：浮现到 metadata.source_tool。
                **({"source_tool": source_tool} if source_tool else {}),
                **display_payload,
            },
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_edit",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "freezone_edit", ctx.project_id, 0, scope=job_id
                ),
                "backend": queued.backend,
                "queue": queued.queue,
            },
        }

    _freezone._raise_project_context_required("freezone_edit")


async def _start_or_enqueue_freezone_edit_path(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    prompt: str,
    base_path: Path,
    extra_reference_paths: list[str],
    aspect_ratio: str,
    image_size: str,
    provider: str | None,
    model: str | None,
    quality: str | None,
) -> dict:
    task_type = "freezone_edit"
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "image",
            model or FREEZONE_DEFAULT_IMAGE_MODEL,
            requester_user_id=ctx.requester_user_id,
        )

    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            payload={
                "prompt": prompt,
                "base_path": base_path.as_posix(),
                "extra_reference_paths": extra_reference_paths,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "provider": provider,
                "model": model,
                "quality": quality,
            },
            queue_kind="default",
        )

    _freezone._raise_project_context_required(task_type)


async def _start_or_enqueue_freezone_mask_edit_path(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    base_path: Path,
    mask_path: Path,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
    quality: str,
    provider: str,
    model: str | None,
) -> dict:
    task_type = "freezone_mask_edit"
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "image",
            model or FREEZONE_DEFAULT_IMAGE_MODEL,
            requester_user_id=ctx.requester_user_id,
        )

    if ctx is not None:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            payload={
                "base_path": base_path.as_posix(),
                "mask_path": mask_path.as_posix(),
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality,
                "provider": provider,
                "model": model,
            },
            queue_kind="default",
        )

    _freezone._raise_project_context_required(task_type)


@router.post(
    "/projects/{project}/freezone/edit",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_edit(
    project: str,
    body: FreezoneEditRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：启动图生图 / 图编辑任务，返回 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    request_schema, model_params, catalog_entry = await _freezone._resolve_catalog_request(
        "image",
        body.model_id or body.model,
        body.model_params,
        mode=body.gen_mode,
        requester_user_id=ctx.requester_user_id,
    )
    execution_provider, execution_model = _freezone._catalog_image_execution_selection(
        catalog_entry,
        requested_provider=body.provider,
        requested_model=body.model,
    )
    return await _freezone._start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=body.prompt,
        base_url=body.base_url,
        extra_reference_urls=list(body.extra_reference_urls or []),
        aspect_ratio=body.aspect_ratio,
        image_size=body.image_size,
        camera=body.camera,
        style=body.style,
        provider=execution_provider,
        model=execution_model,
        quality=body.quality,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        model_id=_freezone._catalog_entry_id(catalog_entry) or body.model_id or None,
        catalog_id=_freezone._catalog_entry_id(catalog_entry) or None,
        gen_mode=body.gen_mode or None,
        model_params=model_params,
        request_schema=request_schema,
        archive_to_library=body.archive_to_library,
        source_tool=body.source_tool,
    )
