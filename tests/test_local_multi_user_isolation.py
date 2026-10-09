"""[aipdd fork delta] CE 最小可用多用户隔离的回归测试。

覆盖：ST_CE_MULTI_USER 开关语义、LocalUserStore（PBKDF2 口令 + 持久会话）、
FileAuthPort 真实 session 校验、项目按 owner 隔离、登录端点与限流。
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason
from novelvideo.ports.project import Principal, ProjectRecord

BOOTSTRAP_PASSWORD = "sup3r-secret"


def _set_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))


@pytest.fixture()
def multi_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ST_CE_MULTI_USER", "true")
    monkeypatch.setenv("ST_COOKIE_SECURE", "false")
    monkeypatch.setenv("ST_LOCAL_PASSWORD", BOOTSTRAP_PASSWORD)
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


def _project(owner_id: str, name: str = "demo") -> ProjectRecord:
    return ProjectRecord(
        id=f"proj-{owner_id}",
        owner_type="user",
        owner_id=owner_id,
        owner_username=owner_id,
        name=name,
        home_node_id="local",
        output_dir="/tmp/out",
        state_dir="/tmp/state",
        runtime_dir="/tmp/runtime",
        status="active",
    )


# --------------------------------------------------------------------- store


@pytest.mark.asyncio
async def test_bootstrap_creates_local_account_idempotently(multi_user) -> None:
    store = multi_user.get_local_user_store()
    record = await store.bootstrap_local_account()
    assert record is not None
    assert record.id == "local"  # 固定 id → 既有 projects.db / 目录零迁移
    assert record.username == "local"

    again = await store.bootstrap_local_account()
    assert again is not None and again.id == "local"
    assert await store.count_users() == 1


@pytest.mark.asyncio
async def test_login_password_verification_and_generic_error(multi_user) -> None:
    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    record = await store.login("local", BOOTSTRAP_PASSWORD)
    assert record.id == "local"

    with pytest.raises(AuthError) as exc:
        await store.login("local", "wrong-password")
    assert exc.value.reason == AuthFailureReason.INVALID

    # 用户不存在与密码错误返回同一形状，避免用户名枚举
    with pytest.raises(AuthError):
        await store.login("ghost-user", BOOTSTRAP_PASSWORD)


@pytest.mark.asyncio
async def test_create_user_rejects_short_password(multi_user) -> None:
    store = multi_user.get_local_user_store()
    with pytest.raises(AuthError):
        await store.create_user("alice", "123")
    record = await store.create_user("alice", "valid-pass-1")
    assert record.username == "alice" and record.id != "local"
    with pytest.raises(AuthError):
        await store.create_user("alice", "another-pass-2")


@pytest.mark.asyncio
async def test_session_lifecycle_persists_and_revokes(multi_user) -> None:
    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    token = await store.create_session("local")

    record = await store.get_session_user(token)
    assert record.id == "local"

    await store.revoke_session(token)
    with pytest.raises(AuthError) as exc:
        await store.get_session_user(token)
    assert exc.value.reason == AuthFailureReason.INVALID


# ------------------------------------------------------------------ auth port


@pytest.mark.asyncio
async def test_file_auth_port_requires_valid_session_when_multi_user(multi_user) -> None:
    from novelvideo.ports.local.auth import FileAuthPort

    port = FileAuthPort()
    with pytest.raises(AuthError) as exc:
        await port.verify_session(None)
    assert exc.value.reason == AuthFailureReason.MISSING

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    token = await store.create_session("local")

    user = await port.verify_session(token)
    assert user["id"] == "local"
    assert user["user_id"] == "local"
    assert user["username"] == "local"
    assert user["role"] == "owner"

    await port.revoke_session(token)
    with pytest.raises(AuthError):
        await port.verify_session(token)


@pytest.mark.asyncio
async def test_file_auth_port_single_user_bypass_unchanged(single_user) -> None:
    from novelvideo.ports.local.auth import FileAuthPort

    user = await FileAuthPort().verify_session("any-cookie")
    assert user["id"] == "local"
    assert user["username"] == "local"
    await FileAuthPort().revoke_session("any-cookie")  # noop, 不抛错


# ------------------------------------------------------------- project access


@pytest.mark.asyncio
async def test_projects_isolated_between_users(multi_user, monkeypatch) -> None:
    from novelvideo.ports import registry as port_registry
    from novelvideo.ports.local.project import AllowAllProjectAccess, SQLiteProjectRegistry

    store = multi_user.get_local_user_store()
    alice = await store.create_user("alice", "alice-pass-1")
    bob = await store.create_user("bob", "bob-pass-2")

    registry = SQLiteProjectRegistry()
    monkeypatch.setitem(port_registry._PORTS, "project_registry", registry)

    proj_a = await registry.create_project(
        owner_user_id=alice.id, owner_username="alice", name="a-proj"
    )
    proj_b = await registry.create_project(
        owner_user_id=bob.id, owner_username="bob", name="b-proj"
    )
    assert proj_a.owner_id == alice.id  # 多用户模式写入真实 owner

    access = AllowAllProjectAccess()
    alice_principals = await access.resolve_requester_principals(alice.id)
    assert alice_principals == [Principal("user", alice.id)]

    visible = await registry.list_accessible_projects(
        [(p.type, p.id) for p in alice_principals]
    )
    assert [p.id for p in visible] == [proj_a.id]

    assert await access.effective_project_role(proj_a, alice_principals) == "owner"
    assert await access.effective_project_role(proj_b, alice_principals) is None
    assert await access.effective_project_role_by_id(proj_b.id, alice_principals) is None
    assert await access.effective_project_role_by_id(proj_a.id, alice_principals) == "owner"


@pytest.mark.asyncio
async def test_single_user_mode_project_semantics_unchanged(single_user, monkeypatch) -> None:
    from novelvideo.ports.local.project import AllowAllProjectAccess, SQLiteProjectRegistry

    registry = SQLiteProjectRegistry()
    created = await registry.create_project(
        owner_user_id="ignored-user-id", owner_username="alice", name="demo"
    )
    assert created.owner_id == "local"  # 关闭时维持旧契约

    access = AllowAllProjectAccess()
    principals = await access.resolve_requester_principals("ignored-user-id")
    assert principals == [Principal("user", "local")]
    assert await access.effective_project_role(created, principals) == "owner"
    assert await access.effective_project_role_by_id(created.id, principals) == "owner"


# --------------------------------------------------------------- login route


class _FakeRequest:
    def __init__(self, host: str = "203.0.113.9") -> None:
        self.client = SimpleNamespace(host=host)


@pytest.mark.asyncio
async def test_login_route_sets_cookie_and_rejects_bad_password(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    res = await auth_routes.login(_FakeRequest(), auth_routes._CredentialRequest(
        username="local", password=BOOTSTRAP_PASSWORD
    ))
    assert res.status_code == 200
    set_cookie = res.headers.get("set-cookie", "")
    assert "st_session" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" not in set_cookie  # ST_COOKIE_SECURE=false（http 部署）

    with pytest.raises(HTTPException) as exc:
        await auth_routes.login(_FakeRequest(), auth_routes._CredentialRequest(
            username="local", password="nope-nope"
        ))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_route_rate_limits_after_failures(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    auth_routes._login_failures.clear()
    req = _FakeRequest()
    for _ in range(auth_routes._LOGIN_FAIL_LIMIT):
        with pytest.raises(HTTPException) as exc:
            await auth_routes.login(req, auth_routes._CredentialRequest(
                username="local", password="bad-password"
            ))
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        await auth_routes.login(req, auth_routes._CredentialRequest(
            username="local", password=BOOTSTRAP_PASSWORD
        ))
    assert exc.value.status_code == 429
    auth_routes._login_failures.clear()


@pytest.mark.asyncio
async def test_login_route_disabled_in_single_user_mode(single_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    with pytest.raises(HTTPException) as exc:
        await auth_routes.login(_FakeRequest(), auth_routes._CredentialRequest(
            username="local", password=BOOTSTRAP_PASSWORD
        ))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_register_route_closed_by_default(multi_user, monkeypatch) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    monkeypatch.delenv("ST_CE_ALLOW_SELF_REGISTER", raising=False)
    with pytest.raises(HTTPException) as exc:
        await auth_routes.register(auth_routes._CredentialRequest(
            username="eve", password="eve-pass-12"
        ))
    assert exc.value.status_code == 403
