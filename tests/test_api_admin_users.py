"""[aipdd fork delta] CE 超管多用户管理的回归测试。

覆盖三块：
1. ``require_owner`` 权限门（member / agent token / 单用户模式）；
2. ``/api/v1/admin/users`` 建号、改角色、禁用、重置口令与"最后一个超管"保护、
   会话吊销；
3. 首次初始化 ``/auth/setup-status`` / ``/auth/setup``，以及 login 自引导收敛
   （没配 ST_LOCAL_PASSWORD 不再偷偷建随机口令账户）。

沿用 tests/test_local_multi_user_isolation.py 的直调路由函数写法，不起完整 app。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason

OWNER = {"id": "local", "user_id": "local", "username": "local", "role": "owner"}
PASSWORD = "sup3r-secret"


def _body(response) -> dict:
    """JSONResponse 本体没有 .json()（那是 TestClient 的 Response），直调路由时自己解。"""
    return json.loads(response.body.decode("utf-8"))


def _set_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))


@pytest.fixture()
def multi_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """多用户模式 + 固定引导口令（与既有部署口径一致）。"""
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
def fresh_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """多用户模式 + 空库 + 没配引导口令：全新安装的首次初始化场景。

    [aipdd fork delta] 本地开发仓库通常带 .env（可能含 ST_LOCAL_PASSWORD），而
    runtime_env._load_env() 用 load_dotenv(override=False) 惰性补键：delenv 把键
    删掉后，任何一次 _load_env()（如 ce_multi_user()）都会把 .env 的口令回填回
    os.environ，让「没配口令」场景失效（CI 无 .env 时不复现）。这里连同 dotenv
    重载一起屏蔽，模拟真正的空配置环境。
    """
    monkeypatch.setenv("ST_CE_MULTI_USER", "true")
    monkeypatch.setenv("ST_COOKIE_SECURE", "false")
    monkeypatch.delenv("ST_LOCAL_PASSWORD", raising=False)
    monkeypatch.delenv("ST_LOCAL_USERNAME", raising=False)

    from novelvideo.shared import runtime_env

    monkeypatch.setattr(runtime_env, "load_project_dotenv", lambda **_: None)
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


# ------------------------------------------------------------------ 权限门


def test_require_owner_rejects_member_and_agent_credentials() -> None:
    from fastapi import HTTPException

    from novelvideo.api.auth import require_owner

    assert require_owner({"role": "owner", "username": "local"})["role"] == "owner"

    with pytest.raises(HTTPException) as exc:
        require_owner({"role": "member", "username": "alice"})
    assert exc.value.status_code == 403

    # agent token 在 LocalAuthSession 里 role 被写死成 owner，必须靠 credential_kind 拦
    with pytest.raises(HTTPException) as exc:
        require_owner(
            {"role": "owner", "username": "alice", "credential_kind": "agent_session"}
        )
    assert exc.value.status_code == 403


def test_admin_routes_are_registered_under_api_v1() -> None:
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
    assert {
        "/admin/users",
        "/admin/users/{user_id}",
        "/admin/users/{user_id}/password",
    } <= paths


@pytest.mark.asyncio
async def test_admin_routes_are_gated_by_require_owner() -> None:
    """四个端点必须全部挂 require_owner。

    直调路由函数会绕过 Depends，所以“非 owner 被拒”的行为断言只能落在
    require_owner 本体上（上一条测试）；这里钉的是“路由确实接了这道门”，
    防的是以后新加端点忘写依赖。
    """
    from novelvideo.api.auth import require_owner
    from novelvideo.api.routes import admin_users

    routes = list(admin_users.router.routes)
    assert len(routes) == 4
    for route in routes:
        calls = {dep.call for dep in route.dependant.dependencies}
        assert require_owner in calls, route.path


@pytest.mark.asyncio
async def test_admin_users_endpoints_require_multi_user_mode(single_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    with pytest.raises(HTTPException) as exc:
        await admin_users.list_users(OWNER)
    assert exc.value.status_code == 400
    assert "ST_CE_MULTI_USER" in exc.value.detail


# ---------------------------------------------------------------- 账户列表


@pytest.mark.asyncio
async def test_list_users_exposes_no_secrets_and_marks_self(multi_user) -> None:
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    await store.create_user("alice", "alice-pass-1")

    res = await admin_users.list_users(OWNER)
    assert res["ok"] is True
    assert {row["username"] for row in res["data"]} == {"local", "alice"}
    for row in res["data"]:
        assert set(row) == {
            "id",
            "username",
            "role",
            "status",
            "created_at",
            "updated_at",
            "is_self",
        }
        assert row["created_at"]  # 建号时间要能显示给用户看
    by_name = {row["username"]: row for row in res["data"]}
    assert by_name["local"]["is_self"] is True
    assert by_name["alice"]["is_self"] is False
    assert by_name["alice"]["role"] == "member"  # 自助/管理员建号默认都不是超管


@pytest.mark.asyncio
async def test_create_user_route_rejects_bad_input_and_duplicates(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    for bad in ("a", "has space", "path/evil", "../etc"):
        with pytest.raises(HTTPException) as exc:
            await admin_users.create_user(
                admin_users._AdminUserCreateRequest(username=bad, password="valid-pass-1"),
                OWNER,
            )
        assert exc.value.status_code == 400, bad

    # 弱口令（未达 store 的普通账户门槛）也走 400，不能静默建号
    with pytest.raises(HTTPException) as exc:
        await admin_users.create_user(
            admin_users._AdminUserCreateRequest(username="bob", password="123"),
            OWNER,
        )
    assert exc.value.status_code == 400
    assert await store.get_user_by_username("bob") is None

    created = await admin_users.create_user(
        admin_users._AdminUserCreateRequest(username="alice", password="valid-pass-1"),
        OWNER,
    )
    assert created["data"]["role"] == "member"
    assert created["data"]["is_self"] is False

    with pytest.raises(HTTPException) as exc:
        await admin_users.create_user(
            admin_users._AdminUserCreateRequest(username="alice", password="other-pass-1"),
            OWNER,
        )
    assert exc.value.status_code == 409

    assert {u.username for u in await store.list_users()} == {"local", "alice"}


@pytest.mark.asyncio
async def test_create_owner_requires_strong_password(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    with pytest.raises(HTTPException) as exc:
        await admin_users.create_user(
            admin_users._AdminUserCreateRequest(username="boss", password="123456", role="owner"),
            OWNER,
        )
    assert exc.value.status_code == 400
    assert await store.get_user_by_username("boss") is None


@pytest.mark.asyncio
async def test_create_user_route_supports_chinese_username(multi_user) -> None:
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    res = await admin_users.create_user(
        admin_users._AdminUserCreateRequest(username="小王", password="valid-pass-1"),
        OWNER,
    )
    assert res["ok"] is True
    assert (await store.get_user_by_username("小王")) is not None


# ------------------------------------------------------------ 角色与状态


@pytest.mark.asyncio
async def test_admin_cannot_change_own_role_or_status(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    local = await store.bootstrap_local_account()

    for payload in (
        admin_users._AdminUserPatchRequest(role="member"),
        admin_users._AdminUserPatchRequest(status="suspended"),
    ):
        with pytest.raises(HTTPException) as exc:
            await admin_users.update_user(local.id, payload, OWNER)
        assert exc.value.status_code == 400

    fresh = await store.get_user_by_id(local.id)
    assert fresh.role == "owner" and fresh.status == "active"


@pytest.mark.asyncio
async def test_patch_requires_role_or_status(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")

    with pytest.raises(HTTPException) as exc:
        await admin_users.update_user(
            alice.id, admin_users._AdminUserPatchRequest(), OWNER
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await admin_users.update_user(
            alice.id, admin_users._AdminUserPatchRequest(status="deleted"), OWNER
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await admin_users.update_user(
            "01MISSING", admin_users._AdminUserPatchRequest(role="member"), OWNER
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_applies_role_and_status_together_atomically(multi_user) -> None:
    """C6: a single PATCH setting both role and status must land both in one write."""
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")

    await admin_users.update_user(
        alice.id,
        admin_users._AdminUserPatchRequest(role="owner", status="suspended"),
        OWNER,
    )

    updated = await store.get_user_by_id(alice.id)
    assert updated is not None
    assert updated.role == "owner"
    assert updated.status == "suspended"


@pytest.mark.asyncio
async def test_disable_revokes_sessions_and_blocks_relogin(multi_user) -> None:
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")
    token = await store.create_session(alice.id)
    assert (await store.get_session_user(token)).username == "alice"

    res = await admin_users.update_user(
        alice.id, admin_users._AdminUserPatchRequest(status="suspended"), OWNER
    )
    assert res["data"]["status"] == "suspended"

    # 吊销后会话行已删：老 cookie 直接失效
    with pytest.raises(AuthError) as exc:
        await store.get_session_user(token)
    assert exc.value.reason == AuthFailureReason.INVALID

    # 纵深防御：就算给已禁用账户现造一个会话，取用户时照样被拦
    with pytest.raises(AuthError) as exc:
        await store.get_session_user(await store.create_session(alice.id))
    assert exc.value.reason == AuthFailureReason.USER_SUSPENDED

    with pytest.raises(AuthError):
        await store.login("alice", "alice-pass-1")

    # 禁用中的账户不能被重置口令（避免"边禁用边改密"的歧义状态）
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await admin_users.reset_password(
            alice.id, admin_users._AdminPasswordResetRequest(password="new-pass-9"), OWNER
        )
    assert exc.value.status_code == 400

    # 重新启用后可以正常登录
    res = await admin_users.update_user(
        alice.id, admin_users._AdminUserPatchRequest(status="active"), OWNER
    )
    assert res["data"]["status"] == "active"
    assert (await store.login("alice", "alice-pass-1")).id == alice.id


@pytest.mark.asyncio
async def test_role_change_takes_effect_without_relogin(multi_user) -> None:
    from novelvideo.api.routes import admin_users
    from novelvideo.ports.local.auth import FileAuthPort

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")
    token = await store.create_session(alice.id)

    port = FileAuthPort()
    assert (await port.verify_session(token))["role"] == "member"

    await admin_users.update_user(
        alice.id, admin_users._AdminUserPatchRequest(role="owner"), OWNER
    )
    assert (await port.verify_session(token))["role"] == "owner"


@pytest.mark.asyncio
async def test_last_active_administrator_cannot_be_demoted_or_disabled(multi_user) -> None:
    store = multi_user.get_local_user_store()
    local = await store.bootstrap_local_account()

    with pytest.raises(AuthError):
        await store.set_user_role(local.id, "member")
    with pytest.raises(AuthError):
        await store.set_user_status(local.id, "suspended")
    assert await store.count_active_owners() == 1

    # 有第二个可用超管之后才允许降级
    await store.create_user("boss", "boss-pass-1", role="owner")
    demoted = await store.set_user_role(local.id, "member")
    assert demoted.role == "member"
    assert await store.count_active_owners(exclude_user_id=local.id) == 1


# ---------------------------------------------------------------- 重置口令


@pytest.mark.asyncio
async def test_password_reset_invalidates_old_cookie_and_accepts_new(
    multi_user,
) -> None:
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")
    token = await store.create_session(alice.id)

    res = await admin_users.reset_password(
        alice.id, admin_users._AdminPasswordResetRequest(password="brand-new-7"), OWNER
    )
    assert res["data"]["revoked_sessions"] == 1
    assert res["data"]["is_self"] is False

    with pytest.raises(AuthError):
        await store.get_session_user(token)
    with pytest.raises(AuthError):
        await store.login("alice", "alice-pass-1")
    assert (await store.login("alice", "brand-new-7")).id == alice.id


@pytest.mark.asyncio
async def test_admin_can_reset_own_password_and_gets_kicked(multi_user) -> None:
    """超管改自己口令也吊销全部会话：包括当前这个 cookie，下一请求必须重新登录。"""
    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    local = await store.bootstrap_local_account()
    token = await store.create_session(local.id)

    res = await admin_users.reset_password(
        local.id, admin_users._AdminPasswordResetRequest(password="mine-now-8"), OWNER
    )
    assert res["data"]["is_self"] is True
    assert res["data"]["revoked_sessions"] == 1
    with pytest.raises(AuthError):
        await store.get_session_user(token)
    assert (await store.login("local", "mine-now-8")).id == local.id


@pytest.mark.asyncio
async def test_password_reset_rejects_weak_password_and_missing_user(
    multi_user,
) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import admin_users

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()
    alice = await store.create_user("alice", "alice-pass-1")

    with pytest.raises(HTTPException) as exc:
        await admin_users.reset_password(
            alice.id, admin_users._AdminPasswordResetRequest(password="1234567"), OWNER
        )
    assert exc.value.status_code == 400
    assert (await store.login("alice", "alice-pass-1")).id == alice.id

    with pytest.raises(HTTPException) as exc:
        await admin_users.reset_password(
            "01MISSING", admin_users._AdminPasswordResetRequest(password="brand-new-7"), OWNER
        )
    assert exc.value.status_code == 404


# ------------------------------------------------------------- 首次初始化


@pytest.mark.asyncio
async def test_setup_status_asks_for_initialisation_on_fresh_install(
    fresh_install,
) -> None:
    from novelvideo.api.routes import auth as auth_routes

    res = await auth_routes.setup_status()
    assert res.status_code == 200
    assert _body(res)["data"]["needs_setup"] is True

    store = fresh_install.get_local_user_store()
    assert await store.count_users() == 0  # GET 不落库、不建账户


@pytest.mark.asyncio
async def test_setup_creates_first_owner_and_mints_session(fresh_install) -> None:
    from novelvideo.api.routes import auth as auth_routes

    res = await auth_routes.setup(
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
        auth_routes._SetupRequest(username="boss", password="Aipdd@2026"),
    )
    assert res.status_code == 200
    body = _body(res)["data"]
    assert body["role"] == "owner"
    assert body["username"] == "boss"
    assert "st_session" in res.headers.get("set-cookie", "")

    store = fresh_install.get_local_user_store()
    record = await store.get_user_by_username("boss")
    assert record is not None
    assert record.id == "local"  # 固定 id：既有 owner_id='local' 数据零迁移
    assert record.role == "owner"

    again = await auth_routes.setup_status()
    assert _body(again)["data"]["needs_setup"] is False


@pytest.mark.asyncio
async def test_setup_is_one_shot_and_validates_input(fresh_install) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with pytest.raises(HTTPException) as exc:
        await auth_routes.setup(
            req, auth_routes._SetupRequest(username="boss", password="short")
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await auth_routes.setup(
            req, auth_routes._SetupRequest(username="bad name", password="Aipdd@2026")
        )
    assert exc.value.status_code == 400
    assert await fresh_install.get_local_user_store().count_users() == 0

    await auth_routes.setup(
        req, auth_routes._SetupRequest(username="boss", password="Aipdd@2026")
    )
    with pytest.raises(HTTPException) as exc:
        await auth_routes.setup(
            req, auth_routes._SetupRequest(username="thief", password="Aipdd@2026")
        )
    assert exc.value.status_code == 409
    assert await fresh_install.get_local_user_store().get_user_by_username("thief") is None


@pytest.mark.asyncio
async def test_login_does_not_bootstrap_random_password_account(
    fresh_install,
) -> None:
    """[aipdd fork delta] 没配 ST_LOCAL_PASSWORD 时登录不再偷偷建账户。

    旧行为会生成一个只出现在日志里的随机口令，把"首次初始化"的窗口吃掉，
    用户既登不进去也拿不到 setup 表单。
    """
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    with pytest.raises(HTTPException) as exc:
        await auth_routes.login(req, auth_routes._CredentialRequest(username="local", password="x"))
    assert exc.value.status_code == 401

    store = fresh_install.get_local_user_store()
    assert await store.count_users() == 0


@pytest.mark.asyncio
async def test_startup_hook_does_not_eat_the_first_run_setup(
    fresh_install,
) -> None:
    """[aipdd fork delta] 启动钩子必须与 login 同一口径：没配口令就不建账户。

    app.py 的 lifespan 以前无条件 bootstrap，随机口令只打一次日志，账户一建
    setup-status 就不再返回 needs_setup，全新安装直接卡在登录页。
    """
    from novelvideo.api.routes.auth import bootstrap_if_env_password

    await bootstrap_if_env_password()
    assert await fresh_install.get_local_user_store().count_users() == 0

    import inspect

    from novelvideo.api import app as app_module

    source = inspect.getsource(app_module)
    assert "bootstrap_if_env_password" in source
    assert "bootstrap_local_account" not in source


@pytest.mark.asyncio
async def test_startup_hook_bootstraps_when_env_password_is_set(
    multi_user,
) -> None:
    """配了 ST_LOCAL_PASSWORD 的部署保持原行为：启动即建引导账户，口令可用。"""
    from novelvideo.api.routes.auth import bootstrap_if_env_password

    await bootstrap_if_env_password()
    store = multi_user.get_local_user_store()
    assert await store.count_users() == 1
    record = await store.login("local", PASSWORD)
    assert record.role == "owner"


@pytest.mark.asyncio
async def test_setup_rejected_in_single_user_mode(single_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    with pytest.raises(HTTPException) as exc:
        await auth_routes.setup(
            SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
            auth_routes._SetupRequest(username="boss", password="Aipdd@2026"),
        )
    assert exc.value.status_code == 400

    res = await auth_routes.setup_status()
    assert _body(res)["data"]["needs_setup"] is False


# ------------------------------------------------------- 自助改密（change-password）


@pytest.mark.asyncio
async def test_change_password_requires_multi_user_mode(single_user) -> None:
    """非多用户模式（单用户/EE）没有可自助修改的登录口令，直接 400。"""
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    with pytest.raises(HTTPException) as exc:
        await auth_routes.change_password(
            auth_routes._ChangePasswordRequest(old_password="x", new_password="y"),
            OWNER,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_change_password_rejects_agent_session_credential(multi_user) -> None:
    """agent 会话 / API Key 没有登录口令可改，须被拦（role 虽写死 owner 也不放过）。"""
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    for kind in ("agent_session", "api_key"):
        with pytest.raises(HTTPException) as exc:
            await auth_routes.change_password(
                auth_routes._ChangePasswordRequest(
                    old_password=PASSWORD, new_password="brand-new-9"
                ),
                {**OWNER, "credential_kind": kind},
            )
        assert exc.value.status_code == 400, kind


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_old_password(multi_user) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    with pytest.raises(HTTPException) as exc:
        await auth_routes.change_password(
            auth_routes._ChangePasswordRequest(
                old_password="totally-wrong", new_password="brand-new-9"
            ),
            OWNER,
        )
    assert exc.value.status_code == 401
    # 旧口令仍然有效：改密未落库
    assert (await store.login("local", PASSWORD)).id == "local"


@pytest.mark.asyncio
async def test_change_password_enforces_owner_strong_password(multi_user) -> None:
    """超管自助改密走严格门槛（>=8）：弱新口令 400，且旧口令保持不变。"""
    from fastapi import HTTPException

    from novelvideo.api.routes import auth as auth_routes

    store = multi_user.get_local_user_store()
    await store.bootstrap_local_account()

    with pytest.raises(HTTPException) as exc:
        await auth_routes.change_password(
            auth_routes._ChangePasswordRequest(old_password=PASSWORD, new_password="short1"),
            OWNER,
        )
    assert exc.value.status_code == 400
    assert (await store.login("local", PASSWORD)).id == "local"


@pytest.mark.asyncio
async def test_change_password_success_keeps_current_session(multi_user) -> None:
    """改密成功后新口令可登录、旧口令失效，且当前会话不被吊销（不强制登出）。"""
    from novelvideo.api.routes import auth as auth_routes

    store = multi_user.get_local_user_store()
    local = await store.bootstrap_local_account()
    token = await store.create_session(local.id)

    res = await auth_routes.change_password(
        auth_routes._ChangePasswordRequest(
            old_password=PASSWORD, new_password="brand-new-9"
        ),
        OWNER,
    )
    assert res.status_code == 200
    assert _body(res)["ok"] is True

    # 新口令生效、旧口令失效
    assert (await store.login("local", "brand-new-9")).id == local.id
    from novelvideo.ports.auth_contract import AuthError

    with pytest.raises(AuthError):
        await store.login("local", PASSWORD)

    # 当前会话保留：set_password 不动 session 表
    assert (await store.get_session_user(token)).id == local.id
