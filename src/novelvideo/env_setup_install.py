# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta req-automcp] 环境自检 → 待办计划 → 逐项安装的编排层。

设置页「环境安装」与 MCP 工具 ``aipdd_drama_env_setup`` 共用这一层。设计边界与
本 fork 的部署形态严格对齐：

- **只读探测**复用 :mod:`novelvideo.env_probe`（ffmpeg / node / npm / uv / 中文字体
  / Chrome CDP / OpenCV 的真实运行时快照），不改动任何状态。
- **可自动安装项**当前只有「3D 世界（3DGS / SHARP）」运行时：精简镜像不含 GB 级
  依赖，点安装后走 :mod:`novelvideo.director_world.world_install` 用 uv 装进**持久卷
  venv**（跨容器重建保留），与「逐项安装」的状态机范式一致。
- **系统级依赖**（ffmpeg / 字体等 apt 包）在运行中的容器里装了重启即失、且需 root，
  无法可靠自动补齐——如实列进待办并给出「在宿主机运行《安装环境-Setup》」的指引，
  而不是假装能一键装好（照 world_install 的诚实口径）。
- **模型渠道密钥**不属于可安装项：它是一个待办步骤，指引用户到「设置 → 模型与渠道」
  配好 AIPDD / 官方密钥后即可开跑。

每个计划条目统一形状::

    {id, status, installed, auto_installable, action, version, detail, guide}

``status`` ∈ ``ready`` / ``installable`` / ``missing``：就绪、可自动装、需人工补。
名称 / 用途 / 修复文案由前端按 ``id`` 取 i18n（后端只回事实 + ASCII 兜底 guide）。
"""

from __future__ import annotations

import os
from typing import Any

from novelvideo import env_probe
from novelvideo.director_world import world_install

# 可在容器内自动安装的能力：目前仅 3D 世界运行时（uv → 持久 venv）。
AUTO_INSTALLABLE_IDS: frozenset[str] = frozenset({"world"})

# 需宿主机 / 重建镜像才能补的系统级依赖：给指引，不自动装。
_MANUAL_FIX_GUIDE: dict[str, str] = {
    "ffmpeg": "在宿主机运行《安装环境-Setup》脚本，或重建部署镜像内置 ffmpeg。",
    "node": "在宿主机运行《安装环境-Setup》脚本，或重建部署镜像内置 Node.js。",
    "npm": "随 Node.js 一同安装；见《安装环境-Setup》脚本。",
    "uv": "3D 世界自动安装依赖 uv；在宿主机运行《安装环境-Setup》脚本安装 uv。",
    "fonts": "安装中文字体（如 Noto Sans CJK）后重启容器；或由《安装环境-Setup》脚本处理。",
    "opencv": "OpenCV 随部署镜像内置；如缺失请重建镜像或在宿主机环境补齐。",
    "chrome": "可选特性：在宿主机以 --remote-debugging-port=9222 启动 Chrome，无需安装。",
    "model_channel": "前往「设置 → 模型与渠道」配置 AIPDD / 官方密钥后即可开跑。",
}


def _dep_status(item: dict[str, Any]) -> dict[str, Any]:
    installed = bool(item.get("installed"))
    dep_id = str(item.get("id") or "")
    optional = dep_id == "chrome"
    return {
        "id": dep_id,
        "status": "ready" if installed else "missing",
        "installed": installed,
        "auto_installable": False,
        "action": None,
        "version": item.get("version"),
        "detail": item.get("detail"),
        "guide": None if installed else _MANUAL_FIX_GUIDE.get(dep_id, "见部署文档补齐该项。"),
        "optional": optional,
    }


def _world_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    state = str(snapshot.get("install_state") or "")
    installed = bool(snapshot.get("installed"))
    can_install = bool(snapshot.get("can_install"))
    if installed and state == world_install.STATE_READY:
        status = "ready"
    elif snapshot.get("installing") or state == world_install.STATE_INSTALLING:
        status = "installable"
    elif can_install:
        status = "installable"
    else:
        status = "missing"
    return {
        "id": "world",
        "status": status,
        "installed": installed,
        "auto_installable": can_install,
        "action": "install" if can_install and not installed else None,
        "version": None,
        "detail": f"install_state={state}" if state else None,
        "guide": (
            None
            if status == "ready"
            else ("点击安装，用 uv 装进持久卷（跨重建保留）。" if can_install
                  else "精简镜像不支持在该路径自动安装，请重建部署镜像。")
        ),
        "optional": True,
    }


def _configured_channel_providers() -> list[str]:
    """settings.db 里已存 upstreamKey 的渠道 provider 名——CE 模型密钥的真源。

    读配置失败一律按空处理：这只是环境指引探测，绝不能因配置读取异常而崩。
    """
    try:
        from novelvideo.model_gateway_settings import get_newapi_provider_channels

        return [
            str(channel.get("provider") or "").strip()
            for channel in get_newapi_provider_channels()
            if str(channel.get("upstreamKey") or "").strip()
        ]
    except Exception:  # noqa: BLE001 - 指引探测降级，不把配置读取失败当用户错误
        return []


def _model_channel_status() -> dict[str, Any]:
    """模型渠道密钥：只作指引，不自动安装。

    真源是 settings.db（在「设置 → 模型与渠道」保存的渠道 key），所以先按渠道存档判定；
    环境变量 ``MODEL_API_KEY`` 只作容器内直连的兼容兜底（旧口径，不进公开模板）。只要任一
    渠道存了 key 或环境有密钥即视为就绪，绝不谎报缺失惊吓用户。
    """
    keyed = sorted({provider for provider in _configured_channel_providers() if provider})
    configured_by_db = bool(keyed)
    configured = configured_by_db or bool((os.environ.get("MODEL_API_KEY") or "").strip())
    detail = f"configured_channels={','.join(keyed)}" if configured_by_db else None
    return {
        "id": "model_channel",
        "status": "ready" if configured else "missing",
        "installed": configured,
        "auto_installable": False,
        "action": None,
        "version": None,
        "detail": detail,
        "guide": None if configured else _MANUAL_FIX_GUIDE["model_channel"],
        "optional": False,
    }


def build_plan() -> dict[str, Any]:
    """探测环境 → 生成统一待办计划（只读、无副作用）。"""
    probed = env_probe.probe()
    items: list[dict[str, Any]] = [_dep_status(item) for item in probed.get("items", [])]
    world_snapshot = world_install.status_snapshot()
    items.append(_world_status(world_snapshot))
    items.append(_model_channel_status())

    ready = sum(1 for it in items if it["status"] == "ready")
    installable = sum(1 for it in items if it["status"] == "installable")
    missing = sum(
        1 for it in items if it["status"] == "missing" and not it.get("optional")
    )
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "ready": ready,
            "installable": installable,
            "missing": missing,
            "all_ready": missing == 0 and installable == 0,
        },
        "world": world_snapshot,
    }


def install_item(item_id: str) -> dict[str, Any]:
    """逐项安装：可自动装项触发安装并回状态；其余回指引（不谎称已装）。"""
    item_id = str(item_id or "").strip()
    if item_id in AUTO_INSTALLABLE_IDS:
        snapshot = world_install.status_snapshot()
        if not snapshot.get("can_install"):
            return {
                "ok": True,
                "manual": True,
                "item": item_id,
                "guide": _MANUAL_FIX_GUIDE.get(
                    item_id, "精简镜像不支持在该路径自动安装，请重建部署镜像。"
                ),
                "status": snapshot,
            }
        return {
            "ok": True,
            "started": True,
            "item": item_id,
            "status": world_install.start_install(),
        }
    # 非自动安装项：只回事实性指引，不动任何状态。
    return {
        "ok": True,
        "manual": True,
        "item": item_id or None,
        "guide": _MANUAL_FIX_GUIDE.get(item_id, "该项无法在运行环境内自动安装，见部署文档。"),
    }
