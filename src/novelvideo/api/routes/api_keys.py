# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 统一 API 密钥管理端点（设置 → API 密钥）。

一把 key 同时用于两处外部调用，避免用户在两处各配一份凭证：
1. 外部 MCP server：``DRAMACLAW_AGENT_TOKEN=st-…`` → ``Authorization: Bearer``；
2. 模型广场的 OpenAI 兼容入口：``POST /api/v1/openai/chat/completions``。

边界（有意划小）：
- 只允许交互式会话（``require_browser_session``）管理密钥。机器凭证一律拒：
  否则一把泄漏的 key 可以无限派生新 key，吊销永远止不住血。
- 每个账户只能看/建/吊销自己的密钥，不跨账户。
- 明文只在生成时返回一次；列表只给前缀、时间戳与用量时间。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from novelvideo.api.auth import require_browser_session
from novelvideo.ports.auth_contract import AuthError
from novelvideo.ports.local.users import get_local_user_store

router = APIRouter()
logger = logging.getLogger("novelvideo.api.api_keys")

MAX_ACTIVE_KEYS_PER_USER = 20


class _ApiKeyCreateRequest(BaseModel):
    name: str = ""


def _caller_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("id") or "").strip()


def _serialize(record) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "key_prefix": record.key_prefix,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "revoked_at": record.revoked_at,
        "revoked": bool(record.revoked_at),
    }


@router.get("/api-keys")
async def list_api_keys(user: dict = Depends(require_browser_session)):
    """列出当前账户的全部密钥（含已吊销，绝不返回明文与散列）。"""
    store = get_local_user_store()
    records = await store.list_api_keys(_caller_id(user))
    return {"ok": True, "data": [_serialize(record) for record in records]}


@router.post("/api-keys")
async def create_api_key(
    payload: _ApiKeyCreateRequest,
    user: dict = Depends(require_browser_session),
):
    """生成一把统一密钥；明文只在这个响应里出现一次。"""
    store = get_local_user_store()
    owner = _caller_id(user)
    if not owner:
        raise HTTPException(status_code=400, detail="account not resolvable")
    active = [
        record
        for record in await store.list_api_keys(owner)
        if not record.revoked_at
    ]
    if len(active) >= MAX_ACTIVE_KEYS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=(
                f"too many active api keys (limit {MAX_ACTIVE_KEYS_PER_USER}); "
                "revoke unused keys first"
            ),
        )
    try:
        record, raw = await store.create_api_key(owner, payload.name)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail or "create failed")
    logger.info(
        "[aipdd api-key] %s 生成了密钥 %s(%s)",
        user.get("username"),
        record.key_prefix,
        record.name,
    )
    return {"ok": True, "data": {**_serialize(record), "key": raw}}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    user: dict = Depends(require_browser_session),
):
    """吊销（软删除）一把密钥；已在用的调用立即失效。"""
    store = get_local_user_store()
    owner = _caller_id(user)
    revoked = await store.revoke_api_key(key_id, user_id=owner)
    if not revoked:
        raise HTTPException(status_code=404, detail="api key not found")
    logger.info("[aipdd api-key] %s 吊销了密钥 %s", user.get("username"), key_id)
    return {"ok": True, "data": {"id": key_id, "revoked": True}}