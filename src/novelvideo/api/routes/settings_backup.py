# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置 →「配置备份 / 恢复」：整盘 settings.db 的导出与回灌。

需求（测试问题发现.md L10）：所有配置可备份，下次重装可恢复。所有可编辑配置都在
``runtime_settings``（settings.db）这一张表里，因此备份 = 导出该表为带版本清单的
可移植 JSON bundle，恢复 = 校验后写回并失效 CE 网关缓存。

边界（有意划小）：
- 两个端点都 ``require_owner``：bundle 明文含 API Key / 口令 / Cookie，只能在交互式
  超管会话下产生与接受；agent 会话与统一 ``st-`` 密钥一律拒。
- 备份只覆盖**配置**，不含媒体 / 项目 / 账号内容——那些走既有卷备份。
- 恢复默认 merge（只覆盖 bundle 内键，保留升级后新增键），不做全库清空覆盖。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from novelvideo.api.auth import require_owner
from novelvideo.model_gateway_settings import export_runtime_settings, import_runtime_settings

router = APIRouter()

FORMAT = "dramaclaw-config-backup"
SCHEMA_VERSION = 1


def _app_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        return str(_pkg_version("novelvideo"))
    except Exception:  # noqa: BLE001 - 版本号只是信息位，取不到就回退环境/空
        return str(os.environ.get("DRAMACLAW_VERSION", "")).strip()


@router.get("/admin/settings/export")
async def export_config(user: dict = Depends(require_owner)):
    """导出整盘配置为可下载 JSON bundle（明文含密钥，仅超管会话可用）。"""
    settings = await asyncio.to_thread(export_runtime_settings)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    bundle = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "app_version": _app_version(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(settings),
        "settings": settings,
    }
    return JSONResponse(
        bundle,
        headers={
            "Content-Disposition": f'attachment; filename="dramaclaw-config-{stamp}.json"',
        },
    )


@router.post("/admin/settings/import")
async def import_config(
    payload: dict[str, Any] = Body(default_factory=dict),
    user: dict = Depends(require_owner),
):
    """导入备份 bundle：校验信封 → merge 写回 → 失效缓存。损坏/非法回 400。"""
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise HTTPException(status_code=400, detail="Not a valid DramaClaw config backup file")
    settings = payload.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise HTTPException(status_code=400, detail="Backup is empty or missing 'settings'")

    warnings: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        warnings.append(
            f"schema_version={schema_version!r}, expected {SCHEMA_VERSION}; restoring best-effort via merge"
        )

    values = {str(k): ("" if v is None else str(v)) for k, v in settings.items()}
    result = await asyncio.to_thread(import_runtime_settings, values)
    return {
        "ok": True,
        "data": {
            "applied": result.get("applied", 0),
            "skipped": result.get("skipped", 0),
            "warnings": warnings,
        },
    }
