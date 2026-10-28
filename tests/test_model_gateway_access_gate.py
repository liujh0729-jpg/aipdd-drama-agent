"""网关管理面权限闸门 `require_model_gateway_access` 的边界回归。

网关配置含渠道上游密钥 + LocalAPI 管理员令牌、决定全平台模型流量去向，属管理员
配置面。曾整组路由仅 `get_api_user`（任意登录态），使机器凭证（agent 会话 /
统一 API 密钥 st-…）与非 owner 成员都能读写网关——现收窄为：机器凭证一律拒；
写操作再要 owner；只读留给浏览器会话（成员 UI 需 /model-plaza、/config 列模型）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import model_gateway
from novelvideo.api.routes.model_gateway._common import require_model_gateway_access


def _user(kind: str | None, role: str = "owner") -> dict:
    u = {"id": "u", "username": "u", "role": role}
    if kind:
        u["credential_kind"] = kind
    return u


def _req(method: str) -> SimpleNamespace:
    return SimpleNamespace(method=method)


@pytest.mark.asyncio
async def test_machine_credentials_are_rejected_on_read() -> None:
    # 只读 GET 也拒机器凭证：拓扑 / 配置对 agent、st- 密钥一律不可见。
    for kind in ("agent_session", "api_key"):
        with pytest.raises(HTTPException) as exc:
            await require_model_gateway_access(_req("GET"), _user(kind))
        assert exc.value.status_code == 403
        assert exc.value.detail == "browser session required"


@pytest.mark.asyncio
async def test_machine_credentials_rejected_before_owner_check_on_write() -> None:
    # 机器凭证即便是 owner 角色（LocalAuthSession 给 agent token 写 role=owner）
    # 也必须在第一道就被拒，绝不因 role 命中而放行。
    for kind in ("agent_session", "api_key"):
        with pytest.raises(HTTPException) as exc:
            await require_model_gateway_access(
                _req("POST"), _user(kind, role="owner")
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "browser session required"


@pytest.mark.asyncio
async def test_member_browser_can_read_but_not_write() -> None:
    # 非 owner 成员浏览器会话：GET 放行（UI 列模型），POST 被 owner 闸门拦下。
    member = _user("browser_session", role="member")
    assert await require_model_gateway_access(_req("GET"), member) is member

    with pytest.raises(HTTPException) as exc:
        await require_model_gateway_access(_req("POST"), member)
    assert exc.value.status_code == 403
    assert exc.value.detail == "administrator privilege required"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_owner_browser_can_write(method: str) -> None:
    owner = _user("browser_session", role="owner")
    assert await require_model_gateway_access(_req(method), owner) is owner


@pytest.mark.asyncio
async def test_single_user_owner_read_no_credential_kind_passes() -> None:
    # 单用户旁路：无 credential_kind、role=owner 的浏览器会话读写皆放行。
    local = _user(None, role="owner")
    assert await require_model_gateway_access(_req("GET"), local) is local
    assert await require_model_gateway_access(_req("POST"), local) is local


def test_router_wires_the_access_gate() -> None:
    # 闸门确实挂在整组路由的 router 级依赖上，而非漏挂。
    calls = [getattr(d, "dependency", None) for d in model_gateway.router.dependencies]
    assert require_model_gateway_access in calls
