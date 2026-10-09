"""freezone.video_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneAnalyzeShotsRequest
from novelvideo.api.schemas import FreezoneAnalyzeVideoStoryRequest
from novelvideo.api.schemas import FreezoneDouyinCopywriteRequest
from novelvideo.api.schemas import FreezoneExtractFramesRequest
from novelvideo.api.schemas import FreezoneExplainerVideoRequest
from novelvideo.api.schemas import FreezoneMediaCopywriteRequest
from novelvideo.api.schemas import FreezoneVideoCloneRequest
from novelvideo.api.schemas import FreezoneVideoIdeaRequest
from novelvideo.api.schemas import FreezoneVideoMixRequest
from novelvideo.api.schemas import FreezoneWhiteboardAnimationRequest
from novelvideo.freezone.explainer import normalize_explainer_background
from novelvideo.freezone.explainer import normalize_explainer_duration_s
from novelvideo.freezone.explainer import resolve_reference_content
from novelvideo.freezone.mix import normalize_mix_aspect_ratio
from novelvideo.freezone.mix import normalize_mix_resolution
from novelvideo.freezone.mix import normalize_mix_subtitle_font_size
from novelvideo.freezone.route_helpers import ensure_existing_paths as _ensure_existing_paths
from novelvideo.freezone.video_node import is_freezone_seedance2_backend
from novelvideo.freezone.video_node import normalize_freezone_seedance2_scene_optimize
from novelvideo.freezone.video_node import normalize_video_aspect_ratio
from novelvideo.freezone.video_node import normalize_video_resolution_for_backend

from ._common import TAG_FREEZONE_VIDEO

router = APIRouter()


# ============================================================
# 视频处理：抽帧 / 镜头分析
# ============================================================


@router.post("/projects/{project}/freezone/extract-frames", tags=[TAG_FREEZONE_VIDEO])
async def freezone_extract_frames(
    project: str,
    body: FreezoneExtractFramesRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：从视频中抽取关键帧，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        video_path = _freezone.resolve_static_url_to_path(body.video_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    job_id = _freezone._new_job_id()
    return await _freezone._enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_extract",
        job_id=job_id,
        payload={
            "video_path": video_path.as_posix(),
            "max_frames": body.max_frames,
            "scene_threshold": body.scene_threshold,
        },
    )


@router.post("/projects/{project}/freezone/analyze-shots", tags=[TAG_FREEZONE_VIDEO])
async def freezone_analyze_shots(
    project: str,
    body: FreezoneAnalyzeShotsRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：分析一组关键帧的镜头内容，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.frame_urls:
        raise HTTPException(400, "frame_urls is required (non-empty)")

    frame_paths: list[str] = []
    for url in body.frame_urls:
        try:
            p = _freezone.resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not p.exists():
            raise HTTPException(404, f"frame not found: {p}")
        frame_paths.append(str(p))

    job_id = _freezone._new_job_id()
    return await _freezone._enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_analyze",
        job_id=job_id,
        payload={
            "frame_paths": frame_paths,
            "analysis_mode": body.analysis_mode,
            "duration_sec": body.duration_sec,
        },
    )


@router.post("/projects/{project}/freezone/analyze-video-story", tags=[TAG_FREEZONE_VIDEO])
async def freezone_analyze_video_story(
    project: str,
    body: FreezoneAnalyzeVideoStoryRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：抽帧并解析视频故事，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        video_path = _freezone.resolve_static_url_to_path(body.video_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    job_id = _freezone._new_job_id()
    return await _freezone._enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_video_story",
        job_id=job_id,
        payload={
            "video_path": video_path.as_posix(),
            "max_frames": body.max_frames,
            "scene_threshold": body.scene_threshold,
            "duration_sec": body.duration_sec,
        },
    )


@router.post("/projects/{project}/freezone/video/clone", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_clone(
    project: str,
    body: FreezoneVideoCloneRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：视频复刻（抽帧解读 → 逐镜规划 → 逐镜生成 → 自动拼接成片）。

    全自动成片：一次提交跑完整链路，任务产出为一条完整视频。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        video_path = _freezone.resolve_static_url_to_path(body.video_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    # [aipdd fork delta] 参考内容：用户上传/从资产库选择的参考图（≤4 张由 schema
    # 约束），解析为本项目静态路径；与提示词一起构成「对原视频的处理方向」。
    reference_paths: list[str] = []
    if body.reference_urls:
        reference_paths = _freezone._resolve_url_list(project_dir, body.reference_urls)
        _ensure_existing_paths(reference_paths, field_name="reference content")

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
        mode=(
            "allReference"
            if body.reference_mode == "frames" or reference_paths
            else None
        ),
        requester_user_id=ctx.requester_user_id,
    )
    if body.reference_mode == "frames":
        mode_enabled = _freezone._catalog_mode_enabled(capabilities, "all_reference")
        if mode_enabled is False:
            raise HTTPException(
                400,
                "this model does not support frame reference; "
                "switch reference_mode to text",
            )
        if (
            mode_enabled is None
            and not is_freezone_seedance2_backend(backend)
            and not _freezone._catalog_omni_reference_capable(capabilities)
        ):
            raise HTTPException(
                400,
                "frame-referenced video clone currently only supports Seedance 2.0 "
                "models; switch reference_mode to text",
            )
        reference_limits = _freezone._catalog_reference_limits(
            capabilities,
            image_default=9,
            video_default=0,
            audio_default=0,
        )
        if reference_limits["image"] < 1:
            raise HTTPException(400, "this model does not accept image references")
    elif reference_paths:
        # [aipdd fork delta] text 模式但带「参考内容」：参考图随每镜提交，同样
        # 需要全能参考能力（与 frames 模式同一门槛，文案区分两种来源）。
        mode_enabled = _freezone._catalog_mode_enabled(capabilities, "all_reference")
        if mode_enabled is False:
            raise HTTPException(
                400,
                "this model does not support reference content; "
                "remove reference_urls or switch model",
            )
        if (
            mode_enabled is None
            and not is_freezone_seedance2_backend(backend)
            and not _freezone._catalog_omni_reference_capable(capabilities)
        ):
            raise HTTPException(
                400,
                "reference content currently only supports Seedance 2.0 "
                "models; remove reference_urls or switch model",
            )
        reference_limits = _freezone._catalog_reference_limits(
            capabilities,
            image_default=9,
            video_default=0,
            audio_default=0,
        )
        if reference_limits["image"] < 1:
            raise HTTPException(400, "this model does not accept image references")

    try:
        duration_sec = await _freezone.probe_video_duration_seconds(video_path.as_posix())
    except (OSError, ValueError) as exc:
        raise HTTPException(
            400, f"unable to read reference video duration: {exc}"
        ) from exc

    generate_audio = body.audio_mode == "generate"
    if (capabilities or {}).get("supportsGenerateAudio") is False:
        generate_audio = False

    job_id = _freezone._new_job_id()
    payload = {
        "video_path": video_path.as_posix(),
        "prompt": body.prompt.strip(),
        # [aipdd fork delta] 逐镜复刻规划的用户显式选型（文本 LLM）；空 = 系统默认链。
        "model_text": body.model_text.strip(),
        "reference_paths": reference_paths,
        "backend": backend,
        "model_id": body.model,
        "catalog_id": _freezone._catalog_entry_id(capabilities) or "",
        "reference_mode": body.reference_mode,
        # [aipdd fork delta] 带参考内容时的优先级：subject 只提交参考内容（实测关键帧
        # 会把参考内容压掉），composition 关键帧 + 参考内容一起提交。
        "reference_priority": body.reference_priority,
        "audio_mode": body.audio_mode,
        "generate_audio": generate_audio,
        "aspect_ratio": normalize_video_aspect_ratio(body.aspect_ratio),
        "resolution": normalize_video_resolution_for_backend(
            backend,
            body.resolution,
            _freezone._catalog_resolution_options(capabilities),
        ),
        "duration_sec": float(duration_sec),
        "max_frames": body.max_frames,
        "scene_threshold": body.scene_threshold,
        "human_review": body.human_review,
        "scene_optimize": normalize_freezone_seedance2_scene_optimize(
            backend, body.scene_optimize
        ),
        "model_params": model_params,
        "request_schema": request_schema,
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_video_clone(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video clone task", exc)
        raise HTTPException(
            503, f"failed to start freezone video clone task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/mix", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_mix(
    project: str,
    body: FreezoneVideoMixRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：混剪短视频（素材池 → 文案 → 配音 → 字幕 → 顺序循环混剪成片）。

    素材来自本项目资产库（图片/视频混合）；一次提交产出一条成片。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.material_urls:
        raise HTTPException(400, "material_urls is required")
    if len(body.material_urls) > _freezone._MIX_MAX_MATERIALS:
        raise HTTPException(400, f"at most {_freezone._MIX_MAX_MATERIALS} materials per mix")
    material_paths: list[str] = []
    material_kinds: list[str] = []
    for url in body.material_urls:
        try:
            material_path = _freezone.resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not material_path.exists():
            raise HTTPException(404, f"material not found: {material_path}")
        suffix = material_path.suffix.lower()
        if suffix in _freezone._MIX_IMAGE_SUFFIXES:
            material_kinds.append("image")
        elif suffix in _freezone._MIX_VIDEO_SUFFIXES:
            material_kinds.append("video")
        else:
            raise HTTPException(
                400, f"unsupported mix material type: {material_path.name}"
            )
        material_paths.append(material_path.as_posix())

    subject = body.subject.strip()
    script = body.script.strip()
    if len(script) > 10_000:
        raise HTTPException(400, "script must be <= 10000 characters")
    if body.audio_mode == "generate" and not subject and not script:
        raise HTTPException(400, "subject or script is required for a voiced mix")

    bgm_path: Path | None = None
    if body.bgm_url.strip():
        try:
            bgm_path = _freezone.resolve_static_url_to_path(body.bgm_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not bgm_path.exists():
            raise HTTPException(404, f"bgm not found: {bgm_path}")

    # [aipdd fork delta] 与画布音频节点同一时机校验音频模型目录：传了 model
    # 就必须命中后台「默认生成模型」的 audio 目录，提交前定型为内部模型名。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = (
            await _freezone._scoped_media_model_catalog(
                "audio",
                requester_user_id=ctx.requester_user_id,
            )
            or []
        )
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model

    voice_ref_payload = body.voice_ref.model_dump() if body.voice_ref else None
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    job_id = _freezone._new_job_id()
    payload = {
        "material_paths": material_paths,
        "material_kinds": material_kinds,
        "subject": subject,
        "script": script,
        # [aipdd fork delta] 主题自动生成口播文案的用户显式选型（文本 LLM）；空 = 系统默认链。
        "model_text": body.model_text.strip(),
        "audio_mode": body.audio_mode,
        "subtitle_enabled": bool(body.subtitle_enabled),
        "subtitle_font_size": normalize_mix_subtitle_font_size(body.subtitle_font_size),
        "bgm_path": bgm_path.as_posix() if bgm_path else "",
        "bgm_volume": body.bgm_volume,
        "aspect_ratio": normalize_mix_aspect_ratio(body.aspect_ratio),
        "resolution": normalize_mix_resolution(body.resolution),
        "clip_duration": float(body.clip_duration),
        "transition": body.transition,
        "fit_mode": body.fit_mode,
        "max_duration_seconds": (
            float(body.max_duration_seconds)
            if body.max_duration_seconds is not None
            else None
        ),
        "emotion_prompt": body.emotion_prompt,
        "voice_ref": voice_ref_payload,
        # [aipdd fork delta] 选中的音频模型（newapi 内部名）；空=默认引擎。
        "model": selected_audio_model,
        "account_voice_username": account_voice_username,
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    if ctx is not None and body.audio_mode == "generate":
        # 音色解析要读的项目态在这里定型：投递方即存放该项目的那台机器，
        # 执行方不必再回头读项目库（与画布音频节点同一机制）。
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_video_mix",
            extra_config={"voice_ref": voice_ref_payload},
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_video_mix(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video mix task", exc)
        raise HTTPException(
            503, f"failed to start freezone video mix task: {exc}"
        ) from exc


@router.post(
    "/projects/{project}/freezone/whiteboard-animation", tags=[TAG_FREEZONE_VIDEO]
)
async def freezone_whiteboard_animation(
    project: str,
    body: FreezoneWhiteboardAnimationRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 白板动画视频：口播文案 → 分镜 → 逐幕线稿出图 → 逐笔揭示成片。

    一次提交产出一条白板手绘风动画 MP4；图像/配音/文本三类模型均可自选，留空
    走系统默认链。渲染逐笔揭示与拼接/混音全部纯本地（OpenCV + ffmpeg）。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    script = body.script.strip()
    if not script:
        raise HTTPException(400, "script is required")
    if len(script) > 10_000:
        raise HTTPException(400, "script must be <= 10000 characters")

    # 线稿出图模型目录（提交前定型为内部执行身份），与逐镜出图同一机制。
    (
        image_request_schema,
        image_model_params,
        image_catalog_entry,
    ) = await _freezone._resolve_catalog_request(
        "image",
        body.model_image,
        body.image_model_params,
        requester_user_id=ctx.requester_user_id,
    )
    image_provider, image_model = _freezone._catalog_image_execution_selection(
        image_catalog_entry,
        requested_provider=None,
        requested_model=body.model_image or None,
    )

    # 配音模型目录：传了 model 就必须命中后台音频目录，提交前定型为内部模型名。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = (
            await _freezone._scoped_media_model_catalog(
                "audio",
                requester_user_id=ctx.requester_user_id,
            )
            or []
        )
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model

    bgm_path = None
    if body.bgm_url.strip():
        try:
            bgm_path = _freezone.resolve_static_url_to_path(body.bgm_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not bgm_path.exists():
            raise HTTPException(404, f"bgm not found: {bgm_path}")

    voice_ref_payload = body.voice_ref.model_dump() if body.voice_ref else None
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    job_id = _freezone._new_job_id()
    payload = {
        "script": script,
        "model_text": body.model_text.strip(),
        "image_provider": image_provider,
        "image_model": image_model,
        "image_model_params": image_model_params,
        "image_request_schema": image_request_schema,
        "model": selected_audio_model,
        "aspect_ratio": normalize_mix_aspect_ratio(body.aspect_ratio),
        "resolution": normalize_mix_resolution(body.resolution),
        "max_scenes": int(body.max_scenes),
        "pen_cursor": bool(body.pen_cursor),
        "subtitle_enabled": bool(body.subtitle_enabled),
        "subtitle_font_size": normalize_mix_subtitle_font_size(body.subtitle_font_size),
        "emotion_prompt": body.emotion_prompt,
        "voice_ref": voice_ref_payload,
        "bgm_path": bgm_path.as_posix() if bgm_path else "",
        "bgm_volume": body.bgm_volume,
        "account_voice_username": account_voice_username,
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_whiteboard_animation",
            extra_config={"voice_ref": voice_ref_payload},
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_whiteboard_animation(
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
            "failed to start freezone whiteboard animation task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone whiteboard animation task: {exc}"
        ) from exc


@router.post(
    "/projects/{project}/freezone/explainer-video", tags=[TAG_FREEZONE_VIDEO]
)
async def freezone_explainer_video(
    project: str,
    body: FreezoneExplainerVideoRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 科普讲解视频：参考资料 → 解说词 → 受限分镜 → 配音 → MG 成片。

    一次提交产出一条横屏 MG 讲解 MP4（带配音与同步字幕带、章节进度条）。文本模型
    负责「把资料改写成解说词」和「按句子编号选图元」，帧号/字幕块一律本地按朗读
    权重确定性分配；画面全部由 Pillow 逐帧代码绘制 + ffmpeg 编码，**不需要浏览器、
    不需要任何前端渲染框架**，模型侧只用文本 + 音频两类既有网关能力。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        reference_content = resolve_reference_content(body.reference_content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 配音模型目录：传了 model 就必须命中后台音频目录，提交前定型为内部模型名。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = (
            await _freezone._scoped_media_model_catalog(
                "audio",
                requester_user_id=ctx.requester_user_id,
            )
            or []
        )
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model

    bgm_path = None
    if body.bgm_url.strip():
        try:
            bgm_path = _freezone.resolve_static_url_to_path(body.bgm_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not bgm_path.exists():
            raise HTTPException(404, f"bgm not found: {bgm_path}")

    voice_ref_payload = body.voice_ref.model_dump() if body.voice_ref else None
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    job_id = _freezone._new_job_id()
    payload = {
        "reference_content": reference_content,
        "title": body.title.strip(),
        "duration_s": normalize_explainer_duration_s(body.duration_s),
        "model_text": body.model_text.strip(),
        "model": selected_audio_model,
        "background": normalize_explainer_background(body.background),
        "resolution": normalize_mix_resolution(body.resolution),
        "emotion_prompt": body.emotion_prompt,
        "voice_ref": voice_ref_payload,
        "bgm_path": bgm_path.as_posix() if bgm_path else "",
        "bgm_volume": body.bgm_volume,
        "account_voice_username": account_voice_username,
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type="freezone_explainer_video",
            extra_config={"voice_ref": voice_ref_payload},
        )
        payload.update(projection_payload)
    try:
        return await _freezone._enqueue_or_start_freezone_explainer_video(
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
            "failed to start freezone explainer video task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone explainer video task: {exc}"
        ) from exc


@router.post("/projects/{project}/freezone/video/idea", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_idea(
    project: str,
    body: FreezoneVideoIdeaRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：一句话生成短视频（ViMax idea2video 口径）。

    一句话创意 → 故事扩写 → 角色提取与三视图 → 分场景脚本 → 分镜/机位树/
    首尾帧 → 逐镜视频 → 拼接成片。帧与肖像走图片模型目录，逐镜视频走视频
    模型目录；全自动成片，一次提交产出完整视频。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    idea = body.idea.strip()
    if not idea:
        raise HTTPException(400, "idea is required")
    if len(idea) > 2000:
        raise HTTPException(400, "idea must be <= 2000 characters")

    # 图片模型目录：角色三视图与逐镜帧（提交前定型为内部执行身份）。
    (
        image_request_schema,
        image_model_params,
        image_catalog_entry,
    ) = await _freezone._resolve_catalog_request(
        "image",
        body.model_image,
        body.image_model_params,
        requester_user_id=ctx.requester_user_id,
    )
    image_provider, image_model = _freezone._catalog_image_execution_selection(
        image_catalog_entry,
        requested_provider=None,
        requested_model=body.model_image or None,
    )

    # 视频模型目录：逐镜生成（首帧/首尾帧口径）。
    try:
        backend = await _freezone._resolve_catalog_video_backend(
            body.model_video,
            requester_user_id=ctx.requester_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request_schema, model_params, capabilities = await _freezone._resolve_catalog_request(
        "video",
        body.model_video,
        body.model_params,
        mode="firstLastFrame",
        requester_user_id=ctx.requester_user_id,
    )

    generate_audio = bool(body.generate_audio)
    if (capabilities or {}).get("supportsGenerateAudio") is False:
        generate_audio = False

    job_id = _freezone._new_job_id()
    payload = {
        "idea": idea,
        "style": body.style.strip(),
        "user_requirement": body.user_requirement.strip(),
        # 文本步骤（故事/角色/脚本/分镜等）的用户显式选型；留空走系统默认链。
        "model_text": body.model_text.strip(),
        "max_scenes": body.max_scenes,
        "max_shots_per_scene": body.max_shots_per_scene,
        "shot_duration": float(body.shot_duration),
        # [需求6] 总时长口径：工具页只传目标总时长，分场/分镜交给 AI。
        "total_duration": (
            float(body.total_duration) if body.total_duration is not None else None
        ),
        "portrait_mode": body.portrait_mode,
        "image_provider": image_provider,
        "image_model": image_model,
        "image_model_params": image_model_params,
        "image_request_schema": image_request_schema,
        "backend": backend,
        "model_id": body.model_video,
        "catalog_id": _freezone._catalog_entry_id(capabilities) or "",
        "aspect_ratio": normalize_video_aspect_ratio(body.aspect_ratio),
        "resolution": normalize_video_resolution_for_backend(
            backend,
            body.resolution,
            _freezone._catalog_resolution_options(capabilities),
        ),
        "generate_audio": generate_audio,
        "human_review": body.human_review,
        "scene_optimize": normalize_freezone_seedance2_scene_optimize(
            backend, body.scene_optimize
        ),
        "model_params": model_params,
        "request_schema": request_schema,
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_video_idea(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone video idea task", exc)
        raise HTTPException(
            503, f"failed to start freezone video idea task: {exc}"
        ) from exc


# ============================================================
# 抖音文案提取（工具页）
# ============================================================


@router.get(
    "/projects/{project}/freezone/douyin-copywrite/models",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_douyin_copywrite_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """抖音文案提取：可用 ASR 模型列表（网关拉取的模型 list + 内置直连通道）。"""
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    from novelvideo.freezone.douyin_copywrite import list_douyin_asr_models

    return {"ok": True, "data": list_douyin_asr_models()}


@router.post(
    "/projects/{project}/freezone/douyin-copywrite",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_douyin_copywrite(
    project: str,
    body: FreezoneDouyinCopywriteRequest,
    user: dict = Depends(get_api_user),
):
    """抖音文案提取：分享链接 → 视频详情解析 → 选定 ASR 模型转写口播文案。

    模型列表由网关拉取的模型清单（本地渠道快照）过滤 ASR 同类模型得到，
    外加内置 FunASR / 豆包语音通道；[aipdd fork delta] 需求 7：工具页不设
    默认模型（必须显式选择）；请求体 model 留空仅作程序化调用（agent /
    旧客户端）的兜底，回落内置 FunASR。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    video = body.video.strip()
    if not video:
        raise HTTPException(400, "video is required")

    from novelvideo.freezone.douyin_copywrite import (
        FUNASR_CANDIDATE_ID,
        list_douyin_asr_models,
    )

    # [aipdd fork delta] 留空兜底内置 FunASR：工具页已无「默认」项（必须显式
    # 选择），这里是 agent / 旧客户端等程序化调用的兼容路径。
    requested = str(body.model or "").strip() or FUNASR_CANDIDATE_ID
    candidates = list_douyin_asr_models()
    candidate = next((item for item in candidates if item["id"] == requested), None)
    if candidate is None:
        raise HTTPException(
            409,
            "ASR 模型不在可用列表里（基于网关拉取的模型 list），请刷新后重选",
        )

    job_id = _freezone._new_job_id()
    payload = {
        "video": video,
        "model": candidate["id"],
        "source_tool": body.source_tool,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_douyin_copywrite(
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
            "failed to start freezone douyin copywrite task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone douyin copywrite task: {exc}"
        ) from exc


# ============================================================
# 音视频提取文案（工具页，req21）
# ============================================================

# 链接来源平台白名单（仅用于抖音双通路开关与 yt-dlp 提示；其余交 yt-dlp 自识别）。
_MEDIA_COPYWRITE_PLATFORMS = {
    "douyin",
    "bilibili",
    "kuaishou",
    "xiaohongshu",
    "tiktok",
    "youtube",
    "other",
}
_MEDIA_COPYWRITE_SOURCE_TYPES = {"link", "upload_video", "upload_audio"}


@router.get(
    "/projects/{project}/freezone/media-copywrite/models",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_media_copywrite_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """音视频提取文案：可用 ASR 模型列表（与抖音文案提取同一候选源）。"""
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    from novelvideo.freezone.douyin_copywrite import list_douyin_asr_models

    return {"ok": True, "data": list_douyin_asr_models()}


@router.post(
    "/projects/{project}/freezone/media-copywrite",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_media_copywrite(
    project: str,
    body: FreezoneMediaCopywriteRequest,
    user: dict = Depends(get_api_user),
):
    """音视频提取文案：链接 / 上传视频 / 上传音频 → 选定 ASR 模型转写口播文案。

    [aipdd fork delta] req21：把抖音文案提取泛化为通用媒体转写——链接统一走
    yt-dlp（抖音双通路回退宿主真实 Chrome 的 CDP 拦截，不引入 Playwright、不
    额外安装浏览器），上传视频 / 音频先分离音频再转写；下载
    视频与分离音频由 runner 自动归档进资产库。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    source_type = str(body.source_type or "").strip().lower()
    if source_type not in _MEDIA_COPYWRITE_SOURCE_TYPES:
        raise HTTPException(400, "source_type 必须是 link / upload_video / upload_audio")
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "url is required")
    platform = str(body.platform or "").strip().lower()
    if platform and platform not in _MEDIA_COPYWRITE_PLATFORMS:
        platform = "other"

    from novelvideo.freezone.douyin_copywrite import (
        FUNASR_CANDIDATE_ID,
        list_douyin_asr_models,
    )

    requested = str(body.model or "").strip() or FUNASR_CANDIDATE_ID
    candidates = list_douyin_asr_models()
    candidate = next((item for item in candidates if item["id"] == requested), None)
    if candidate is None:
        raise HTTPException(
            409,
            "ASR 模型不在可用列表里（基于网关拉取的模型 list），请刷新后重选",
        )

    job_id = _freezone._new_job_id()
    payload = {
        "source_type": source_type,
        "url": url,
        "platform": platform,
        "model": candidate["id"],
        "source_tool": body.source_tool,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_media_copywrite(
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
            "failed to start freezone media copywrite task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone media copywrite task: {exc}"
        ) from exc
