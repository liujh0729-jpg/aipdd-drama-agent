from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _reset_modules():
    import novelvideo.ports as ports
    import novelvideo.ports.registry as registry

    registry._PORTS.clear()
    registry._BOOTSTRAPPED = False
    api_auth = importlib.import_module("novelvideo.api.auth")
    return registry, ports, api_auth


def _request(*, cookie: str | None = None, authorization: str | None = None) -> Request:
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"st_session={cookie}".encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/me",
            "headers": headers,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )


@pytest.mark.asyncio
async def test_browser_path_without_registered_auth_port_returns_pinned_503() -> None:
    _registry, _ports, api_auth = _reset_modules()

    with pytest.raises(HTTPException) as exc:
        await api_auth.get_api_user(_request())

    assert exc.value.status_code == 503
    assert exc.value.detail == "auth backend not initialised"


@pytest.mark.asyncio
async def test_cookie_path_without_registered_auth_port_returns_pinned_503() -> None:
    _registry, _ports, api_auth = _reset_modules()

    with pytest.raises(HTTPException) as exc:
        await api_auth.get_api_user(_request(cookie="bad-cookie"))

    assert exc.value.status_code == 503
    assert exc.value.detail == "auth backend not initialised"


@pytest.mark.asyncio
async def test_bearer_path_without_registered_auth_session_port_returns_pinned_401() -> None:
    _registry, _ports, api_auth = _reset_modules()

    with pytest.raises(HTTPException) as exc:
        await api_auth.get_api_user(_request(authorization="Bearer bad-token"))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Agent sessions require control plane"


@pytest.mark.asyncio
async def test_verify_credential_for_request_keeps_ce_cookie_less_stream_alive(monkeypatch) -> None:
    registry, _ports, api_auth = _reset_modules()
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("ST_LOCAL_USERNAME", "alice")
    registry.ensure_bootstrap()

    user = await api_auth.verify_credential_for_request(_request())

    assert user == {"id": "local", "user_id": "local", "username": "alice", "role": "owner"}


@pytest.mark.asyncio
async def test_verify_credential_for_request_returns_none_for_ee_missing_cookie(
    monkeypatch,
) -> None:
    _registry, _ports, api_auth = _reset_modules()

    async def fail_browser_session(_raw_cookie):
        raise HTTPException(status_code=401, detail="Missing session or agent token")

    monkeypatch.setattr(api_auth, "_verify_browser_session", fail_browser_session)

    assert await api_auth.verify_credential_for_request(_request()) is None


@pytest.mark.asyncio
async def test_unexpected_auth_backend_error_is_logged_then_fails_closed(
    monkeypatch, caplog
) -> None:
    """A2: a non-AuthError crash must log the real cause, not vanish into a 503."""
    import logging

    _registry, _ports, api_auth = _reset_modules()

    class _BoomPort:
        async def verify_session(self, _raw_cookie):
            raise RuntimeError("auth db unreachable")

    monkeypatch.setattr(api_auth, "get_auth_port", lambda: _BoomPort())

    with caplog.at_level(logging.ERROR, logger="novelvideo.api.auth"):
        with pytest.raises(HTTPException) as exc:
            await api_auth._verify_browser_session("cookie")

    assert exc.value.status_code == 503
    assert exc.value.detail == "Authentication service unavailable"
    # the client-facing body stays generic, but the traceback must be recorded
    assert "browser session verification failed unexpectedly" in caplog.text
    assert any(
        record.exc_info and "auth db unreachable" in str(record.exc_info)
        for record in caplog.records
    )


def test_require_scope_denial_does_not_echo_token_scopes() -> None:
    """A3: the 403 must name only the missing scope, never the full grant list."""
    import asyncio

    _registry, _ports, api_auth = _reset_modules()

    check = api_auth.require_scope("project:write")
    user = {"username": "agent", "scopes": ["project:read", "media:read"]}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(check(user=user))

    assert exc.value.status_code == 403
    assert "project:write" in exc.value.detail
    # the other grants the token holds must not leak into the response body
    assert "project:read" not in exc.value.detail
    assert "media:read" not in exc.value.detail
    assert "token has" not in exc.value.detail
