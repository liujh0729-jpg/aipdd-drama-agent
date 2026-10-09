"""[aipdd fork delta req47] Seedance 私域素材库 / 真人活体认证路由。

一切操作都先过「这台 NewAPI 到底有没有素材库」的探测闸门（``newapi_asset_library``
模块文档串里记着实测差分判据）：探测不支持就 503 拦下并给出可执行出路，绝不带着
「应该有吧」的假设去打卡不存在的接口。上游错误原文一律透出到 ``detail``，前端
``getRequestErrorMessage`` 只吃字符串 detail，所以这里统一拼成 ``[码] 原文``。

URL 前缀 ``/model-gateway/seedance-assets``：整组路由已在包级 router 上挂登录闸门。
"""

from __future__ import annotations

import asyncio
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from novelvideo import newapi_asset_library as assets
from novelvideo.newapi_asset_library import AssetLibraryError

from .plaza import _browser_origin

router = APIRouter()

# kind → HTTP 状态：探测类结论不该伪装成本服务错误。
_STATUS_BY_KIND = {
    "unconfigured": 400,
    "unsupported": 503,
    "unauthorized": 502,
    "transport": 502,
    "upstream_error": 502,
}


class CreateAssetBody(BaseModel):
    group_id: str = Field(alias="groupId")
    asset_type: str = Field(alias="assetType")
    url: str
    name: str = ""
    project_name: str = Field(default="", alias="projectName")


class ValidateSessionBody(BaseModel):
    callback_url: str = Field(default="", alias="callbackUrl")
    project_name: str = Field(default="", alias="projectName")


def _detail(exc: AssetLibraryError) -> str:
    message = str(exc.message or "").strip() or "Seedance 素材库请求失败"
    if exc.code and exc.code not in message:
        return f"[{exc.code}] {message}"
    return message


def _raise_http(exc: AssetLibraryError) -> NoReturn:
    status = _STATUS_BY_KIND.get(exc.kind, 502)
    raise HTTPException(status_code=status, detail=_detail(exc)) from exc


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


async def _call(fn, *args, **kwargs) -> dict[str, object]:
    """上游是同步 httpx：放到线程里跑，别把 api 事件循环钉住 60 秒。"""

    return await asyncio.to_thread(fn, *args, **kwargs)


# 面板进场第一枪：只读探测 + 目标来源，不改数据、不产生上游计费。
@router.get("/seedance-assets/status")
async def seedance_asset_library_status(refresh: bool = False) -> dict[str, Any]:
    data = await _call(assets.probe_asset_library_support, force=bool(refresh))
    return {"ok": True, "data": data}


@router.get("/seedance-assets/groups")
async def seedance_asset_groups(
    groupType: str = "AIGC",  # noqa: N803 - 与上游字段同名，接口怎么传这里就怎么收
    name: str = "",
    groupIds: str = "",  # noqa: N803
    maxResults: int = 50,  # noqa: N803
    nextToken: str = "",  # noqa: N803
    sortBy: str = "CreateTime",  # noqa: N803
    sortOrder: str = "Desc",  # noqa: N803
) -> dict[str, Any]:
    try:
        data = await _call(
            assets.list_asset_groups,
            group_type=groupType,
            group_ids=_split_csv(groupIds),
            name=name,
            max_results=maxResults,
            next_token=nextToken,
            sort_by=sortBy,
            sort_order=sortOrder,
        )
    except AssetLibraryError as exc:
        _raise_http(exc)
    return {"ok": True, "data": data}


@router.get("/seedance-assets/assets")
async def seedance_assets_list(
    groupType: str = "AIGC",  # noqa: N803
    groupIds: str = "",  # noqa: N803
    name: str = "",
    assetType: str = "",  # noqa: N803
    statuses: str = "",
    maxResults: int = 50,  # noqa: N803
    nextToken: str = "",  # noqa: N803
    sortBy: str = "CreateTime",  # noqa: N803
    sortOrder: str = "Desc",  # noqa: N803
) -> dict[str, Any]:
    try:
        data = await _call(
            assets.list_assets,
            group_type=groupType,
            group_ids=_split_csv(groupIds),
            name=name,
            asset_type=assetType,
            statuses=_split_csv(statuses),
            max_results=maxResults,
            next_token=nextToken,
            sort_by=sortBy,
            sort_order=sortOrder,
        )
    except AssetLibraryError as exc:
        _raise_http(exc)
    return {"ok": True, "data": data}


# 单条素材：审核状态（Processing → Active/Failed）轮询就用它。
@router.get("/seedance-assets/assets/{asset_id}")
async def seedance_asset_detail(asset_id: str) -> dict[str, Any]:
    try:
        data = await _call(assets.get_asset, asset_id)
    except AssetLibraryError as exc:
        _raise_http(exc)
    return {"ok": True, "data": data}


# 建素材：只有探测判定「支持」才放行；URL 必须是上游能直接抓的公网直链。
@router.post("/seedance-assets/assets")
async def seedance_asset_create(body: CreateAssetBody) -> dict[str, Any]:
    try:
        assets.require_asset_library_supported()
    except AssetLibraryError as exc:
        _raise_http(exc)
    try:
        data = await _call(
            assets.create_asset,
            group_id=body.group_id,
            asset_type=body.asset_type,
            url=body.url,
            name=body.name,
            project_name=body.project_name,
        )
    except AssetLibraryError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": data}


# 真人活体认证会话：CallbackURL 缺省取「用户实际看到的那个 origin」——
# 认证是本人用手机做的，跳回来的必须是公网可达地址（req41 不写死站点同一口径）。
@router.post("/seedance-assets/validate-sessions")
async def seedance_validate_session_create(
    request: Request,
    body: ValidateSessionBody,
) -> dict[str, Any]:
    try:
        assets.require_asset_library_supported()
    except AssetLibraryError as exc:
        _raise_http(exc)
    callback_url = str(body.callback_url or "").strip()
    if not callback_url:
        origin = _browser_origin(request)
        if not origin:
            raise HTTPException(
                status_code=400,
                detail=(
                    "无法推导回调地址：请在请求里带 callbackUrl（真人认证要跳到公网可达"
                    "的页面，反代没给出 Host 时系统不猜）。"
                ),
            )
        callback_url = f"{origin}/"
    try:
        data = await _call(
            assets.create_visual_validate_session,
            callback_url=callback_url,
            project_name=body.project_name,
        )
    except AssetLibraryError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": {"callbackUrl": callback_url, **data}}


# 认证结果：本人做完活体后拿 BytedToken 换锁定的真人素材组 Id。
@router.get("/seedance-assets/validate-results")
async def seedance_validate_result_query(bytedToken: str) -> dict[str, Any]:  # noqa: N803
    try:
        data = await _call(assets.get_visual_validate_result, bytedToken)
    except AssetLibraryError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": data}


# 网关 Key 变更后清缓存：否则面板还要等 TTL 才松绑。
@router.post("/seedance-assets/probe/refresh")
async def seedance_asset_probe_refresh() -> dict[str, Any]:
    assets.invalidate_asset_library_probe()
    data = await _call(assets.probe_asset_library_support, force=True)
    return {"ok": True, "data": data}
