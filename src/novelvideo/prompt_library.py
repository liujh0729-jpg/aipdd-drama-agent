"""[aipdd fork delta] 提示词库：内置提示词注册表 + 用户覆盖。

原先写死在生成器 / Agent 里的高频提示词统一登记到
``prompt_library_defaults.DEFAULT_PROMPTS``，用户在「设置 → 提示词库」里的修改
以 ``prompt_library.<key>`` 前缀写进 CE 的 runtime_settings KV 表（与模型网关
配置同一个 settings.db），生成时优先读取覆盖值，未覆盖则回退内置默认。

渲染约定：只替换模板中已知的占位符，未知占位符原样保留；任何存储 / 渲染异常
都回退到内置默认，保证生成链路不因提示词库故障中断。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from novelvideo.prompt_library_defaults import DEFAULT_PROMPTS
from novelvideo.sqlite_pragmas import configure_sqlite_connection

logger = logging.getLogger("novelvideo.prompt_library")

KEY_PREFIX = "prompt_library."
MAX_OVERRIDE_CHARS = 20_000
_CACHE_TTL_SECONDS = 1.0
_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class PromptEntry:
    """一条提示词登记项。"""

    key: str
    group: str
    label: str
    description: str
    variables: tuple[str, ...]
    default: str


_PROMPTS: tuple[PromptEntry, ...] = tuple(
    PromptEntry(
        key=str(item["key"]),
        group=str(item["group"]),
        label=str(item["label"]),
        description=str(item["description"]),
        variables=tuple(str(name) for name in item.get("variables", ())),
        default=str(item["default"]),
    )
    for item in DEFAULT_PROMPTS
)
_PROMPTS_BY_KEY: dict[str, PromptEntry] = {entry.key: entry for entry in _PROMPTS}
GROUP_ORDER: tuple[str, ...] = tuple(dict.fromkeys(entry.group for entry in _PROMPTS))


def prompt_entries() -> tuple[PromptEntry, ...]:
    """按登记顺序返回全部提示词条目。"""
    return _PROMPTS


def get_prompt_entry(key: str) -> PromptEntry:
    """按 key 取登记项，未知 key 直接抛 KeyError（属编程错误）。"""
    entry = _PROMPTS_BY_KEY.get(str(key or "").strip())
    if entry is None:
        raise KeyError(f"unknown prompt key: {key!r}")
    return entry


# ---------------------------------------------------------------------------
# runtime_settings 存储（与 model_gateway_settings 共用 settings.db / 同一张表）
# ---------------------------------------------------------------------------


def _settings_db_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_overrides() -> dict[str, tuple[str, str]]:
    """读取全部覆盖项：key -> (value, updated_at)。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM runtime_settings WHERE key LIKE ?",
            (f"{KEY_PREFIX}%",),
        ).fetchall()
    finally:
        conn.close()
    return {
        str(row["key"])[len(KEY_PREFIX) :]: (str(row["value"]), str(row["updated_at"]))
        for row in rows
    }


def _write_override(key: str, value: str) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO runtime_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (f"{KEY_PREFIX}{key}", value, _now_iso()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _delete_override(key: str) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM runtime_settings WHERE key = ?",
            (f"{KEY_PREFIX}{key}",),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 进程内读缓存（1 秒 TTL，多 worker 下保存后最多 1 秒生效）
# ---------------------------------------------------------------------------

_override_cache: dict[str, tuple[str, str]] = {}
_override_cache_at = 0.0
_override_cache_lock = threading.Lock()


def _invalidate_override_cache() -> None:
    global _override_cache_at
    with _override_cache_lock:
        _override_cache.clear()
        _override_cache_at = 0.0


def _overrides() -> dict[str, tuple[str, str]]:
    global _override_cache_at
    with _override_cache_lock:
        now = time.monotonic()
        if _override_cache_at and now - _override_cache_at < _CACHE_TTL_SECONDS:
            return dict(_override_cache)
        try:
            rows = _read_overrides()
        except Exception as exc:  # pragma: no cover - 存储故障时不影响生成
            logger.warning("prompt library overrides unreadable: %s", exc)
            return dict(_override_cache)
        _override_cache.clear()
        _override_cache.update(rows)
        _override_cache_at = now
        return dict(_override_cache)


def _effective_text(entry: PromptEntry, override: tuple[str, str] | None) -> str:
    if override is None:
        return entry.default
    value = str(override[0])
    return value if value.strip() else entry.default


# ---------------------------------------------------------------------------
# 对外读接口
# ---------------------------------------------------------------------------


def get_prompt_text(key: str) -> str:
    """返回提示词生效文本（用户覆盖优先，否则内置默认）。"""
    entry = get_prompt_entry(key)
    return _effective_text(entry, _overrides().get(entry.key))


def render_prompt(key: str, **values: object) -> str:
    """渲染提示词模板：只替换已知占位符，未知占位符原样保留。"""
    return render_template(get_prompt_text(key), values)


def render_template(template: str, values: dict[str, object]) -> str:
    """容错渲染：只替换 values 中存在的占位符，其余保持原样。"""
    if not values:
        return template

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return str(values[name])
        return match.group(0)

    return _PLACEHOLDER_PATTERN.sub(_replace, template)


def list_prompt_items() -> list[dict[str, object]]:
    """返回设置面板用的完整清单（含默认值、生效值与是否被覆盖）。"""
    overrides = _overrides()
    items: list[dict[str, object]] = []
    for entry in _PROMPTS:
        override = overrides.get(entry.key)
        items.append(
            {
                "key": entry.key,
                "group": entry.group,
                "label": entry.label,
                "description": entry.description,
                "variables": list(entry.variables),
                "default": entry.default,
                "value": _effective_text(entry, override),
                "is_overridden": override is not None,
                "updated_at": override[1] if override is not None else None,
            }
        )
    return items


def get_prompt_item(key: str) -> dict[str, object]:
    """按 key 返回单条清单项。"""
    entry = get_prompt_entry(key)
    for item in list_prompt_items():
        if item["key"] == entry.key:
            return item
    raise KeyError(entry.key)


def revision() -> str:
    """提示词库版本号；覆盖集合变化时版本号变化（供 Agent 缓存失效判断）。"""
    overrides = _overrides()
    if not overrides:
        return "0"
    latest = max(updated_at for _, updated_at in overrides.values())
    return f"{len(overrides)}:{latest}"


# ---------------------------------------------------------------------------
# 对外写接口
# ---------------------------------------------------------------------------


def set_prompt_override(key: str, value: str) -> dict[str, object]:
    """保存一条覆盖；与内置默认一致时视为恢复默认。"""
    entry = get_prompt_entry(key)
    text = str(value or "")
    if not text.strip():
        raise ValueError("提示词内容不能为空")
    if len(text) > MAX_OVERRIDE_CHARS:
        raise ValueError(f"提示词内容过长（上限 {MAX_OVERRIDE_CHARS} 字符）")
    if text.strip() == entry.default.strip():
        _delete_override(entry.key)
    else:
        _write_override(entry.key, text)
    _invalidate_override_cache()
    return get_prompt_item(entry.key)


def reset_prompt_override(key: str) -> dict[str, object]:
    """删除一条覆盖，恢复内置默认。"""
    entry = get_prompt_entry(key)
    _delete_override(entry.key)
    _invalidate_override_cache()
    return get_prompt_item(entry.key)