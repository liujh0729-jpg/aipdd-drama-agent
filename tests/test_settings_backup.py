# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→「配置备份 / 恢复」：导出/导入往返与路由契约回归。

两层覆盖：
1. :func:`model_gateway_settings.import_runtime_settings` 的 merge / 空键丢弃 /
   缓存失效，配合 :func:`export_runtime_settings` 做导出→导入往返；
2. ``/admin/settings/export`` 与 ``/admin/settings/import`` 路由：owner 拿到带
   ``Content-Disposition`` 的 bundle、坏信封回 400，member 与 agent token 一律被拒。

settings DB 由 ``tests/conftest.py`` autouse 隔离到 per-test 临时目录，绝不污染真实配置。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import model_gateway_settings as mgs
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import settings_backup as settings_backup_routes


# ---------------------------------------------------------------- 读写原语


def test_export_import_roundtrip():
    mgs.import_runtime_settings({"newapi.base_url": "http://gw", "newapi.api_key": "sk-1"})
    exported = mgs.export_runtime_settings()
    assert exported["newapi.base_url"] == "http://gw"
    assert exported["newapi.api_key"] == "sk-1"

    # 清空后再从导出结果回灌，值应原样恢复。
    mgs.import_runtime_settings({"newapi.base_url": "", "newapi.api_key": ""})
    assert mgs.export_runtime_settings()["newapi.api_key"] == ""
    mgs.import_runtime_settings({k: v for k, v in exported.items() if k.startswith("newapi.")})
    assert mgs.export_runtime_settings()["newapi.api_key"] == "sk-1"


def test_import_merge_preserves_keys_outside_bundle():
    mgs.import_runtime_settings({"keep.me": "original", "overwrite.me": "old"})
    result = mgs.import_runtime_settings({"overwrite.me": "new"})
    assert result == {"applied": 1, "skipped": 0}
    after = mgs.export_runtime_settings()
    assert after["keep.me"] == "original"
    assert after["overwrite.me"] == "new"


def test_import_skips_blank_keys():
    result = mgs.import_runtime_settings({"real": "v", "": "x", "   ": "y"})
    assert result["applied"] == 1
    assert result["skipped"] == 2
    assert mgs.export_runtime_settings()["real"] == "v"


def test_import_triggers_runtime_refresh(monkeypatch):
    calls: list[int] = []

    def _fake_refresh():
        calls.append(1)

    # import_runtime_settings 内以 `from novelvideo import model_gateway_runtime`
    # 延迟取模块，故 patch 该模块属性即可捕获调用。
    import novelvideo.model_gateway_runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "refresh_model_gateway_runtime", _fake_refresh)
    mgs.import_runtime_settings({"some.key": "value"})
    assert calls == [1]


def test_import_survives_refresh_failure(monkeypatch):
    def _boom():
        raise RuntimeError("not CE")

    import novelvideo.model_gateway_runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "refresh_model_gateway_runtime", _boom)
    # 刷新失败不得回滚已写入的配置，也不得抛出。
    result = mgs.import_runtime_settings({"persist.key": "persist.val"})
    assert result["applied"] == 1
    assert mgs.export_runtime_settings()["persist.key"] == "persist.val"


# ---------------------------------------------------------------- 路由契约


def _client(user):
    app = FastAPI()
    app.include_router(settings_backup_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


_OWNER = {"id": "u", "username": "admin", "role": "owner"}


def test_export_route_owner_returns_bundle_with_attachment():
    mgs.import_runtime_settings({"newapi.api_key": "sk-secret"})
    resp = _client(_OWNER).get("/api/v1/admin/settings/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "dramaclaw-config-" in resp.headers.get("content-disposition", "")
    bundle = resp.json()
    assert bundle["format"] == settings_backup_routes.FORMAT
    assert bundle["schema_version"] == settings_backup_routes.SCHEMA_VERSION
    assert bundle["settings"]["newapi.api_key"] == "sk-secret"
    assert bundle["count"] == len(bundle["settings"])


def test_export_route_member_forbidden():
    resp = _client({"id": "u", "username": "bob", "role": "member"}).get(
        "/api/v1/admin/settings/export"
    )
    assert resp.status_code == 403


def test_export_route_agent_token_forbidden():
    resp = _client(
        {"id": "u", "username": "admin", "role": "owner", "credential_kind": "agent_session"}
    ).get("/api/v1/admin/settings/export")
    assert resp.status_code == 403


def test_import_route_roundtrip_owner():
    client = _client(_OWNER)
    bundle = {
        "format": settings_backup_routes.FORMAT,
        "schema_version": settings_backup_routes.SCHEMA_VERSION,
        "settings": {"restored.key": "restored.val"},
    }
    resp = client.post("/api/v1/admin/settings/import", json=bundle)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["applied"] == 1
    assert mgs.export_runtime_settings()["restored.key"] == "restored.val"


def test_import_route_warns_on_schema_mismatch():
    client = _client(_OWNER)
    bundle = {
        "format": settings_backup_routes.FORMAT,
        "schema_version": settings_backup_routes.SCHEMA_VERSION + 1,
        "settings": {"k": "v"},
    }
    resp = client.post("/api/v1/admin/settings/import", json=bundle)
    assert resp.status_code == 200
    assert resp.json()["data"]["warnings"]


def test_import_route_rejects_bad_format():
    client = _client(_OWNER)
    resp = client.post("/api/v1/admin/settings/import", json={"format": "wrong", "settings": {"k": "v"}})
    assert resp.status_code == 400


def test_import_route_rejects_empty_settings():
    client = _client(_OWNER)
    resp = client.post(
        "/api/v1/admin/settings/import",
        json={"format": settings_backup_routes.FORMAT, "schema_version": 1, "settings": {}},
    )
    assert resp.status_code == 400


def test_import_route_member_forbidden():
    resp = _client({"id": "u", "username": "bob", "role": "member"}).post(
        "/api/v1/admin/settings/import", json={"format": "x", "settings": {}}
    )
    assert resp.status_code == 403
