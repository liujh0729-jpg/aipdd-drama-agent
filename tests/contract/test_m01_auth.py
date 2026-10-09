from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason

pytestmark = pytest.mark.m01


def _reset_port_modules():
    import novelvideo.ports as ports
    import novelvideo.ports.local as local_ports
    import novelvideo.ports.registry as registry

    registry = importlib.reload(registry)
    ports = importlib.reload(ports)
    local_ports = importlib.reload(local_ports)
    return registry, ports, local_ports


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"

    import novelvideo.api.deps as deps
    import novelvideo.api.routes.projects as project_routes
    import novelvideo.config as config
    import novelvideo.project_config as project_config
    import novelvideo.project_context as project_context
    import novelvideo.utils.project_paths as project_paths

    for module in (config, deps, project_paths):
        monkeypatch.setattr(module, "OUTPUT_DIR", str(output), raising=False)
        monkeypatch.setattr(module, "STATE_DIR", str(state), raising=False)
        monkeypatch.setattr(module, "RUNTIME_DIR", str(runtime), raising=False)
    monkeypatch.setattr(project_config, "OUTPUT_DIR", str(state), raising=False)
    monkeypatch.setattr(project_config, "STATE_DIR", str(state), raising=False)
    monkeypatch.setattr(project_routes, "resolve_worker_id", lambda: "node_local", raising=False)
    monkeypatch.setattr(project_context, "resolve_worker_id", lambda: "node_local")


class _RejectingAuthPort:
    async def verify_session(self, raw_cookie: str | None) -> dict:
        if raw_cookie is None:
            raise AuthError(AuthFailureReason.MISSING, "Missing session or agent token")
        raise AuthError(AuthFailureReason.INVALID, "Invalid session")

    async def revoke_session(self, raw_cookie: str) -> None:  # noqa: ARG002
        return None


def _api_modules() -> dict[str, object]:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "novelvideo.api" or name.startswith("novelvideo.api.")
    }


_MISSING = object()


@contextlib.contextmanager
def _ce_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A CE app built against freshly imported API modules.

    Building it means dropping every novelvideo.api.* module so the rebuilt app
    picks up the patched roots. Those rebuilt modules must not outlive this
    block: any test collected earlier holds functions whose globals belong to
    the modules dropped here, and leaving the replacements behind turns those
    references into orphans.

    Restoring sys.modules is only half of it. Importing a submodule also binds
    it as an attribute of its parent package, and the rebuild rebinds
    novelvideo.api on novelvideo itself. A dotted patch target is resolved by
    walking those attributes rather than by reading sys.modules, so restoring
    one without the other leaves a patch landing on a module nobody is running.

    Everything after the first change to sys.modules sits inside try, because a
    failure while building the app would otherwise leave the process in exactly
    the state this exists to prevent.
    """
    import novelvideo

    original = _api_modules()
    original_api_attr = getattr(novelvideo, "api", _MISSING)

    registry, _, _ = _reset_port_modules()
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")

    try:
        for module_name in list(original):
            sys.modules.pop(module_name, None)
        _patch_roots(monkeypatch, tmp_path)

        from novelvideo.ports.local import project as local_project

        monkeypatch.setattr(
            local_project, "resolve_worker_id", lambda: "node_local", raising=False
        )
        registry.ensure_bootstrap()

        from novelvideo.api.app import create_app

        app = create_app()
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()
        with TestClient(app) as client:
            yield client
    finally:
        for name in list(_api_modules()):
            sys.modules.pop(name, None)
        sys.modules.update(original)

        # With nothing loaded beforehand there is no module to put back, but the
        # attribute the rebuild created still points at a package that is no
        # longer in sys.modules — an orphan of exactly the kind this guards.
        if original_api_attr is _MISSING:
            if hasattr(novelvideo, "api"):
                delattr(novelvideo, "api")
        else:
            novelvideo.api = original_api_attr

        # Parents before children, so each rebinding attaches to the package
        # that has itself already been restored.
        for name, module in sorted(
            original.items(), key=lambda item: item[0].count(".")
        ):
            parent_name, _, attr = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, attr, module)


def test_ce_auth_me_logout_and_project_crud_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _ce_client(monkeypatch, tmp_path) as client:
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == {
            "ok": True,
            "data": {
                "username": "local",
                "role": "owner",
                # [aipdd fork delta] WP-D: CE 不做额度记账，/auth/me 不再回
                # credit_balance；下面的字典等值断言本身就是键集护栏。
                "credential_kind": "user",
                "current_scope_kind": None,
                "current_project_id": None,
                "scopes": None,
            },
        }

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"ok": True}
        assert "st_session=" in logout.headers["set-cookie"]
        assert "Max-Age=0" in logout.headers["set-cookie"]

        login = client.post("/api/v1/auth/login", json={"username": "local", "password": "x"})
        # [aipdd fork delta] CE 最小可用多用户隔离：登录路由在 CE 已存在（路由
        # 本体），但 ST_CE_MULTI_USER 未开启时拒绝登录 → 400，不再是 404。
        assert login.status_code == 400

        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        # [aipdd fork delta] 同上：login 路由已进 CE 契约面。
        assert "/api/v1/auth/login" in openapi.json()["paths"]

        created = client.post("/api/v1/projects", json={"name": "demo"})
        assert created.status_code == 200
        project_id = created.json()["data"]["project_id"]

        listed = client.get("/api/v1/projects")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == project_id

        detail = client.get(f"/api/v1/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["project_id"] == project_id
        # New projects use structured extraction and are deliberately not bound
        # to an embedding model. The binding is permanent once written, so this
        # is decided at creation time; existing projects keep theirs.
        assert detail.json()["data"]["knowledge_pipeline"] == "structured_v1"
        assert "cognee_embedding_model" not in detail.json()["data"]
        assert "cognee_embedding_dimension" not in detail.json()["data"]


@pytest.mark.ee
def test_ee_auth_missing_and_bad_cookie_contract() -> None:
    registry, _, _ = _reset_port_modules()
    registry.register_port("auth", _RejectingAuthPort())

    from novelvideo.api.app import create_app

    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    with TestClient(app) as client:
        missing = client.get("/api/v1/auth/me")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Missing session or agent token"

        bad = client.get("/api/v1/auth/me", cookies={"st_session": "bad-cookie"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid session"

        missing_logout = client.post("/api/v1/auth/logout")
        assert missing_logout.status_code == 200
        assert missing_logout.json() == {"ok": True}
        assert "st_session=" in missing_logout.headers["set-cookie"]
        assert "Max-Age=0" in missing_logout.headers["set-cookie"]

        bad_logout = client.post(
            "/api/v1/auth/logout",
            cookies={"st_session": "bad-cookie"},
        )
        assert bad_logout.status_code == 200
        assert bad_logout.json() == {"ok": True}
        assert "st_session=" in bad_logout.headers["set-cookie"]
        assert "Max-Age=0" in bad_logout.headers["set-cookie"]


def test_ce_client_leaves_api_module_identity_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the CE app must not outlive itself in the import system.

    Every test collected earlier holds functions whose globals belong to the
    modules this client drops. If the replacements survive, a later patch given
    as a dotted string lands on a module nobody is running, and the test it was
    written for silently exercises unpatched code.
    """
    import novelvideo.api.routes.projects as before_projects

    before = sys.modules["novelvideo.api.routes.projects"]

    with _ce_client(monkeypatch, tmp_path):
        pass

    assert sys.modules["novelvideo.api.routes.projects"] is before
    assert before_projects is before
    # Dotted-string patch targets are resolved by walking package attributes,
    # so those have to come back as well as sys.modules.
    import novelvideo

    assert novelvideo.api.routes.projects is before


def test_ce_client_restores_the_import_system_even_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while building the app must not leak the rebuilt modules.

    Everything after the first change to sys.modules has to be covered, or the
    one case where isolation matters most — something went wrong — is the one
    case without it.
    """
    import novelvideo
    import novelvideo.api.routes.projects  # noqa: F401

    before = sys.modules["novelvideo.api.routes.projects"]

    # Fail inside the try, after sys.modules has already been changed. Patching
    # anything under novelvideo.api would not work: those modules are dropped
    # and freshly imported, so the patch would not survive to be called.
    def explode(*_args, **_kwargs) -> None:
        raise RuntimeError("app build failed")

    monkeypatch.setattr(sys.modules[__name__], "_patch_roots", explode)

    with pytest.raises(RuntimeError, match="app build failed"):
        with _ce_client(monkeypatch, tmp_path):
            pass

    assert sys.modules["novelvideo.api.routes.projects"] is before
    assert novelvideo.api.routes.projects is before


def test_ce_client_leaves_nothing_behind_when_nothing_was_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running this file alone starts with no novelvideo.api.* loaded.

    Restoring an empty snapshot puts no module back, but the attribute the
    rebuild created still points at a package that is gone from sys.modules —
    an orphan of the same kind, reached the same way.
    """
    import novelvideo

    stashed = _api_modules()
    stashed_attr = getattr(novelvideo, "api", _MISSING)
    for name in stashed:
        sys.modules.pop(name, None)
    if hasattr(novelvideo, "api"):
        delattr(novelvideo, "api")

    try:
        with _ce_client(monkeypatch, tmp_path):
            pass

        assert not _api_modules(), "rebuilt modules outlived the client"
        assert not hasattr(novelvideo, "api"), (
            "novelvideo.api still points at a package no longer importable"
        )
    finally:
        sys.modules.update(stashed)
        if stashed_attr is not _MISSING:
            novelvideo.api = stashed_attr
        for name, module in sorted(stashed.items(), key=lambda i: i[0].count(".")):
            parent_name, _, attr = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, attr, module)
