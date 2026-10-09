# ruff: noqa: F401
"""网格生成模块（历史名 NanoBananaPro，实际模型不写死）。

[aipdd fork delta] 出图模型按当前选中的默认图像模型动态分发：self.provider / self.model
来自 __init__ 的 config（渲染重生链路传入 get_generation_config(selection_override=image_selection)），
并非固定 NanoBananaPro / Seedream。

Sketch 模式使用 3x3 网格（每张 9 panel 位），按 beat 顺序分块，自动产出 ceil(N/9) 张网格。

生成流程:
1. 从 beats 数据构建网格 Prompt
2. 按 self.provider / self.model 调用当前选中的默认图像模型生成网格图
3. 使用 grid_splitter 分割成独立分镜
4. 需要时对分镜做图生图高清修复（upscale_image，同样按 self.provider / self.model 分发）

B3 拆包：单文件 grid_generation.py（8,272 行）→ 本包；导入路径与符号面零变化。
子模块：constants / egress / newapi_protocol / image_sizes / regen_modes / plan / packing / modes / models / panels / crop_split / batch_split / api_entries / comfyui / dispatch / sketch_flow / provider_text_api / aipdd_geometry / provider_newapi / provider_huimeng / grid_generator / entry。
"""

import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from novelvideo.config import (
    IMAGE_DEFAULT_STYLE,
    get_grid_generation_config,
    get_style_preset,
)
from novelvideo.ports import get_usage_meter, update_current_model_call_log
from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.ports.authz import AdmissionContext
from novelvideo.ports.egress import EgressError
from novelvideo.ports.egress_operations import (
    OperationClaimResult,
    HandleKind,
    OperationSpec,
    canonical_request_digest,
)
from novelvideo.ports.model_credentials import ModelCredentialError, RequestCredential
from novelvideo.shared import openai_compat
from novelvideo.shared.upstream_quota_errors import is_fatal_upstream_quota_error
from novelvideo.generators.huimengi import (
    HuimengTaskFailed,
    HuimengiTaskClient,
    bytes_to_data_url,
    extract_huimeng_result_url,
    validate_huimeng_media_download,
)
from novelvideo.generators.prompt_builder import (
    PromptComponents,
    PromptContext,
    PromptMode,
    UnifiedPromptBuilder,
    create_prompt_context,
)
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.models import (
    beat_scene_id,
    collect_prop_marker_ids_from_beat,
    real_detected_identities,
)
from novelvideo.manual_shots import beat_order_value
from novelvideo.services.style_service import StyleService
from novelvideo.utils.asset_resolver import AssetResolver
from novelvideo.usage.image_request_usage import (
    infer_episode_from_path,
    infer_project_output_dir,
    record_image_request,
    update_image_request_status,
)
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    media_relay_ttl_seconds,
    relay_tenant_image_bytes_from_context,
    upload_image_bytes,
)

from .constants import (
    _VALID_IMAGE_SIZES,
    _OPENROUTER_IMAGE_CAPABILITY_CACHE,
    _OPENAI_VALID_QUALITIES,
    _OPENAI_MIN_PIXELS,
    _OPENAI_MAX_PIXELS,
    _OPENAI_MAX_EDGE,
    _OPENAI_MAX_RATIO,
    _HUIMENG_IMAGE_POLL_INTERVAL_SECONDS,
    _HUIMENG_IMAGE_MAX_POLLS,
    HUIMENG_IMAGE2_SINGLE_CELL_SELECTION,
    HUIMENG_IMAGE2_SINGLE_CELL_REASON,
    SINGLE_CELL_RENDER_MODE_KEY,
    SINGLE_CELL_RENDER_MODE_BY_ASPECT,
    logger,
    NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
)
from .egress import (
    _OrganizationImageEgress,
    _validate_egress_context,
    _prepare_organization_image_egress,
    _complete_organization_image_egress,
    _abandon_organization_image_egress,
    _newapi_request_id_from_headers,
)
from .newapi_protocol import (
    NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS,
    NEWAPI_IMAGE_MAX_ATTEMPTS,
    NEWAPI_IMAGE_RETRY_BASE_SECONDS,
    NEWAPI_IMAGE_POLL_INTERVAL_SECONDS,
    NEWAPI_IMAGE_MAX_POLLS,
    _NEWAPI_IMAGE_SUCCESS_STATUSES,
    _NEWAPI_IMAGE_FAILURE_STATUSES,
    _NEWAPI_IMAGE_PENDING_STATUSES,
    _NEWAPI_TRANSIENT_IMAGE_MARKERS,
    _is_newapi_transient_image_status,
    _newapi_image_status,
    _newapi_image_error,
    _newapi_image_output,
    _newapi_safe_header_summary,
    _newapi_safe_request_context,
    _newapi_context_for_error,
    _beat_display_sort_key,
    image_generation_selection_forces_single_cell,
)
from .image_sizes import (
    _InlineImagePart,
    _infer_project_dir,
    _resolve_scene_prop_asset_refs,
    _global_prop_marker_colors,
    normalize_image_size,
    _legacy_newapi_image_quality_default,
    _huimeng_image_resolution_for_model,
    _round_openai_edge,
    resolve_openai_image_size,
    normalize_openai_quality,
    _extract_openai_unknown_parameter,
    _truncate_openrouter_debug,
    _check_openrouter_image_capability,
    clamp_image_size,
)
from .regen_modes import (
    DEFAULT_SKETCH_ASPECT_RATIO,
    REGEN_MODE_CONFIGS,
    get_sketch_default_mode_key,
    get_sketch_nxn_modes,
    SKETCH_DEFAULT_MODE_KEY,
    PLANNER_VERSION,
)
from .plan import (
    PlanEntry,
    hash_plan,
    compute_input_fingerprint,
    cell_aspect_ratio,
    sketch_pass1_mode_key,
    _sketch_cfg,
    SKETCH_GRID_CONFIG,
    SKETCH_GRID_PLAN,
    SKETCH_REGEN_MODE_KEYS,
)
from .packing import (
    LOCATION_POOL_TEMPLATE,
    LANDSCAPE_RENDER_POOL_TEMPLATE,
    DEFAULT_POOL_TEMPLATE,
    CHARACTER_POOL_2,
    CHARACTER_POOL_3,
    MANY_CHARS_REF_THRESHOLD,
    MANY_CHARS_MAX_CAPACITY,
    _cap_pool_for_many_chars,
    _count_batch_composite_chars,
    _smart_repack_beats,
    pad_to_aspect_ratio,
    pack_beats,
)
from .modes import (
    LOCATION_ASPECT_CONFIGS,
    parse_regen_mode,
    get_default_mode_for_grid,
    get_regen_modes_for_grid,
    grid_mode_to_mode_key,
    _single_cell_render_mode_key,
    _render_pool_template_for_aspect,
    SHOT_GRID_CONFIGS,
    get_shot_grid_config,
)
from .models import GridGenerationRequest, GridGenerationResult
from .panels import (
    filter_character_map_for_beats,
    _has_director_image_ref,
    build_color_map_from_character_map,
    _extract_hex_color,
    _color_for_identity,
    _draw_blocking_stick_figure,
    _prepare_director_blocking_refs,
    detect_panel_characters,
    filter_character_map_by_sketch,
    load_precomputed_panel_detected,
    filter_character_map_by_precomputed,
    resolve_render_reference_order,
)
from .batch_split import (
    _merge_small_grids,
    _pack_location_beats,
    build_regen_plan,
    _get_beat_visual_composite_chars,
    _flush_to_grids,
    character_grid_split,
    sketch_grid_split,
    _coalesce_locations,
)
from .crop_split import (
    crop_sketch_panels,
    get_optimal_grid_size,
    perfect_grid_split,
    scene_grid_split,
)
from .comfyui import _comfyui_image_dimensions, _generate_comfyui_image
from .dispatch import _generate_image
from .api_entries import (
    SKETCH_NXN_MODES,
    generate_text_to_image,
    generate_reference_edit_image,
)
from .sketch_flow import (
    _pick_nxn_mode,
    _group_beats_by_location,
    _is_space_map_beat,
    sketch_scene_grid_split,
    find_sketch_for_beat_range,
)
from .provider_text_api import _call_openrouter_image_api, _call_openai_image_api
from .aipdd_geometry import (
    _AIPDD_IMAGE_TIER_LONG_EDGES,
    _AIPDD_IMAGE_TIER_RE,
    _AIPDD_IMAGE_PIXEL_RE,
    _AIPDD_IMAGE_RATIO_RE,
    _aipdd_image_geometry_endpoint,
    _aipdd_tier_number,
    _aipdd_pixel_dimensions,
    _aipdd_ratio_number,
    _map_aipdd_image_size,
    _map_aipdd_image_ratio,
    _apply_aipdd_image_geometry,
)
from .provider_newapi import (
    _call_newapi_image_api,
    _reference_image_bytes,
    _call_newapi_image_api_with_egress,
    _relay_reference_images_for_newapi,
    _ORIGINAL_RELAY_REFERENCE_IMAGES_FOR_NEWAPI,
)
from .provider_huimeng import _call_huimeng_image_api
from .grid_generator import GridGenerator
from .entry import (
    _generation_beat_number,
    regenerate_selected_beats,
    create_grid_generator,
)
