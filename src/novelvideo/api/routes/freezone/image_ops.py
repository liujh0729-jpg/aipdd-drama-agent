"""freezone.image_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneImageTo3GSRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneOutpaintRequest
from novelvideo.api.schemas import FreezoneRedrawRequest
from novelvideo.api.schemas import FreezoneRelightRequest
from novelvideo.api.schemas import FreezoneStageAssetAcceptedResponse
from novelvideo.api.schemas import FreezoneTemplateEditRequest
from novelvideo.api.schemas import FreezoneUpscaleRequest
from novelvideo.freezone.route_helpers import FREEZONE_DEFAULT_IMAGE_MODEL
from novelvideo.freezone.route_helpers import build_erase_prompt as _build_erase_prompt
from novelvideo.freezone.route_helpers import build_outpaint_prompt as _build_outpaint_prompt
from novelvideo.freezone.route_helpers import build_redraw_prompt as _build_redraw_prompt
from novelvideo.freezone.route_helpers import build_relight_prompt as _build_relight_prompt
from novelvideo.freezone.route_helpers import build_template_edit_prompt as _build_template_edit_prompt
from novelvideo.freezone.route_helpers import build_upscale_prompt as _build_upscale_prompt
from novelvideo.freezone.route_helpers import get_freezone_image_camera_options as _get_freezone_image_camera_options
from novelvideo.freezone.route_helpers import get_freezone_image_style_asset_base as _get_freezone_image_style_asset_base
from novelvideo.freezone.route_helpers import get_freezone_image_style_manifest_version as _get_freezone_image_style_manifest_version
from novelvideo.freezone.route_helpers import get_freezone_image_style_templates as _get_freezone_image_style_templates
from novelvideo.freezone.route_helpers import merge_prompt_with_style_and_camera as _merge_prompt_with_style_and_camera
from novelvideo.freezone.route_helpers import prepare_padded_outpaint_base as _prepare_padded_outpaint_base
from novelvideo.freezone.route_helpers import resolve_freezone_image_provider as _resolve_freezone_image_provider
from novelvideo.freezone.route_helpers import resolve_outpaint_aspect_ratio as _resolve_outpaint_aspect_ratio
from novelvideo.freezone.route_helpers import split_provider_and_model as _split_provider_and_model
from novelvideo.freezone.route_helpers import template_edit_aspect_ratio as _template_edit_aspect_ratio

from ._common import TAG_FREEZONE_IMAGE

router = APIRouter()


@router.post(
    "/projects/{project}/freezone/relight",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_relight(
    project: str,
    body: FreezoneRelightRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：打光。基于源图和打光参考图的光照重塑接口。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    return await _freezone._start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_relight_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[body.lighting_reference_url] if body.lighting_reference_url else [],
        aspect_ratio="16:9",
        image_size=body.image_size or "2K",
        camera=None,
        style=None,
        provider=None,
        model=body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
        quality=body.quality or "medium",
        archive_to_library=body.archive_to_library,
        source_tool=body.source_tool,
    )


@router.post(
    "/projects/{project}/freezone/template-edit",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_template_edit(
    project: str,
    body: FreezoneTemplateEditRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：九宫格下拉菜单统一编辑接口。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    # 与 /freezone/edit 同一口径：EE 里目录是权威的，模型和目录身份都要在目录里
    # 对得上才放行。原来这条路由直接采信 body —— 客户端可以随便报一个便宜的
    # catalog_id 配一个贵的 model，报价按前者、执行按后者。
    # schema 也要一起传给 runner：它是目录里配置的动态参数（以及 includeQuality）
    # 写进网关请求体的唯一依据，只解析不入参等于目录配了参数却永远不下发。
    request_schema, _params, catalog_entry = await _freezone._resolve_catalog_request(
        "image",
        body.catalog_id or body.model,
        None,
        mode="image_to_image",
        requester_user_id=ctx.requester_user_id,
    )
    # provider 也一并取目录条目的：这条路由和 /freezone/edit 共用
    # `_start_or_enqueue_freezone_edit_job`，它会按 provider/model 走网关，
    # 目录里配的 openrouter 模型再按默认 provider 路由就送错家了。
    execution_provider, execution_model = _freezone._catalog_image_execution_selection(
        catalog_entry,
        requested_provider=None,
        requested_model=body.model,
    )
    return await _freezone._start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_template_edit_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[],
        aspect_ratio=_template_edit_aspect_ratio(body.mode),
        image_size=body.image_size or "2K",
        camera=body.camera,
        style=body.style,
        provider=execution_provider,
        model=execution_model or body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
        quality=body.quality or "medium",
        archive_to_library=body.archive_to_library,
        source_tool=body.source_tool,
        # 画布报价时带了目录身份就必须原样传下来，否则前端按目录规则报价、
        # 后端按旧的 image_selection 规则扣费，两边价格对不上。
        # 身份以目录条目为准，不采信 body —— 它直接决定按哪条目录规则扣费。
        catalog_id=_freezone._catalog_entry_id(catalog_entry) or body.catalog_id or None,
        request_schema=request_schema or None,
    )


@router.get("/projects/{project}/freezone/image/camera-options", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_camera_options(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回摄像机参数选项列表。"""
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": _get_freezone_image_camera_options()}


@router.get("/projects/{project}/freezone/image/style-templates", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_style_templates(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回风格模板列表(默认内置清单,可用 STYLE_GALLERY_MANIFEST 覆盖)。

    `data` 必须保持**裸列表**。这个端点同时服务多个仓库的前端,而它们的 `apiCall`
    只拆一层 `{ok, data}` 信封、不校验 `data` 的形状;一旦把 `data` 换成对象,所有
    没跟进的调用方一句 `templates.find(...)` 就抛 `is not a function`,冒泡到根错误
    边界变成整页「页面加载失败」。清单元信息因此挂在信封同级 —— 只取 `data` 的老
    客户端会自然忽略这些多余字段,新客户端另行读取。
    """
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _get_freezone_image_style_templates(),
        "asset_base": _get_freezone_image_style_asset_base(),
        "version": _get_freezone_image_style_manifest_version(),
    }


def _freezone_not_implemented(endpoint: str) -> None:
    raise HTTPException(
        status_code=501,
        detail=(
            f"{endpoint} is reserved in the API surface but not implemented yet. "
            "Keep using the existing image routes or frontend-local tools for now."
        ),
    )


@router.post(
    "/projects/{project}/freezone/image-to-3gs",
    response_model=FreezoneStageAssetAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_image_to_3gs(
    project: str,
    body: FreezoneImageTo3GSRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：把 Freezone 图片节点作为 SHARP 输入，生成 Freezone 3GS PLY。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")
    if source_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, f"source must be an image: {source_path}")

    scene_id = _freezone._infer_image_to_3gs_scene_id(source_path, project_dir)
    source_kind = body.source_kind
    step = "pano_sharp" if source_kind == "pano" else "single_face_sharp"
    job_id = _freezone._new_job_id()
    if source_kind == "pano":
        params = {
            "pano_path": source_path.as_posix(),
            "depth_source": "da2",
            "depth_device": "auto",
            "device": "auto",
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
            "source_url": body.source_url,
        }
    else:
        params = {
            "image_path": source_path.as_posix(),
            "source_kind": source_kind,
            "face_name": "front",
            "depth_meters": 8.0,
            "device": "auto",
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
            "source_url": body.source_url,
        }
    try:
        task_data = await _freezone._start_or_enqueue_freezone_image_to_3gs(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            scene_id=scene_id,
            source_path=source_path,
            source_kind=source_kind,
            params=params,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start image-to-3gs task", exc)
        raise HTTPException(503, f"failed to start image-to-3gs task: {exc}") from exc

    return {
        "ok": True,
        "data": {
            "task_type": "freezone_image_to_3gs",
            "job_id": job_id,
            "scope": job_id,
            "scene_id": scene_id,
            "step": step,
            **task_data,
        },
    }


@router.post("/projects/{project}/freezone/upscale", tags=[TAG_FREEZONE_IMAGE])
async def freezone_upscale(
    project: str,
    body: FreezoneUpscaleRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：高清放大接口。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")

    job_id = _freezone._new_job_id()
    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(source_path, "original")
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    try:
        return await _freezone._start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_upscale_prompt(), body.style, body.camera
            ),
            base_path=source_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as e:
        _freezone._handle_task_start_runtime_error("failed to start upscale task", e)
        raise HTTPException(503, f"failed to start upscale task: {e}") from e


@router.post(
    "/projects/{project}/freezone/outpaint",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_outpaint(
    project: str,
    body: FreezoneOutpaintRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：扩图接口。

    做法是先把原图补白到目标宽高比，再复用现有图片编辑任务，
    让模型去生成新暴露出来的外部区域，而不是简单拉伸原图。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")
    if body.num_images != 1:
        raise HTTPException(400, "outpaint currently supports only num_images = 1")

    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(
        source_path,
        body.target_aspect_ratio,
    )
    padded_base_path = _prepare_padded_outpaint_base(
        source_path=source_path,
        project_dir=project_dir,
        target_aspect_ratio=resolved_aspect_ratio,
    )
    job_id = _freezone._new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    try:
        return await _freezone._start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_outpaint_prompt(), body.style, body.camera
            ),
            base_path=padded_base_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as e:
        _freezone._handle_task_start_runtime_error("failed to start outpaint task", e)
        raise HTTPException(503, f"failed to start outpaint task: {e}") from e


@router.post(
    "/projects/{project}/freezone/redraw",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_redraw(
    project: str,
    body: FreezoneRedrawRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：重绘接口。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")

    if body.num_images != 1:
        raise HTTPException(400, "num_images is currently limited to 1")

    job_id = _freezone._new_job_id()
    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(source_path, body.aspect_ratio)
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    if body.mask_url:
        try:
            mask_path = _freezone.resolve_static_url_to_path(body.mask_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not mask_path.exists():
            raise HTTPException(404, f"mask not found: {mask_path}")

        try:
            return await _freezone._start_or_enqueue_freezone_mask_edit_path(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=output_dir,
                job_id=job_id,
                base_path=source_path,
                mask_path=mask_path,
                prompt=_merge_prompt_with_style_and_camera(
                    (
                        _build_redraw_prompt(body.prompt)
                        if body.prompt.strip()
                        else _build_erase_prompt()
                    ),
                    body.style,
                    body.camera,
                ),
                aspect_ratio=resolved_aspect_ratio,
                image_size=body.image_size,
                quality=body.quality or "medium",
                archive_to_library=body.archive_to_library,
                source_tool=body.source_tool,
                provider=provider,
                model=resolved_model,
            )
        except RuntimeError as e:
            _freezone._handle_task_start_runtime_error("failed to start masked redraw task", e)
            raise HTTPException(503, f"failed to start masked redraw task: {e}") from e

    try:
        return await _freezone._start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_redraw_prompt(body.prompt), body.style, body.camera
            ),
            base_path=source_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as e:
        _freezone._handle_task_start_runtime_error("failed to start redraw task", e)
        raise HTTPException(503, f"failed to start redraw task: {e}") from e
