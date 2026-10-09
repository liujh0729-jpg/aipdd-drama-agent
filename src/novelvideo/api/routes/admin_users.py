# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] CE 超管的账户管理端点。

上游 CE 只有 login / register / logout / me 四个认证端点，没有任何"管理员管人"的
能力：多用户开关打开后，除了改环境变量开自助注册（而注册出来的账户原来还是
owner），运维没有第二条路加人。这组端点补的就是这一段：建用户、重置口令、
禁用/启用、改角色。

边界（有意划小）：
- 全部要求浏览器会话里的 owner（``require_owner``），agent token 一律拒。
- 只做账户管理，不做项目共享：项目仍按 owner_id 完全隔离，管理员也只看自己的项目。
- 组织授权（ORG_* 那套）属于 EE，不在 CE 里实现。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from novelvideo.api.auth import require_owner
from novelvideo.ports.auth_contract import AuthError
from novelvideo.ports.local.users import (
    ROLE_MEMBER,
    ROLE_OWNER,
    STATUS_ACTIVE,
    VALID_ROLES,
    VALID_STATUSES,
    get_local_user_store,
    normalize_username,
    validate_privileged_password,
)
from novelvideo.shared import runtime_env

router = APIRouter()
logger = logging.getLogger("novelvideo.api.admin.users")


def _ensure_multi_user() -> None:
    """单用户模式下账户没有意义（鉴权全走旁路），直接明确拒绝而不是给个空列表。"""
    if not runtime_env.ce_multi_user():
        raise HTTPException(
            status_code=400,
            detail="multi-user mode is disabled (set ST_CE_MULTI_USER=true)",
        )


def _validate_username(username: str) -> str:
    # 字符集约束存在 store 里（create_user 自己也会校），这里只负责把 AuthError
    # 翻译成 HTTP 语义，避免两处规则漂移。
    try:
        return normalize_username(username)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail or "invalid username")


class _AdminUserCreateRequest(BaseModel):
    username: str = ""
    password: str = ""
    role: str = Field(default=ROLE_MEMBER, description=f"one of {VALID_ROLES}")


class _AdminUserPatchRequest(BaseModel):
    role: str | None = None
    status: str | None = None


class _AdminPasswordResetRequest(BaseModel):
    password: str = ""


def _serialize(record, *, is_self: bool) -> dict:
    return {
        "id": record.id,
        "username": record.username,
        "role": record.role,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "is_self": is_self,
    }


def _caller_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("id") or "").strip()


@router.get("/admin/users")
async def list_users(user: dict = Depends(require_owner)):
    """列出全部账户（绝不返回 pass_hash / salt）。"""
    _ensure_multi_user()
    store = get_local_user_store()
    caller = _caller_id(user)
    records = await store.list_users()
    return {
        "ok": True,
        "data": [
            _serialize(record, is_self=record.id == caller) for record in records
        ],
    }


@router.post("/admin/users")
async def create_user(
    payload: _AdminUserCreateRequest,
    user: dict = Depends(require_owner),
):
    _ensure_multi_user()
    username = _validate_username(payload.username)
    role = (payload.role or ROLE_MEMBER).strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {role}")
    if role == ROLE_OWNER:
        # 管理员能造出另一个超管，所以超管口令走严格门槛
        try:
            validate_privileged_password(payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=exc.detail or "weak password")
    store = get_local_user_store()
    try:
        record = await store.create_user(username, payload.password, role=role)
    except AuthError as exc:
        detail = exc.detail or "create failed"
        raise HTTPException(
            status_code=409 if "exists" in detail else 400, detail=detail
        )
    logger.info(
        "[aipdd multi-user] 管理员 %s 创建账户 %s (role=%s)",
        user.get("username"),
        username,
        role,
    )
    return {"ok": True, "data": _serialize(record, is_self=False)}


@router.patch("/admin/users/{user_id}")
async def update_user(
    user_id: str,
    payload: _AdminUserPatchRequest,
    user: dict = Depends(require_owner),
):
    """改角色 / 启用禁用。角色改完即刻生效（会话每次请求都回库读 role），无需踢线；
    禁用则立刻吊销该账户全部会话。
    """
    _ensure_multi_user()
    if payload.role is None and payload.status is None:
        raise HTTPException(status_code=400, detail="role or status is required")
    if payload.role is not None and payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {payload.role}")
    if payload.status is not None and payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status: {payload.status}")
    if user_id == _caller_id(user):
        # 把自己降级/禁用是把自己锁在门外，且没有任何恢复入口（没有第二个管理员）
        raise HTTPException(
            status_code=400, detail="cannot change role or status of your own account"
        )

    store = get_local_user_store()
    try:
        # 一次原子写：role/status 归入单条 UPDATE，消除旧两步写的部分变更窗口。
        record = await store.update_admin_fields(
            user_id, role=payload.role, status=payload.status
        )
    except AuthError as exc:
        detail = exc.detail or "update failed"
        raise HTTPException(
            status_code=404 if "not found" in detail else 400, detail=detail
        )

    if record is None:  # defensive: guard against -O stripping an assert
        raise HTTPException(status_code=500, detail="update returned no record")
    if record.status != STATUS_ACTIVE:
        revoked = await store.revoke_all_sessions(record.id)
        logger.info(
            "[aipdd multi-user] 账户 %s 已禁用，吊销会话 %s 个", record.username, revoked
        )
    return {"ok": True, "data": _serialize(record, is_self=False)}


@router.post("/admin/users/{user_id}/password")
async def reset_password(
    user_id: str,
    payload: _AdminPasswordResetRequest,
    user: dict = Depends(require_owner),
):
    """重置口令并吊销该账户全部会话（旧 cookie 立刻失效）。"""
    _ensure_multi_user()
    store = get_local_user_store()
    caller = _caller_id(user)
    target = await store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=400, detail="cannot reset the password of a disabled account"
        )
    try:
        validate_privileged_password(payload.password)
        await store.set_password(user_id, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail or "reset failed")
    revoked = await store.revoke_all_sessions(user_id)
    logger.info(
        "[aipdd multi-user] 管理员 %s 重置了 %s 的口令，吊销会话 %s 个",
        user.get("username"),
        target.username,
        revoked,
    )
    return {
        "ok": True,
        "data": {
            "revoked_sessions": revoked,
            "is_self": user_id == caller,
        },
    }
