# ruff: noqa: F401
"""Scene asset workbench endpoints."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from novelvideo.api.asset_metadata import newest_updated_at, tree_updated_at
from novelvideo.api.auth import get_api_user
from novelvideo.api.upload_workers import (
    run_asset_upload_operation,
    scene_ply_upload_limiter,
    scene_upload_lock,
)
from novelvideo.api.deps import (
    may_run_asset_repair,
    make_sqlite_store_for_context,
    make_static_url_for_context,
)
from novelvideo.api.schemas import (
    PanoViewerCorrection,
    SceneCreate,
    ScenePanoGenerateRequest,
    SceneReferenceGenerateRequest,
    SceneUpdate,
)
from novelvideo.api.task_start_errors import handle_task_start_runtime_error
from novelvideo.api.viewer_manifests import (
    build_director_stage_manifest,
    build_pano_viewer_manifest,
)
from novelvideo.director_world import stage_manifest
from novelvideo.models import (
    NovelScene,
    build_scene_effective_prompt,
    resolve_scene_plate_from_records,
)
from novelvideo.novel_source import has_imported_novel, novel_import_required_response
from novelvideo.ports import get_task_backend
from novelvideo.project_config import load_project_config_file
from novelvideo.project_context import ProjectContext, resolve_project_context
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.project_config import load_project_config_file_from_state_dir
from novelvideo.scene_prerequisites import (
    SceneBuildNotApplicableError,
    ScenePlanningRunningError,
    running_scene_planner,
    scene_build_applies,
    scene_prerequisite_response,
)
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.task_scopes import scene_reference_asset_scope, stage_asset_scope
from novelvideo.utils.asset_names import move_asset_dir, path_safe_asset_name
from novelvideo.utils.async_ops import metadata_io_limiter
from novelvideo.utils.derived_scenes import (
    compose_derived_scene_name,
)
from novelvideo.utils.path_resolver import (
    canonical_scene_master_path,
    compute_scene_master_path,
    compute_scene_reverse_master_path,
)
from novelvideo.utils.upload_safety import create_staged_upload_file
from novelvideo.utils.static_urls import project_static_url
from novelvideo.utils.upload_safety import (
    MAX_PROJECT_UPLOAD_BYTES,
    UploadTooLargeError,
    stream_to_file_with_limit,
)


# ── 子模块 re-export（保持原模块公共属性面）──
from ._common import (
    logger,
    _InvalidSceneImageError,
    _InvalidScenePanoRatioError,
    _EmptyScenePackageError,
    _SCENE_TIME_TOKENS,
    _project_style,
    _asset_url,
    _resolve_scene_project,
    _scene_project_scope,
)
from .task_ops import (_start_or_enqueue_stage_asset, _start_or_enqueue_scene_pano)
from .payloads import (
    _scene_360_description,
    _stage_file_payload,
    _stage_3gs_payload,
    _scene_payload,
    _scene_summary_payload,
    _require_scene,
    _derived_scene_names_for,
    _is_derived_scene,
    _derived_scene_guard_error,
    _scene_plate_preview_payload,
    _compose_scene_asset_name,
)
from .upload_ops import (
    _copy_upload_to_temp_file,
    _persist_custom_scene_upload,
    _load_scene_upload_image,
    _publish_scene_image,
    _persist_scene_master_upload,
    _persist_scene_pano_upload,
    _scene_upload_lock_key,
    _delete_scene_pano_files,
    _delete_scene_custom_files,
    _move_dir_if_exists,
    _rename_scene_asset_dirs,
    _heal_path_unsafe_scene_names,
)
from .scene_routes import (
    list_scenes,
    preview_scene_plate,
    create_scene,
    update_scene,
    delete_scene,
    build_scenes,
    upload_scene_master,
    delete_scene_master,
)
from .pano_routes import (
    get_scene_pano_manifest,
    update_scene_pano_correction,
    upload_scene_pano,
    delete_scene_pano,
    generate_scene_pano,
)
from .director_routes import (
    get_scene_director_stage_manifest,
    save_scene_director_world,
    save_scene_director_world_source,
    clear_scene_director_world,
)
from .gen_routes import (
    generate_scene_master,
    generate_scene_reverse_master,
    _start_scene_reference_task,
    _start_3gs_single_face_task,
    generate_scene_3gs_master_ply,
    generate_scene_3gs_reverse_ply,
    generate_scene_3gs_pano_ply,
)
from .custom_routes import (upload_scene_custom_package, delete_scene_custom_package)
from .scene_routes import router as _router_scene_routes
from .pano_routes import router as _router_pano_routes
from .director_routes import router as _router_director_routes
from .gen_routes import router as _router_gen_routes
from .custom_routes import router as _router_custom_routes

router = APIRouter()
router.include_router(_router_scene_routes)
router.include_router(_router_pano_routes)
router.include_router(_router_director_routes)
router.include_router(_router_gen_routes)
router.include_router(_router_custom_routes)
