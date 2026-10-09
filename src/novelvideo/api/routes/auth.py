"""认证端点：登录 / 登出 / 当前用户信息 / 首次初始化。"""

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from novelvideo.api.auth import (
    AUTH_COOKIE_NAME,
    get_api_user,
    resolve_auth_cookie_from_request,
)
from novelvideo.ports import get_auth_port
from novelvideo.ports.auth_contract import AuthError
from novelvideo.shared import runtime_env
from novelvideo.shared.runtime_env import cookie_secure as runtime_cookie_secure

router = APIRouter()
logger = logging.getLogger("novelvideo.api.auth")

_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _cookie_secure() -> bool:
    return runtime_cookie_secure()


def _set_auth_cookie(response: Response, cookie_value: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    # Mirror the cookie attributes used on set so CDN edges and Safari ITP
    # match it reliably. secure is echoed for the same reason.
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=_cookie_secure(),
    )


# ---------------------------------------------------------------------------
# [aipdd fork delta] CE 最小多用户：登录/注册端点（仅 ST_CE_MULTI_USER 开启时有意义）
# ---------------------------------------------------------------------------


class _CredentialRequest(BaseModel):
    username: str = ""
    password: str = ""


async def _bootstrap_if_env_password(store) -> None:
    """[aipdd fork delta] 只有显式配了 ST_LOCAL_PASSWORD 才自动引导账户。

    原来 login 无条件 bootstrap：没配这个环境变量时会用一个随机口令建账户，
    而口令只出现在一次 WARNING 日志里，用户根本登不进去，“首次初始化”也无从谈起。
    现在这条路收紧，剩下的空库场景交给 POST /auth/setup（用户自己定用户名密码）。
    已部署实例（库里已有账户）行为零变化：bootstrap 本身幂等，这里不走也不影响。
    """
    if os.environ.get("ST_LOCAL_PASSWORD", "").strip():
        await store.bootstrap_local_account()


_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW_SECONDS = 60
_login_failures: dict[tuple[str, str], list[float]] = {}


def _login_fail_key(request: Request, username: str) -> tuple[str, str]:
    client_ip = request.client.host if request.client else "unknown"
    return (client_ip, username.strip().lower())


def _too_many_login_failures(key: tuple[str, str]) -> bool:
    now = time.monotonic()
    fails = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_FAIL_WINDOW_SECONDS]
    if fails:
        _login_failures[key] = fails
    else:
        _login_failures.pop(key, None)
    return len(fails) >= _LOGIN_FAIL_LIMIT


def _record_login_failure(key: tuple[str, str]) -> None:
    _login_failures.setdefault(key, []).append(time.monotonic())
    if len(_login_failures) > 10_000:  # 防止伪造 IP 洪流撑爆内存
        _login_failures.clear()


@router.post("/auth/login")
async def login(request: Request, payload: _CredentialRequest):
    """Verify username/password and mint the HttpOnly session cookie."""
    if not runtime_env.ce_multi_user():
        raise HTTPException(status_code=400, detail="multi-user mode is disabled")
    from novelvideo.ports.local.users import get_local_user_store

    store = get_local_user_store()
    await _bootstrap_if_env_password(store)

    key = _login_fail_key(request, payload.username)
    if _too_many_login_failures(key):
        raise HTTPException(
            status_code=429, detail="too many login attempts, retry later"
        )
    try:
        record = await store.login(payload.username, payload.password)
    except AuthError:
        _record_login_failure(key)
        # 不区分"用户不存在/密码错误"，避免用户名枚举
        raise HTTPException(status_code=401, detail="invalid username or password")
    _login_failures.pop(key, None)
    token = await store.create_session(record.id)
    response = JSONResponse(
        {
            "ok": True,
            "data": {
                "username": record.username,
                "role": record.role,
                "credential_kind": "browser_session",
            },
        }
    )
    _set_auth_cookie(response, token)
    return response


@router.post("/auth/register")
async def register(payload: _CredentialRequest):
    """Self-service signup, disabled unless ST_CE_ALLOW_SELF_REGISTER is on."""
    if not runtime_env.ce_multi_user():
        raise HTTPException(status_code=400, detail="multi-user mode is disabled")
    if not runtime_env.ce_allow_self_register():
        raise HTTPException(status_code=403, detail="self-registration is disabled")
    from novelvideo.ports.local.users import get_local_user_store

    store = get_local_user_store()
    try:
        record = await store.create_user(payload.username, payload.password)
    except AuthError as exc:
        status = 409 if "exists" in (exc.detail or "") else 400
        raise HTTPException(status_code=status, detail=exc.detail or "register failed")
    return JSONResponse({"ok": True, "data": {"username": record.username, "role": record.role}})


# ---------------------------------------------------------------------------
# [aipdd fork delta] 首次初始化：空库时第一个设置用户名/口令的账户就是超管。
#
# 为什么需要这个：旧行为把“首个账户”锁死在环境变量里（ST_LOCAL_USERNAME /
# ST_LOCAL_PASSWORD），没配就只能去容器里翻日志。现在登录页先查 setup-status，
# 空库时直接给初始化表单。“先到先当超管”是该模式的固有代价，适合内网/受控
# 部署；一旦初始化完成本端点永久关闭，后续加人只走 /api/v1/admin/users。
# ---------------------------------------------------------------------------


class _SetupRequest(BaseModel):
    username: str = ""
    password: str = ""


# 引导账户 id 固定 'local'，两个并发初始化会主键碰撞；上一把锁把“只能初始化
# 一次”做成真串行，而不是靠碰运气报 500。
_setup_lock = asyncio.Lock()


@router.get("/auth/setup-status")
async def setup_status():
    """登录页进场探测：库里没有任何账户 → 需要初始化。不需要登录态。

    一并回 ``multi_user``：前端设置弹窗靠它决定“用户管理”页要不要出现，
    不然单用户模式下点进去只能看到一个加载失败。
    """
    multi_user = runtime_env.ce_multi_user()
    if not multi_user:
        # 单用户模式鉴权全走旁路，没有初始化概念
        return JSONResponse(
            {"ok": True, "data": {"needs_setup": False, "multi_user": False}}
        )
    from novelvideo.ports.local.users import get_local_user_store

    store = get_local_user_store()
    # 配了固定口令的部署保持原行为：这里先把引导账户建好，前端就不会误弹初始化表单。
    await _bootstrap_if_env_password(store)
    needs_setup = await store.count_users() == 0
    return JSONResponse(
        {
            "ok": True,
            "data": {"needs_setup": needs_setup, "multi_user": True},
        }
    )


@router.post("/auth/setup")
async def setup(request: Request, payload: _SetupRequest):
    """创建首个超管并直接下发会话 cookie。仅空库可用，第二次调用就拒。"""
    if not runtime_env.ce_multi_user():
        raise HTTPException(status_code=400, detail="multi-user mode is disabled")
    from novelvideo.ports.local.users import (
        ROLE_OWNER,
        get_local_user_store,
        normalize_username,
        validate_privileged_password,
    )

    store = get_local_user_store()
    try:
        username = normalize_username(payload.username)
        validate_privileged_password(payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail or "invalid input")
    async with _setup_lock:
        if await store.count_users() > 0:
            raise HTTPException(
                status_code=409, detail="system has already been initialised"
            )
        try:
            # user_id 固定 'local'：与 bootstrap 保持同一约定，既有数据零迁移
            record = await store.create_user(
                username, payload.password, role=ROLE_OWNER, user_id="local"
            )
        except AuthError as exc:
            detail = exc.detail or "setup failed"
            raise HTTPException(
                status_code=409 if "exists" in detail else 400, detail=detail
            )
        logger.warning(
            "[aipdd multi-user] 系统已初始化，超管账户：%s（后续加人走「设置 → 用户管理」）",
            username,
        )
        token = await store.create_session(record.id)
    response = JSONResponse(
        {
            "ok": True,
            "data": {
                "username": record.username,
                "role": record.role,
                "credential_kind": "browser_session",
            },
        }
    )
    _set_auth_cookie(response, token)
    return response


@router.post("/auth/logout")
async def logout(request: Request):
    """Best-effort revoke the session and always clear the browser cookie."""
    cookie_value = resolve_auth_cookie_from_request(request)
    if cookie_value:
        try:
            await get_auth_port().revoke_session(cookie_value)
        except Exception:
            # Logout is a recovery endpoint. An expired/invalid cookie or a
            # temporarily unavailable auth backend must not prevent the
            # browser from deleting its local credential.
            logger.warning("Failed to revoke session during logout", exc_info=True)

    response = JSONResponse({"ok": True})
    _clear_auth_cookie(response)
    return response


@router.get("/auth/me")
async def me(user: dict = Depends(get_api_user)):
    return JSONResponse(
        {
            "ok": True,
            "data": {
                "username": user["username"],
                "role": user["role"],
                "credential_kind": user.get("credential_kind") or "user",
                "current_scope_kind": user.get("current_scope_kind"),
                "current_project_id": user.get("current_project_id"),
                "scopes": user.get("scopes"),
            },
        }
    )


class _ChangePasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str = ""


@router.post("/auth/change-password")
async def change_password(
    payload: _ChangePasswordRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 自助修改当前账户的登录口令。

    口令哈希存 auth.db（改后 DB 权威、无需回写环境变量）；保留当前会话，
    不强制登出。仅多用户 CE 浏览器会话可用；agent 会话 / API Key 无登录口令可改。
    """
    if not runtime_env.ce_multi_user():
        raise HTTPException(status_code=400, detail="multi-user mode is disabled")
    if str(user.get("credential_kind") or "").strip() in {"agent_session", "api_key"}:
        raise HTTPException(
            status_code=400, detail="只能为浏览器登录会话修改口令"
        )
    from novelvideo.ports.local.users import (
        ROLE_OWNER,
        get_local_user_store,
        validate_privileged_password,
    )

    store = get_local_user_store()
    user_id = str(user.get("id") or user.get("user_id") or "").strip()
    username = str(user.get("username") or "").strip()
    if not user_id or not username:
        raise HTTPException(status_code=400, detail="无法解析当前账户")
    # 旧口令校验：走 store.login 复用同一校验路径（不区分用户不存在/密码错）。
    try:
        await store.login(username, payload.old_password)
    except AuthError:
        raise HTTPException(status_code=401, detail="当前密码错误")
    # 新口令强度：owner 走严格门槛，其余由 set_password 内置基础校验兜底。
    if str(user.get("role") or "").strip() == ROLE_OWNER:
        try:
            validate_privileged_password(payload.new_password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=exc.detail or "密码太弱")
    try:
        await store.set_password(user_id, payload.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail or "修改失败")
    return JSONResponse({"ok": True})
