# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→「环境安装」：部署环境依赖探测的回归测试。

两层覆盖：
1. :mod:`novelvideo.env_probe` 的探测单元（command / fonts / chrome）与 probe 汇总，
   全程 monkeypatch shutil.which / subprocess.run / urllib，绝不碰真实环境；
2. /admin/env-setup/status 路由契约：owner 放行拿清单，member 与 agent token 被拒。
"""

from __future__ import annotations

import socket
import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import env_probe, env_setup_install
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import env_setup as env_setup_routes


# ------------------------------------------------------------------ 探测单元
def test_command_item_reports_path_and_parsed_version(monkeypatch):
    monkeypatch.setattr(env_probe.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    class _Done:
        returncode = 0
        stdout = "ffmpeg version 6.1.1 Copyright (c) 2000-2023\n"
        stderr = ""

    monkeypatch.setattr(env_probe.subprocess, "run", lambda *a, **k: _Done())
    item = env_probe._command_item("ffmpeg", "ffmpeg", ["-version"])
    assert item == {
        "id": "ffmpeg",
        "installed": True,
        "version": "6.1.1",
        "detail": "/usr/bin/ffmpeg",
    }


def test_command_item_missing_binary(monkeypatch):
    monkeypatch.setattr(env_probe.shutil, "which", lambda name: None)
    item = env_probe._command_item("uv", "uv", ["--version"])
    assert item["installed"] is False
    assert item["version"] is None
    assert item["detail"] is None


def test_fonts_item_matches_candidate(monkeypatch):
    class _Done:
        returncode = 0
        stdout = "Noto Sans CJK SC,Noto Sans CJK SC Regular\nDejaVu Sans\n"
        stderr = ""

    monkeypatch.setattr(env_probe.subprocess, "run", lambda *a, **k: _Done())
    item = env_probe._fonts_item()
    assert item["installed"] is True
    assert item["detail"] == "Noto Sans CJK SC"


def test_fonts_item_missing_when_fc_list_fails(monkeypatch):
    class _Done:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(env_probe.subprocess, "run", lambda *a, **k: _Done())
    assert env_probe._fonts_item()["installed"] is False


def test_chrome_item_ready_when_version_endpoint_responds(monkeypatch):
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://127.0.0.1:9222")
    seen: list[str] = []

    def _fake(url, timeout=None):
        seen.append(url)
        return _FakeResp(b'{"Browser": "Chrome/154.0.8037.98"}')

    monkeypatch.setattr(env_probe.urllib.request, "urlopen", _fake)
    item = env_probe._chrome_item()
    assert item["installed"] is True
    # 版本号从 Browser 字段抠出来，给设置页的版本徽标。
    assert item["version"] == "154.0.8037.98"
    assert item["detail"] == "http://127.0.0.1:9222"
    assert seen == ["http://127.0.0.1:9222/json/version"]


def test_chrome_item_probes_the_ip_resolved_url(monkeypatch):
    """探测地址必须跟抖音拦截同口径换算成 IP（主机名会被 Chrome 拒 Host 头）。"""
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://host.docker.internal:9222")
    seen: list[str] = []

    def _fake(url, timeout=None):
        seen.append(url)
        return _FakeResp(b'{"Browser": "Chrome/154.0.8037.98"}')

    monkeypatch.setattr(env_probe.urllib.request, "urlopen", _fake)
    monkeypatch.setattr(
        env_probe,
        "browsable_cdp_url",
        lambda url: url.replace("host.docker.internal", "192.168.5.2"),
    )
    item = env_probe._chrome_item()
    assert item["installed"] is True
    assert seen == ["http://192.168.5.2:9222/json/version"]


def test_chrome_item_unreachable_when_connection_refused(monkeypatch):
    """宿主 Chrome 没起（最常见的情状）：detail 要能看出是「没人听」而不是只报异常类名。"""
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://127.0.0.1:9222")

    def _boom(url, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(env_probe.urllib.request, "urlopen", _boom)
    item = env_probe._chrome_item()
    assert item["installed"] is False
    assert item["version"] is None
    detail = item["detail"]
    assert "URLError" in detail
    # 底层成因不能被包成“URLError”三个字就完事。
    assert "Connection refused" in detail


def test_chrome_item_detail_distinguishes_dns_failure(monkeypatch):
    """ST_CHROME_CDP_URL 写错 / 非 Docker 部署：成因得与「端口没人听」区分开。"""
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://nope.invalid:9222")
    monkeypatch.setattr(env_probe, "browsable_cdp_url", lambda url: url)

    def _gai(url, timeout=None):
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(env_probe.urllib.request, "urlopen", _gai)
    detail = env_probe._chrome_item()["detail"]
    assert "Name or service not known" in detail


def test_chrome_item_not_green_when_endpoint_rejects_host_header(monkeypatch):
    """TCP 握得上但 Chrome 回 500：不能再亮绿灯（旧纯 TCP 探测的假绿）。"""
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://127.0.0.1:9222")

    def _reject(url, timeout=None):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr(env_probe.urllib.request, "urlopen", _reject)
    item = env_probe._chrome_item()
    assert item["installed"] is False
    assert "HTTP 500" in item["detail"]


def test_chrome_item_not_green_when_port_is_not_chrome(monkeypatch):
    """端口上有人但 /json/version 不回 Browser 字段：不是 Chrome 调试端口。"""
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(
        env_probe.urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(b"{}"),
    )
    item = env_probe._chrome_item()
    assert item["installed"] is False
    assert "Browser" in item["detail"]


class _FakeResp:
    """假 urlopen 响应：只用到上下文管理器与 read()。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_opencv_item_reports_version_when_importable(monkeypatch):
    import sys

    class _FakeCv2:
        __version__ = "4.11.0"

    monkeypatch.setitem(sys.modules, "cv2", _FakeCv2())
    item = env_probe._opencv_item()
    assert item == {
        "id": "opencv",
        "installed": True,
        "version": "4.11.0",
        "detail": None,
    }


def test_opencv_item_missing_when_import_fails(monkeypatch):
    import sys

    # sys.modules 中置 None 会让 `import cv2` 抛 ImportError，模拟未安装。
    monkeypatch.setitem(sys.modules, "cv2", None)
    item = env_probe._opencv_item()
    assert item["id"] == "opencv"
    assert item["installed"] is False
    assert item["version"] is None
    assert item["detail"]


def test_probe_returns_all_dependency_ids(monkeypatch):
    monkeypatch.setattr(env_probe.shutil, "which", lambda name: f"/usr/bin/{name}")

    class _Done:
        returncode = 0
        stdout = "1.2.3\n"
        stderr = ""

    monkeypatch.setattr(env_probe.subprocess, "run", lambda *a, **k: _Done())
    monkeypatch.setattr(
        env_probe.urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(b'{"Browser": "Chrome/154.0.0.0"}'),
    )
    monkeypatch.setattr(env_probe, "_opencv_item", lambda: {"id": "opencv", "installed": True, "version": None, "detail": None})
    items = env_probe.probe()["items"]
    assert [it["id"] for it in items] == ["ffmpeg", "node", "npm", "uv", "fonts", "chrome", "opencv"]


# ---------------------------------------------------------------------- 路由
def _build_client(user):
    app = FastAPI()
    app.include_router(env_setup_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


def test_status_owner_gets_items(monkeypatch):
    monkeypatch.setattr(
        env_probe,
        "probe",
        lambda: {"items": [{"id": "ffmpeg", "installed": True, "version": None, "detail": None}]},
    )
    client = _build_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.get("/api/v1/admin/env-setup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["items"][0]["id"] == "ffmpeg"


def test_status_rejects_member():
    client = _build_client({"id": "u", "username": "bob", "role": "member"})
    assert client.get("/api/v1/admin/env-setup/status").status_code == 403


def test_status_rejects_agent_token():
    client = _build_client(
        {"id": "u", "username": "admin", "role": "owner", "credential_kind": "agent_session"}
    )
    assert client.get("/api/v1/admin/env-setup/status").status_code == 403


# -------------------------------------------------- 自动化安装环境（req-automcp）
def _fake_probe(*items):
    return lambda: {"items": list(items)}


def test_build_plan_classifies_deps_world_and_channel(monkeypatch):
    monkeypatch.setattr(
        env_probe,
        "probe",
        _fake_probe(
            {"id": "ffmpeg", "installed": True, "version": "6.1", "detail": "/usr/bin/ffmpeg"},
            {"id": "node", "installed": False, "version": None, "detail": None},
        ),
    )
    monkeypatch.setattr(
        env_setup_install.world_install,
        "status_snapshot",
        lambda: {
            "install_state": "not_installed",
            "installed": False,
            "can_install": True,
            "installing": False,
        },
    )
    monkeypatch.setattr(
        env_setup_install, "_configured_channel_providers", lambda: ["aipdd", "volcengine"]
    )
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    plan = env_setup_install.build_plan()
    by_id = {it["id"]: it for it in plan["items"]}

    assert by_id["ffmpeg"]["status"] == "ready"
    assert by_id["node"]["status"] == "missing"
    assert by_id["node"]["auto_installable"] is False
    assert by_id["node"]["guide"]  # 系统级依赖给补齐指引
    assert by_id["world"]["status"] == "installable"
    assert by_id["world"]["auto_installable"] is True
    assert by_id["world"]["action"] == "install"
    assert by_id["model_channel"]["status"] == "ready"
    assert "aipdd" in (by_id["model_channel"]["detail"] or "")  # 真源是渠道存档
    summary = plan["summary"]
    assert summary["total"] == len(plan["items"])
    assert summary["ready"] + summary["installable"] + summary["missing"] <= summary["total"]


def test_build_plan_world_not_installable(monkeypatch):
    monkeypatch.setattr(env_probe, "probe", _fake_probe())
    monkeypatch.setattr(
        env_setup_install.world_install,
        "status_snapshot",
        lambda: {"install_state": "not_installed", "installed": False, "can_install": False},
    )
    monkeypatch.setattr(env_setup_install, "_configured_channel_providers", lambda: [])
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    plan = env_setup_install.build_plan()
    by_id = {it["id"]: it for it in plan["items"]}
    assert by_id["world"]["status"] == "missing"
    assert by_id["world"]["auto_installable"] is False
    assert by_id["world"]["action"] is None
    # 未配密钥：model_channel 是指引型待办（非 optional 的 missing）。
    assert by_id["model_channel"]["status"] == "missing"
    assert by_id["model_channel"]["guide"]


def test_build_plan_model_channel_env_fallback(monkeypatch):
    # 渠道存档为空但环境有 MODEL_API_KEY 时，仍判就绪（容器内直连的兼容兜底），且不谎报渠道清单。
    monkeypatch.setattr(env_probe, "probe", _fake_probe())
    monkeypatch.setattr(
        env_setup_install.world_install,
        "status_snapshot",
        lambda: {"install_state": "ready", "installed": True, "can_install": True},
    )
    monkeypatch.setattr(env_setup_install, "_configured_channel_providers", lambda: [])
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "sk-envonly")
    plan = env_setup_install.build_plan()
    by_id = {it["id"]: it for it in plan["items"]}
    assert by_id["model_channel"]["status"] == "ready"
    assert by_id["model_channel"]["detail"] is None


def test_install_item_world_starts(monkeypatch):
    monkeypatch.setattr(
        env_setup_install.world_install,
        "status_snapshot",
        lambda: {"installed": False, "can_install": True},
    )
    monkeypatch.setattr(
        env_setup_install.world_install,
        "start_install",
        lambda: {"install_state": "installing", "installing": True},
    )
    result = env_setup_install.install_item("world")
    assert result["ok"] is True
    assert result["started"] is True
    assert result["status"]["install_state"] == "installing"


def test_install_item_world_cannot_install_is_manual(monkeypatch):
    monkeypatch.setattr(
        env_setup_install.world_install,
        "status_snapshot",
        lambda: {"installed": False, "can_install": False},
    )

    def _boom():  # pragma: no cover - 断言不应被调用
        raise AssertionError("start_install must not run when can_install is False")

    monkeypatch.setattr(env_setup_install.world_install, "start_install", _boom)
    result = env_setup_install.install_item("world")
    assert result["ok"] is True
    assert result["manual"] is True
    assert result.get("started") is None


def test_install_item_manual_returns_guidance_without_touching_state(monkeypatch):
    def _boom():  # pragma: no cover
        raise AssertionError("install_item must not start world install for a system dep")

    monkeypatch.setattr(env_setup_install.world_install, "start_install", _boom)
    result = env_setup_install.install_item("ffmpeg")
    assert result["ok"] is True
    assert result["manual"] is True
    assert result["guide"]


# ------------------------------------------------------------------ plan/install 路由
def _build_plan_client(user):
    app = FastAPI()
    app.include_router(env_setup_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


def test_plan_route_returns_plan(monkeypatch):
    monkeypatch.setattr(
        env_setup_install,
        "build_plan",
        lambda: {
            "items": [{"id": "world", "status": "installable", "auto_installable": True}],
            "summary": {"total": 1, "ready": 0, "installable": 1, "missing": 0, "all_ready": False},
            "world": {"installing": False},
        },
    )
    client = _build_plan_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.get("/api/v1/admin/env-setup/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["items"][0]["id"] == "world"
    assert body["data"]["summary"]["installable"] == 1


def test_plan_route_allows_agent_credential(monkeypatch):
    # req-automcp：MCP 的 st- 密钥（走 get_api_user）要能读计划，不再被 owner 闸门拒。
    monkeypatch.setattr(
        env_setup_install,
        "build_plan",
        lambda: {"items": [], "summary": {"total": 0}, "world": {}},
    )
    client = _build_plan_client(
        {"id": "u", "username": "admin", "role": "owner", "credential_kind": "agent_session"}
    )
    assert client.get("/api/v1/admin/env-setup/plan").status_code == 200


def test_install_route_posts_item(monkeypatch):
    seen: list[str] = []

    def _fake_install(item):
        seen.append(item)
        return {"ok": True, "started": True, "item": item, "status": {"install_state": "installing"}}

    monkeypatch.setattr(env_setup_install, "install_item", _fake_install)
    client = _build_plan_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.post("/api/v1/admin/env-setup/install", json={"item": "world"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["started"] is True
    assert seen == ["world"]


# ------------------------------------------------------------- self-heal 路由
def test_self_heal_route_returns_data(monkeypatch):
    seen: list[dict] = []

    def _fake_run(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "degraded": False, "analysis": {"diagnosis": "网关不通"}}

    monkeypatch.setattr(env_setup_routes.self_heal, "run_self_heal", _fake_run)
    client = _build_plan_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.post(
        "/api/v1/admin/self-heal",
        json={
            "symptom": "生成草图 500",
            "tool_name": "aipdd_drama_generate_sketches",
            "error": "HTTP 500",
            "mode": "repair",
            "scope": "environment",
            "allow_heavy": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["analysis"]["diagnosis"] == "网关不通"
    assert seen == [
        {
            "symptom": "生成草图 500",
            "tool_name": "aipdd_drama_generate_sketches",
            "error": "HTTP 500",
            "scope": "environment",
            "mode": "repair",
            "model": "",
            "allow_heavy": True,
        }
    ]


def test_self_heal_route_allows_agent_credential(monkeypatch):
    monkeypatch.setattr(
        env_setup_routes.self_heal,
        "run_self_heal",
        lambda **_: {"ok": True, "degraded": True},
    )
    client = _build_plan_client(
        {"id": "u", "username": "admin", "role": "owner", "credential_kind": "agent_session"}
    )
    resp = client.post("/api/v1/admin/self-heal", json={"symptom": "失败"})
    assert resp.status_code == 200


def test_self_heal_route_requires_symptom():
    client = _build_plan_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.post("/api/v1/admin/self-heal", json={})
    assert resp.status_code == 400
    assert "symptom" in resp.json()["detail"]


def test_self_heal_route_maps_value_error_to_400(monkeypatch):
    def _fake_run(**_):
        raise ValueError("mode must be 'diagnose' or 'repair'")

    monkeypatch.setattr(env_setup_routes.self_heal, "run_self_heal", _fake_run)
    client = _build_plan_client({"id": "u", "username": "admin", "role": "owner"})
    resp = client.post("/api/v1/admin/self-heal", json={"symptom": "失败", "mode": "bogus"})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]
