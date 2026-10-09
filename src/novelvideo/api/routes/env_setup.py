# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置 →「环境安装」：部署环境运行依赖的就绪探测端点。

本 fork 走 Docker 源码部署，容器里常驻的运行时依赖（ffmpeg / node / npm / uv /
中文字体 / Chrome CDP）就是产品能力的地基。超管在「设置 → 环境安装」里看到的
是**服务端真实环境**的只读快照——缺哪个、怎么补，一目了然。真正的探测逻辑在
:mod:`novelvideo.env_probe`，本路由只做透传；3D 世界（world）子块的按需安装
仍走既有 `/admin/world/*`（见 :mod:`novelvideo.api.routes.world`）。

边界（有意划小）：要求浏览器会话里的 owner（``require_owner``），agent token 一律拒。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from novelvideo import env_probe
from novelvideo.api.auth import require_owner

router = APIRouter()


@router.get("/admin/env-setup/status")
async def get_env_setup_status(user: dict = Depends(require_owner)):
    """返回部署环境运行依赖的就绪清单（供设置页渲染）。只读、无副作用。"""
    data = await asyncio.to_thread(env_probe.probe)
    return {"ok": True, "data": data}
