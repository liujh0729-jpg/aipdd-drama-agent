# ruff: noqa: F401
"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
SuperTale 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import math
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    make_cognee_store_for_context,
    make_sqlite_store,
    make_sqlite_store_for_context,
    make_static_url_for_context,
    sqlite_store_for_context_scope,
)
from novelvideo.api.schemas import (
    CanvasPayload,
    CreateIdentityAssetRequest,
    FreezoneAnalyzeShotsRequest,
    FreezoneAnalyzeVideoStoryRequest,
    FreezoneAssetCopyRequest,
    FreezoneAssetLibraryFolderPatchRequest,
    FreezoneAssetLibraryFolderRequest,
    FreezoneAssetLibraryItemPatchRequest,
    FreezoneAudioMusicRequest,
    FreezoneAudioSeparateRequest,
    FreezoneAudioSpeechRequest,
    FreezoneCharacterMultiViewRequest,
    FreezoneDouyinCopywriteRequest,
    FreezoneEditRequest,
    FreezoneExplainerVideoRequest,
    FreezoneExtractFramesRequest,
    FreezoneFrameFromContextRequest,
    FreezoneGenRequest,
    FreezoneImageCameraConfig,
    FreezoneImageReversePromptRequest,
    FreezoneImageStyleConfig,
    FreezoneImageTo3GSRequest,
    FreezoneImageToVideoRequest,
    FreezoneJobAcceptedResponse,
    FreezoneKeyframeVideoRequest,
    FreezoneMarkDetectRequest,
    FreezoneMarkDetectResponse,
    FreezoneOutpaintRequest,
    FreezoneRedrawRequest,
    FreezoneRelightRequest,
    FreezoneScene360Request,
    FreezoneSketchFromContextRequest,
    FreezoneStageAssetAcceptedResponse,
    FreezoneStoryScriptCharacterRef,
    FreezoneStoryScriptGenerateRequest,
    FreezoneTemplateEditRequest,
    FreezoneTextGenerateRequest,
    FreezoneTextTranslateRequest,
    FreezoneThreeDViewerScreenshotRequest,
    FreezoneUpscaleRequest,
    FreezoneVideoCharacterLibraryItemRequest,
    FreezoneVideoCloneRequest,
    FreezoneVideoComposeRequest,
    FreezoneVideoEditRequest,
    FreezoneVideoExtendRequest,
    FreezoneVideoEraseRequest,
    FreezoneVideoGenRequest,
    FreezoneVideoIdeaRequest,
    FreezoneVideoMixRequest,
    FreezoneWhiteboardAnimationRequest,
    FreezoneVideoOmniGenRequest,
    FreezoneVideoUpscaleRequest,
    ImpactRequest,
    PresetCanvasRequest,
    ProjectionPresetCanvasRequest,
    ProjectionRemoveRequest,
    ProjectionStatusRequest,
    PushRequest,
)
from novelvideo.api.task_start_errors import handle_task_start_runtime_error
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.director_world import DirectorWorldService
from novelvideo.director_world.staging_prop_ai import generate_ai_staging_prop
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.freezone import canvas_store
from novelvideo.freezone.asset_copy import (
    AssetCopyError,
    allocate_target_path,
    copy_project_file,
    parse_project_asset_url,
    resolve_source_file,
)
from novelvideo.i18n_message import log_lines_text
from novelvideo.catalog.media_model_request_schema import (
    MediaModelSchemaError,
    media_request_schema_for_mode,
    normalize_media_model_mode,
    validate_media_model_params,
    validate_media_request_schema,
)
from novelvideo.ports.authz import find_authz_error
from novelvideo.freezone.audio_node import (
    VOICE_FILE_UNREADABLE_MESSAGE,
    VoicePrerequisiteError,
    create_user_audio_voice,
    freezone_audio_eleven_music_output_path,
    freezone_audio_speech_output_path,
    generate_freezone_audio_speech,
    is_readable_audio_file,
    list_user_audio_voices,
    resolve_speech_voice,
    resolve_user_audio_voice,
)
from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.freezone.canvas_media_scope import (
    CanvasMediaScopeError,
    reject_new_foreign_media_refs,
    scan_foreign_media_refs,
)
from novelvideo.freezone.canvas_static_urls import (
    migrate_canvas_static_urls_in_memory,
    sanitize_project_local_paths_in_memory,
)
from novelvideo.freezone.history import (
    append_generation_history,
    build_node_history_record,
    delete_generation_history_record,
    read_canvas_generation_history,
    read_generation_history,
)
from novelvideo.freezone.image_node import (
    DEFAULT_IMAGE_REVERSE_PROMPT_INSTRUCTION,
    reverse_prompt_from_image,
)
from novelvideo.freezone.mark_node import detect_freezone_mark
from novelvideo.freezone.mix import (
    normalize_mix_aspect_ratio,
    normalize_mix_resolution,
)
from novelvideo.freezone.paths import (
    CANVAS_ID_RE,
    canvases_dir,
    freezone_root,
    output_path_for_job,
    outputs_dir,
    resolve_static_url_to_path,
    safe_upload_filename,
    uploads_dir,
)
from novelvideo.freezone.presets import (
    build_asset_preset_context,
    build_beat_preset_context,
    build_canvas_payload_from_context,
    build_episode_preset_context,
    canvas_id_for_preset,
    preset_key_for_request,
)
from novelvideo.freezone.route_helpers import (
    FREEZONE_DEFAULT_IMAGE_MODEL,
)
from novelvideo.ports import get_usage_meter
from novelvideo.freezone.route_helpers import (
    accepted_job_response as _accepted_job_response,
)
from novelvideo.freezone.route_helpers import (
    build_erase_prompt as _build_erase_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_multi_view_prompt as _build_multi_view_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_outpaint_prompt as _build_outpaint_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_redraw_prompt as _build_redraw_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_relight_prompt as _build_relight_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_scene_360_prompt as _build_scene_360_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_style_prompt as _build_style_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_template_edit_prompt as _build_template_edit_prompt,
)
from novelvideo.freezone.route_helpers import (
    build_upscale_prompt as _build_upscale_prompt,
)
from novelvideo.freezone.route_helpers import (
    get_freezone_image_camera_options as _get_freezone_image_camera_options,
)
from novelvideo.freezone.route_helpers import (
    get_freezone_image_style_asset_base as _get_freezone_image_style_asset_base,
)
from novelvideo.freezone.route_helpers import (
    get_freezone_image_style_manifest_version as _get_freezone_image_style_manifest_version,
)
from novelvideo.freezone.route_helpers import (
    get_freezone_image_style_templates as _get_freezone_image_style_templates,
)
from novelvideo.freezone.route_helpers import (
    infer_scene_id_from_master_path as _infer_scene_id_from_master_path,
)
from novelvideo.freezone.route_helpers import (
    load_video_character_items_by_ids as _load_video_character_items_by_ids,
)
from novelvideo.freezone.route_helpers import (
    merge_prompt_with_style_and_camera as _merge_prompt_with_style_and_camera,
)
from novelvideo.freezone.route_helpers import (
    new_freezone_job_id as _new_job_id,
)
from novelvideo.freezone.route_helpers import (
    prepare_padded_outpaint_base as _prepare_padded_outpaint_base,
)
from novelvideo.freezone.route_helpers import (
    resolve_freezone_image_provider as _resolve_freezone_image_provider,
)
from novelvideo.freezone.route_helpers import (
    resolve_freezone_image_style_template as _resolve_freezone_image_style_template,
)
from novelvideo.freezone.route_helpers import (
    ensure_existing_paths as _ensure_existing_paths,
)
from novelvideo.freezone.route_helpers import (
    resolve_outpaint_aspect_ratio as _resolve_outpaint_aspect_ratio,
)
from novelvideo.freezone.route_helpers import (
    resolve_url_list as _resolve_url_list,
)
from novelvideo.freezone.route_helpers import (
    split_provider_and_model as _split_provider_and_model,
)
from novelvideo.freezone.route_helpers import (
    template_edit_aspect_ratio as _template_edit_aspect_ratio,
)
from novelvideo.freezone.skill_registry import (
    ResolvedSkillInput,
    SkillDefinition,
    SkillErrorEnvelope,
    SkillInputAcceptSpec,
    SkillRunOutput,
    SkillRunRequest,
    SkillRunResponse,
    SkillRunResult,
    find_skill,
    list_skills,
)
from novelvideo.freezone.slots import (
    SCENE_ASSET_KINDS,
    IdentityTarget,
    PushTarget,
    backup_slot_if_exists,
    compute_slot_impact,
    is_global_asset_slot,
    record_slot_stale_marks,
    slot_target_path,
    sync_slot_after_write,
    validate_source_for_slot,
)
from novelvideo.freezone.text_node import (
    bind_story_script_assets,
    generate_freezone_text,
    generate_freezone_story_script,
    generate_freezone_story_script_with_vision,
    translate_freezone_text,
)
from novelvideo.freezone.video_node import (
    MAINLINE_FOLDER_KEY,
    STYLE_COVER_ITEM_PREFIX,
    add_video_character_folder,
    add_video_character_library_item,
    build_freezone_image_to_video_prompt,
    build_freezone_keyframe_video_prompt,
    build_freezone_omni_video_prompt,
    build_freezone_video_extend_prompt,
    build_freezone_video_prompt,
    compose_library_with_style_covers,
    delete_video_character_folder,
    delete_video_character_library_item,
    get_video_camera_template,
    get_video_camera_templates,
    is_freezone_seedance2_backend,
    is_freezone_seedance_backend,
    library_folder_keys,
    load_video_character_folders,
    load_video_character_library,
    sync_mainline_assets_into_library,
    normalize_freezone_seedance2_scene_optimize,
    normalize_video_aspect_ratio,
    normalize_video_duration_for_backend,
    normalize_video_resolution_for_backend,
    rename_video_character_library_item,
    resolve_freezone_video_backend,
    summarize_omni_reference_counts,
    update_video_character_folder,
    validate_omni_reference_audio_durations,
    validate_omni_reference_limits,
    MAX_OMNI_REFERENCE_AUDIO_SECONDS,
    MAX_OMNI_REFERENCE_AUDIO_TOTAL_SECONDS,
    MIN_OMNI_REFERENCE_AUDIO_SECONDS,
)
from novelvideo.models import CharacterIdentity, beat_scene_id, real_detected_identities
from novelvideo.project_config import (
    load_effective_narration_style_for_voice_from_state_dir,
    load_narrator_reference_audio_from_state_dir,
)
from novelvideo.project_context import (
    ProjectContext,
    require_project_home_node,
    resolve_project_context,
)
from novelvideo.seedance2_i2v.voice_clone import resolve_character_voice
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import (
    project_task_state_key,
    selection_scope,
    task_config_scope,
    task_state_key,
)
from novelvideo.usage.video_billing import (
    probe_total_video_duration_seconds,
    probe_video_duration_seconds,
)
from novelvideo.task_state import get_task_manager
from novelvideo.utils.background_anchor import copy_to_beat_selected_background
from novelvideo.utils.path_resolver import (
    PathResolver,
    canonical_beat_director_env_only_path,
    canonical_beat_selected_background_path,
    canonical_identity_composite_path,
    canonical_identity_costume_path,
    canonical_identity_face_mat_path,
    canonical_identity_path,
    canonical_identity_portrait_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_360_path,
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
)
from novelvideo.utils.static_urls import project_static_url
from novelvideo.utils.upload_safety import create_staged_upload_file



# ── 子模块 re-export（保持原模块公共属性面）──
from ._common import (
    _resolve_freezone_project,
    _raise_project_context_required,
    _handle_task_start_runtime_error,
    _project_job_response,
    logger,
    FrameReviewReviewer,
    TAG_FREEZONE_BOOTSTRAP,
    TAG_FREEZONE_MEDIA,
    TAG_FREEZONE_AUDIO,
    TAG_FREEZONE_IMAGE,
    TAG_FREEZONE_VIDEO,
    TAG_FREEZONE_TEXT,
    TAG_FREEZONE_CANVAS,
    TAG_FREEZONE_ASSETS,
    TAG_FREEZONE_COMMIT,
    TAG_FREEZONE_JOBS,
    TAG_FREEZONE_SKILLS,
    CANVAS_EVENT_SCHEMA_VERSION,
    MAINLINE_SKETCH_IMAGE_SIZE,
    MAINLINE_SKETCH_IMAGE_QUALITY,
    MAINLINE_FRAME_IMAGE_SIZE,
    MAINLINE_SCENE_360_IMAGE_SIZE,
    _IMAGE_SIZE_TIERS,
    _IMAGE_SIZE_TIER_BY_CASEFOLD,
    _SKILL_RUN_ID_RE,
    _canvas_events_dir,
    _canvas_event_log_path,
    _canvas_event_actor,
    _append_canvas_event,
)
from .gen_ops import (
    _start_or_enqueue_freezone_video_gen,
    _start_or_enqueue_freezone_image_to_3gs,
    _start_or_enqueue_freezone_gen_job,
    freezone_gen,
    freezone_sketch_from_context,
    freezone_frame_from_context,
    freezone_scene_360,
)
from .mainline import (
    _load_freezone_beat_context,
    _scene_ref_label,
    _collect_mainline_typed_reference_urls,
    _skill_beat_context_as_prompt_beat,
    _is_standalone_beat_context_input,
    _list_text_values,
    _standalone_beat_context_sketch_colors,
    _standalone_beat_context_prop_marker_colors,
    _standalone_identity_prompt_parts,
    _standalone_beat_context_identity_names,
    _standalone_beat_context_prompt_identity_map,
    _standalone_beat_context_prompt_visual_description,
    _standalone_beat_context_character_map,
    _standalone_beat_context_unified_sketch_prompt,
    _beat_by_number,
    _normalize_mainline_skill_aspect_ratio,
    _normalize_mainline_frame_quality,
    _mainline_mode_key_for_aspect,
    _mainline_skill_aspect_ratio_from_image,
    _skill_run_parameters,
    _skill_background_reference_mode,
    _mainline_single_beat_config,
    _installed_task_projector,
    _build_task_projection,
    _task_projection_payload,
    _start_or_enqueue_mainline_sketch_from_context_job,
    _raise_if_frame_identity_detection_missing,
    _start_or_enqueue_mainline_frame_from_context_job,
    _standalone_beat_context_frame_config,
    _start_or_enqueue_standalone_frame_from_context_job,
    _start_or_enqueue_mainline_direct_sketch_task,
    _start_or_enqueue_mainline_director_control_sketch_job,
    _start_or_enqueue_mainline_beat_sketch_task,
    _start_or_enqueue_mainline_scene_360_candidate_job,
    _start_or_enqueue_mainline_scene_360_task,
    _cap_mainline_scene_360_image_size,
)
from .edit_ops import (
    _start_or_enqueue_freezone_edit_job,
    _start_or_enqueue_freezone_edit_path,
    _start_or_enqueue_freezone_mask_edit_path,
    freezone_edit,
)
from .media_jobs import (
    _enqueue_or_start_freezone_video_analysis,
    _enqueue_or_start_freezone_video_clone,
    _MIX_IMAGE_SUFFIXES,
    _MIX_VIDEO_SUFFIXES,
    _MIX_MAX_MATERIALS,
    _enqueue_or_start_freezone_video_mix,
    _enqueue_or_start_freezone_whiteboard_animation,
    _enqueue_or_start_freezone_explainer_video,
    # [aipdd fork delta] 动效大片渲染（MG）：与讲解视频同走 video 队列的纯本地长任务。
    _enqueue_or_start_freezone_motion_render,
    # [aipdd fork delta] 动效大片拍点预检：渲染契约第 4 条，占用同一个包内 Chrome。
    _enqueue_or_start_freezone_motion_stills,
    # [aipdd fork delta] 动效大片交付：逐镜 MG 成片落成剪映工程目录。
    _enqueue_or_start_freezone_motion_draft,
    _enqueue_or_start_freezone_channel_short_video,
    _enqueue_or_start_freezone_narration_smart_edit,
    _enqueue_or_start_freezone_article_write,
    _enqueue_or_start_freezone_video_idea,
    _enqueue_or_start_freezone_douyin_copywrite,
    _enqueue_or_start_freezone_media_copywrite,
    _enqueue_or_start_freezone_media_job,
    _enqueue_freezone_background_job,
)
from .skill_runs import (
    _skill_runs_dir,
    _skill_run_metadata_path,
    _write_skill_run_metadata,
    _read_skill_run_metadata,
    _skill_error_envelope,
    _raise_skill_error,
    _skill_run_idempotency_dir,
    _skill_run_idempotency_record_path,
    _skill_run_request_hash,
    _read_skill_run_idempotency_record,
    _write_skill_run_idempotency_record,
    _idempotent_skill_run_response,
    _persist_skill_run_idempotency_response,
    _input_extra,
    _dict_extra,
    _input_mainline_contexts,
    _first_text_value,
    _inferred_slot_target_from_input,
    _reference_target_for_input,
    _slot_target_for_input,
    _canvas_reference_from_input,
    _canvas_references_from_inputs,
    _string_id_set,
    _detected_reference_ids_from_beat_context_data,
    _detected_reference_ids_from_skill_input,
    _reference_id_from_edge,
    _reference_id_from_canvas_reference,
    _reference_id_from_node,
    _reference_target_for_role,
    _synced_reference_edge_id,
    _synced_reference_edge,
    _filter_canvas_references_by_beat_context,
    _sync_frame_context_reference_edges,
    _input_media_kind,
    _validate_skill_input_accepts,
    _normalize_skill_input_url_scope,
    _group_and_validate_skill_inputs,
    _single_input,
    _required_input,
    _required_image_url,
    _input_image_urls,
    _episode_and_beat_from_input,
    _slot_target_from_inputs,
    _skill_output_slot_target,
    _skill_node_is_preset_managed,
    _skill_output_metadata,
    _skill_run_status_from_task_status,
    _project_path_from_rel,
    _director_control_bundle_from_input,
    _copy_director_control_bundle_to_mainline,
    _extract_result_image_url,
    _static_url_for_skill_output_path,
    _static_url_for_task_result_path,
    _static_url_for_skill_slot_target,
    _normalize_task_result_outputs,
    _skill_output_path_for_job,
    _scene_id_from_scene_master_input,
    _scene_prompt_from_input,
    _run_set_selected_background_skill,
    _run_set_director_combined_skill,
    _parse_skill_output_push_target,
    _touch_canonical_slot_revision,
    _copy_skill_output_to_slot,
    _finalize_skill_run_outputs,
    _deterministic_frame_review,
    _build_frame_review_prompt,
    _review_frame_text,
)
from .skill_routes import (
    freezone_skills,
    _run_ai_staging_prop,
    freezone_ai_staging_prop,
    freezone_skill_run,
    freezone_multi_view,
)
from .uploads import (
    REFERENCE_FILE_MAX_BYTES,
    _UPLOAD_READ_CHUNK_BYTES,
    _read_upload_contents,
    _persist_freezone_upload,
    _save_freezone_upload,
    freezone_upload,
    freezone_reference_file_upload,
    _ASSET_COPY_CONCURRENCY,
    freezone_copy_assets_from_project,
    freezone_three_d_viewer_screenshot,
)
from .image_ops import (
    freezone_relight,
    freezone_template_edit,
    freezone_image_camera_options,
    freezone_image_style_templates,
    _freezone_not_implemented,
    freezone_image_to_3gs,
    freezone_upscale,
    freezone_outpaint,
    freezone_redraw,
)
from .video_ops import (
    freezone_extract_frames,
    freezone_analyze_shots,
    freezone_analyze_video_story,
    freezone_video_clone,
    freezone_video_mix,
    freezone_whiteboard_animation,
    freezone_explainer_video,
    freezone_video_idea,
    freezone_douyin_copywrite_models,
    freezone_douyin_copywrite,
    freezone_media_copywrite_models,
    freezone_media_copywrite,
)
from .text_ops import (
    _text_translate_output_path,
    _text_generate_output_path,
    _freezone_history_preview,
    _record_freezone_node_history,
    _start_freezone_text_translate_task,
    _start_freezone_text_generate_task,
    freezone_text_generate,
    freezone_text_translate,
    _read_freezone_text_file,
    _story_script_output_path,
    _image_reverse_prompt_output_path,
    _video_compose_output_path,
    _video_erase_output_path,
    _video_upscale_output_path,
    _audio_separate_audio_output_path,
    _audio_separate_mute_video_output_path,
    _public_freezone_video_story_result,
    _infer_image_to_3gs_scene_id,
    _copy_image_matching_existing_target,
    _resolve_story_script_character_refs,
    _start_freezone_story_script_task,
    freezone_story_script_generate,
    # [aipdd fork delta] 视频反推剧本工具页提交路由（须在包命名空间再导出，
    # 与 story_script 同口径，测试与 router 挂载都按包属性解析）。
    freezone_video_script_reverse,
    _start_freezone_image_reverse_prompt_task,
)
from .audio_ops import (
    _start_freezone_video_compose_task,
    _start_freezone_video_erase_task,
    _start_freezone_video_upscale_task,
    _start_freezone_audio_separate_task,
    _start_freezone_audio_speech_task,
    FREEZONE_AUDIO_AGE_GROUP_LABELS,
    _freezone_audio_ref_payload,
    _user_voice_media_url,
    _attach_user_voice_media_urls,
    _freezone_character_audio_refs,
    freezone_audio_references,
    create_freezone_audio_voice,
    get_freezone_audio_voice_media,
    freezone_audio_separate,
    freezone_audio_speech,
    freezone_audio_eleven_music,
    # [aipdd fork delta] 工具页「人声复刻 → 替换人声」提交路由（router 挂载与
    # 测试都按包属性解析，须在包命名空间再导出）。
    freezone_voice_replace,
    # [aipdd fork delta] 工具页「视频配音」提交路由（router 挂载与测试都按包
    # 属性解析，须在包命名空间再导出）。
    freezone_video_dubbing,
    # [aipdd fork delta req46] 工具页「音乐创作」五模式提交与两步翻唱预处理：
    # router 挂载与测试都按包属性解析，须在包命名空间再导出。
    freezone_music_create,
    freezone_music_cover_preprocess,
    _start_freezone_music_generate_task,
)
from .catalog import (
    _ee_media_model_catalog,
    _scoped_media_model_catalog,
    _media_model_unavailable,
    _require_scoped_media_model,
    _static_media_model_catalog,
    _custom_gateway_media_catalog,
    _merge_media_model_catalog_defaults,
    _catalog_entry_identifiers,
    _catalog_entry_id,
    _catalog_image_execution_selection,
    _resolve_catalog_request,
    _catalog_mode_enabled,
    _require_catalog_video_mode,
    _catalog_omni_reference_capable,
    # [aipdd fork delta req43] 参考素材能否递到上游，只认渠道自己声明的在线绑定；
    # omni 路由按包属性调用，须在包命名空间再导出。
    _omni_reference_binding_facts,
    _require_omni_reference_inputs_supported,
    _catalog_resolution_options,
    _catalog_ratio_options,
    _resolve_omni_aspect_ratio,
    _catalog_reference_limits,
    # [aipdd fork delta req44] 关键帧张数超图槽上限时的诊断文案（含渠道申报 + 出路），
    # 由 video_gen 路由按包属性调用，须在包命名空间再导出。
    _video_frame_slot_shortfall,
    _catalog_reference_duration_bounds,
    _catalog_audio_total_duration_max,
    _probe_reference_audio_seconds,
    _validate_catalog_reference_audio_items,
    _catalog_duration_bounds,
    _resolve_catalog_video_backend,
    freezone_video_camera_templates,
    freezone_video_models,
    freezone_image_models,
    freezone_audio_models,
    freezone_mark_detect,
    freezone_image_reverse_prompt,
)
from .library import (
    freezone_video_character_library,
    freezone_add_video_character_library_item,
    freezone_asset_library_folders,
    freezone_add_asset_library_folder,
    freezone_update_asset_library_folder,
    freezone_delete_asset_library_folder,
    freezone_sync_asset_library_from_mainline,
    freezone_rename_video_character_library_item,
    freezone_delete_video_character_library_item,
)
from .video_gen import (
    freezone_video_gen,
    freezone_video_i2v,
    freezone_video_keyframes,
    freezone_video_omni_gen,
    freezone_video_edit,
    freezone_video_extend,
    freezone_video_erase,
    freezone_video_upscale,
)
from .jobs import (freezone_video_compose, freezone_job_result, freezone_skill_run_result)
from .canvas_presets import (
    _default_push_target_for_preset,
    _latest_preset_canvas,
    _canonical_preset_canvas,
    _preset_key_from_canvas_metadata,
    _canvas_state_project_dir,
    _canvas_actor_id,
    _canvas_scope_from_payload,
    _merge_canvas_metadata,
    _prepare_canvas_payload_for_write,
    _refresh_preset_canvas_payload_on_read,
    _stamp_canvas_mainline_context_project_id,
    _raise_canvas_store_http,
    _merge_restored_preset_canvas,
    _node_projection_key,
    _edge_projection_key,
    _is_replaceable_projection_node,
    _projection_is_intact_in_payload,
    _is_replaceable_projection_edge,
    _archive_projection_node,
    _user_owned_projection_node,
    _merge_projected_preset_canvas,
    _remove_projected_preset_canvas,
    _is_preset_managed_canvas_node,
    _PRESET_FACTS_SIGNATURE_OMIT_KEYS,
    _canonical_preset_facts_value,
    _preset_facts_signature,
    _stamp_preset_facts_signature,
    _stamp_projection_key,
    _projection_group_id,
    _projection_group_label,
    _NODE_DESIGN_SIZES,
    _DEFAULT_NODE_DESIGN_SIZE,
    _node_display_size,
    _wrap_projection_payload_in_group,
    _stamp_projection_metadata,
    _projection_facts_signature_from_payload,
    _preset_facts_signature_from_payload,
    _build_canvas_payload_for_preset_request,
    _build_projection_payload_for_request,
)
from .canvas_routes import (
    create_canvas_from_preset,
    build_projection_from_preset,
    project_canvas_from_preset,
    remove_canvas_projection,
    projection_status,
    list_canvases,
    get_canvas,
    list_canvas_history,
    restore_canvas_history,
    get_node_generation_history,
    delete_node_generation_history_record,
    get_canvas_generation_history,
    put_canvas,
    delete_canvas,
)
from .assets import (
    _asset_record_from_path,
    _asset_record_from_optional_project_path,
    _character_asset_history_links,
    _compact_mainline_context,
    _slot_target_for_asset_record,
    _mainline_context_for_asset_record,
    _tab_for_beat_context_ref,
    _is_beat_director_control_path,
    _is_mainline_beat_director_control_ref,
    _director_control_bundle_from_combined_ref,
    _is_mainline_beat_selected_background_ref,
    _is_beat_context_metadata_ref,
    _beat_context_asset_from_ref,
    _is_freezone_scene_library_role,
    DIRECTOR_CAPTURE_FILES,
    _freezone_director_control_frames_dir,
    _freezone_director_capture_base,
    _director_capture_file_payload,
    _beat_for_capture,
    _persist_freezone_selected_background_scene_ref,
    freezone_impact,
    _sync_env_only_to_selected_background,
    freezone_director_capture_manifest,
    freezone_director_capture_sync_background,
    freezone_scene_assets_for_beat,
    freezone_push,
    list_freezone_assets,
    list_freezone_beat_context_assets,
    freezone_create_identity_asset,
    init_freezone,
)
from .skill_routes import router as _router_skill_routes
from .uploads import router as _router_uploads
from .gen_ops import router as _router_gen_ops
from .image_ops import router as _router_image_ops
from .video_ops import router as _router_video_ops
from .text_ops import router as _router_text_ops
from .article_ops import (
    _article_write_output_path,
    _article_html_path,
    freezone_article_write,
    freezone_article_publish,
)
from .article_ops import router as _router_article_ops
from .audio_ops import router as _router_audio_ops
from .catalog import router as _router_catalog
from .library import router as _router_library
from .video_gen import router as _router_video_gen
from .jobs import router as _router_jobs
from .edit_ops import router as _router_edit_ops
from .canvas_routes import router as _router_canvas_routes
from .assets import router as _router_assets
from .motion import router as _router_motion


@asynccontextmanager
async def freezone_store_scope(ctx, username: str, project: str):
    """freezone 子模块统一的「按请求作用域 + 自动关闭」SQLiteStore。

    与 :func:`novelvideo.api.deps.sqlite_store_for_context_scope` 不同，本 helper
    故意按名字直接读 ``freezone`` 模块全局的工厂（而非 deps 全局）：保留 freezone
    测试以「monkeypatch freezone.make_*store*」为既定接缝的习惯，迁移时无需
    扫一遍测试把接缝改到 deps。与 generation_store_scope 同构。
    """
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx is not None
        else await make_sqlite_store(username, project)
    )
    try:
        yield store
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@asynccontextmanager
async def freezone_store_for_context_scope(ctx):
    """ctx-only 版：给已确定持有 ProjectContext 的读路径用。

    工厂同样按 ``freezone`` 模块全局解析，保留既定测试接缝。
    """
    store = await make_sqlite_store_for_context(ctx)
    try:
        yield store
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@asynccontextmanager
async def freezone_cognee_store_for_context_scope(ctx):
    """ctx-only 版 CogneeStore 作用域，同样按 ``freezone`` 全局解析工厂。"""
    store = await make_cognee_store_for_context(ctx)
    try:
        yield store
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


router = APIRouter()
router.include_router(_router_skill_routes)
router.include_router(_router_uploads)
router.include_router(_router_gen_ops)
router.include_router(_router_image_ops)
router.include_router(_router_video_ops)
router.include_router(_router_text_ops)
router.include_router(_router_article_ops)
router.include_router(_router_audio_ops)
router.include_router(_router_catalog)
router.include_router(_router_library)
router.include_router(_router_video_gen)
router.include_router(_router_jobs)
router.include_router(_router_edit_ops)
router.include_router(_router_canvas_routes)
router.include_router(_router_assets)
router.include_router(_router_motion)

# ── 包级命名空间状态（原单文件全局，供 tests patch）──
_agent_review_frame_reviewer: FrameReviewReviewer | None = None
