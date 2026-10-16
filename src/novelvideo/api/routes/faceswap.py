# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置 →「换脸直播」：本机换脸引擎的按需部署 + 开关 + 控制转发。

对齐 :mod:`novelvideo.api.routes.world`（3D 世界）的端点结构。真正的安装编排、状态机、
venv/模型解析与边车进程生命周期都在 :mod:`novelvideo.faceswap.faceswap_install`。

AGPL 隔离与「Web 不推流」：本路由**从不 import** 边车内部模块，只把浏览器控制面板的
控制类请求（上传人脸图 / 改参数 / 启停 / 取状态）经 **localhost HTTP** 转发到边车的本地
控制口；换脸视频走 摄像头→边车→系统虚拟摄像头→OBS，**永不经过本路由或浏览器**。

边界（有意划小）：全部要求浏览器会话里的 owner（``require_owner``），agent token 一律拒。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from novelvideo.api.auth import require_owner
from novelvideo.faceswap import faceswap_install

router = APIRouter()
logger = logging.getLogger("novelvideo.api.admin.faceswap")


class _SourcesRequest(BaseModel):
    pip_index_url: str | None = None
    hf_endpoint: str | None = None
    https_proxy: str | None = None


class _EnableRequest(BaseModel):
    enabled: bool = Field(..., description="是否在「工具」开放换脸直播入口")


class _EngineConfigRequest(BaseModel):
    """透传给边车控制面板的运行参数（字段与边车 /config 约定一致，缺省不动）。"""

    many_faces: bool | None = None
    mouth_mask: bool | None = None
    nsfw_filter: bool | None = None
    face_enhancer: bool | None = None
    live_mirror: bool | None = None
    det_size: int | None = Field(default=None, ge=64, le=640)
    execution_provider: str | None = None
    source_image: str | None = None


def _engine_base() -> str:
    return f"http://127.0.0.1:{faceswap_install.control_port()}"


# ── 环境管理：状态 / 下载源 / 开关 / 部署 / 卸载 ─────────────────────────
@router.get("/admin/faceswap/status")
async def get_faceswap_status(user: dict = Depends(require_owner)):
    """返回换脸直播特性的完整状态快照（供设置页 + 控制面板渲染）。"""
    return {"ok": True, "data": faceswap_install.status_snapshot()}


@router.put("/admin/faceswap/sources")
async def update_faceswap_sources(
    payload: _SourcesRequest,
    user: dict = Depends(require_owner),
):
    """更新下载源（只覆盖传入字段）。依赖安装 / 模型下载下次生效。"""
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    sources = {str(k): str(v).strip() for k, v in patch.items()}
    faceswap_install.save_settings({"sources": sources})
    return {"ok": True, "data": faceswap_install.status_snapshot()}


@router.post("/admin/faceswap/enable")
async def set_faceswap_enabled(
    payload: _EnableRequest,
    user: dict = Depends(require_owner),
):
    """打开/关闭「工具」里的换脸直播入口。关闭不影响已装 venv/模型。"""
    faceswap_install.save_settings({"enabled": bool(payload.enabled)})
    return {"ok": True, "data": faceswap_install.status_snapshot()}


@router.post("/admin/faceswap/install")
async def start_faceswap_install(user: dict = Depends(require_owner)):
    """异步启动「下载并部署」（建 venv 装依赖 + 下模型）。已在跑则返回当前状态。"""
    return {"ok": True, "data": faceswap_install.start_install()}


@router.post("/admin/faceswap/uninstall")
async def uninstall_faceswap(user: dict = Depends(require_owner)):
    """删除引擎 venv 与模型缓存（省磁盘）。安装中拒绝。"""
    if faceswap_install.install_running():
        raise HTTPException(status_code=409, detail="换脸引擎正在安装中，请稍后再试。")
    return {"ok": True, "data": faceswap_install.uninstall()}


# ── 边车进程生命周期（本机）──────────────────────────────────────────────
@router.post("/admin/faceswap/engine/start")
async def start_faceswap_engine(user: dict = Depends(require_owner)):
    """拉起边车引擎（抓摄像头→换脸→写虚拟摄像头）。未就绪返回 409。"""
    if not faceswap_install.is_local_runtime():
        raise HTTPException(
            status_code=409,
            detail="换脸直播仅在本机/桌面形态可用（需物理摄像头与系统虚拟摄像头）。",
        )
    try:
        return {"ok": True, "data": faceswap_install.start_engine()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/admin/faceswap/engine/stop")
async def stop_faceswap_engine(user: dict = Depends(require_owner)):
    """停边车引擎。"""
    return {"ok": True, "data": faceswap_install.stop_engine()}


# ── 控制面板转发（localhost，Web 不推流）────────────────────────────────
@router.get("/admin/faceswap/engine/state")
async def engine_state(user: dict = Depends(require_owner)) -> dict[str, Any]:
    """取边车运行态（running/fps/最近状态/参数）。边车未起返回 idle。"""
    if not faceswap_install.engine_running():
        return {"ok": True, "data": {"running": False}}
    return await _forward("GET", "/state")


@router.post("/admin/faceswap/engine/config")
async def engine_config(
    payload: _EngineConfigRequest,
    user: dict = Depends(require_owner),
) -> dict[str, Any]:
    """把控制面板改的运行参数透传给边车 /config。

    控制面板用 ``execution_provider`` 语义化命名，边车 ``/config`` 收的是 ``provider``，
    在此收口字段名，不让两侧命名漂移。
    """
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "execution_provider" in body:
        body["provider"] = body.pop("execution_provider")
    return await _forward("POST", "/config", json=body)


@router.post("/admin/faceswap/engine/face")
async def engine_face(
    file: UploadFile = File(...),
    user: dict = Depends(require_owner),
) -> dict[str, Any]:
    """把上传的人脸图透传存到边车（multipart /face）。视频不经此口。"""
    content = await file.read()
    files = {"file": (file.filename or "face.png", content, file.content_type or "image/png")}
    if not faceswap_install.engine_running():
        raise HTTPException(status_code=409, detail="换脸引擎未运行，请先点「启动」。")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_engine_base()}/face", files=files)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        return {"ok": True, "data": data}
    except httpx.HTTPError as exc:
        logger.warning("faceswap engine face forward failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"转发人脸图到换脸引擎失败：{exc}") from exc


async def _forward(
    method: str, path: str, *, json: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not faceswap_install.engine_running():
        return {"ok": True, "data": {"running": False}}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, f"{_engine_base()}{path}", json=json)
        resp.raise_for_status()
        # 边车自身已用 {"ok", "data"} 信封返回，这里剥开再套一层，避免双层 data 嵌套。
        payload = resp.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        return {"ok": True, "data": data}
    except httpx.HTTPError as exc:
        logger.warning("faceswap engine forward %s %s failed: %s", method, path, exc)
        raise HTTPException(status_code=502, detail=f"换脸引擎通信失败：{exc}") from exc
