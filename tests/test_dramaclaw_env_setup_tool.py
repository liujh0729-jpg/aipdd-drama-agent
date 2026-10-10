# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta req-automcp] AIPDD Drama MCP 环境自动安装工具 ``aipdd_drama_env_setup``。

工具经内部 HTTP ``_request`` 打既有 /admin/env-setup/{plan,install} 路由，本测试零网络：
打桩 ``_request`` 记录 method/path/body 并回 canned dict，验证 check→GET plan、install→
POST install(带 item)、缺 item 的结构化错误、上游异常的 ``{ok:false}``，以及 schema 注册在列。
"""

from __future__ import annotations

import json

from novelvideo.chat import dramaclaw_tools as dt

TOOL_NAME = "aipdd_drama_env_setup"


def _tool_entry():
    for entry in dt.TOOLS:
        if entry[0] == TOOL_NAME:
            return entry
    raise AssertionError(f"{TOOL_NAME} 未注册到 TOOLS")


def test_tool_registered_with_schema() -> None:
    name, schema, handler = _tool_entry()
    assert name == TOOL_NAME
    assert callable(handler)
    assert schema["name"] == TOOL_NAME
    props = schema["parameters"]["properties"]
    assert {"action", "item"} <= set(props)
    assert props["action"]["enum"] == ["check", "install"]


def test_check_action_hits_plan_route(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"ok": True, "data": {"items": [], "summary": {"total": 0}}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = json.loads(dt._handle_env_setup({"action": "check"}))
    assert out["ok"] is True
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/api/v1/admin/env-setup/plan"


def test_default_action_is_check(monkeypatch) -> None:
    seen: list[str] = []

    def fake_request(method, path, *, query=None, body=None):
        seen.append(path)
        return {"ok": True, "data": {"items": []}}

    monkeypatch.setattr(dt, "_request", fake_request)
    dt._handle_env_setup({})
    assert seen == ["/api/v1/admin/env-setup/plan"]


def test_install_action_posts_item(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"ok": True, "data": {"started": True}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = json.loads(dt._handle_env_setup({"action": "install", "item": "world"}))
    assert out["ok"] is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/v1/admin/env-setup/install"
    assert calls[0]["body"] == {"item": "world"}


def test_install_without_item_is_structured_error() -> None:
    out = json.loads(dt._handle_env_setup({"action": "install"}))
    assert out["ok"] is False
    assert "item" in out["error"].lower()


def test_unknown_action_is_structured_error() -> None:
    out = json.loads(dt._handle_env_setup({"action": "bogus"}))
    assert out["ok"] is False
    assert "action" in out["error"].lower()
