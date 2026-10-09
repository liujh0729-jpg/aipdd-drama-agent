"""[aipdd fork delta] 统一 API 密钥（MCP + 模型广场）的回归测试。

覆盖四块：
1. ``LocalUserStore`` 的 api_keys 表生命周期（生成 / 列表 / 吊销 / 校验 / 禁用收敛）；
2. ``get_api_user`` 接受 ``st-`` 密钥（credential_kind=api_key），且机器凭证
   不能反过来管理凭证（``require_browser_session`` / ``require_owner``）；
3. ``/api/v1/api-keys`` 直调路由函数：明文只出现一次、序列化不泄露散列；
4. ``/api/v1/openai/*`` 转发：未配置 503、上游故障 502、URL/令牌/body/响应透传
   （httpx.MockTransport，不发真网络）。

沿用 tests/test_api_admin_users.py 的直调路由函数写法，不起完整 app。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason

PASSWORD = "sup3r-secret"
OWNER = {"id": "local", "user_id": "local", "username": "local", "role": "owner"}


def _set_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))


@pytest.fixture()
def multi_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ST_CE_MULTI_USER", "true")
    monkeypatch.setenv("ST_COOKIE_SECURE", "false")
    monkeypatch.setenv("ST_LOCAL_PASSWORD", PASSWORD)
    monkeypatch.delenv("ST_LOCAL_USERNAME", raising=False)
    _set_state_root(monkeypatch, tmp_path)

    from novelvideo.ports.local import users as users_mod

    users_mod.reset_local_user_store_for_tests()
    yield users_mod
    users_mod.reset_local_user_store_for_tests()


@pytest.fixture()
def single_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("ST_CE_MULTI_USER", raising=False)
    _set_state_root(monkeypatch, tmp_path)

    from novelvideo.ports.local import users as users_mod

    users_mod.reset_local_user_store_for_tests()
    yield users_mod
    users_mod.reset_local_user_store_for_tests()


def _request(
    headers: dict[str, str] | None = None,
    *,
    path: str = "/api/v1/openai/models",
    method: str = "GET",
    body: bytes = b"",
):
    from starlette.requests import Request

    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    if body:
        raw_headers.append((b"content-length", str(len(body)).encode("latin-1")))
    pending = [body] if body else []

    async def receive() -> dict:
        chunk = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": chunk, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode("latin-1"),
            "headers": raw_headers,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )


# ------------------------------------------------------------- store 生命周期


@pytest.mark.asyncio
async def test_api_key_lifecycle_hashes_at_rest_and_revokes(multi_user) -> None:
    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    record, raw = await store.create_api_key("local", "MCP")
    assert raw.startswith("st-")
    assert record.key_prefix == raw[:12]
    assert record.name == "MCP"
    assert record.created_at

    user = await store.verify_api_key(raw)
    assert (user.id, user.username, user.role, user.status) == (
        "local",
        "local",
        "owner",
        "active",
    )

    # 明文绝不落库：库里只有 sha256 与展示用前缀；last_used_at 已被 verify 写入。
    listed = await store.list_api_keys("local")
    assert [row.id for row in listed] == [record.id]
    assert listed[0].last_used_at != ""
    blob = (Path(store._db_path())).read_bytes()
    assert raw.encode() not in blob
    assert hashlib.sha256(raw.encode()).hexdigest().encode() in blob

    # 吊销即失效，重复吊销 / 越权吊销都返回 False。
    assert await store.revoke_api_key(record.id, user_id="local") is True
    with pytest.raises(AuthError) as exc:
        await store.verify_api_key(raw)
    assert exc.value.reason == AuthFailureReason.INVALID
    assert await store.revoke_api_key(record.id, user_id="local") is False
    other, _ = await store.create_api_key("local", "other")
    assert await store.revoke_api_key(other.id, user_id="u-else") is False


@pytest.mark.asyncio
async def test_api_key_respects_account_status_and_member_role(multi_user) -> None:
    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")
    _record, raw = await store.create_api_key(alice.id, "plaza")

    user = await store.verify_api_key(raw)
    assert (user.id, user.username, user.role) == (alice.id, "alice", "member")

    await store.set_user_status(alice.id, "suspended")
    with pytest.raises(AuthError) as exc:
        await store.verify_api_key(raw)
    assert exc.value.reason == AuthFailureReason.USER_SUSPENDED


@pytest.mark.asyncio
async def test_verify_rejects_unknown_and_missing_keys(multi_user) -> None:
    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    with pytest.raises(AuthError) as exc:
        await store.verify_api_key("st-not-a-real-key")
    assert exc.value.reason == AuthFailureReason.INVALID

    with pytest.raises(AuthError) as exc:
        await store.verify_api_key("")
    assert exc.value.reason == AuthFailureReason.MISSING


@pytest.mark.asyncio
async def test_single_user_mode_key_resolves_bootstrap_owner(single_user) -> None:
    """未开启多用户时 users 表为空，密钥仍要能落在引导账户语义（local/owner）。"""
    store = single_user.get_local_user_store()
    _record, raw = await store.create_api_key("local", "solo")

    user = await store.verify_api_key(raw)
    assert (user.id, user.username, user.role, user.status) == (
        "local",
        "local",
        "owner",
        "active",
    )


# ------------------------------------------------------------------ 凭证入口


@pytest.mark.asyncio
async def test_get_api_user_accepts_unified_api_key(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.auth import get_api_user

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    _record, raw = await store.create_api_key("local", "mcp")

    user = await get_api_user(_request({"authorization": f"Bearer {raw}"}))
    assert user["credential_kind"] == "api_key"
    assert user["user_id"] == "local"
    assert user["role"] == "owner"
    # 密钥不携带 agent 作用域：普通项目接口按“登录用户”语义放行（项目仍按 owner_id 隔离）。
    assert "scopes" not in user

    with pytest.raises(HTTPException) as exc:
        await get_api_user(_request({"authorization": "Bearer st-bogus"}))
    assert exc.value.status_code == 401

    # 无凭证走浏览器会话路径：完整 app 由 startup 注册 ports，这里手动补齐。
    from novelvideo.ports.registry import ensure_bootstrap

    ensure_bootstrap()
    with pytest.raises(HTTPException) as exc:
        await get_api_user(_request({}))
    assert exc.value.status_code == 401


def test_machine_credentials_cannot_manage_credentials() -> None:
    from fastapi import HTTPException

    from novelvideo.api.auth import require_browser_session, require_owner

    api_key_user = {"role": "owner", "username": "local", "credential_kind": "api_key"}
    agent_user = {"role": "owner", "username": "local", "credential_kind": "agent_session"}

    for user in (api_key_user, agent_user):
        with pytest.raises(HTTPException) as exc:
            require_browser_session(user)
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            require_owner(user)
        assert exc.value.status_code == 403

    assert require_browser_session({"role": "member", "username": "alice"})["role"] == "member"
    assert require_owner({"role": "owner", "username": "local"})["role"] == "owner"


# ------------------------------------------------------------- /api/v1/api-keys


@pytest.mark.asyncio
async def test_api_key_endpoints_create_list_revoke(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import api_keys

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    created = await api_keys.create_api_key(
        api_keys._ApiKeyCreateRequest(name="MCP"), OWNER
    )
    data = created["data"]
    assert data["key"].startswith("st-")
    assert data["key_prefix"] == data["key"][:12]
    assert data["revoked"] is False

    listed = await api_keys.list_api_keys(OWNER)
    assert listed["ok"] is True
    assert len(listed["data"]) == 1
    row = listed["data"][0]
    assert set(row) == {
        "id",
        "name",
        "key_prefix",
        "created_at",
        "last_used_at",
        "revoked_at",
        "revoked",
    }
    assert "key" not in row  # 明文只在生成响应里出现一次

    revoked = await api_keys.revoke_api_key(row["id"], OWNER)
    assert revoked["data"] == {"id": row["id"], "revoked": True}

    with pytest.raises(HTTPException) as exc:
        await api_keys.revoke_api_key(row["id"], OWNER)
    assert exc.value.status_code == 404

    # 归属校验：别的账户吊销不到我的密钥。
    member = {"id": "u-alice", "user_id": "u-alice", "username": "alice", "role": "member"}
    with pytest.raises(HTTPException) as exc:
        await api_keys.revoke_api_key(row["id"], member)
    assert exc.value.status_code == 404


def test_unified_routes_are_registered_under_api_v1() -> None:
    from novelvideo.api import api_router

    paths: set[str] = set()
    for included in api_router.routes:
        original = getattr(included, "original_router", None)
        if original is None:
            continue
        for route in original.routes:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
    assert {"/api-keys", "/api-keys/{key_id}", "/openai/{path:path}"} <= paths


# ---------------------------------------------------------------- 统一模型入口


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    from novelvideo.api.routes import openai_proxy

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return httpx.AsyncClient(*args, **kwargs)

    monkeypatch.setattr(
        openai_proxy,
        "httpx",
        SimpleNamespace(
            AsyncClient=factory, HTTPError=httpx.HTTPError, Timeout=httpx.Timeout
        ),
    )


@pytest.mark.asyncio
async def test_openai_relay_injects_gateway_token_and_passes_body(monkeypatch) -> None:
    from novelvideo.api.routes import openai_proxy

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["accept_encoding"] = request.headers.get("accept-encoding")
        captured["content_type"] = request.headers.get("content-type")
        captured["trace"] = request.headers.get("x-trace")
        captured["body"] = request.read()
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-upstream": "yes"},
            content=json.dumps({"model": "gpt-x", "ok": True}).encode(),
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        openai_proxy,
        "get_newapi_runtime_credentials",
        lambda: ("gw-secret", "http://newapi:3000/"),
    )

    request = _request(
        {
            "authorization": "Bearer st-anything",
            "content-type": "application/json",
            "x-trace": "abc",
        },
        path="/api/v1/openai/chat/completions",
        method="POST",
        body=b'{"model":"gpt-x"}',
    )
    response = await openai_proxy.relay_openai("chat/completions", request, OWNER)

    assert response.status_code == 200
    # base 自动补齐 /v1；调用方密钥被换掉，网关令牌由服务端注入。
    assert captured["url"] == "http://newapi:3000/v1/chat/completions"
    assert captured["auth"] == "Bearer gw-secret"
    assert captured["accept_encoding"] == "identity"
    assert captured["content_type"] == "application/json"
    assert captured["trace"] == "abc"
    assert captured["body"] == b'{"model":"gpt-x"}'

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert json.loads(body)["model"] == "gpt-x"
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-upstream"] == "yes"
    assert "content-length" not in response.headers  # 长度由本层重算


@pytest.mark.asyncio
async def test_openai_relay_reports_unconfigured_gateway(monkeypatch) -> None:
    from novelvideo.api.routes import openai_proxy

    monkeypatch.setattr(openai_proxy, "get_newapi_runtime_credentials", lambda: ("", ""))

    response = await openai_proxy.relay_openai("models", _request({}), OWNER)
    assert response.status_code == 503
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "gateway_not_configured"


@pytest.mark.asyncio
async def test_openai_relay_maps_upstream_failure_to_502(monkeypatch) -> None:
    from novelvideo.api.routes import openai_proxy

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        openai_proxy,
        "get_newapi_runtime_credentials",
        lambda: ("gw-secret", "http://newapi:3000"),
    )

    response = await openai_proxy.relay_openai("models", _request({}), OWNER)
    assert response.status_code == 502
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "gateway_unreachable"


@pytest.mark.asyncio
async def test_openai_relay_rejects_traversal_path(monkeypatch) -> None:
    """C6: a `..`/`.` subpath must be refused before any upstream forwarding."""
    from novelvideo.api.routes import openai_proxy

    def _boom(*_a, **_k):
        raise AssertionError("gateway credentials must not be read for a bad path")

    monkeypatch.setattr(openai_proxy, "get_newapi_runtime_credentials", _boom)

    for bad in ("../api/user", "chat/../../admin", ".", "models/./x"):
        response = await openai_proxy.relay_openai(bad, _request({}), OWNER)
        assert response.status_code == 404, bad
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"]["code"] == "invalid_relay_path", bad


@pytest.mark.asyncio
async def test_openai_relay_allows_dotted_legitimate_path(monkeypatch) -> None:
    # A task id or model name containing a dot (but not a bare . / .. segment)
    # must still relay normally.
    from novelvideo.api.routes import openai_proxy

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        openai_proxy,
        "get_newapi_runtime_credentials",
        lambda: ("gw-secret", "http://newapi:3000"),
    )

    response = await openai_proxy.relay_openai(
        "videos/some.task-42", _request({}), OWNER
    )
    assert response.status_code == 200
    assert captured["url"] == "http://newapi:3000/v1/videos/some.task-42"

# ------------------------------------------------------------ 统一模型入口 · 组织凭证归因（#1）


@pytest.mark.asyncio
async def test_openai_relay_uses_organization_credential_not_platform_key(monkeypatch) -> None:
    """#1：组织身份的透传必须用其自身网关凭据，不得由平台 ambient key 垫付。"""
    import novelvideo.api.egress_binding as egress_binding
    import novelvideo.model_gateway_runtime as mgr
    from novelvideo.api.routes import openai_proxy
    from novelvideo.egress_context import TrustedEgressContext
    from novelvideo.ports.authz import BillingPrincipal
    from novelvideo.ports.model_credentials import CredentialReference, RequestCredential

    org_ctx = TrustedEgressContext(
        envelope_id="req-1",
        project_id="__gateway_relay__",
        task_type="openai_relay",
        requester_user_id="local",
        root_task_id="root-1",
        admission_id="adm-1",
        admitted_at="2026-10-09T00:00:00+00:00",
        membership_id="m-1",
        authz_version=1,
        billing_principal=BillingPrincipal(kind="organization", id="org-1"),
        credential=CredentialReference(
            source="organization",
            credential_id="org-key",
            key_version=1,
            org_id="org-1",
        ),
    )

    async def _fake_build(**_kw):
        return org_ctx

    class _FakeCreds:
        async def resolve(self, admission):
            return RequestCredential(
                reference=admission.credential,
                api_key="org-secret",
                base_url="http://org-gw:8000",
            )

    monkeypatch.setattr(egress_binding, "build_request_egress_context", _fake_build)
    monkeypatch.setattr(mgr, "get_model_credentials", lambda: _FakeCreds())

    def _boom(*_a, **_k):
        raise AssertionError("org relay must not fall back to ambient platform key")

    monkeypatch.setattr(openai_proxy, "get_newapi_runtime_credentials", _boom)

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, handler)
    request = _request(
        {"authorization": "Bearer st-caller"},
        path="/api/v1/openai/chat/completions",
        method="POST",
        body=b'{"model":"m"}',
    )
    response = await openai_proxy.relay_openai("chat/completions", request, OWNER)
    assert response.status_code == 200
    assert captured["auth"] == "Bearer org-secret"
    assert captured["url"] == "http://org-gw:8000/v1/chat/completions"


@pytest.mark.asyncio
async def test_openai_relay_falls_back_to_platform_when_authz_port_absent(monkeypatch) -> None:
    """#1：裸单机 CE（无 authz 端口）→ PortNotRegistered 回落平台凭据，路径逐字节不变。"""
    import novelvideo.api.egress_binding as egress_binding
    from novelvideo.api.routes import openai_proxy
    from novelvideo.ports.registry import PortNotRegistered

    async def _raise(**_kw):
        raise PortNotRegistered("authz")

    monkeypatch.setattr(egress_binding, "build_request_egress_context", _raise)
    monkeypatch.setattr(
        openai_proxy,
        "get_newapi_runtime_credentials",
        lambda: ("gw-secret", "http://newapi:3000"),
    )

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, handler)
    response = await openai_proxy.relay_openai("models", _request({}), OWNER)
    assert response.status_code == 200
    assert captured["auth"] == "Bearer gw-secret"
    assert captured["url"] == "http://newapi:3000/v1/models"
