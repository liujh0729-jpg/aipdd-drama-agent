"""freezone.gen_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneFrameFromContextRequest
from novelvideo.api.schemas import FreezoneGenRequest
from novelvideo.api.schemas import FreezoneImageCameraConfig
from novelvideo.api.schemas import FreezoneImageStyleConfig
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneScene360Request
from novelvideo.api.schemas import FreezoneSketchFromContextRequest
from novelvideo.freezone.route_helpers import FREEZONE_DEFAULT_IMAGE_MODEL
from novelvideo.freezone.route_helpers import infer_scene_id_from_master_path as _infer_scene_id_from_master_path
from novelvideo.freezone.route_helpers import merge_prompt_with_style_and_camera as _merge_prompt_with_style_and_camera
from novelvideo.freezone.route_helpers import resolve_freezone_image_provider as _resolve_freezone_image_provider
from novelvideo.freezone.route_helpers import split_provider_and_model as _split_provider_and_model
from novelvideo.freezone.video_node import normalize_freezone_seedance2_scene_optimize
from novelvideo.freezone.video_node import validate_omni_reference_audio_durations
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key

from ._common import TAG_FREEZONE_IMAGE

router = APIRouter()


async def _start_or_enqueue_freezone_video_gen(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    prompt: str,
    reference_items: list[dict],
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int | None,
    generate_audio: bool,
    human_review: bool,
    scene_optimize: str | None,
    backend: str,
    last_frame_path: str | None = None,
    audio_setting: str | None = None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    catalog_id: str | None = None,
    gen_mode: str | None = None,
    requested_gen_mode: str | None = None,
    model_params: dict[str, Any] | None = None,
    request_schema: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    archive_to_library: bool = False,
    source_tool: str = "",
) -> dict:

    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "video",
            catalog_id or model_id or backend,
            requester_user_id=ctx.requester_user_id,
        )

    from novelvideo.freezone.reference_validation import validate_reference_media

    validation_items = list(reference_items)
    if last_frame_path and not any(
        item.get("path") == last_frame_path for item in validation_items
    ):
        validation_items.append(
            {"type": "image", "path": last_frame_path, "role": "last_frame"}
        )
    reference_errors = await asyncio.to_thread(
        validate_reference_media, validation_items, capabilities or {}, project_dir
    )
    if reference_errors:
        raise HTTPException(
            400, detail={"code": "REFERENCE_MEDIA_INVALID", "errors": reference_errors}
        )

    # Catalog fields are optional for backward compatibility. Missing means
    # the legacy behavior (native audio supported); an explicit false is an
    # authoritative capability boundary and cannot be bypassed by old clients.
    effective_generate_audio = bool(generate_audio)
    if (capabilities or {}).get("supportsGenerateAudio") is False:
        effective_generate_audio = False

    video_input_paths = [
        str(item.get("path") or "").strip()
        for item in reference_items
        if str(item.get("type") or "").strip().lower() == "video"
        and str(item.get("path") or "").strip()
    ]
    video_duration_bounds = _freezone._catalog_reference_duration_bounds(capabilities, "video")
    measured_video_durations: list[tuple[str, float]] | None = None
    try:
        if video_input_paths and any(value is not None for value in video_duration_bounds):
            video_seconds = await asyncio.gather(
                *(_freezone.probe_video_duration_seconds(path) for path in video_input_paths)
            )
            measured_video_durations = [
                (Path(path).name or f"video{index}", seconds)
                for index, (path, seconds) in enumerate(
                    zip(video_input_paths, video_seconds), start=1
                )
            ]
            input_video_duration_seconds = sum(video_seconds)
        else:
            input_video_duration_seconds = await _freezone.probe_total_video_duration_seconds(
                video_input_paths
            )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            400, f"unable to read reference video duration: {exc}"
        ) from exc
    if measured_video_durations is not None:
        try:
            validate_omni_reference_audio_durations(
                measured_video_durations,
                min_seconds=video_duration_bounds[0],
                max_seconds=video_duration_bounds[1],
                total_min_seconds=video_duration_bounds[2],
                total_max_seconds=video_duration_bounds[3],
                media_label="video",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    normalized_mode = str(gen_mode or "").strip()
    if normalized_mode == "video_edit":
        if not video_input_paths or input_video_duration_seconds <= 0:
            raise HTTPException(400, "video editing requires a readable source video")
        # 视频编辑的输出时长跟随主输入视频。输入和输出沿用同一套整秒
        # 计费口径，不为视频编辑额外引入向上取整规则；发给 NewAPI 的
        # 最终协议仍会强制使用 duration=auto。
        effective_duration_seconds = max(
            int(math.floor(input_video_duration_seconds)),
            1,
        )
    elif duration_seconds is not None:
        effective_duration_seconds = max(int(duration_seconds), 1)
    else:
        raise HTTPException(400, "duration_seconds is required for this video mode")

    # Duration probing above may await external media inspection. Recheck the
    # authoritative organization scope after that asynchronous boundary so a
    # model revoked in the meantime cannot be billed or enqueued.
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "video",
            catalog_id or model_id or backend,
            requester_user_id=ctx.requester_user_id,
        )

    payload = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "model_id": model_id or "",
        "catalog_id": catalog_id or "",
        "gen_mode": gen_mode or "",
        "requested_gen_mode": requested_gen_mode or gen_mode or "",
        "prompt": prompt,
        "reference_items": reference_items,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration_seconds": effective_duration_seconds,
        "generate_audio": effective_generate_audio,
        "human_review": human_review,
        "scene_optimize": normalize_freezone_seedance2_scene_optimize(
            backend, scene_optimize
        ),
        "backend": backend,
        "last_frame_path": last_frame_path,
        "audio_setting": audio_setting or "",
        "project_dir": str(project_dir),
        "model_params": model_params or {},
        "request_schema": request_schema or {},
        # [aipdd fork delta] 工具页批量任务标记：产物完成后 runner 会把它登记进
        # 资产库「待分类资产」；画布等其它入口不带该键。
        **({"archive_to_library": True} if archive_to_library else {}),
        # [aipdd fork delta] 工具页任务归属：来自哪个功能（取工具 kind），
        # 经 display_metadata_for_task 浮现到 metadata.source_tool，任务列表据此
        # 把 omni 类共享 task_type=freezone_video_gen 的任务只显示在产生它的功能页。
        **({"source_tool": source_tool} if source_tool else {}),
    }
    if ctx is not None:
        queued = await _freezone.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="freezone",
            task_type="freezone_video_gen",
            queue_kind="video",
            episode=0,
            scope=job_id,
            payload=payload,
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_gen",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "freezone_video_gen", ctx.project_id, 0, scope=job_id
                ),
                "backend": queued.backend,
                "queue": queued.queue,
            },
        }

    _freezone._raise_project_context_required("freezone_video_gen")


async def _start_or_enqueue_freezone_image_to_3gs(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    scene_id: str,
    source_path: Path,
    source_kind: str,
    params: dict,
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> dict:
    task_type = "freezone_image_to_3gs"
    payload = {
        "job_id": job_id,
        "scene_id": scene_id,
        "source_path": source_path.as_posix(),
        "source_kind": source_kind,
        "params": params,
        "project_dir": str(project_dir),
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
    }
    if ctx is not None:
        queued = await _freezone.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="freezone",
            task_type=task_type,
            queue_kind="world",
            episode=0,
            scope=job_id,
            payload=payload,
        )
        return {
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(task_type, ctx.project_id, 0, scope=job_id),
            "backend": queued.backend,
            "queue": queued.queue,
        }

    _freezone._raise_project_context_required(task_type)


async def _start_or_enqueue_freezone_gen_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
    reference_urls: list[str],
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
    task_display: dict[str, str] | None = None,
    model_params: dict[str, Any] | None = None,
    request_schema: dict[str, Any] | None = None,
    archive_to_library: bool = False,
    source_tool: str = "",
) -> dict:
    if ctx is not None:
        await _freezone._require_scoped_media_model(
            "image",
            catalog_id or model_id or model or FREEZONE_DEFAULT_IMAGE_MODEL,
            requester_user_id=ctx.requester_user_id,
        )
    reference_paths = _freezone._resolve_url_list(project_dir, reference_urls)
    for path_text in reference_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    job_id = _freezone._new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(provider, model)
    normalized_provider = _resolve_freezone_image_provider(resolved_provider)

    prompt_text = _merge_prompt_with_style_and_camera(prompt, style, camera)
    display_payload = {
        "task_family": "freezone_canvas",
        "task_label": "自由生成图片",
        "display_name": "自由生成图片",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await _freezone.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="freezone",
            task_type="freezone_gen",
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "prompt": prompt_text,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "reference_paths": reference_paths,
                "provider": normalized_provider,
                "model": resolved_model,
                "quality": quality,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or "",
                "catalog_id": catalog_id or "",
                "gen_mode": gen_mode or "",
                "model_params": model_params or {},
                "request_schema": request_schema or {},
                **({"archive_to_library": True} if archive_to_library else {}),
                **({"source_tool": source_tool} if source_tool else {}),
                **display_payload,
            },
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_gen",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "freezone_gen", ctx.project_id, 0, scope=job_id
                ),
                "backend": queued.backend,
                "queue": queued.queue,
            },
        }

    _freezone._raise_project_context_required("freezone_gen")


@router.post(
    "/projects/{project}/freezone/gen",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_gen(
    project: str,
    body: FreezoneGenRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：启动文生图任务，返回可供 SSE 追踪的 `task_key`。"""
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
    reference_image_max = (
        catalog_entry.get("referenceImageMax") if catalog_entry else None
    )
    if (
        type(reference_image_max) is int
        and reference_image_max >= 0
        and len(body.reference_urls or []) > reference_image_max
    ):
        raise HTTPException(
            400,
            f"image model supports at most {reference_image_max} reference images",
        )
    from novelvideo.freezone.reference_validation import validate_reference_media

    reference_errors = await asyncio.to_thread(
        validate_reference_media,
        [
            {"type": "image", "path": path}
            for path in _freezone._resolve_url_list(project_dir, list(body.reference_urls or []))
        ],
        catalog_entry or {},
        project_dir,
    )
    if reference_errors:
        raise HTTPException(
            400, detail={"code": "REFERENCE_MEDIA_INVALID", "errors": reference_errors}
        )
    return await _start_or_enqueue_freezone_gen_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=body.prompt,
        aspect_ratio=body.aspect_ratio,
        image_size=body.image_size,
        reference_urls=list(body.reference_urls or []),
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


@router.post(
    "/projects/{project}/freezone/sketch-from-context",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_sketch_from_context(
    project: str,
    body: FreezoneSketchFromContextRequest,
    user: dict = Depends(get_api_user),
):
    """主线上下文：从 Beat / 背景 / 导演合成图生成草图候选。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    beat = await _freezone._load_freezone_beat_context(
        ctx=ctx,
        username=username,
        project=project_name,
        episode=body.episode,
        beat=body.beat,
    )
    source_url = (body.source_url or "").strip()
    source_label = {
        "beat": "Beat 上下文",
        "selected_background": "当前背景",
        "director_combined": "导演合成图",
        "background_candidate": "背景候选",
    }.get(body.source_kind, "输入参考")
    task_display = {
        "task_family": "mainline_skill",
        "task_label": "生成草图",
        "display_name": f"生成草图 · EP{body.episode} / Beat {body.beat}",
        "source_label": source_label,
        "target_label": "当前草图",
        "skill_id": "freezone.sketch_from_context",
    }
    if body.source_kind == "director_combined":
        if not source_url:
            raise HTTPException(400, "source_url is required for director_combined")
        return await _freezone._start_or_enqueue_mainline_director_control_sketch_job(
            ctx=ctx,
            project_dir=project_dir,
            episode=body.episode,
            beat=body.beat,
            director_combined_url=source_url,
            aspect_ratio=body.aspect_ratio,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            task_display={
                **task_display,
                "skill_id": "freezone.sketch_from_director_combined",
                "source_label": "导演合成图",
            },
        )
    if source_url:
        return await _freezone._start_or_enqueue_mainline_sketch_from_context_job(
            ctx=ctx,
            username=username,
            project_name=project_name,
            project_dir=project_dir,
            episode=body.episode,
            beat=body.beat,
            beat_payload=beat,
            background_url=source_url,
            aspect_ratio=body.aspect_ratio,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            task_display=task_display,
        )
    return await _freezone._start_or_enqueue_mainline_beat_sketch_task(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        episode=body.episode,
        beat=body.beat,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        task_display=task_display,
    )


@router.post(
    "/projects/{project}/freezone/frame-from-context",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_frame_from_context(
    project: str,
    body: FreezoneFrameFromContextRequest,
    user: dict = Depends(get_api_user),
):
    """主线上下文：从草图和可选背景生成分镜候选。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    beat = await _freezone._load_freezone_beat_context(
        ctx=ctx,
        username=username,
        project=project_name,
        episode=body.episode,
        beat=body.beat,
    )
    return await _freezone._start_or_enqueue_mainline_frame_from_context_job(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        episode=body.episode,
        beat=body.beat,
        beat_payload=beat,
        sketch_url=body.sketch_url,
        reference_urls=[body.background_url] if body.background_url else [],
        extra_reference_urls=[*body.identity_urls, *body.prop_urls],
        identity_references=[],
        prop_references=[],
        aspect_ratio=body.aspect_ratio,
        quality=body.quality,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        task_display={
            "task_family": "mainline_skill",
            "task_label": "渲染分镜",
            "display_name": f"渲染分镜 · EP{body.episode} / Beat {body.beat}",
            "source_label": "草图 + 背景 + 身份/道具",
            "target_label": "当前分镜",
            "skill_id": "freezone.frame_from_context",
        },
    )


@router.post(
    "/projects/{project}/freezone/scene-360",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_scene_360(
    project: str,
    body: FreezoneScene360Request,
    user: dict = Depends(get_api_user),
):
    """图片处理：基于场景 master 源图生成 2:1 的 360 全景候选图。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    base_paths = _freezone._resolve_url_list(project_dir, [body.reference_url])
    if not base_paths:
        raise HTTPException(400, "reference_url is required")
    base_path = Path(base_paths[0])
    scene_id = _infer_scene_id_from_master_path(base_path, project_dir)
    if not scene_id:
        raise HTTPException(400, "could not infer scene_id from reference_url")
    # 与 /freezone/gen 同一口径：EE 里目录是权威的，模型和目录身份都要在目录里
    # 对得上才放行。原来这条路由直接采信 body —— 客户端可以随便报一个便宜的
    # catalog_id 配一个贵的 model，报价按前者、执行按后者。
    _schema, _params, catalog_entry = await _freezone._resolve_catalog_request(
        "image",
        body.catalog_id or body.model,
        None,
        mode="image_to_image",
        requester_user_id=ctx.requester_user_id,
    )
    # 只取模型：这条老路由的 provider 是 scene_360_builder 那边按环境变量解析的
    # （`resolve_scene_360_image_provider`），不走网关那套 provider/model 组合，
    # 所以这里必须发裸 apiModel，不能带 provider 前缀。
    _catalog_provider, catalog_model = _freezone._catalog_image_execution_selection(
        catalog_entry,
        requested_provider=None,
        requested_model=body.model,
    )
    kwargs = {
        "ctx": ctx,
        "project_dir": project_dir,
        "scene_id": scene_id,
        "description": None,
        "master_url": body.reference_url,
        "reverse_url": body.reverse_reference_url,
        "model": catalog_model or body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
        # 画布面板按所选模型实际支持的档位报价并把它发下来；这里在 2K 上限内
        # 原样使用，否则「报价 1K、执行 2K」，媒体模型目录配的分辨率在这条路由
        # 等于没生效。
        "image_size": _freezone._cap_mainline_scene_360_image_size(body.image_size),
        "quality": body.quality,
        # 计费身份以目录条目为准，不采信 body —— 它直接决定按哪条目录规则扣费。
        "catalog_id": _freezone._catalog_entry_id(catalog_entry) or body.catalog_id or None,
        # Only an entry resolved by the server catalog may authorize a dynamic
        # execution model. A client-supplied legacy catalog_id is legacy data,
        # not model authority.
        "execution_catalog_id": _freezone._catalog_entry_id(catalog_entry) or None,
        "canvas_id": body.canvas_id or None,
        "node_id": body.node_id or None,
        "task_display": {
            "task_family": "mainline_skill",
            "task_label": "生成 360 全景",
            "display_name": f"生成 360 全景 · {scene_id or '场景'}",
            "source_label": "Master + Reverse",
            "target_label": "360 全景",
            "skill_id": "freezone.scene_360",
        },
    }
    if body.mode == "commit":
        return await _freezone._start_or_enqueue_mainline_scene_360_task(
            **kwargs,
            auto_commit=True,
        )
    return await _freezone._start_or_enqueue_mainline_scene_360_candidate_job(**kwargs)
