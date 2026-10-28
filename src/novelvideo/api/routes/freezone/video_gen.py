"""freezone.video_gen 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneImageToVideoRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneKeyframeVideoRequest
from novelvideo.api.schemas import FreezoneVideoEditRequest
from novelvideo.api.schemas import FreezoneVideoEraseRequest
from novelvideo.api.schemas import FreezoneVideoExtendRequest
from novelvideo.api.schemas import FreezoneVideoGenRequest
from novelvideo.api.schemas import FreezoneVideoOmniGenRequest
from novelvideo.api.schemas import FreezoneVideoUpscaleRequest
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.freezone.route_helpers import load_video_character_items_by_ids as _load_video_character_items_by_ids
from novelvideo.freezone.video_node import build_freezone_image_to_video_prompt
from novelvideo.freezone.video_node import build_freezone_keyframe_video_prompt
from novelvideo.freezone.video_node import build_freezone_omni_video_prompt
from novelvideo.freezone.video_node import build_freezone_video_extend_prompt
from novelvideo.freezone.video_node import build_freezone_video_prompt
from novelvideo.freezone.video_node import get_video_camera_template
from novelvideo.freezone.video_node import is_freezone_seedance2_backend
from novelvideo.freezone.video_node import normalize_video_aspect_ratio
from novelvideo.freezone.video_node import normalize_video_duration_for_backend
from novelvideo.freezone.video_node import normalize_video_resolution_for_backend
from novelvideo.freezone.video_node import summarize_omni_reference_counts
from novelvideo.freezone.video_node import validate_omni_reference_limits
from novelvideo.newapi_asset_library import asset_reference as _asset_reference
from novelvideo.newapi_asset_library import is_asset_reference as _is_asset_reference

from ._common import TAG_FREEZONE_VIDEO

router = APIRouter()


@router.post("/projects/{project}/freezone/video/gen", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_gen(
    project: str,
    body: FreezoneVideoGenRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：文生视频。

    `model` 可选，前端应优先使用 `/api/v1/projects/{project}/freezone/video/models`
    返回的模型名称列表作为入参。

    运镜通过模板库和补充提示词控制，角色库通过已上传的人物参考图提供身份一致性。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=body.gen_mode,
        requester_user_id=ctx.requester_user_id,
    )
    _freezone._require_catalog_video_mode(capabilities, body.gen_mode)
    character_items = _load_video_character_items_by_ids(project_dir, body.character_ids)
    character_names = [str(item.get("name") or "") for item in character_items]
    character_reference_urls: list[str] = []
    for item in character_items:
        for url in item.get("image_urls") or []:
            if isinstance(url, str) and url:
                character_reference_urls.append(url)

    character_reference_paths = _freezone._resolve_url_list(project_dir, character_reference_urls)
    reference_items = [
        {"type": "image", "path": path, "role": "角色参考"} for path in character_reference_paths
    ]
    final_prompt = build_freezone_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        character_names=character_names,
        marks=[item.model_dump() for item in body.marks],
    )
    job_id = _freezone._new_job_id()

    try:
        return await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=normalize_video_duration_for_backend(
                backend,
                body.duration_seconds,
                *_freezone._catalog_duration_bounds(capabilities),
            ),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode="text_to_video",
            requested_gen_mode=body.gen_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=bool(body.archive_to_library),
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video gen task", exc)
        raise HTTPException(
            503, f"failed to start freezone video gen task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/i2v", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_i2v(
    project: str,
    body: FreezoneImageToVideoRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：图片驱动视频。

    统一承接：
    - 单图图生视频（单张图片参考，不锁定第一帧）
    - 多图图片参考视频
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    requested_mode = body.gen_mode
    execution_mode = "image_reference"
    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=requested_mode,
        requester_user_id=ctx.requester_user_id,
    )
    _freezone._require_catalog_video_mode(capabilities, requested_mode)
    reference_limits = _freezone._catalog_reference_limits(
        capabilities,
        image_default=9,
        video_default=0,
        audio_default=0,
    )
    if not body.image_urls:
        raise HTTPException(400, "image_urls is required")
    if requested_mode == "imageToVideo" and len(body.image_urls) != 1:
        raise HTTPException(400, "imageToVideo requires exactly one image reference")
    image_limit = 1 if requested_mode == "imageToVideo" else reference_limits["image"]
    if len(body.image_urls) > image_limit:
        raise HTTPException(
            400,
            f"this mode supports at most {image_limit} image references",
        )

    source_paths = _freezone._resolve_url_list(project_dir, list(body.image_urls))
    if not source_paths:
        raise HTTPException(400, "at least one valid image_url is required")
    if len(source_paths) != len(body.image_urls):
        raise HTTPException(400, "some image_urls could not be resolved")
    if capabilities is None and (
        len(source_paths) > 1 and not is_freezone_seedance2_backend(backend)
    ):
        raise HTTPException(
            400,
            "multiple image references currently only support Seedance 2.0 models",
        )

    reference_items = [
        {"type": "image", "path": path, "role": "图片参考"}
        for path in source_paths
    ]
    final_prompt = build_freezone_image_to_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_image_count=len(source_paths),
    )
    job_id = _freezone._new_job_id()

    try:
        return await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=normalize_video_duration_for_backend(
                backend,
                body.duration_seconds,
                *_freezone._catalog_duration_bounds(capabilities),
            ),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode=execution_mode,
            requested_gen_mode=requested_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=bool(body.archive_to_library),
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone image-to-video task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone image-to-video task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/keyframes", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_keyframes(
    project: str,
    body: FreezoneKeyframeVideoRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：关键帧视频。

    接受仅首帧、首帧+尾帧或仅尾帧；至少需要提供一个。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    if body.gen_mode == "firstFrame":
        if not body.first_frame_url:
            raise HTTPException(400, "firstFrame requires first_frame_url")
        if body.last_frame_url:
            raise HTTPException(400, "firstFrame does not accept last_frame_url")
    elif not (body.first_frame_url or body.last_frame_url):
        raise HTTPException(400, "firstLastFrame requires at least one keyframe")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    requested_mode = body.gen_mode
    execution_mode = (
        "first_frame" if requested_mode == "firstFrame" else "first_last_frame"
    )

    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=requested_mode,
        requester_user_id=ctx.requester_user_id,
    )
    _freezone._require_catalog_video_mode(capabilities, requested_mode)
    reference_limits = _freezone._catalog_reference_limits(
        capabilities,
        image_default=2,
        video_default=0,
        audio_default=0,
    )
    frame_count = int(bool(body.first_frame_url)) + int(bool(body.last_frame_url))
    if frame_count > reference_limits["image"]:
        # [aipdd fork delta req44] 旧文案只有一句 ``this model supports at most N image
        # references``：用户看不出是渠道**申报的图槽数量**不够，也不知道该改哪里。页签按
        # 产品口径放开了（不拿渠道接线不足当能力边界），提交这一步更得把事实与出路说全。
        raise HTTPException(
            400,
            _freezone._video_frame_slot_shortfall(
                model=body.model,
                capabilities=capabilities,
                limit=reference_limits["image"],
                needed=frame_count,
                mode=execution_mode,
            ),
        )

    first_paths = _freezone._resolve_url_list(
        project_dir, [body.first_frame_url] if body.first_frame_url else []
    )
    last_paths = _freezone._resolve_url_list(
        project_dir, [body.last_frame_url] if body.last_frame_url else []
    )
    first_path = first_paths[0] if first_paths else ""
    last_path = last_paths[0] if last_paths else ""
    if body.first_frame_url and not first_path:
        raise HTTPException(400, "first_frame_url could not be resolved")
    if body.last_frame_url and not last_path:
        raise HTTPException(400, "last_frame_url could not be resolved")

    reference_items = []
    if first_path:
        reference_items.append({"type": "image", "path": first_path, "role": "首帧"})
    if last_path:
        reference_items.append({"type": "image", "path": last_path, "role": "尾帧"})

    final_prompt = build_freezone_keyframe_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        has_first_frame=bool(first_path),
        has_last_frame=bool(last_path),
    )
    job_id = _freezone._new_job_id()

    try:
        return await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            # 关键帧画幅跟随首帧；仅尾帧时跟随尾帧，不接受固定比例。
            aspect_ratio="auto",
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=normalize_video_duration_for_backend(
                backend,
                body.duration_seconds,
                *_freezone._catalog_duration_bounds(capabilities),
            ),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            last_frame_path=last_path or None,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode=execution_mode,
            requested_gen_mode=requested_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=bool(body.archive_to_library),
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone keyframe video task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone keyframe video task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/omni-gen", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_omni_gen(
    project: str,
    body: FreezoneVideoOmniGenRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：全能参考文生视频。

    支持文本、图像、视频、音频、文件和公开网页链接混合输入。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    # [aipdd fork delta req42] 源视频本身就是答案：允许不写提示词（画布智能剪辑的
    # 换人 / 换产品就是空提示词调的，旧口径直接 400），任务指令由后端按参考素材组成
    # 补出。只有既没提示词、又没有可当画面基准的源视频时才拒绝。
    if not body.prompt.strip() and not any(
        str(item.type or "").strip().lower() == "video" for item in body.references
    ):
        raise HTTPException(400, "prompt is required")
    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=body.gen_mode,
        requester_user_id=ctx.requester_user_id,
    )
    mode_enabled = _freezone._catalog_mode_enabled(capabilities, "all_reference")
    if mode_enabled is False:
        raise HTTPException(400, "this model does not support omni reference mode")
    if (
        mode_enabled is None
        and not is_freezone_seedance2_backend(backend)
        and not _freezone._catalog_omni_reference_capable(capabilities)
    ):
        raise HTTPException(
            400, "omni video currently only supports Seedance 2.0 models"
        )

    raw_reference_items = [item.model_dump() for item in body.references]

    # [aipdd fork delta req43] 递不上去的参考素材 = 一条不相干的片。该提交模式要走的
    # 那条在线绑定根本不吃这类入参时当场拒单，不再烧额度出废片（判据是绑定接线，
    # 不是模型能力结论：渠道接错/接窄了不等于模型不支持）。
    # 这一刀跑在额度校验之前：额度只能告诉用户「挂了几条」，只有绑定事实能说出
    # 「为什么递不进去 + 渠道要补哪一侧」。
    try:
        _freezone._require_omni_reference_inputs_supported(
            body.model,
            raw_reference_items,
            mode=body.gen_mode,
            declared=capabilities,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    reference_limits = _freezone._catalog_reference_limits(
        capabilities,
        image_default=9,
        video_default=3,
        audio_default=3,
        file_default=0,
        link_default=0,
    )
    try:
        validate_omni_reference_limits(
            raw_reference_items,
            image_max=reference_limits["image"],
            video_max=reference_limits["video"],
            audio_max=reference_limits["audio"],
            file_max=reference_limits["file"],
            link_max=reference_limits["link"],
            # [aipdd fork delta req46] 这里的 total 是「各类上限之和」，是一个**代理值**，
            # 不是渠道申报的混投总闸：目录把 ``maxReferenceInputs`` 单独声明的那一层现在
            # 还没接进能力面（接它要动设置页 schema 与前端类型，不属本轮取证范围）。
            # 两者在 H3 上恰好相等（9 + 3 + 0 = 12 = 渠道写的 maxReferenceInputs），而对
            # 真正按单类之和递不进去的那一类渠道，提交侧 :func:`_enforce_reference_caps`
            # 会拿目录里的 ``maxReferenceInputs`` 再过一次并拒单摊数字，不会静默丢素材。
            total_max=(
                sum(reference_limits.values())
                if capabilities
                and any(
                    type(capabilities.get(key)) is int
                    for key in (
                        "referenceImageMax",
                        "referenceVideoMax",
                        "referenceAudioMax",
                        "referenceFileMax",
                        "referenceLinkMax",
                    )
                )
                else 12
            ),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    reference_items: list[dict[str, str]] = []
    for item in raw_reference_items:
        reference_type = str(item.get("type") or "image")
        reference_url = str(item.get("url") or "").strip()
        if not reference_url:
            raise HTTPException(400, "reference url is required")
        if _is_asset_reference(reference_url):
            # [aipdd fork delta req47] 素材库引用：它就是上游库里那份**已过审**的素材，
            # 所以既不解析本地路径（解析器只认同源引用，带 scheme 会被当外链拒掉）、也不过
            # 「文件扩展名」白名单（类型与审核状态入库时就定了）。形态给错（``asset://`` 后面
            # 是空的）当场报错，不递一条注定被上游拒、还可能照扣的引用。
            try:
                resolved_reference = _asset_reference(reference_url)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        elif reference_type == "link":
            parsed = urlsplit(reference_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise HTTPException(400, "reference link must be a public HTTP/HTTPS URL")
            resolved_reference = reference_url
        elif reference_type == "file" and reference_url.startswith(("http://", "https://")):
            resolved_reference = reference_url
        else:
            path_list = _freezone._resolve_url_list(project_dir, [reference_url])
            if not path_list:
                raise HTTPException(400, "reference url is required")
            resolved_reference = path_list[0]

        if reference_type == "file" and not _is_asset_reference(resolved_reference):
            allowed_types = capabilities.get("referenceFileTypes") if capabilities else None
            if isinstance(allowed_types, list) and allowed_types:
                extension = Path(unquote(urlsplit(reference_url).path)).suffix.lower().lstrip(".")
                normalized_types = {
                    str(value).strip().lower().lstrip(".") for value in allowed_types
                }
                if extension not in normalized_types:
                    raise HTTPException(
                        400,
                        "reference file type is not supported; expected one of: "
                        + ", ".join(str(value) for value in allowed_types),
                    )
        reference_items.append(
            {
                "type": reference_type,
                "path": resolved_reference,
                "role": str(item.get("role") or ""),
            }
        )

    # 音频时长必须在投递任务之前校验。
    try:
        await _freezone._validate_catalog_reference_audio_items(
            reference_items,
            capabilities=capabilities,
            backend=backend,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    final_prompt = build_freezone_omni_video_prompt(
        user_prompt=body.prompt,
        theme=body.theme,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_items=reference_items,
        # 「替换视频产品 / 人物」「对口型」要改哪一样由工具自己说清楚，别让模型猜。
        source_tool=body.source_tool,
    )
    job_id = _freezone._new_job_id()

    try:
        response = await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            aspect_ratio=await _freezone._resolve_omni_aspect_ratio(
                body.aspect_ratio,
                reference_items,
                capabilities,
            ),
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=normalize_video_duration_for_backend(
                backend,
                body.duration_seconds,
                *_freezone._catalog_duration_bounds(capabilities),
            ),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode="all_reference",
            requested_gen_mode=body.gen_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone omni video gen task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone omni video gen task: {exc}"
        ) from exc

    counts = summarize_omni_reference_counts(raw_reference_items)
    return {
        **response,
        "meta": counts,
    }

@router.post("/projects/{project}/freezone/video/video-edit", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_edit(
    project: str,
    body: FreezoneVideoEditRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：视频编辑。

    输入 1 个源视频，并按目录能力发送参考图片和独立参考音频。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=body.gen_mode,
        requester_user_id=ctx.requester_user_id,
    )
    mode_enabled = _freezone._catalog_mode_enabled(capabilities, "video_edit")
    if mode_enabled is not True:
        raise HTTPException(400, "this model does not support video_edit mode")

    if not body.video_url.strip():
        raise HTTPException(400, "video_url is required")
    video_paths = _freezone._resolve_url_list(project_dir, [body.video_url])
    if not video_paths:
        raise HTTPException(400, "video_url could not be resolved")

    image_paths = _freezone._resolve_url_list(project_dir, list(body.image_urls))
    if len(image_paths) != len(body.image_urls):
        raise HTTPException(400, "some image_urls could not be resolved")
    audio_paths = _freezone._resolve_url_list(project_dir, list(body.audio_urls))
    if len(audio_paths) != len(body.audio_urls):
        raise HTTPException(400, "some audio_urls could not be resolved")
    reference_limits = _freezone._catalog_reference_limits(
        capabilities,
        image_default=5,
        video_default=1,
        audio_default=0,
    )
    # [aipdd fork delta req46] 源视频**不拿 ``referenceVideoMax`` 门控**：那个数量在渠道目录里
    # 说的是「参考视频」（H3 现在明说 maxVideoInputs 0），而本路径递的是一个**源视频**（角色
    # 就是「视频编辑源」）。上一版在这里写 ``reference_limits["video"] < 1 → 400``，于是一个
    # 只针对参考视频的申报会把还能用的编辑入口顺手打死（本轮审查 H-3）。这条路径能不能吃的
    # 权威是上面的模式声明：渠道把 ``video_edit`` 写进 supportedModes 就是它自证接得住源视频；
    # 模式没声明时 ``_catalog_mode_enabled`` 回 None，而 None 在上面已经按「不表态交给网关」
    # 的路子放行，不再在这里用一个错位的多余闸门兑底。
    if len(image_paths) > reference_limits["image"]:
        raise HTTPException(
            400,
            f"this model supports at most {reference_limits['image']} image references",
        )
    if len(audio_paths) > reference_limits["audio"]:
        raise HTTPException(
            400,
            f"this model supports at most {reference_limits['audio']} audio references",
        )

    reference_items: list[dict[str, str]] = [
        {"type": "video", "path": video_paths[0], "role": "视频编辑源"}
    ]
    for path in image_paths:
        reference_items.append({"type": "image", "path": path, "role": "图片参考"})
    for path in audio_paths:
        reference_items.append({"type": "audio", "path": path, "role": "配乐参考"})

    try:
        await _freezone._validate_catalog_reference_audio_items(
            reference_items,
            capabilities=capabilities,
            backend=backend,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    final_prompt = build_freezone_image_to_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_image_count=len(image_paths),
    )
    job_id = _freezone._new_job_id()

    try:
        return await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            # 视频编辑的画幅与输出时长均跟随源视频；用户只选择分辨率。
            aspect_ratio="auto",
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=None,
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=None,
            backend=backend,
            audio_setting=body.audio_setting,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode="video_edit",
            requested_gen_mode=body.gen_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=body.archive_to_library,
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone video edit task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone video edit task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/video-extend", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_extend(
    project: str,
    body: FreezoneVideoExtendRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：从一个源视频的结尾继续生成指定时长。"""

    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model,
        body.model_params,
        mode=body.gen_mode,
        requester_user_id=ctx.requester_user_id,
    )
    if _freezone._catalog_mode_enabled(capabilities, "video_extend") is not True:
        raise HTTPException(400, "this model does not support video_extend mode")

    if not body.video_url.strip():
        raise HTTPException(400, "video_url is required")
    video_paths = _freezone._resolve_url_list(project_dir, [body.video_url])
    if not video_paths:
        raise HTTPException(400, "video_url could not be resolved")
    # [aipdd fork delta req46] 延长任务只有一个源视频，没有其他参考槽，而源视频不该由
    # ``referenceVideoMax``（渠道那里说的是参考视频）门控 —— 能不能吃的权威是上面的
    # ``video_extend`` 模式声明（理由见本文件视频编辑那一处，本轮审查 H-3）。
    reference_items = [
        {"type": "video", "path": video_paths[0], "role": "视频延长源"}
    ]
    final_prompt = build_freezone_video_extend_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
    )
    job_id = _freezone._new_job_id()
    try:
        return await _freezone._start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            reference_items=reference_items,
            # 延长任务沿用源视频画幅，但时长表示新生成片段的长度。
            aspect_ratio="auto",
            resolution=normalize_video_resolution_for_backend(
                backend,
                body.resolution,
                _freezone._catalog_resolution_options(capabilities),
            ),
            duration_seconds=normalize_video_duration_for_backend(
                backend,
                body.duration_seconds,
                *_freezone._catalog_duration_bounds(capabilities),
            ),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=None,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model,
            catalog_id=_freezone._catalog_entry_id(capabilities) or None,
            gen_mode="video_extend",
            requested_gen_mode=body.gen_mode,
            model_params=model_params,
            request_schema=request_schema,
            capabilities=capabilities,
            archive_to_library=bool(body.archive_to_library),
            source_tool=body.source_tool,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone video extend task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone video extend task: {exc}"
        ) from exc


@router.post(
    "/projects/{project}/freezone/video/erase",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_erase(
    project: str,
    body: FreezoneVideoEraseRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：智能去字幕 / 框选擦除。

    当前为稳定的一期实现：
    - `smart_subtitle`：自动估计底部字幕区域后执行视频擦除
    - `box`：按前端传入的固定框执行区域擦除
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")
    if body.mode == "box" and None in {body.box_x, body.box_y, body.box_width, body.box_height}:
        raise HTTPException(400, "box mode requires box_x, box_y, box_width and box_height")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_erase",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "mode": body.mode,
                    # [aipdd fork delta] 工具页引擎三选一：local / mediakit / auto。
                    "engine": body.engine,
                    "box_x": body.box_x,
                    "box_y": body.box_y,
                    "box_width": body.box_width,
                    "box_height": body.box_height,
                    # [aipdd fork delta] 工具页批量任务标记：产物完成后 runner 会
                    # 把它登记进资产库「待分类资产」。
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                },
            )
        _freezone._start_freezone_video_erase_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            body=body,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video erase task", exc)
        raise HTTPException(503, f"failed to start freezone video erase task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_erase",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.post(
    "/projects/{project}/freezone/video/upscale",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_upscale(
    project: str,
    body: FreezoneVideoUpscaleRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：基础版高清增强。

    当前实现使用 ffmpeg 做传统缩放、轻度降噪和锐化：
    - 保持原始画面比例
    - 按 `resolution` 对长边缩放
    - 保留原视频音轨
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_upscale",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "resolution": body.resolution,
                    "frame_interpolation": body.frame_interpolation,
                    "denoise_strength": body.denoise_strength,
                    # [aipdd fork delta] 超分结果是最终产物，带标记时 runner 登记
                    # 进资产库「待分类资产」。
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                },
            )
        _freezone._start_freezone_video_upscale_task(
            username=username,
            project=project_name,
            project_id=ctx.project_id,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            body=body,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video upscale task", exc)
        raise HTTPException(
            503,
            f"failed to start freezone video upscale task: {exc}",
        ) from exc

    return _accepted_job_response(
        task_type="freezone_video_upscale",
        username=username,
        project=project_name,
        job_id=job_id,
    )
