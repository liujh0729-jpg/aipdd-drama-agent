"""Scoped chat persistence shared by NiceGUI and the React WebSocket API.

Lovart-style split:
    * home scope: user-level conversation before a project exists.
    * project scope: project/canvas conversation and iteration history.

The project chat DB path intentionally matches ``chat_service.py`` so existing
NiceGUI history remains readable by the future React UI.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from novelvideo.sqlite_pragmas import configure_sqlite_connection


def _assistant_prefix_candidates(previous_assistant: object) -> list[str]:
    if isinstance(previous_assistant, (list, tuple)):
        items = [str(item or "").strip() for item in previous_assistant if str(item or "").strip()]
        candidates = ["".join(items[index:]) for index in range(len(items))]
        candidates.extend(items)
        return sorted(set(candidates), key=len, reverse=True)
    prefix = str(previous_assistant or "").strip()
    return [prefix] if prefix else []


def _strip_replayed_assistant_prefix(content: str, previous_assistant: object) -> str:
    text = str(content or "")
    for prefix in _assistant_prefix_candidates(previous_assistant):
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
        compact_prefix = "".join(prefix.split())
        if not compact_prefix:
            continue
        matched = 0
        end_index = 0
        for index, char in enumerate(text):
            if char.isspace():
                continue
            if matched >= len(compact_prefix) or char != compact_prefix[matched]:
                break
            matched += 1
            end_index = index + 1
            if matched == len(compact_prefix):
                return text[end_index:].lstrip()
    return text


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / "state"


@dataclass(frozen=True)
class ChatScope:
    kind: Literal["home", "project", "asset", "task"]
    id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ChatScope":
        payload = payload or {"kind": "home"}
        kind = str(payload.get("kind") or "home")
        if kind not in {"home", "project", "asset", "task"}:
            raise ValueError(f"unsupported chat scope: {kind}")
        raw_id = payload.get("id")
        scope_id = str(raw_id).strip() if raw_id is not None else None
        if kind == "home":
            scope_id = None
        if kind != "home" and not scope_id:
            raise ValueError(f"scope id is required for {kind}")
        return cls(kind=kind, id=scope_id)

    def to_dict(self) -> dict[str, str | None]:
        return {"kind": self.kind, "id": self.id}


class ChatStore:
    def db_for(self, username: str, scope: ChatScope) -> Path:
        if scope.kind == "home":
            return _state_root() / username / "_home" / "chat.db"
        if scope.kind == "project":
            return _state_root() / username / str(scope.id) / "chat.db"
        return _state_root() / username / f"_{scope.kind}" / str(scope.id) / "chat.db"

    def connect(self, username: str, scope: ChatScope) -> sqlite3.Connection:
        db_path = self.db_for(username, scope)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              media_json TEXT NOT NULL DEFAULT '[]',
              turn_id TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        if "turn_id" not in columns:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN turn_id TEXT")
        if "metadata_json" not in columns:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_ui_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              turn_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_ui_events_turn_id
              ON chat_ui_events(turn_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        return conn

    def append_message(
        self,
        username: str,
        scope: ChatScope,
        role: str,
        content: str,
        media: list[dict[str, Any]] | None = None,
        *,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        media = media or []
        metadata = metadata or {}
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self.connect(username, scope)
        try:
            cursor = conn.execute(
                """
                INSERT INTO chat_messages(role, content, media_json, turn_id, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    role,
                    content,
                    json.dumps(media, ensure_ascii=False),
                    turn_id,
                    json.dumps(metadata, ensure_ascii=False),
                    created_at,
                ),
            )
            conn.commit()
            return {
                "id": int(cursor.lastrowid),
                "role": role,
                "content": content,
                "media": media,
                "attachments": media,
                **({"turn_id": turn_id} if turn_id else {}),
                **({"metadata": metadata} if metadata else {}),
                "created_at": created_at,
            }
        finally:
            conn.close()

    def append_ui_event(
        self,
        username: str,
        scope: ChatScope,
        turn_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        event_type = str(event.get("type") or event.get("event_type") or "ui_event").strip()
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self.connect(username, scope)
        try:
            cursor = conn.execute(
                """
                INSERT INTO chat_ui_events(turn_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (turn_id, event_type, json.dumps(event, ensure_ascii=False), created_at),
            )
            conn.commit()
            return {
                "id": int(cursor.lastrowid),
                "turn_id": turn_id,
                "type": event_type,
                "payload": event,
                "created_at": created_at,
            }
        finally:
            conn.close()

    def _load_ui_events(self, conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
        rows = conn.execute(
            """
            SELECT id, turn_id, event_type, payload_json, created_at
              FROM chat_ui_events
             ORDER BY id ASC
            """
        ).fetchall()
        events_by_turn: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            turn_id = str(row["turn_id"] or "").strip()
            if not turn_id:
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            payload = {
                "id": int(row["id"]),
                "type": str(row["event_type"] or payload.get("type") or "ui_event"),
                "turn_id": turn_id,
                "created_at": str(row["created_at"]),
                **payload,
            }
            events_by_turn.setdefault(turn_id, []).append(payload)
        return events_by_turn

    @staticmethod
    def _attach_ui_events_to_messages(
        messages: list[dict[str, Any]],
        events_by_turn: dict[str, list[dict[str, Any]]],
    ) -> None:
        if not messages or not events_by_turn:
            return
        for turn_id, events in events_by_turn.items():
            if not events:
                continue
            target_index: int | None = None
            for index, message in enumerate(messages):
                if message.get("role") == "assistant" and message.get("turn_id") == turn_id:
                    target_index = index
                    break
            if target_index is None:
                user_index = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if message.get("role") == "user" and message.get("turn_id") == turn_id
                    ),
                    None,
                )
                if user_index is not None:
                    for index in range(user_index + 1, len(messages)):
                        if messages[index].get("role") == "assistant":
                            target_index = index
                            break
            if target_index is None:
                continue
            existing = messages[target_index].get("ui_events")
            if not isinstance(existing, list):
                existing = []
            messages[target_index]["ui_events"] = [*existing, *events]

    def list_messages(
        self,
        username: str,
        scope: ChatScope,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conn = self.connect(username, scope)
        try:
            rows = conn.execute(
                """
                SELECT id, role, content, media_json, turn_id, metadata_json, created_at
                  FROM chat_messages
                 WHERE role <> 'trace'
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
            events_by_turn = self._load_ui_events(conn)
        finally:
            conn.close()
        messages: list[dict[str, Any]] = []
        previous_assistants: list[str] = []
        for row in reversed(rows):
            try:
                media = json.loads(row["media_json"] or "[]")
            except json.JSONDecodeError:
                media = []
            role = str(row["role"])
            content = str(row["content"])
            if role == "assistant":
                raw_content = content
                content = _strip_replayed_assistant_prefix(content, previous_assistants)
                previous_assistants.append(raw_content)
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            messages.append(
                {
                    "id": int(row["id"]),
                    "role": role,
                    "content": content,
                    "media": media if isinstance(media, list) else [],
                    "attachments": media if isinstance(media, list) else [],
                    **({"turn_id": str(row["turn_id"])} if row["turn_id"] else {}),
                    **metadata,
                    "created_at": str(row["created_at"]),
                }
            )
        self._attach_ui_events_to_messages(messages, events_by_turn)
        return messages


chat_store = ChatStore()
