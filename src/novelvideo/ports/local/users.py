"""[aipdd fork delta] CE 最小可用多用户隔离：本地用户与会话存储。

设计要点（v1，评估结论见对话）：
- 零新增依赖：口令散列用 stdlib ``hashlib.pbkdf2_hmac``（SHA-256, 240k 轮）+
  ``hmac.compare_digest``；会话 token 用 ``secrets.token_urlsafe``。
- 会话持久化在 ``STATE_DIR/local/auth.db``（与 projects.db 同目录），
  进程重启不丢登录态；``verify_session`` 命中时滑动续期。
- 引导账户 id 固定为 ``local``（username 取 ST_LOCAL_USERNAME，默认 local），
  这样 projects.db / output 里既有的 owner_id='local' 数据零迁移。
- 仅当 ``ST_CE_MULTI_USER`` 打开时才会被用到；关闭时鉴权仍走原单用户旁路。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from ulid import ULID

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason
from novelvideo.sqlite_pragmas import configure_sqlite_connection_async

logger = logging.getLogger("novelvideo.ports.local.users")

_PASSWORD_ITERATIONS = 240_000
_DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600
_MIN_PASSWORD_LENGTH = 6

# [aipdd fork delta] CE 只有两档角色：owner=超管（管理用户），member=普通成员。
# 项目仍按 owner_id 完全隔离，角色不授予跨用户可见性。
ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
VALID_ROLES = (ROLE_OWNER, ROLE_MEMBER)

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
VALID_STATUSES = (STATUS_ACTIVE, STATUS_SUSPENDED)

# [aipdd fork delta] 用户名会直接当输出目录的分片名用（项目按 username 落盘），
# 所以字符集必须先过一遍，不能只靠调用方自觉；允许中文是因为部署面向中文团队。
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}$")

# 管理员/超管口令门槛比自助注册更高：CE 是多单用户自建部署，端口直接暴露在内网。
PRIVILEGED_MIN_PASSWORD_LENGTH = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_username() -> str:
    return os.environ.get("ST_LOCAL_USERNAME", "").strip() or "local"


def session_ttl_seconds() -> int:
    raw = os.environ.get("ST_CE_SESSION_TTL_SECONDS", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_SESSION_TTL_SECONDS
    except ValueError:
        value = _DEFAULT_SESSION_TTL_SECONDS
    return value if value > 0 else _DEFAULT_SESSION_TTL_SECONDS


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (pass_hash_hex, salt_hex)。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PASSWORD_ITERATIONS,
    ).hex()
    return digest, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


@dataclass(frozen=True)
class LocalUserRecord:
    id: str
    username: str
    role: str
    status: str
    # [aipdd fork delta] 用户管理页需要展示建立时间；默认空串保证既有构造点不崩。
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "LocalUserRecord":
        keys = row.keys()
        return cls(
            id=str(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]) if "created_at" in keys else "",
            updated_at=str(row["updated_at"]) if "updated_at" in keys else "",
        )


# [aipdd fork delta] 统一 API 密钥：设置页生成一把 key，同时用于外部 MCP 与
# 模型广场（OpenAI 兼容入口）调用。密钥本身是高熵随机串，sha256 存库即可，
# 不需要口令级 KDF；明文只在创建时返回一次，库里只留前缀供列表展示。
API_KEY_PREFIX = "st-"
API_KEY_LAST_USED_THROTTLE_SECONDS = 60


def is_api_key_format(raw: str) -> bool:
    """统一密钥都带 'st-' 前缀；agent 短会话 token 是 'local-…'，不会误判。"""
    return (raw or "").strip().startswith(API_KEY_PREFIX)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256((raw or "").strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LocalApiKeyRecord:
    id: str
    user_id: str
    username: str
    name: str
    key_prefix: str
    created_at: str = ""
    last_used_at: str = ""
    revoked_at: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "LocalApiKeyRecord":
        keys = row.keys()
        return cls(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            name=str(row["name"]),
            key_prefix=str(row["key_prefix"]),
            created_at=str(row["created_at"]) if "created_at" in keys else "",
            last_used_at=str(row["last_used_at"] or "") if "last_used_at" in keys else "",
            revoked_at=str(row["revoked_at"] or "") if "revoked_at" in keys else "",
        )


def normalize_username(username: str) -> str:
    """清洗并校验用户名；不合法抛 AuthError（由路由映射成 400）。"""
    cleaned = (username or "").strip()
    if not USERNAME_RE.match(cleaned):
        raise AuthError(
            AuthFailureReason.INVALID,
            "username must be 2-32 characters of letters, digits, -, _ or Chinese",
        )
    return cleaned


def _validate_password(password: str, *, min_length: int = _MIN_PASSWORD_LENGTH) -> None:
    if len(password or "") < min_length:
        raise AuthError(
            AuthFailureReason.INVALID,
            f"password must be at least {min_length} characters",
        )


def validate_privileged_password(password: str) -> None:
    """超管口令门槛：首次初始化与管理员建号共用，避免两处各自抄一份。"""
    _validate_password(password, min_length=PRIVILEGED_MIN_PASSWORD_LENGTH)


class LocalUserStore:
    """users + sessions + api_keys 三表的 SQLite 存储（CE 本地鉴权后端）。"""

    def __init__(self) -> None:
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    def _db_path(self) -> Path:
        from novelvideo import config

        return Path(config.STATE_DIR) / "local" / "auth.db"

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
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        pass_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'owner',
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS sessions_user_idx
                        ON sessions(user_id);
                    CREATE INDEX IF NOT EXISTS sessions_expiry_idx
                        ON sessions(expires_at);

                    CREATE TABLE IF NOT EXISTS api_keys (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        username TEXT NOT NULL,
                        name TEXT NOT NULL,
                        key_hash TEXT NOT NULL UNIQUE,
                        key_prefix TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT,
                        revoked_at TEXT
                    );

                    CREATE INDEX IF NOT EXISTS api_keys_user_idx
                        ON api_keys(user_id);
                    """
                )
                await db.commit()
            finally:
                await db.close()
            self._schema_ready = True

    # ------------------------------------------------------------------ users

    async def count_users(self) -> int:
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT COUNT(*) AS n FROM users")
            row = await cursor.fetchone()
            await cursor.close()
            return int(row["n"]) if row else 0
        finally:
            await db.close()

    async def get_user_by_username(self, username: str) -> LocalUserRecord | None:
        if not username:
            return None
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            return LocalUserRecord.from_row(row) if row else None
        finally:
            await db.close()

    async def get_user_by_id(self, user_id: str) -> LocalUserRecord | None:
        if not user_id:
            return None
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            await cursor.close()
            return LocalUserRecord.from_row(row) if row else None
        finally:
            await db.close()

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        role: str = ROLE_MEMBER,
        user_id: str | None = None,
    ) -> LocalUserRecord:
        """新建账户。

        [aipdd fork delta] 默认角色改为 member：原来写死 owner，一旦打开自助
        注册就是“注册即管理员”。引导账户 / setup 页建超管时显式传 role=owner。
        """
        username = normalize_username(username)
        if role not in VALID_ROLES:
            raise AuthError(AuthFailureReason.INVALID, f"unknown role: {role}")
        _validate_password(password)
        if await self.get_user_by_username(username) is not None:
            raise AuthError(AuthFailureReason.INVALID, "username already exists")
        pass_hash, salt = hash_password(password)
        now = _now_iso()
        record = LocalUserRecord(
            id=user_id or str(ULID()),
            username=username,
            role=role,
            status=STATUS_ACTIVE,
            created_at=now,
            updated_at=now,
        )
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO users (id, username, pass_hash, salt, role, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (record.id, username, pass_hash, salt, role, now, now),
            )
            await db.commit()
        finally:
            await db.close()
        return record

    async def bootstrap_local_account(self) -> LocalUserRecord | None:
        """users 表为空时创建引导账户（id 固定 'local' → 既有数据零迁移）。

        口令取 ST_LOCAL_PASSWORD；未设置则生成随机口令并打一次 WARNING 日志。
        幂等：已有任何用户时不再创建，返回既有 local 账户（若有）。
        """
        await self._ensure_schema()
        if await self.count_users() > 0:
            return await self.get_user_by_username(local_username())
        password = os.environ.get("ST_LOCAL_PASSWORD", "").strip()
        if not password:
            password = secrets.token_urlsafe(12)
            logger.warning(
                "[aipdd multi-user] ST_LOCAL_PASSWORD 未设置；已为账户 '%s' 生成一次性口令，"
                "请立即用其登录并修改： %s",
                local_username(),
                password,
            )
        record = await self.create_user(
            local_username(), password, role=ROLE_OWNER, user_id="local"
        )
        logger.info("[aipdd multi-user] bootstrap 账户已创建: id=%s username=%s", record.id, record.username)
        return record

    # -------------------------------------------------------------- admin ops
    #
    # 下面这几个方法只给 /api/v1/admin/users 用，调用方必须先过 require_owner。
    # “最后一个可用超管”的守护故意同时留在 store 里：路由忘写一次也不会把系统
    # 改到无主，不能只依赖调用方自觉。

    async def list_users(self) -> list[LocalUserRecord]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM users ORDER BY created_at ASC, username ASC"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [LocalUserRecord.from_row(row) for row in rows]
        finally:
            await db.close()

    async def count_active_owners(self, *, exclude_user_id: str = "") -> int:
        """可用超管数量（可排除某个账户，用于“改自己之前先看还有没有别人”）。"""
        sql = (
            "SELECT COUNT(*) AS n FROM users"
            " WHERE role = ? AND status = ?"
        )
        params: list[object] = [ROLE_OWNER, STATUS_ACTIVE]
        if exclude_user_id:
            sql += " AND id != ?"
            params.append(exclude_user_id)
        db = await self._connect()
        try:
            cursor = await db.execute(sql, tuple(params))
            row = await cursor.fetchone()
            await cursor.close()
            return int(row["n"]) if row else 0
        finally:
            await db.close()

    async def set_user_role(self, user_id: str, role: str) -> LocalUserRecord:
        if role not in VALID_ROLES:
            raise AuthError(AuthFailureReason.INVALID, f"unknown role: {role}")
        current = await self.get_user_by_id(user_id)
        if current is None:
            raise AuthError(AuthFailureReason.INVALID, "user not found")
        if (
            current.role == ROLE_OWNER
            and role != ROLE_OWNER
            and current.status == STATUS_ACTIVE
            and await self.count_active_owners(exclude_user_id=user_id) == 0
        ):
            raise AuthError(
                AuthFailureReason.INVALID, "cannot demote the last active administrator"
            )
        return await self._update_user_fields(user_id, role=role)

    async def set_user_status(self, user_id: str, status: str) -> LocalUserRecord:
        if status not in VALID_STATUSES:
            raise AuthError(AuthFailureReason.INVALID, f"unknown status: {status}")
        current = await self.get_user_by_id(user_id)
        if current is None:
            raise AuthError(AuthFailureReason.INVALID, "user not found")
        if (
            status != STATUS_ACTIVE
            and current.role == ROLE_OWNER
            and current.status == STATUS_ACTIVE
            and await self.count_active_owners(exclude_user_id=user_id) == 0
        ):
            raise AuthError(
                AuthFailureReason.INVALID, "cannot disable the last active administrator"
            )
        return await self._update_user_fields(user_id, status=status)

    async def update_admin_fields(
        self,
        user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
    ) -> LocalUserRecord:
        """原子地改 role 与/或 status：一次读、两组守门、**单条 UPDATE**。

        旧路由对 role/status 分两次写，第二步失败时第一步不回滚 → 部分变更。这
        里把两个字段归到一次写入（``_update_user_fields`` 本就一条 SQL 更新两列）。
        守门仍逐字段与单字段方法一致（最后一个活跃管理员不可降级/不可禁用）。
        """

        if role is None and status is None:
            raise AuthError(AuthFailureReason.INVALID, "role or status is required")
        if role is not None and role not in VALID_ROLES:
            raise AuthError(AuthFailureReason.INVALID, f"unknown role: {role}")
        if status is not None and status not in VALID_STATUSES:
            raise AuthError(AuthFailureReason.INVALID, f"unknown status: {status}")
        current = await self.get_user_by_id(user_id)
        if current is None:
            raise AuthError(AuthFailureReason.INVALID, "user not found")
        # 只在“本次变更会使该账户不再是活跃 owner”时跑最后一个管理员守门：
        # 降级（role 离开 owner）与禁用（status 离开 active）共用同一条件。
        loses_active_owner = (
            current.role == ROLE_OWNER
            and current.status == STATUS_ACTIVE
            and (
                (role is not None and role != ROLE_OWNER)
                or (status is not None and status != STATUS_ACTIVE)
            )
        )
        if loses_active_owner and await self.count_active_owners(
            exclude_user_id=user_id
        ) == 0:
            raise AuthError(
                AuthFailureReason.INVALID,
                "cannot demote or disable the last active administrator",
            )
        return await self._update_user_fields(user_id, role=role, status=status)

    async def set_password(self, user_id: str, password: str) -> None:
        _validate_password(password)
        pass_hash, salt = hash_password(password)
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE users SET pass_hash = ?, salt = ?, updated_at = ? WHERE id = ?",
                (pass_hash, salt, _now_iso(), user_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise AuthError(AuthFailureReason.INVALID, "user not found")
            await cursor.close()
        finally:
            await db.close()

    async def _update_user_fields(
        self, user_id: str, *, role: str | None = None, status: str | None = None
    ) -> LocalUserRecord:
        sets: list[str] = []
        params: list[object] = []
        if role is not None:
            sets.append("role = ?")
            params.append(role)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if not sets:
            existing = await self.get_user_by_id(user_id)
            if existing is None:
                raise AuthError(AuthFailureReason.INVALID, "user not found")
            return existing
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(user_id)
        db = await self._connect()
        try:
            cursor = await db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise AuthError(AuthFailureReason.INVALID, "user not found")
            await cursor.close()
        finally:
            await db.close()
        updated = await self.get_user_by_id(user_id)
        if updated is None:
            raise AuthError(AuthFailureReason.INVALID, "user not found")
        return updated

    # ------------------------------------------------------------------- auth

    async def login(self, username: str, password: str) -> LocalUserRecord:
        """校验用户名+口令；失败一律抛 INVALID（不泄露用户是否存在）。"""
        row_user, row_hash, row_salt = await self._fetch_credential(
            (username or "").strip()
        )
        if row_user is None:
            # 恒时消耗，抹平"用户不存在"与"口令错误"的时间差
            hash_password(password or "", secrets.token_hex(16))
            raise AuthError(AuthFailureReason.INVALID, "invalid username or password")
        if not verify_password(password or "", row_hash, row_salt):
            raise AuthError(AuthFailureReason.INVALID, "invalid username or password")
        if row_user.status != "active":
            raise AuthError(AuthFailureReason.USER_SUSPENDED, "user suspended")
        return row_user

    async def _fetch_credential(
        self, username: str
    ) -> tuple[LocalUserRecord | None, str, str]:
        if not username:
            return None, "", ""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            await cursor.close()
        finally:
            await db.close()
        if row is None:
            return None, "", ""
        return LocalUserRecord.from_row(row), str(row["pass_hash"]), str(row["salt"])

    # --------------------------------------------------------------- sessions

    async def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db = await self._connect()
        try:
            await db.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, _now_iso(), now + session_ttl_seconds()),
            )
            await db.commit()
        finally:
            await db.close()
        return token

    async def get_session_user(self, token: str) -> LocalUserRecord:
        """按会话 token 取用户；缺失/过期/吊销 → AuthError。命中时滑动续期。"""
        if not token:
            raise AuthError(AuthFailureReason.MISSING, "session cookie missing")
        db = await self._connect()
        try:
            cursor = await db.execute(
                """
                SELECT u.*, s.expires_at AS s_expires_at
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = ?
                """,
                (token,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise AuthError(AuthFailureReason.INVALID, "session not found")
            if float(row["s_expires_at"]) < time.time():
                await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
                await db.commit()
                raise AuthError(AuthFailureReason.EXPIRED, "session expired")
            # 滑动续期：只在剩余不足一半时写库，减少高频请求下的写放大
            ttl = session_ttl_seconds()
            if float(row["s_expires_at"]) - time.time() < ttl / 2:
                await db.execute(
                    "UPDATE sessions SET expires_at = ? WHERE token = ?",
                    (time.time() + ttl, token),
                )
                await db.commit()
            record = LocalUserRecord.from_row(row)
        finally:
            await db.close()
        if record.status != "active":
            raise AuthError(AuthFailureReason.USER_SUSPENDED, "user suspended")
        return record

    async def revoke_session(self, token: str) -> None:
        if not token:
            return
        db = await self._connect()
        try:
            await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            await db.commit()
        finally:
            await db.close()

    async def purge_expired_sessions(self) -> int:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (time.time(),)
            )
            await db.commit()
            return cursor.rowcount or 0
        finally:
            await db.close()

    async def revoke_all_sessions(self, user_id: str) -> int:
        """吊销某账户全部会话：禁用 / 改口令 / 改角色后都要立刻踢下线。"""
        if not user_id:
            return 0
        db = await self._connect()
        try:
            cursor = await db.execute(
                "DELETE FROM sessions WHERE user_id = ?", (user_id,)
            )
            await db.commit()
            return cursor.rowcount or 0
        finally:
            await db.close()

    # --------------------------------------------------------------- api keys

    async def create_api_key(
        self, user_id: str, name: str = "", *, key_id: str | None = None
    ) -> tuple[LocalApiKeyRecord, str]:
        """创建一把统一 API 密钥，返回 (记录, 明文)；明文只在这里返回一次。

        单用户未开启多用户时 users 表为空，也允许建 key：账户名回落引导名，
        鉴权时按 owner 处理（见 verify_api_key）。
        """
        owner = (user_id or "").strip()
        if not owner:
            raise AuthError(AuthFailureReason.INVALID, "user id is required")
        record_name = (name or "").strip()[:64] or "default"
        user = await self.get_user_by_id(owner)
        if user is not None:
            if user.status != STATUS_ACTIVE:
                raise AuthError(AuthFailureReason.USER_SUSPENDED, "user suspended")
            username = user.username
        else:
            username = local_username()
        raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
        record = LocalApiKeyRecord(
            id=key_id or str(ULID()),
            user_id=owner,
            username=username,
            name=record_name,
            key_prefix=raw[:12],
            created_at=_now_iso(),
        )
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO api_keys
                    (id, user_id, username, name, key_hash, key_prefix, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    owner,
                    username,
                    record_name,
                    hash_api_key(raw),
                    record.key_prefix,
                    record.created_at,
                ),
            )
            await db.commit()
        finally:
            await db.close()
        return record, raw

    async def list_api_keys(self, user_id: str | None = None) -> list[LocalApiKeyRecord]:
        sql = "SELECT * FROM api_keys"
        params: tuple[object, ...] = ()
        if user_id:
            sql += " WHERE user_id = ?"
            params = (user_id,)
        sql += " ORDER BY created_at DESC"
        db = await self._connect()
        try:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
            return [LocalApiKeyRecord.from_row(row) for row in rows]
        finally:
            await db.close()

    async def revoke_api_key(self, key_id: str, *, user_id: str | None = None) -> bool:
        """软删除（置 revoked_at）；user_id 传入时校验归属，防止跨账户吊销。"""
        if not key_id:
            return False
        sql = "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
        params: list[object] = [_now_iso(), key_id]
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        db = await self._connect()
        try:
            cursor = await db.execute(sql, tuple(params))
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()

    async def verify_api_key(self, raw_key: str) -> LocalUserRecord:
        """校验统一 API 密钥；无效/被吊销 → INVALID，账户被禁用 → USER_SUSPENDED。"""
        token = (raw_key or "").strip()
        if not token:
            raise AuthError(AuthFailureReason.MISSING, "api key missing")
        db = await self._connect()
        try:
            cursor = await db.execute(
                """
                SELECT k.*, u.username AS u_username, u.role AS u_role, u.status AS u_status
                FROM api_keys k LEFT JOIN users u ON u.id = k.user_id
                WHERE k.key_hash = ? AND k.revoked_at IS NULL
                """,
                (hash_api_key(token),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise AuthError(AuthFailureReason.INVALID, "api key not found")
            user_status = row["u_status"]
            if user_status is None:
                # users 表没有这个账户：只可能是未开启多用户的单用户部署（引导
                # 账户只在多用户模式创建）。回到引导账户语义（owner/active）；
                # 多用户模式下 join 不到用户说明数据被外力改动，直接拒绝。
                from novelvideo.shared.runtime_env import ce_multi_user

                if ce_multi_user():
                    raise AuthError(AuthFailureReason.INVALID, "api key owner missing")
                username = str(row["username"] or "") or local_username()
                role = ROLE_OWNER
            else:
                if str(user_status) != STATUS_ACTIVE:
                    raise AuthError(AuthFailureReason.USER_SUSPENDED, "user suspended")
                username = str(row["u_username"])
                role = str(row["u_role"])
            # last_used_at 只作展示，不必每次请求都写库：60 秒内不重复更新。
            stale = True
            last = str(row["last_used_at"] or "")
            if last:
                try:
                    age = time.time() - datetime.fromisoformat(last).timestamp()
                    stale = age > API_KEY_LAST_USED_THROTTLE_SECONDS
                except ValueError:
                    stale = True
            if stale:
                await db.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (_now_iso(), row["id"]),
                )
                await db.commit()
            user_id = str(row["user_id"])
        finally:
            await db.close()
        return LocalUserRecord(
            id=user_id, username=username, role=role, status=STATUS_ACTIVE
        )


# --------------------------------------------------------------------- 单例


_local_user_store: LocalUserStore | None = None


def get_local_user_store() -> LocalUserStore:
    global _local_user_store
    if _local_user_store is None:
        _local_user_store = LocalUserStore()
    return _local_user_store


def reset_local_user_store_for_tests() -> None:
    """测试钩子：丢弃缓存实例（配合临时 STATE_DIR 使用）。"""
    global _local_user_store
    _local_user_store = None
