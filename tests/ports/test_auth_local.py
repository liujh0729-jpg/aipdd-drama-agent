import time

import pytest

from novelvideo.ports.auth_contract import (
    AgentSessionToken,
    AuthenticatedUser,
    AuthError,
    AuthFailureReason,
)
from novelvideo.ports.local.auth import FileAuthPort, LocalAuthSession

BROWSER_LEGACY_KEYS = {"id", "user_id", "username", "role"}
AGENT_LEGACY_KEYS = BROWSER_LEGACY_KEYS | {
    "credential_kind",
    "agent_session_id",
    "agent_kind",
    "worker_id",
    "scopes",
    "current_scope_kind",
    "current_project_id",
    "parent_session_id",
}


@pytest.mark.asyncio
async def test_file_auth_port_returns_browser_legacy_dict(monkeypatch) -> None:
    monkeypatch.setenv("ST_LOCAL_USERNAME", "alice")
    user = await FileAuthPort().verify_session("any-cookie")

    assert user == AuthenticatedUser(
        id="local",
        username="alice",
        role="owner",
    ).to_legacy_dict()


@pytest.mark.asyncio
async def test_file_auth_port_accepts_missing_cookie_and_revoke_is_noop(monkeypatch) -> None:
    monkeypatch.setenv("ST_LOCAL_USERNAME", "alice")
    port = FileAuthPort()

    user = await port.verify_session(None)
    await port.revoke_session("any-cookie")

    assert user == AuthenticatedUser(
        id="local",
        username="alice",
        role="owner",
    ).to_legacy_dict()


@pytest.mark.asyncio
async def test_file_auth_port_uses_authenticated_user_legacy_serializer(monkeypatch) -> None:
    monkeypatch.setenv("ST_LOCAL_USERNAME", "alice")

    def fake_to_legacy_dict(self):
        return {
            "id": self.id,
            "user_id": self.id,
            "username": self.username,
            "role": self.role,
            "serializer_marker": "dto",
        }

    monkeypatch.setattr(AuthenticatedUser, "to_legacy_dict", fake_to_legacy_dict)

    assert await FileAuthPort().verify_session("any-cookie") == {
        "id": "local",
        "user_id": "local",
        "username": "alice",
        "role": "owner",
        "serializer_marker": "dto",
    }


@pytest.mark.asyncio
async def test_local_auth_session_create_verify_update_revoke_lifecycle() -> None:
    session = LocalAuthSession()

    token = await session.create_agent_session(
        username="alice",
        scopes=["projects:read"],
        worker_id="worker-1",
        current_scope_kind="home",
    )

    assert isinstance(token, AgentSessionToken)
    assert token.value.startswith("local-")
    assert token.user == "alice"
    assert token.scopes == ("projects:read",)
    assert token.worker_id == "worker-1"
    data = await session.verify_agent_session(token.value)
    assert set(data) == AGENT_LEGACY_KEYS
    assert data["credential_kind"] == "agent_session"
    assert data["current_scope_kind"] == "home"
    assert data["current_project_id"] is None

    await session.update_agent_session_scope(
        token.value,
        scope_kind="project",
        project_id="proj-1",
    )
    updated = await session.verify_agent_session(token.value)
    assert updated["current_scope_kind"] == "project"
    assert updated["current_project_id"] == "proj-1"

    await session.revoke_agent_session(token.value)
    with pytest.raises(AuthError) as exc:
        await session.verify_agent_session(token.value)
    assert exc.value.reason == AuthFailureReason.INVALID


@pytest.mark.asyncio
async def test_local_auth_session_unknown_token_raises_auth_error() -> None:
    session = LocalAuthSession()

    with pytest.raises(AuthError) as exc:
        await session.verify_agent_session("missing-token")

    assert exc.value.reason == AuthFailureReason.INVALID


@pytest.mark.asyncio
async def test_local_auth_session_update_revoked_token_raises_auth_error() -> None:
    session = LocalAuthSession()
    token = await session.create_agent_session(
        username="alice",
        scopes=["projects:read"],
        worker_id="worker-1",
    )
    await session.revoke_agent_session(token.value)

    with pytest.raises(AuthError) as exc:
        await session.update_agent_session_scope(
            token.value,
            scope_kind="project",
            project_id="proj-1",
        )

    assert exc.value.reason == AuthFailureReason.INVALID


@pytest.mark.asyncio
async def test_local_auth_session_expired_token_still_verifies_until_revoked() -> None:
    session = LocalAuthSession()
    token = await session.create_agent_session(
        username="alice",
        scopes=["projects:read"],
        ttl_seconds=-1,
        worker_id="worker-1",
    )

    assert token.exp < time.time()
    data = await session.verify_agent_session(token.value)
    assert data["username"] == "alice"
    assert data["credential_kind"] == "agent_session"

    await session.revoke_agent_session(token.value)
    with pytest.raises(AuthError):
        await session.verify_agent_session(token.value)
