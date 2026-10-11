# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置 →「系统整体自检」：一键聚合只读体检。

需求（测试问题发现.md L9）：给超管一个入口，一次看清「环境 / 模型 / 模型调用是否
就绪 / 依赖」整盘状态。这里只做**只读聚合**，绝不改动任何状态：

- 运行依赖直接复用 :mod:`novelvideo.env_probe`（ffmpeg / node / npm / uv / 中文字体
  / Chrome CDP / OpenCV 的真实运行时快照）。
- 模型渠道：取生效网关（mode / base_url / key 是否配齐），并做一次**轻量连通性**
  探测——GET 上游 ``/v1/models`` 拿模型清单，确认「填了 key 且上游真能答话」。有意
  **不发起任何生成请求**：调用就绪只验配置与连通，零额度消耗、可放心频繁自检。
- 分类默认模型：逐个媒体类型（image / video / audio / music / text / vision /
  embedding）解析是否已设默认，未设如实列出（与「设置 → 模型与渠道」面板同口径）。
- 存储：``STATE_DIR`` 磁盘余量 + settings.db 可读写（PRAGMA quick_check）。

每个条目统一形状 ``{id, level, detail?}``，``level`` ∈ ``pass|warn|fail|info``。
名称 / 用途 / 修复文案由前端按 ``section`` + ``id`` 取 i18n，这里只回事实。
任一子检查异常一律降级为 ``warn`` / ``fail`` 摘要，**绝不让整盘自检崩掉**。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from novelvideo import config, env_probe

# 上游连通性探测超时：短，绝不让自检卡住。
_GATEWAY_PROBE_TIMEOUT = 5.0
# 磁盘余量低于此阈值报 warn（字节）。部署机一般几十 GB，2GB 足够触发提醒。
_DISK_WARN_BYTES = 2 * 1024 * 1024 * 1024

# 「默认生成模型」分类栏目（与前端 MEDIA_DEFAULT_TABS 排除 other 后同集合）。
_DEFAULT_MODEL_TABS: tuple[str, ...] = (
    "image",
    "video",
    "audio",
    "music",
    "text",
    "vision",
    "embedding",
)

# 依赖 id 中属于「可选特性」的：未就绪不是故障，用中性 info 而非红 fail。
_OPTIONAL_DEP_IDS: frozenset[str] = frozenset({"chrome"})


def _dep_level(item: dict[str, Any]) -> str:
    if item.get("installed"):
        return "pass"
    return "info" if str(item.get("id")) in _OPTIONAL_DEP_IDS else "fail"


def _environment_section() -> dict[str, Any]:
    probed = env_probe.probe()
    items = [
        {"id": str(item.get("id")), "level": _dep_level(item), "detail": item.get("detail")}
        for item in probed.get("items", [])
    ]
    return {"id": "environment", "items": items}


def _gateway_probe(base_url: str, api_key: str) -> tuple[bool, str]:
    """GET 上游模型清单确认「填了 key 且上游能答话」——只读、零生成消耗。

    openai 兼容网关：依次试 ``{base}/models``、``{base}/v1/models``（base 已含
    版本段时前者命中）。拿到 2xx 且 JSON 里有 ``data`` / ``models`` 视为可达。
    失败成因（HTTP 码 / 连接拒绝 / 超时 / 非 JSON）原样回摘要，绝不谎报就绪。
    """

    root = base_url.rstrip("/")
    candidates = [root]
    if not root.endswith("/v1"):
        candidates.append(f"{root}/v1")
    last_reason = "no-base-url" if not root else "empty-response"
    for candidate in candidates:
        url = f"{candidate}/models"
        request = urllib.request.Request(url, method="GET")
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(request, timeout=_GATEWAY_PROBE_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as exc:
            last_reason = f"{url} (HTTP {exc.code})"
            continue
        except Exception as exc:  # noqa: BLE001 - 探测失败一律如实回成因
            underlying = getattr(exc, "reason", None) or exc
            last_reason = f"{url} ({type(exc).__name__}: {underlying})"
            continue
        if isinstance(payload, dict) and (payload.get("data") or payload.get("models")):
            return True, url
        last_reason = f"{url} (no model list in response)"
    return False, last_reason


def _model_gateway_section() -> dict[str, Any]:
    from novelvideo.model_gateway_settings import get_effective_newapi_config

    items: list[dict[str, Any]] = []
    base_url = ""
    api_key = ""
    mode = ""
    try:
        cfg = get_effective_newapi_config()
        base_url = str(cfg.base_url or "").strip()
        api_key = str(cfg.api_key or "").strip()
        mode = str(cfg.mode or "")
    except Exception as exc:  # noqa: BLE001 - 读取生效配置失败降级为 fail 摘要
        items.append({"id": "gateway_configured", "level": "fail", "detail": str(exc)})
        return {"id": "model_gateway", "items": items}

    configured = bool(base_url and api_key)
    items.append(
        {
            "id": "gateway_configured",
            "level": "pass" if configured else "fail",
            "detail": (f"mode={mode} base_url={base_url}" if base_url else f"mode={mode}"),
        }
    )
    if not configured:
        items.append(
            {"id": "gateway_reachable", "level": "fail", "detail": "no gateway base_url or api_key"}
        )
    else:
        reachable, detail = _gateway_probe(base_url, api_key)
        items.append({"id": "gateway_reachable", "level": "pass" if reachable else "fail", "detail": detail})
    return {"id": "model_gateway", "items": items}


def _resolve_default_model(media_type: str) -> str:
    from novelvideo.model_gateway_settings import (
        get_effective_default_text_model,
        resolve_default_media_model,
    )

    if media_type == "text":
        return get_effective_default_text_model()
    return resolve_default_media_model(media_type)


def _default_models_section() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for media_type in _DEFAULT_MODEL_TABS:
        try:
            resolved = _resolve_default_model(media_type)
        except Exception as exc:  # noqa: BLE001 - 单类解析失败按未设处理，不崩整盘
            items.append({"id": media_type, "level": "fail", "detail": str(exc)})
            continue
        if str(resolved or "").strip():
            items.append({"id": media_type, "level": "pass", "detail": str(resolved)})
        else:
            items.append({"id": media_type, "level": "fail", "detail": None})
    return {"id": "default_models", "items": items}


def _storage_section() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    state_dir = Path(config.STATE_DIR)
    try:
        free = shutil.disk_usage(str(state_dir)).free
        items.append(
            {
                "id": "disk_space",
                "level": "pass" if free >= _DISK_WARN_BYTES else "warn",
                "detail": f"{free // (1024 * 1024)} MB",
            }
        )
    except OSError as exc:  # noqa: BLE001 - 磁盘探测失败不阻断自检
        items.append({"id": "disk_space", "level": "warn", "detail": str(exc)})

    db_path = state_dir / "local" / "settings.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            conn.execute("SELECT 1").fetchone()
            ok = str(conn.execute("PRAGMA quick_check").fetchone()[0]).lower() == "ok"
        finally:
            conn.close()
        items.append(
            {
                "id": "settings_db",
                "level": "pass" if ok else "warn",
                "detail": str(db_path),
            }
        )
    except sqlite3.Error as exc:  # noqa: BLE001 - 数据库探测失败按 fail 回显，不抛
        items.append({"id": "settings_db", "level": "fail", "detail": str(exc)})

    return {"id": "storage", "items": items}


def run_diagnostics() -> dict[str, Any]:
    """聚合环境 / 模型渠道 / 默认模型 / 存储四组只读体检，回结构化报告。

    每个分组独立容错：某一组探测抛异常只降级为该组的 ``fail`` 摘要，绝不让整盘
    自检崩掉（这正是「一键体检」应有的稳健性）。
    """
    builders = {
        "environment": _environment_section,
        "model_gateway": _model_gateway_section,
        "default_models": _default_models_section,
        "storage": _storage_section,
    }
    sections: list[dict[str, Any]] = []
    for section_id, build in builders.items():
        try:
            sections.append(build())
        except Exception as exc:  # noqa: BLE001 - 单组崩降级为一条 fail，不拖垮全盘
            sections.append(
                {"id": section_id, "items": [{"id": "check_error", "level": "fail", "detail": str(exc)}]}
            )
    all_items = [item for section in sections for item in section["items"]]
    counts = {"pass": 0, "warn": 0, "fail": 0, "info": 0}
    for item in all_items:
        level = str(item.get("level"))
        if level in counts:
            counts[level] += 1
    attention = counts["warn"] + counts["fail"]
    return {
        "sections": sections,
        "summary": {
            "total": len(all_items),
            "pass": counts["pass"],
            "warn": counts["warn"],
            "fail": counts["fail"],
            "info": counts["info"],
            "all_ok": attention == 0,
        },
    }
