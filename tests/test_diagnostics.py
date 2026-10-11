# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→「系统整体自检」：diagnostics 聚合与 /admin/diagnostics 契约回归。

两层覆盖：
1. :mod:`novelvideo.diagnostics` 的分组判定（环境 / 网关连通性 / 默认模型 / 存储）与
   ``_gateway_probe`` 的成功 / HTTP 失败 / 异常降级，全程 monkeypatch，绝不碰真实环境；
2. ``/admin/diagnostics`` 路由契约：owner 放行拿报告，member 与 agent token 被拒。
"""

from __future__ import annotations

import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import diagnostics
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import env_setup as env_setup_routes
from novelvideo import model_gateway_settings as mgs

# ---------------------------------------------------------------- 探测单元


def test_environment_section_maps_dependency_levels(monkeypatch):
    monkeypatch.setattr(
        diagnostics.env_probe,
        "probe",
        lambda: {
            "items": [
                {"id": "ffmpeg", "installed": True, "detail": "/usr/bin/ffmpeg"},
                {"id": "node", "installed": False, "detail": None},
                {"id": "chrome", "installed": False, "detail": None},
            ]
        },
    )
    items = {it["id"]: it["level"] for it in diagnostics._environment_section()["items"]}
    assert items == {"ffmpeg": "pass", "node": "fail", "chrome": "info"}


def test_gateway_probe_success_when_model_list_returns(monkeypatch):
    class _Resp:
        def read(self) -> bytes:
            return b'{"data": [{"id": "gpt"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", lambda *a, **k: _Resp())
    ok, detail = diagnostics._gateway_probe("http://gw", "sk-1")
    assert ok is True
    assert "/models" in detail


def test_gateway_probe_reports_http_error(monkeypatch):
    def _raise(url, timeout=None):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", _raise)
    ok, detail = diagnostics._gateway_probe("http://gw", "sk-1")
    assert ok is False
    assert "HTTP 401" in detail


def test_gateway_probe_reports_transport_reason(monkeypatch):
    def _raise(url, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", _raise)
    ok, detail = diagnostics._gateway_probe("http://gw", "sk-1")
    assert ok is False
    assert "Connection refused" in detail


def test_model_gateway_section_configured_and_reachable(monkeypatch):
    class _Cfg:
        mode = "custom"
        base_url = "http://gw"
        api_key = "sk-1"

    monkeypatch.setattr(mgs, "get_effective_newapi_config", lambda **k: _Cfg())
    monkeypatch.setattr(diagnostics, "_gateway_probe", lambda base, key: (True, "ok"))
    items = {it["id"]: it["level"] for it in diagnostics._model_gateway_section()["items"]}
    assert items == {"gateway_configured": "pass", "gateway_reachable": "pass"}


def test_model_gateway_section_unconfigured_skips_probe(monkeypatch):
    class _Cfg:
        mode = "custom"
        base_url = ""
        api_key = ""

    monkeypatch.setattr(mgs, "get_effective_newapi_config", lambda **k: _Cfg())
    items = {it["id"]: it["level"] for it in diagnostics._model_gateway_section()["items"]}
    assert items == {"gateway_configured": "fail", "gateway_reachable": "fail"}


def test_default_models_section_lists_missing_categories(monkeypatch):
    monkeypatch.setattr(
        mgs, "resolve_default_media_model", lambda mt: {"image": "flux", "video": ""}.get(mt, "")
    )
    monkeypatch.setattr(mgs, "get_effective_default_text_model", lambda: "gpt")
    items = {it["id"]: it["level"] for it in diagnostics._default_models_section()["items"]}
    assert items["image"] == "pass"
    assert items["text"] == "pass"
    assert items["video"] == "fail"
    assert items["audio"] == "fail"


def test_run_diagnostics_never_raises_when_a_section_blows_up(monkeypatch):
    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(diagnostics, "_environment_section", _boom)
    report = diagnostics.run_diagnostics()
    env = next(s for s in report["sections"] if s["id"] == "environment")
    assert env["items"][0]["level"] == "fail"
    assert env["items"][0]["id"] == "check_error"
    # 其它三组仍然产出，整盘不崩。
    assert {s["id"] for s in report["sections"]} == {
        "environment",
        "model_gateway",
        "default_models",
        "storage",
    }


def test_run_diagnostics_summary_counts(monkeypatch):
    monkeypatch.setattr(diagnostics.env_probe, "probe", lambda: {"items": []})

    class _Cfg:
        mode = "custom"
        base_url = "http://gw"
        api_key = "sk"

    monkeypatch.setattr(mgs, "get_effective_newapi_config", lambda **k: _Cfg())
    monkeypatch.setattr(diagnostics, "_gateway_probe", lambda base, key: (True, "ok"))
    monkeypatch.setattr(mgs, "resolve_default_media_model", lambda mt: "m")
    monkeypatch.setattr(mgs, "get_effective_default_text_model", lambda: "m")
    # 存储组依赖真实磁盘 / db 路径，这里桩成全绿以聚焦 summary 计数逻辑。
    monkeypatch.setattr(
        diagnostics,
        "_storage_section",
        lambda: {"id": "storage", "items": [{"id": "disk_space", "level": "pass"}]},
    )
    report = diagnostics.run_diagnostics()
    assert report["summary"]["fail"] == 0
    assert report["summary"]["all_ok"] is True


# ---------------------------------------------------------------- 路由契约


def _client(user):
    app = FastAPI()
    app.include_router(env_setup_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


def test_diagnostics_route_owner_ok(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "run_diagnostics",
        lambda: {"sections": [], "summary": {"total": 0, "pass": 0, "warn": 0, "fail": 0, "info": 0, "all_ok": True}},
    )
    resp = _client({"id": "u", "username": "admin", "role": "owner"}).get("/api/v1/admin/diagnostics")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_diagnostics_route_member_forbidden():
    resp = _client({"id": "u", "username": "bob", "role": "member"}).get("/api/v1/admin/diagnostics")
    assert resp.status_code == 403


def test_diagnostics_route_agent_token_forbidden():
    resp = _client(
        {"id": "u", "username": "admin", "role": "owner", "credential_kind": "agent_session"}
    ).get("/api/v1/admin/diagnostics")
    assert resp.status_code == 403
