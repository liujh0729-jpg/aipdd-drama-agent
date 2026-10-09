# ruff: noqa: F401
"""画面/网格/视频生成端点。"""

import json
import io
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.api.deps import (
    get_user_base_dir,
    get_state_dir,
    make_sqlite_store_for_context,
    make_sqlite_store,
    make_static_url_for_context,
    resolve_project_scope,
    sqlite_store_for_context_scope,
    sqlite_store_scope,
)
from novelvideo.api.schemas import (
    GlobalOptimizeRequest,
    VideoGenerateRequest,
    VideoBackendOption,
    VideoComposeRequest,
    TTSGenerateRequest,
    TTSPreviewRequest,
    SketchGenerateRequest,
    GridRegenerateRequest,
    BeatsRegenerateRequest,
    SketchRegenerateRequest,
    SingleVideoRequest,
    PoolSelectRequest,
    VideoPoolSelectRequest,
    GridCutRequest,
    GridSketchPreviewRequest,
    PlanEntryOut,
    OperatorPasswordVerifyRequest,
    RenderPlanExecuteRequest,
    RenderPlanExecuteResponse,
    RenderPlanRequest,
    RenderPlanResponse,
    RenderSettingsUpdate,
    SketchRegenQueueUpdate,
    SketchSettingsUpdate,
    BeatBackgroundAnchorUpdate,
    Seedance2AssetAudioTrimRequest,
    Seedance2AssetCropRequest,
    Seedance2AssetDeleteRequest,
)
from novelvideo.utils.source_language import detect_episode_asset_language
from novelvideo.api.viewer_manifests import (
    build_director_stage_manifest,
    build_pano_viewer_manifest,
    default_director_stage_palette,
)
from novelvideo.generators.grid_generation import (
    build_regen_plan,
    compute_input_fingerprint,
    hash_plan,
)
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.manual_shots import pick_beats_by_number
from novelvideo.render_plan.ref_image_hash import RefImageHasher
from novelvideo.seedance2_i2v.pipeline import (
    is_huimeng_seedance2_backend,
    prepare_seedance2_generation_inputs,
)
from novelvideo.seedance2_i2v.voice_clone import normalize_seedance2_audio_type
from novelvideo.project_config import load_project_config, save_project_config
from novelvideo.project_context import ProjectContext
from novelvideo.ports import get_task_backend, get_usage_meter
from novelvideo.task_backend.limit_logging import log_task_limit_rejection
from novelvideo.task_backend.limits import (
    ChannelTaskLimitExceeded,
    ProjectTaskLimitExceeded,
    ProjectUserTaskLimitExceeded,
    UserTaskLimitExceeded,
)
from novelvideo.task_identity import project_task_state_key
from novelvideo.models import beat_scene_id
from novelvideo.services.background_anchor_service import (
    BackgroundAnchorError,
    build_background_anchors_payload,
    crop_background_anchor_to_selected,
    save_uploaded_background_anchor_image,
    select_background_anchor,
)
from novelvideo.utils.path_resolver import PathResolver, compute_identity_path, compute_portrait_path



# ── 子模块 re-export（保持原模块公共属性面）──
from ._common import (
    _TemporaryFileResponse,
    logger,
    _single_render_mode_from_sketch,
    _resolve_generation_project,
    _requester_user_id,
    _color_assignment_requires_full_sketch_clean,
    normalize_beat_indices,
    validate_beat_indices,
    _render_plan_feature_disabled,
    _resolve_render_image_selection,
    _resolve_sketch_image_selection,
    _resolve_render_bool_setting,
    _render_settings_payload,
    _sketch_settings_payload,
    _plan_entry_to_dict,
    _plan_to_dicts,
    _parse_grid_beat_numbers,
    _safe_grid_token,
    _uploaded_grid_filename,
    _safe_grids_file,
    _find_pool_grid_entry,
    _FANOUT_ACTIVE_LIMIT_ERRORS,
    _FanoutLimitError,
    _task_limit_reason,
    _log_partial_dispatch_rejection,
    _task_limit_rejection,
    _custom_render_plan_error,
)
from .prepare import (
    _read_uploaded_rgb_image,
    _register_uploaded_pool_image,
    _runtime_prop_menu_with_global_props,
    _prop_marker_colors_from_menu,
    _episode_from_store_or_none,
    _build_character_map,
    _validate_seedance_pro_dialogue_only,
    _is_seedance2_backend,
    _is_happyhorse_backend,
    _is_grok_video_backend,
    _seedance2_api_resolution,
    SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL,
    SEEDANCE2_DEFAULT_RESOLUTION_OPTIONS,
    HAPPYHORSE_RESOLUTION_OPTIONS,
    HAPPYHORSE_RATIO_OPTIONS,
    HAPPYHORSE_SUPPORTED_MODES,
    GROK_VIDEO_RESOLUTION_OPTIONS,
    GROK_VIDEO_RATIO_OPTIONS,
    GROK_VIDEO_SUPPORTED_MODES,
    _seedance2_model_from_backend,
    _seedance2_resolution_options_for_backend,
    _seedance2_resolution_for_backend,
    _happyhorse_resolution_for_backend,
    _happyhorse_ratio_for_backend,
    _grok_video_resolution_for_backend,
    _grok_video_ratio_for_backend,
    _seedance2_initial_prompt,
    _legacy_video_prompt_for_mode,
    _missing_video_prompt_error,
    SEEDANCE2_SINGLE_VIDEO_CONFIG_FIELDS,
    _seedance2_request_config_overrides,
    _merge_seedance2_request_config,
    _api_audio_duration_seconds,
    _prepare_seedance2_api_beat,
    _prepare_happyhorse_api_beat,
    _prepare_grok_video_api_beat,
)
from .seedance2 import (
    _seedance2_asset_status_payload,
    _seedance2_returned_last_frame_status_payload,
    _seedance2_voice_status_payload,
    _seedance2_panel_context,
    _seedance2_status_response,
    get_seedance2_beat_status,
    upload_seedance2_asset,
    delete_seedance2_asset,
    crop_seedance2_asset,
    trim_seedance2_audio_asset,
)
from .backends import (
    _api_video_backend_options,
    _catalog_positive_int,
    _catalog_str_list,
    _api_video_backend_options_from_catalog,
    get_video_backend_options,
    get_render_settings,
    update_render_settings,
    get_sketch_settings,
    update_sketch_settings,
)
from .episode_media import (
    _sketch_regen_queue_key,
    _is_react_sketch_regen_queue_items,
    _react_sketch_regen_queues,
    _sketch_regen_queue_payload,
    get_sketch_regen_queue,
    update_sketch_regen_queue,
    get_sketch_image_usage,
    _image_generation_guard_payload,
    get_image_generation_guard,
    verify_image_generation_guard_password,
    compose_video,
    get_final_video,
    generate_tts,
    preview_tts,
    list_tts_voices,
    generate_sketches,
    _audio_generation_plan,
    _voice_prereq_error_response,
    generate_audio,
)
from .grid_ops import (
    _global_optimize_pending_beat_numbers,
    global_optimize_video,
    regenerate_grid,
)
from .render import (render_plan, render_execute, regenerate_beats, regenerate_sketches)
from .director import (
    _canonical_sketch_path,
    _canonical_sketch_url,
    _director_control_scope,
    _director_control_payload,
    _director_overlay_beat_context,
    _director_same_scene_beats,
    _director_overlay_payload,
    _director_overlay_status_payload,
    _decode_png_data_url,
    _director_control_frame_export_payload,
    _episode_beat_from_resolution,
    _beat_scene_name,
    _api_background_reference_url_builder,
    _api_background_anchor_url_builder,
    _background_anchors_payload,
    get_beat_pano_background_manifest,
    get_default_director_stage_palette,
    get_beat_director_stage_manifest,
    get_beat_director_stage_overlay,
    save_beat_director_stage_overlay,
    export_beat_director_stage_control_frame,
    get_beat_background_anchors,
    update_beat_background_anchor,
    crop_beat_background_anchor,
    upload_beat_background_anchor,
    get_director_control_frame_status,
    director_control_to_sketch,
)
from .sketch_ops import (
    get_sketch_pose_editor,
    save_sketch_pose_editor,
    crop_current_sketch,
    generate_missing_manual_sketches,
)
from .video_ops import generate_single_video
from .pool import (
    list_video_pool,
    select_video_pool,
    list_grids,
    rebuild_grids_pool_index,
    get_beat_sketch_candidates,
    select_pool_image,
    upload_beat_sketch,
    upload_beat_render,
)
from .media_exports import (
    regenerate_beat_audio,
    export_srt,
    export_final_video,
    upload_grid,
    export_grid_prompt,
    sketch_grid_preview,
    cut_grid,
    export_zip,
)
from .sketch_colors import (assign_sketch_colors, detect_sketch_identities)
from .seedance2 import router as _router_seedance2
from .backends import router as _router_backends
from .episode_media import router as _router_episode_media
from .grid_ops import router as _router_grid_ops
from .render import router as _router_render
from .director import router as _router_director
from .sketch_ops import router as _router_sketch_ops
from .video_ops import router as _router_video_ops
from .pool import router as _router_pool
from .media_exports import router as _router_media_exports
from .sketch_colors import router as _router_sketch_colors


@asynccontextmanager
async def generation_store_scope(ctx, username: str, project: str):
    """generation 子模块统一的「按请求作用域 + 自动关闭」SQLiteStore。

    历史写法在每个 handler 里重复一段三元裸工厂：
    ``store = (await make_sqlite_store_for_context(ctx) if ctx
    else await make_sqlite_store(username, project))``，然后把关闭责任
    丢给调用点——抄漏一处就是一条静默泄漏的 SQLite 连接 + 后台线程。
    这里把生命周期收口到一个上下文：handler 只需 ``async with
    generation_store_scope(ctx, username, project) as store:``。

    与 :func:`novelvideo.api.deps.sqlite_store_for_context_scope` 不同，本
    helper 故意按名字直接读 ``generation`` 模块全局的工厂（而非 deps
    全局）：保留 generation 测试以「monkeypatch generation.make_*store*」为
    既定接缝的习惯，迁移时无需扫一遍测试把接缝改到 deps。
    """
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project)
    )
    try:
        yield store
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


router = APIRouter()
router.include_router(_router_seedance2)
router.include_router(_router_backends)
router.include_router(_router_episode_media)
router.include_router(_router_grid_ops)
router.include_router(_router_render)
router.include_router(_router_director)
router.include_router(_router_sketch_ops)
router.include_router(_router_video_ops)
router.include_router(_router_pool)
router.include_router(_router_media_exports)
router.include_router(_router_sketch_colors)
