# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 3D 世界（world / 3DGS / SHARP）运行时按需安装与开关端点。

精简镜像只常驻轻工具（git/nodejs/npm/@playcanvas/splat-transform），不含 torch/sharp/da2
这些 GB 级重依赖。超管在「设置 → 3D 世界」里：
- 看当前状态（是否已启用、依赖是否装好、安装进度与日志尾部）；
- 配置下载源（pip 镜像 / torch 索引 / npm registry / HuggingFace endpoint / 代理），
  默认已填国内可用镜像，用户可自行覆盖；
- 打开/关闭画布上的 3D 世界入口（开关）；
- 点「下载并部署」触发后台任务把 world extra 用 uv 装到持久卷独立 venv；
- 需要时卸载（删 venv，省磁盘）。

边界（有意划小）：全部要求浏览器会话里的 owner（``require_owner``），agent token 一律拒。
真正的安装编排、状态机与 venv/env 解析都在 :mod:`novelvideo.director_world.world_install`。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from novelvideo.api.auth import require_owner
from novelvideo.director_world import world_install

router = APIRouter()
logger = logging.getLogger("novelvideo.api.admin.world")


class _SourcesRequest(BaseModel):
    pip_index_url: str | None = None
    torch_index_url: str | None = None
    npm_registry: str | None = None
    hf_endpoint: str | None = None
    https_proxy: str | None = None


class _EnableRequest(BaseModel):
    enabled: bool = Field(..., description="是否在画布开放 3D 世界入口")


@router.get("/admin/world/status")
async def get_world_status(user: dict = Depends(require_owner)):
    """返回 3D 世界特性的完整状态快照（供设置页渲染）。"""
    return {"ok": True, "data": world_install.status_snapshot()}


@router.put("/admin/world/sources")
async def update_world_sources(
    payload: _SourcesRequest,
    user: dict = Depends(require_owner),
):
    """更新下载源（只覆盖传入的字段，其余保留）。安装/权重下载下次生效。"""
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    sources = {str(k): str(v).strip() for k, v in patch.items()}
    world_install.save_settings({"sources": sources})
    return {"ok": True, "data": world_install.status_snapshot()}


@router.post("/admin/world/enable")
async def set_world_enabled(
    payload: _EnableRequest,
    user: dict = Depends(require_owner),
):
    """打开/关闭 3D 世界入口。关闭不影响已安装的 venv，只是不再对外提供能力。"""
    world_install.save_settings({"enabled": bool(payload.enabled)})
    return {"ok": True, "data": world_install.status_snapshot()}


@router.post("/admin/world/install")
async def start_world_install(user: dict = Depends(require_owner)):
    """异步启动「下载并部署」。已在跑则直接返回当前状态，不重复下载。"""
    snapshot = world_install.start_install()
    return {"ok": True, "data": snapshot}


@router.post("/admin/world/uninstall")
async def uninstall_world(user: dict = Depends(require_owner)):
    """删除 world venv（保留权重缓存以便再次快速安装）。安装中拒绝。"""
    if world_install.install_running():
        raise HTTPException(status_code=409, detail="3D 世界依赖正在安装中，请稍后再试。")
    return {"ok": True, "data": world_install.uninstall()}
