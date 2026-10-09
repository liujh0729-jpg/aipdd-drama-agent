"""characters.selection_routes 子模块（自 api/routes/characters.py 拆分）。"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import AssetImageSourceSelectionRequest
from novelvideo.api.schemas import CharacterImageSelectionRequest
from novelvideo.config import image_generation_selection_options

from ._common import (
    ASSET_IMAGE_SELECTION_CONFIG_KEYS,
    CHARACTER_IMAGE_SELECTION_CONFIG_KEY,
    CHARACTER_IMAGE_USAGE_TASK_TYPES,
)

router = APIRouter()


@router.get("/projects/{project}/character-image-selection")
async def get_project_character_image_selection(
    project: str,
    user: dict = Depends(get_api_user),
):
    """获取项目级角色/身份图生成源选择。"""
    async with _characters._character_project_scope(project, user, required_role="viewer") as (_ctx, username, project_name, _project_dir, _output_dir, _store):
        return {
            "ok": True,
            "data": _characters._character_image_selection_payload(username, project_name),
        }


@router.patch("/projects/{project}/character-image-selection")
async def update_project_character_image_selection(
    project: str,
    body: CharacterImageSelectionRequest,
    user: dict = Depends(get_api_user),
):
    """保存项目级角色/身份图生成源选择。"""
    async with _characters._character_project_scope(project, user) as (_ctx, username, project_name, _project_dir, _output_dir, _store):
        selection = str(body.character_image_selection or "").strip()
        options = _characters.character_image_selection_options()
        if selection not in options:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Invalid character_image_selection: {selection}",
                },
            )

        def _apply(config: dict) -> None:
            config[CHARACTER_IMAGE_SELECTION_CONFIG_KEY] = selection

        _characters.update_project_config_file(username, project_name, _apply)
        return {
            "ok": True,
            "data": _characters._character_image_selection_payload(username, project_name),
        }


@router.get("/projects/{project}/image-source-selection/{asset_kind}")
async def get_project_asset_image_source_selection(
    project: str,
    asset_kind: str,
    user: dict = Depends(get_api_user),
):
    """获取项目级素材图源选择。"""
    normalized_kind = _characters._validate_asset_image_source_kind(asset_kind)
    if normalized_kind is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Unsupported image source kind: {asset_kind}"},
        )
    async with _characters._character_project_scope(project, user, required_role="viewer") as (_ctx, username, project_name, _project_dir, _output_dir, _store):
        return {
            "ok": True,
            "data": _characters._asset_image_source_selection_payload(username, project_name, normalized_kind),
        }


@router.patch("/projects/{project}/image-source-selection/{asset_kind}")
async def update_project_asset_image_source_selection(
    project: str,
    asset_kind: str,
    body: AssetImageSourceSelectionRequest,
    user: dict = Depends(get_api_user),
):
    """保存项目级素材图源选择。"""
    normalized_kind = _characters._validate_asset_image_source_kind(asset_kind)
    if normalized_kind is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Unsupported image source kind: {asset_kind}"},
        )
    async with _characters._character_project_scope(project, user) as (_ctx, username, project_name, _project_dir, _output_dir, _store):
        selection = str(body.image_source_selection or "").strip()
        options = image_generation_selection_options()
        if selection not in options:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"Invalid image_source_selection: {selection}"},
            )
        config_key = ASSET_IMAGE_SELECTION_CONFIG_KEYS[normalized_kind]

        def _apply(config: dict) -> None:
            config[config_key] = selection

        _characters.update_project_config_file(username, project_name, _apply)
        return {
            "ok": True,
            "data": _characters._asset_image_source_selection_payload(username, project_name, normalized_kind),
        }


@router.get("/projects/{project}/character-image-usage")
async def get_project_character_image_usage(
    project: str,
    user: dict = Depends(get_api_user),
):
    """获取角色/身份图请求用量统计。"""
    async with _characters._character_project_scope(project, user, required_role="viewer") as (_ctx, _username, _project_name, project_dir, _output_dir, _store):
        summary = _characters.get_image_usage_summary(
            project_output_dir=project_dir,
            task_types=CHARACTER_IMAGE_USAGE_TASK_TYPES,
        )
        return {"ok": True, "data": summary}
