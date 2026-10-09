"""Local CE project port implementations."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from ulid import ULID

from novelvideo.ports.project import Principal, ProjectRecord
from novelvideo.shared.project_dirs import default_project_dirs
from novelvideo.sqlite_pragmas import configure_sqlite_connection_async


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_username() -> str:
    return os.environ.get("ST_LOCAL_USERNAME", "").strip() or "local"


def _row_to_record(row: aiosqlite.Row) -> ProjectRecord:
    return ProjectRecord(
        id=str(row["id"]),
        owner_type=str(row["owner_type"]),
        owner_id=str(row["owner_id"]),
        owner_username=str(row["owner_username"]),
        name=str(row["name"]),
        home_node_id=str(row["home_node_id"]),
        output_dir=str(row["output_dir"]),
        state_dir=str(row["state_dir"]),
        runtime_dir=str(row["runtime_dir"]),
        status=str(row["status"]),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        purged_at=str(row["purged_at"]) if row["purged_at"] else None,
    )


async def _fetchone(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
    cursor = await db.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _fetchall(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
    cursor = await db.execute(sql, params)
    try:
        return await cursor.fetchall()
    finally:
        await cursor.close()


class SQLiteProjectRegistry:
    """CE project registry backed by STATE_DIR/local/projects.db."""

    def __init__(self) -> None:
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    def _db_path(self) -> Path:
        from novelvideo import config

        return Path(config.STATE_DIR) / "local" / "projects.db"

    async def _connect(self) -> aiosqlite.Connection:
        await self._ensure_schema()
        db = await aiosqlite.connect(self._db_path())
        db.row_factory = aiosqlite.Row
        # [O2] 统一套 WAL + busy_timeout，降低写并发下 "database is locked" 抖动。
        await configure_sqlite_connection_async(db)
        return db

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            path = self._db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(path)
            try:
                # [O2] 建表连接也先套 busy_timeout，避免并发首次初始化撞锁。
                await configure_sqlite_connection_async(db)
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        owner_type TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        owner_username TEXT NOT NULL,
                        name TEXT NOT NULL,
                        home_node_id TEXT NOT NULL,
                        output_dir TEXT NOT NULL,
                        state_dir TEXT NOT NULL,
                        runtime_dir TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        purged_at TEXT,
                        UNIQUE(owner_type, owner_id, name)
                    );

                    CREATE INDEX IF NOT EXISTS projects_owner_updated_idx
                        ON projects(owner_type, owner_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS projects_status_updated_idx
                        ON projects(status, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS projects_home_node_idx
                        ON projects(home_node_id);
                    """
                )
                await db.execute(
                    """
                    INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (1, ?)
                    """,
                    (_now_iso(),),
                )
                await db.commit()
            finally:
                await db.close()
            self._schema_ready = True

    async def get_project(self, project_id: str) -> ProjectRecord | None:
        if not project_id:
            return None
        db = await self._connect()
        try:
            row = await _fetchone(db, "SELECT * FROM projects WHERE id = ?", (project_id,))
        finally:
            await db.close()
        return _row_to_record(row) if row else None

    async def get_project_by_owner_name(
        self,
        owner_user_id: str,
        name: str,
    ) -> ProjectRecord | None:
        # [aipdd fork delta] 多用户：按真实 owner_user_id 查；单用户下调用方恒为 local，行为等价。
        if not owner_user_id or not name:
            return None
        db = await self._connect()
        try:
            row = await _fetchone(
                db,
                """
                SELECT * FROM projects
                WHERE owner_type = 'user' AND owner_id = ? AND name = ?
                  AND purged_at IS NULL
                """,
                (owner_user_id, name),
            )
        finally:
            await db.close()
        return _row_to_record(row) if row else None

    async def create_project(
        self,
        *,
        owner_user_id: str,
        owner_username: str,
        name: str,
        home_node_id: str | None = None,
        output_dir: str | None = None,
        state_dir: str | None = None,
        runtime_dir: str | None = None,
    ) -> ProjectRecord:
        if not owner_user_id or not name:
            raise ValueError("owner_user_id and name are required")
        # [aipdd fork delta] 单用户模式保持旧契约：owner 强制归 local（既有测试钉死）；
        # 多用户模式写入真实 owner_user_id。
        from novelvideo.shared import runtime_env

        if not runtime_env.ce_multi_user():
            owner_user_id = "local"
        owner_username = owner_username.strip() if owner_username else _local_username()
        default_output, default_state, default_runtime = default_project_dirs(owner_username, name)
        now = _now_iso()
        project_id = str(ULID())
        db = await self._connect()
        try:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """
                    INSERT INTO projects (
                        id, owner_type, owner_id, owner_username, name, home_node_id,
                        output_dir, state_dir, runtime_dir, status, created_at, updated_at
                    )
                    VALUES (?, 'user', ?, ?, ?, 'local', ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        project_id,
                        # [aipdd fork delta] 写入真实 owner，不再硬编码 'local'
                        owner_user_id,
                        owner_username,
                        name,
                        output_dir or default_output,
                        state_dir or default_state,
                        runtime_dir or default_runtime,
                        now,
                        now,
                    ),
                )
                row = await _fetchone(
                    db,
                    "SELECT * FROM projects WHERE id = ?",
                    (project_id,),
                )
                await db.commit()
            except sqlite3.IntegrityError as exc:
                await db.rollback()
                raise ValueError(f"Project '{name}' already exists") from exc
            except Exception:
                await db.rollback()
                raise
        finally:
            await db.close()
        return _row_to_record(row)

    async def list_accessible_projects(
        self,
        principals: list[tuple[str, str]],
    ) -> list[ProjectRecord]:
        # [aipdd fork delta] 多用户：按 principals 里的真实 user id 过滤；
        # 单用户模式下 principals 恒为 ('user','local')，行为与改前一致。
        user_ids = sorted({pid for kind, pid in principals if kind == "user" and pid})
        if not user_ids:
            return []
        db = await self._connect()
        try:
            rows = await _fetchall(
                db,
                f"""
                SELECT * FROM projects
                WHERE owner_type = 'user' AND owner_id IN ({",".join("?" * len(user_ids))})
                ORDER BY updated_at DESC
                """,
                tuple(user_ids),
            )
        finally:
            await db.close()
        return [_row_to_record(row) for row in rows]

    async def update_project_status(
        self,
        project_id: str,
        status: str,
    ) -> ProjectRecord | None:
        if not project_id:
            return None
        db = await self._connect()
        try:
            await db.execute(
                """
                UPDATE projects
                SET status = ?, updated_at = ?
                WHERE id = ? AND purged_at IS NULL
                """,
                (status, _now_iso(), project_id),
            )
            await db.commit()
            row = await _fetchone(db, "SELECT * FROM projects WHERE id = ?", (project_id,))
        finally:
            await db.close()
        return _row_to_record(row) if row else None

    async def mark_project_purged(self, project_id: str) -> ProjectRecord | None:
        if not project_id:
            return None
        now = _now_iso()
        db = await self._connect()
        try:
            row = await _fetchone(db, "SELECT * FROM projects WHERE id = ?", (project_id,))
            if row is None:
                return None
            record = _row_to_record(row)
            await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await db.commit()
        finally:
            await db.close()
        return replace(record, status="deleted", updated_at=now, purged_at=now)

    async def delete_uncommitted_project(self, project_id: str) -> None:
        if not project_id:
            return None
        db = await self._connect()
        try:
            await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await db.commit()
        finally:
            await db.close()
        return None

    async def delete_project_home(self, project_id: str) -> None:
        return None

    async def resolve_username_by_user_id(self, user_id: str) -> str | None:
        if not user_id:
            return None
        if user_id == "local":
            return _local_username()
        from novelvideo.shared import runtime_env

        if runtime_env.ce_multi_user():
            from novelvideo.ports.local.users import get_local_user_store

            record = await get_local_user_store().get_user_by_id(user_id)
            if record:
                return record.username
        return None

    async def resolve_user_id_by_username(self, username: str) -> str | None:
        if not username:
            return None
        from novelvideo.shared import runtime_env

        if runtime_env.ce_multi_user():
            from novelvideo.ports.local.users import get_local_user_store

            record = await get_local_user_store().get_user_by_username(username)
            return record.id if record else None
        return "local" if username else None


FileProjectRegistry = SQLiteProjectRegistry


class AllowAllProjectAccess:
    """CE 项目访问端口。

    [aipdd fork delta] ST_CE_MULTI_USER 打开时收紧为 owner-only 隔离（非 owner →
    None，由上层 403/404 机制承接）；关闭时保持 allow-all 语义不变（类名沿用
    是为了不破坏既有契约测试的引用）。
    """

    async def resolve_requester_principals(self, user_id: str) -> list[Principal]:
        from novelvideo.shared import runtime_env

        if not runtime_env.ce_multi_user():
            return [Principal("user", "local")] if user_id else []
        return [Principal("user", user_id)] if user_id else []

    async def effective_project_role(
        self,
        project: ProjectRecord,
        principals: list[Principal],
    ) -> str | None:
        from novelvideo.shared import runtime_env

        if not runtime_env.ce_multi_user():
            return "owner"
        user_ids = {p.id for p in principals if p.type == "user"}
        return "owner" if project.owner_id in user_ids else None

    async def effective_project_role_by_id(
        self,
        project_id: str,
        principals: list[Principal],
    ) -> str | None:
        from novelvideo.shared import runtime_env

        if not runtime_env.ce_multi_user():
            return "owner"
        from novelvideo.ports.registry import get_port

        registry = get_port("project_registry")
        project = await registry.get_project(project_id)
        if project is None:
            return None
        return await self.effective_project_role(project, principals)

    async def count_project_task_eligible_users(
        self,
        *,
        project_id: str,
        owner_type: str,
        owner_id: str,
    ) -> int:
        return 1
