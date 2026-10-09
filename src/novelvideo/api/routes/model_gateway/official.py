"""官方（official）网关路由：官方密钥配置、模式启用与官方媒体目录管理。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析，保持 tests 对
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 的拦截契约。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from novelvideo import config as app_config
from novelvideo.api.routes import model_gateway as _gateway
from novelvideo.model_gateway_settings import (
    MODE_OFFICIAL,
    build_model_gateway_status,
    get_official_media_catalog_update_status,
    normalize_api_key,
    save_official_media_catalog_auto_update,
    save_official_newapi_key,
    set_model_gateway_mode,
)
from novelvideo.official_media_catalog_remote import (
    check_official_media_catalog_update,
)

from ._common import _permission_error, require_ce_gateway_management

router = APIRouter()


class OfficialGatewayBody(BaseModel):
    new_api_api_key: str = Field(alias="newApiApiKey")


class OfficialMediaCatalogPreferencesBody(BaseModel):
    auto_update: bool = Field(alias="autoUpdate")


def _require_ce_media_catalog_management() -> None:
    if not _gateway.is_ce_effective():
        raise PermissionError("official media catalog management is only available in CE")


@router.get("/official/media-catalog")
async def get_official_media_catalog_status() -> dict[str, Any]:
    try:
        _require_ce_media_catalog_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    return {"ok": True, "data": get_official_media_catalog_update_status()}


@router.post("/official/media-catalog/preferences")
async def save_official_media_catalog_preferences(
    body: OfficialMediaCatalogPreferencesBody,
) -> dict[str, Any]:
    try:
        _require_ce_media_catalog_management()
        status = save_official_media_catalog_auto_update(body.auto_update)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    return {"ok": True, "data": status}


@router.post("/official/media-catalog/check")
async def check_official_media_catalog() -> dict[str, Any]:
    try:
        _require_ce_media_catalog_management()
        updated, status = await check_official_media_catalog_update()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "data": {**status, "updated": updated}}


@router.post("/official/enable")
async def enable_official_gateway() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    status = build_model_gateway_status(
        official_base_url=app_config.official_newapi_base_url(),
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    if not status["official"]["configured"]:
        # [aipdd fork delta req41] 官方模式地址不在代码里预设（没配就是没配），所以这里
        # 得告诉用户去哪儿配，而不是只说一句「未配置」。
        raise HTTPException(
            status_code=400,
            detail=(
                "官方 NewAPI 网关还没配地址：请在环境变量 OFFICIAL_NEWAPI_BASE_URL "
                "填网关的 OpenAI 兼容地址，或在「设置 › 模型」里连接自定义网关。"
            ),
        )
    set_model_gateway_mode(MODE_OFFICIAL)
    runtime = _gateway.refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.official_newapi_base_url(),
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/official/config")
async def save_official_gateway_config(body: OfficialGatewayBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    api_key = normalize_api_key(body.new_api_api_key)
    if not api_key:
        raise HTTPException(status_code=400, detail="newApiApiKey is required")
    save_official_newapi_key(api_key=api_key, activate=True)
    runtime = _gateway.refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.official_newapi_base_url(),
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }