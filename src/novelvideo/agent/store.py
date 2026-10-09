# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 存储层：会话 / 消息 / 设置（SQLite）+ 用户级数据目录。

与主线数据完全隔离：默认落在 ``STATE_DIR/_shared/agent/<safe_username>/``——
``_shared`` 是项目既有约定里放跨项目共享数据的目录，不会与
``STATE_DIR/<username>/<project>`` 的项目目录互相踩踏。也可以通过环境变量
``NOVELVIDEO_AGENT_DATA_DIR`` 覆盖根目录（测试用）。

目录内容：
- ``agent.db``         会话、消息、设置（SQLite）
- ``skills/<name>/``   用户安装的技能（SKILL.md 及其附件）
- ``memory/MEMORY.md`` 长期记忆
- ``mcp_servers.json`` MCP 服务器配置
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.sqlite_pragmas import configure_sqlite_connection

logger = logging.getLogger("novelvideo.agent.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_events_json TEXT NOT NULL DEFAULT '',
    media_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_conversation
    ON agent_messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS agent_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_SAFE_USERNAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
TITLE_MAX_LEN = 80
MESSAGE_MAX_CHARS = 60000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_data_root() -> Path:
    """Agent 数据根目录（所有用户共享的父目录）。"""
    override = os.environ.get("NOVELVIDEO_AGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # 动态读取 config.STATE_DIR：测试里 monkeypatch config.STATE_DIR 后，
    # 路径解析必须跟着走（模块级 from import 会把它钉死在导入时刻）。
    from novelvideo import config

    return (Path(config.STATE_DIR) / "_shared" / "agent").resolve()


def safe_username(username: str) -> str:
    """把用户名收敛成安全的目录名；收敛后为空时用哈希兜底。"""
    raw = str(username or "").strip()
    clean = _SAFE_USERNAME_RE.sub("_", raw)[:64].strip("._")
    if clean:
        return clean
    return "u" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def agent_root(username: str) -> Path:
    root = agent_data_root() / safe_username(username)
    root.mkdir(parents=True, exist_ok=True)
    return root


def skills_root(username: str) -> Path:
    path = agent_root(username) / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_dir(username: str) -> Path:
    path = agent_root(username) / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mcp_servers_file(username: str) -> Path:
    return agent_root(username) / "mcp_servers.json"


def _connect(username: str) -> sqlite3.Connection:
    conn = sqlite3.connect(agent_root(username) / "agent.db", timeout=15)
    configure_sqlite_connection(conn)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _row_to_conversation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "project": row["project"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "tool_events": _loads(row["tool_events_json"], []),
        "media": _loads(row["media_json"], []),
        "created_at": row["created_at"],
    }


def _loads(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def list_conversations(username: str, project: str | None = None) -> list[dict[str, Any]]:
    conn = _connect(username)
    try:
        if project:
            rows = conn.execute(
                "SELECT * FROM agent_conversations WHERE project = ? ORDER BY updated_at DESC",
                (project,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [_row_to_conversation(row) for row in rows]
    finally:
        conn.close()


def get_conversation(username: str, conversation_id: str) -> dict[str, Any] | None:
    conn = _connect(username)
    try:
        row = conn.execute(
            "SELECT * FROM agent_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return _row_to_conversation(row) if row else None
    finally:
        conn.close()


def create_conversation(username: str, *, project: str = "", title: str = "") -> dict[str, Any]:
    timestamp = now_iso()
    conversation = {
        "id": "aconv_" + uuid.uuid4().hex[:16],
        "title": (title or "").strip()[:TITLE_MAX_LEN],
        "project": (project or "").strip(),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    conn = _connect(username)
    try:
        conn.execute(
            "INSERT INTO agent_conversations (id, title, project, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                conversation["id"],
                conversation["title"],
                conversation["project"],
                conversation["created_at"],
                conversation["updated_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return conversation


def update_conversation(
    username: str,
    conversation_id: str,
    *,
    title: str | None = None,
    project: str | None = None,
) -> dict[str, Any] | None:
    conn = _connect(username)
    try:
        row = conn.execute(
            "SELECT * FROM agent_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        new_title = row["title"] if title is None else (title or "").strip()[:TITLE_MAX_LEN]
        new_project = row["project"] if project is None else (project or "").strip()
        conn.execute(
            "UPDATE agent_conversations SET title = ?, project = ?, updated_at = ? WHERE id = ?",
            (new_title, new_project, now_iso(), conversation_id),
        )
        conn.commit()
        return _row_to_conversation(
            conn.execute(
                "SELECT * FROM agent_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        )
    finally:
        conn.close()


def touch_conversation(username: str, conversation_id: str) -> None:
    conn = _connect(username)
    try:
        conn.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
            (now_iso(), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_conversation(username: str, conversation_id: str) -> bool:
    conn = _connect(username)
    try:
        cursor = conn.execute(
            "DELETE FROM agent_conversations WHERE id = ?", (conversation_id,)
        )
        conn.execute(
            "DELETE FROM agent_messages WHERE conversation_id = ?", (conversation_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------


def list_messages(username: str, conversation_id: str) -> list[dict[str, Any]]:
    conn = _connect(username)
    try:
        rows = conn.execute(
            "SELECT * FROM agent_messages WHERE conversation_id = ? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
        return [_row_to_message(row) for row in rows]
    finally:
        conn.close()


def append_message(
    username: str,
    conversation_id: str,
    *,
    role: str,
    content: str = "",
    tool_events: list[dict[str, Any]] | None = None,
    media: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message = {
        "id": "amsg_" + uuid.uuid4().hex[:16],
        "conversation_id": conversation_id,
        "role": role,
        "content": str(content or "")[:MESSAGE_MAX_CHARS],
        "tool_events": tool_events or [],
        "media": media or [],
        "created_at": now_iso(),
    }
    conn = _connect(username)
    try:
        conn.execute(
            "INSERT INTO agent_messages"
            " (id, conversation_id, role, content, tool_events_json, media_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message["id"],
                message["conversation_id"],
                message["role"],
                message["content"],
                json.dumps(message["tool_events"], ensure_ascii=False),
                json.dumps(message["media"], ensure_ascii=False),
                message["created_at"],
            ),
        )
        conn.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
            (message["created_at"], conversation_id),
        )
        conn.commit()
    finally:
        conn.close()
    return message


def clear_messages(username: str, conversation_id: str) -> int:
    conn = _connect(username)
    try:
        cursor = conn.execute(
            "DELETE FROM agent_messages WHERE conversation_id = ?", (conversation_id,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 设置（kv）
# ---------------------------------------------------------------------------


def get_setting(username: str, key: str, default: str = "") -> str:
    conn = _connect(username)
    try:
        row = conn.execute(
            "SELECT value FROM agent_settings WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default
    finally:
        conn.close()


def set_setting(username: str, key: str, value: str) -> None:
    conn = _connect(username)
    try:
        conn.execute(
            "INSERT INTO agent_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value or "")),
        )
        conn.commit()
    finally:
        conn.close()


def get_json_setting(username: str, key: str, default: Any) -> Any:
    raw = get_setting(username, key, "")
    if not raw:
        return default
    return _loads(raw, default)


def set_json_setting(username: str, key: str, value: Any) -> None:
    set_setting(username, key, json.dumps(value, ensure_ascii=False))