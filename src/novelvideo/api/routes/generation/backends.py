"""generation.backends 子模块（自 api/routes/generation.py 拆分）。"""

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import RenderSettingsUpdate
from novelvideo.api.schemas import SketchSettingsUpdate
from novelvideo.api.schemas import VideoBackendOption
from novelvideo.api.schemas import VideoGenerateRequest

from ._common import logger

router = APIRouter()


def _api_video_backend_options() -> list[VideoBackendOption]:
    from novelvideo.config import NEWAPI_VIDEO_DURATION_BOUNDS
    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator,
        newapi_video_backend_options,
        parse_newapi_video_backend,
    )

    hidden_mainline_backends = {
        "newapi_seedance-2.0-value",
        "newapi_seedance-2.0-fast-value",
        "newapi_happyhorse-1.0",
    }
    options = {
        value: label
        for value, label in newapi_video_backend_options(
            include_seedance2_variants=True
        ).items()
        if value not in hidden_mainline_backends
    }
    duration_bounds = NewApiVideoGenerator._parse_duration_bounds_config(
        NEWAPI_VIDEO_DURATION_BOUNDS
    )
    default_backend = VideoGenerateRequest().video_backend
    backend_options: list[VideoBackendOption] = []
    for value, label in options.items():
        model = parse_newapi_video_backend(value)
        bounds = duration_bounds.get(model or "")
        if model == "seedance-2.0-mini" and not bounds:
            bounds = (4, 15)
        if model == "happyhorse-1.0" and not bounds:
            bounds = (3, 15)
        if model == "grok-video-channel" and not bounds:
            bounds = (6, 30)
        is_happyhorse = _generation._is_happyhorse_backend(value)
        is_grok_video = _generation._is_grok_video_backend(value)
        backend_options.append(
            VideoBackendOption(
                value=value,
                label=label,
                is_default=value == default_backend,
                is_seedance2=_generation._is_seedance2_backend(value),
                is_happyhorse=is_happyhorse,
                is_grok_video=is_grok_video,
                dialogue_only=value in {"seedance_pro", "newapi_seedance-1.5-pro"},
                min_duration=bounds[0] if bounds else None,
                max_duration=bounds[1] if bounds else None,
                resolution_options=(
                    list(_generation.HAPPYHORSE_RESOLUTION_OPTIONS)
                    if is_happyhorse
                    else list(_generation.GROK_VIDEO_RESOLUTION_OPTIONS)
                    if is_grok_video
                    else None
                ),
                ratio_options=(
                    list(_generation.HAPPYHORSE_RATIO_OPTIONS)
                    if is_happyhorse
                    else list(_generation.GROK_VIDEO_RATIO_OPTIONS)
                    if is_grok_video
                    else None
                ),
                supported_modes=(
                    list(_generation.HAPPYHORSE_SUPPORTED_MODES)
                    if is_happyhorse
                    else list(_generation.GROK_VIDEO_SUPPORTED_MODES)
                    if is_grok_video
                    else None
                ),
                reference_image_max=7 if is_grok_video else 9 if is_happyhorse else None,
                reference_video_max=0 if is_grok_video else 1 if is_happyhorse else None,
                reference_audio_max=0 if is_grok_video or is_happyhorse else None,
            )
        )
    return backend_options


def _catalog_positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _catalog_str_list(entry: dict[str, Any], key: str) -> list[str] | None:
    """读目录条目里声明的档位列表（分辨率 / 比例）；没声明返回 None。"""

    raw = entry.get(key)
    if not isinstance(raw, list):
        return None
    cleaned = [str(item or "").strip() for item in raw]
    cleaned = [item for item in cleaned if item]
    return cleaned or None


def _api_video_backend_options_from_catalog(
    catalog: list[dict[str, Any]],
) -> list[VideoBackendOption]:
    """把视频媒体模型目录（与画布 /freezone/video/models 同源）映射成分镜视频后端选项。

    [aipdd fork delta] 分镜视频过去下拉来自写死的 ``NEWAPI_VIDEO_MODELS``，与画布走的
    网关实拉目录不同源，导致「列表写死、参数按模型名写死」。现在：

    - 可选模型 = 目录里的全部视频模型（不再写死）；
    - 分辨率 / 比例 / 时长档位优先取目录里每个模型自己的声明，只有目录没声明时才
      回落原来的按家族名常量 / bounds（保住 seedance2 / happyhorse / grok 的既有富 UI）；
    - 家族标记（is_seedance2 / is_happyhorse / is_grok_video / dialogue_only）、
      supported_modes、参考素材上限仍按名字派生 —— 这些是目录不承载的前端 UI 能力。
    """

    from novelvideo.config import NEWAPI_VIDEO_DURATION_BOUNDS
    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator,
        parse_newapi_video_backend,
    )

    hidden_mainline_backends = {
        "newapi_seedance-2.0-value",
        "newapi_seedance-2.0-fast-value",
        "newapi_happyhorse-1.0",
    }
    duration_bounds = NewApiVideoGenerator._parse_duration_bounds_config(
        NEWAPI_VIDEO_DURATION_BOUNDS
    )
    default_backend = VideoGenerateRequest().video_backend
    backend_options: list[VideoBackendOption] = []
    seen: set[str] = set()
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        value = str(
            entry.get("apiModel") or entry.get("api_model") or entry.get("id") or ""
        ).strip()
        if not value or value in hidden_mainline_backends or value in seen:
            continue
        seen.add(value)
        label = str(entry.get("label") or entry.get("catalogId") or value).strip() or value
        model = parse_newapi_video_backend(value)
        is_happyhorse = _generation._is_happyhorse_backend(value)
        is_grok_video = _generation._is_grok_video_backend(value)
        min_duration = _catalog_positive_int(entry.get("minDuration"))
        max_duration = _catalog_positive_int(entry.get("maxDuration"))
        if min_duration is None or max_duration is None:
            bounds = duration_bounds.get(model or "")
            if model == "seedance-2.0-mini" and not bounds:
                bounds = (4, 15)
            if model == "happyhorse-1.0" and not bounds:
                bounds = (3, 15)
            if model == "grok-video-channel" and not bounds:
                bounds = (6, 30)
            if min_duration is None:
                min_duration = bounds[0] if bounds else None
            if max_duration is None:
                max_duration = bounds[1] if bounds else None
        resolution_options = _catalog_str_list(entry, "resolutionOptions")
        if resolution_options is None:
            resolution_options = (
                list(_generation.HAPPYHORSE_RESOLUTION_OPTIONS)
                if is_happyhorse
                else list(_generation.GROK_VIDEO_RESOLUTION_OPTIONS)
                if is_grok_video
                else None
            )
        ratio_options = _catalog_str_list(entry, "ratioOptions")
        if ratio_options is None:
            ratio_options = (
                list(_generation.HAPPYHORSE_RATIO_OPTIONS)
                if is_happyhorse
                else list(_generation.GROK_VIDEO_RATIO_OPTIONS)
                if is_grok_video
                else None
            )
        backend_options.append(
            VideoBackendOption(
                value=value,
                label=label,
                is_default=value == default_backend,
                is_seedance2=_generation._is_seedance2_backend(value),
                is_happyhorse=is_happyhorse,
                is_grok_video=is_grok_video,
                dialogue_only=value in {"seedance_pro", "newapi_seedance-1.5-pro"},
                min_duration=min_duration,
                max_duration=max_duration,
                resolution_options=resolution_options,
                ratio_options=ratio_options,
                supported_modes=(
                    list(_generation.HAPPYHORSE_SUPPORTED_MODES)
                    if is_happyhorse
                    else list(_generation.GROK_VIDEO_SUPPORTED_MODES)
                    if is_grok_video
                    else None
                ),
                reference_image_max=7 if is_grok_video else 9 if is_happyhorse else None,
                reference_video_max=0 if is_grok_video else 1 if is_happyhorse else None,
                reference_audio_max=0 if is_grok_video or is_happyhorse else None,
            )
        )
    return backend_options


@router.get("/projects/{project}/video-backends")
async def get_video_backend_options(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return video backend options shared with the NiceGUI render workbench.

    [aipdd fork delta] 模型列表改走与画布同源的网关实拉视频目录；目录不可用 / 为空时
    回落到写死的 ``_api_video_backend_options()``，绝不打成空列表。
    """
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    catalog: list[dict[str, Any]] | None = None
    try:
        from novelvideo.api.routes.freezone import _scoped_media_model_catalog

        catalog = await _scoped_media_model_catalog(
            "video",
            requester_user_id=getattr(resolved.ctx, "requester_user_id", ""),
        )
    except Exception:  # noqa: BLE001 - 目录不可用不能打挂分镜视频下拉
        logger.exception(
            "video backend catalog lookup failed; falling back to static options"
        )
        catalog = None
    if catalog:
        backend_options = _api_video_backend_options_from_catalog(catalog)
        if backend_options:
            return {
                "ok": True,
                "data": [item.model_dump() for item in backend_options],
            }
    return {"ok": True, "data": [item.model_dump() for item in _api_video_backend_options()]}


@router.get("/projects/{project}/render-settings")
async def get_render_settings(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return Render-stage image model and sizing settings for React."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _generation._render_settings_payload(resolved.username, resolved.project_name),
    }


@router.patch("/projects/{project}/render-settings")
async def update_render_settings(
    project: str,
    body: RenderSettingsUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist Render-stage image model and sizing settings."""
    from novelvideo.config import image_generation_selection_options

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    updates: dict[str, Any] = {}

    if body.render_image_selection is not None:
        selection = str(body.render_image_selection or "").strip()
        if selection not in image_generation_selection_options():
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Invalid render_image_selection: {selection}",
                },
            )
        updates["render_image_selection"] = selection
    if body.sketch_aspect_padding is not None:
        updates["sketch_aspect_padding"] = bool(body.sketch_aspect_padding)

    if updates:
        _generation.save_project_config(username, project_name, config=updates)
    return {"ok": True, "data": _generation._render_settings_payload(username, project_name)}


@router.get("/projects/{project}/sketch-settings")
async def get_sketch_settings(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return Sketch-stage image model settings for React."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _generation._sketch_settings_payload(resolved.username, resolved.project_name),
    }


@router.patch("/projects/{project}/sketch-settings")
async def update_sketch_settings(
    project: str,
    body: SketchSettingsUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist Sketch-stage image model settings."""
    from novelvideo.config import image_generation_selection_options

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    updates: dict[str, Any] = {}

    if body.sketch_image_selection is not None:
        selection = str(body.sketch_image_selection or "").strip()
        if selection not in image_generation_selection_options():
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Invalid sketch_image_selection: {selection}",
                },
            )
        updates["sketch_image_selection"] = selection

    if updates:
        _generation.save_project_config(username, project_name, config=updates)
    return {"ok": True, "data": _generation._sketch_settings_payload(username, project_name)}
