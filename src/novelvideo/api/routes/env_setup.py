# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置 →「环境安装」：部署环境运行依赖的就绪探测端点。

本 fork 走 Docker 源码部署，容器里常驻的运行时依赖（ffmpeg / node / npm / uv /
中文字体 / Chrome CDP）就是产品能力的地基。超管在「设置 → 环境安装」里看到的
是**服务端真实环境**的只读快照——缺哪个、怎么补，一目了然。真正的探测逻辑在
:mod:`novelvideo.env_probe`，本路由只做透传；3D 世界（world）子块的按需安装
仍走既有 `/admin/world/*`（见 :mod:`novelvideo.api.routes.world`）。

边界（有意划小）：`status` 只读快照要求浏览器会话里的 owner（``require_owner``），
agent token 一律拒。req-automcp 新增的 `plan` / `install` 面向「自动化安装环境」的
MCP 工具，改用 ``get_api_user``：既放行浏览器 owner，也放行统一 ``st-`` API 密钥——
这与外部 AI 客户端（Claude Code / Codex）经 MCP 驱动全系统、以及模型广场共用一把
``st-`` 密钥的既有口径一致；安装是重操作但不触碰账户 / 密钥管理面。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends

from novelvideo import diagnostics, env_probe, env_setup_install
from novelvideo.api.auth import get_api_user, require_owner

router = APIRouter()


@router.get("/admin/env-setup/status")
async def get_env_setup_status(user: dict = Depends(require_owner)):
    """返回部署环境运行依赖的就绪清单（供设置页渲染）。只读、无副作用。"""
    data = await asyncio.to_thread(env_probe.probe)
    return {"ok": True, "data": data}


@router.get("/admin/diagnostics")
async def get_diagnostics(user: dict = Depends(require_owner)):
    """[aipdd fork delta] 系统整体自检：环境 / 模型渠道 / 默认模型 / 存储的一盘只读体检。

    供设置页「系统整体自检」消费。复用 :mod:`novelvideo.diagnostics` 聚合 env_probe +
    网关连通性（只读上游模型清单，零生成消耗）+ 分类默认模型覆盖 + 磁盘/数据库。
    只读、无副作用；浏览器 owner 会话限定（``require_owner``）。
    """
    data = await asyncio.to_thread(diagnostics.run_diagnostics)
    return {"ok": True, "data": data}


@router.get("/admin/env-setup/plan")
async def get_env_setup_plan(user: dict = Depends(get_api_user)):
    """环境自检 → 统一待办计划（就绪 / 可自动装 / 需指引）。

    只读、无副作用；供 MCP 工具 ``aipdd_drama_env_setup`` 与设置页向导消费。
    agent 的 ``st-`` 密钥与浏览器 owner 均可调用。
    """
    data = await asyncio.to_thread(env_setup_install.build_plan)
    return {"ok": True, "data": data}


@router.post("/admin/env-setup/install")
async def install_env_setup_item(
    payload: dict[str, Any] = Body(default_factory=dict),
    user: dict = Depends(get_api_user),
):
    """逐项安装待办计划里的可自动装项（当前为 3D 世界运行时）。

    不可自动安装的项只回指引，不谎称已装；可自动装项触发异步安装并回当前状态。
    """
    item_id = str((payload or {}).get("item") or "").strip()
    data = await asyncio.to_thread(env_setup_install.install_item, item_id)
    return {"ok": True, "data": data}
