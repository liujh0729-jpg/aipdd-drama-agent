# ruff: noqa: F401
"""角色列表 & 肖像/身份图生成端点。"""

import asyncio
import logging
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, AsyncIterator
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, UploadFile, File, Query
from fastapi.responses import JSONResponse

from novelvideo.api.asset_metadata import newest_updated_at, tree_updated_at
from novelvideo.api.auth import get_api_user
from novelvideo.api.upload_workers import (
    asset_resource_lock,
    run_asset_upload_operation,
)
from novelvideo.api.deps import (
    may_run_asset_repair,
    make_sqlite_store,
    make_sqlite_store_for_context,
    make_static_url_for_context,
    resolve_project_scope,
    sqlite_store_for_context_scope,
    sqlite_store_scope,
)
from novelvideo.project_context import ProjectContext
from novelvideo.novel_source import has_imported_novel, novel_import_required_response
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import project_task_state_key
from novelvideo.api.schemas import (
    AssetImageSourceSelectionRequest,
    PortraitGenRequest,
    CharacterCreate,
    CharacterUpdate,
    CharacterImageSelectionRequest,
    CharacterAssetRestoreRequest,
    IdentityCreate,
    IdentityUpdate,
    IdentityImageGenRequest,
    CharacterVoiceRecordRequest,
    CharacterVoiceTrimRequest,
)
from novelvideo.config import (
    image_generation_selection_options,
    character_image_selection_options,
    get_character_image_selection,
    normalize_image_generation_selection,
    normalize_character_image_selection,
)
from novelvideo.image_request_usage import get_image_usage_summary
from novelvideo.project_config import (
    load_project_config,
    load_project_config_file,
    update_project_config_file,
)
from novelvideo.utils.asset_names import move_asset_dir, path_safe_asset_name
from novelvideo.utils.path_resolver import (
    compute_portrait_path,
    compute_identity_path,
    compute_identity_costume_path,
    compute_identity_portrait_path,
    compute_identity_face_mat_path,
    compute_identity_composite_path,
    canonical_portrait_path,
    canonical_identity_path,
    canonical_identity_costume_path,
    canonical_identity_portrait_path,
    canonical_identity_composite_path,
)
from novelvideo.utils.static_urls import project_static_url
from novelvideo.utils.async_ops import metadata_io_limiter
from novelvideo.utils.upload_safety import create_staged_upload_file
from novelvideo.seedance2_i2v.character_voice_storage import (
    AGE_GROUP_SLOTS as VOICE_AGE_GROUP_SLOTS,
    ALL_SLOTS as VOICE_SAMPLE_SLOTS,
    DEFAULT_SLOT as VOICE_DEFAULT_SLOT,
    character_voice_resource_key,
    clear_character_voice_file,
    decode_recorded_audio_data_url,
    persist_character_voice_file,
    run_voice_media_operation,
    trim_existing_character_voice_file,
    voice_resource_lock,
)
from novelvideo.sqlite_store import SQLiteStore

# ── 子模块 re-export（保持原模块公共属性面）──
from ._common import (
    logger,
    CHARACTER_IMAGE_SELECTION_CONFIG_KEY,
    ASSET_IMAGE_SELECTION_CONFIG_KEYS,
    CHARACTER_IMAGE_USAGE_TASK_TYPES,
    CHARACTER_ASSET_KINDS,
    VOICE_SLOT_LABELS,
    _resolve_character_project,
    _character_project_scope,
    _character_image_selection_payload,
    _asset_image_source_selection_payload,
    _validate_asset_image_source_kind,
    _resolve_character_image_model,
)
from .asset_ops import (
    _safe_asset_name,
    _identity_by_id,
    _asset_url,
    _backup_character_asset,
    _persist_uploaded_character_image,
    _persist_uploaded_character_voice,
    _persist_recorded_character_voice,
    _resolve_character_asset_path,
    _history_id_for_path,
    _character_asset_history_entries,
    _character_asset_history_path,
    _sync_restored_identity_asset,
    _character_asset_links,
)
from .voice_ops import (
    _voice_slot_metadata,
    _voice_slot_update_fields,
    _voice_sample_url,
    _convention_asset_url,
    _voice_slot_payload,
    _voice_samples_payload,
    _character_voice_fields,
    _identity_voice_fields,
    _apply_character_voice_update,
)
from .maintenance import (
    _unset_other_main_characters,
    _repair_duplicate_main_characters,
    _heal_path_unsafe_character_names,
)
from .character_routes import (
    list_characters,
    add_character,
    build_characters,
    update_character,
    delete_character,
)
from .selection_routes import (
    get_project_character_image_selection,
    update_project_character_image_selection,
    get_project_asset_image_source_selection,
    update_project_asset_image_source_selection,
    get_project_character_image_usage,
)
from .identity_routes import (
    get_character_identities,
    add_identity,
    update_identity,
    delete_identity,
    get_identity_attempts,
)
from .asset_routes import (list_character_asset_history, restore_character_asset_history)
from .voice_routes import (
    list_character_voice_samples,
    upload_character_voice_sample,
    record_character_voice_sample,
    trim_character_voice_sample,
    delete_character_voice_sample,
)
from .portrait_routes import (
    generate_single_portrait_async,
    generate_single_portrait,
    upload_portrait,
    upload_identity_image,
    delete_identity_image,
    upload_identity_costume,
    delete_identity_costume,
    upload_identity_portrait,
    generate_identity_portrait_async,
    generate_identity_portrait,
)
from .face_mat_routes import (
    generate_identity_face_mat_async,
    stitch_identity_composite,
    generate_identity_image_async,
    generate_identity_image,
)
from .character_routes import router as _router_character_routes
from .selection_routes import router as _router_selection_routes
from .identity_routes import router as _router_identity_routes
from .asset_routes import router as _router_asset_routes
from .voice_routes import router as _router_voice_routes
from .portrait_routes import router as _router_portrait_routes
from .face_mat_routes import router as _router_face_mat_routes

router = APIRouter()
router.include_router(_router_character_routes)
router.include_router(_router_selection_routes)
router.include_router(_router_identity_routes)
router.include_router(_router_asset_routes)
router.include_router(_router_voice_routes)
router.include_router(_router_portrait_routes)
router.include_router(_router_face_mat_routes)
