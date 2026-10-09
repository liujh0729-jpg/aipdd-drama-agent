# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta C] AIPDD Drama MCP 落片工具 ``aipdd_drama_commit_white_model_video``。

工具经内部 HTTP ``_request`` 打既有 freezone Blender 路由，本测试零网络：打桩 ``_request``
记录 method/path/body 并回 canned dict，验证 from_blender / video_url 两分支打对的路由、
commit_slot 透传、参数缺失与上游异常的 ``{ok:false}`` 结构化错误，以及 schema 注册在列。
"""

from __future__ import annotations

import json

from novelvideo.chat import dramaclaw_tools as dt

TOOL_NAME = "aipdd_drama_commit_white_model_video"


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
    assert set(schema["parameters"]["required"]) >= {"canvas_id", "node_id"}
    props = schema["parameters"]["properties"]
    assert {"canvas_id", "node_id", "from_blender", "video_url", "commit_slot"} <= set(props)


def test_from_blender_hits_pull_route(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"ok": True, "data": {"status": "pulled", "url": "/static/projects/p/x.mp4"}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = dt._handle_commit_white_model_video(
        {
            "project_id": "proj_demo",
            "canvas_id": "default",
            "node_id": "wm1",
            "from_blender": True,
            "commit_slot": True,
        }
    )
    assert json.loads(out)["data"]["status"] == "pulled"
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/v1/projects/proj_demo/freezone/blender/pull-white-model-video"
    assert call["body"] == {"canvas_id": "default", "node_id": "wm1", "commit_slot": True}
    assert "url" not in call["body"]


def test_video_url_hits_ingest_route(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"ok": True, "data": {"status": "ingested", "url": "/static/projects/p/y.mp4"}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = dt._handle_commit_white_model_video(
        {
            "project_id": "proj_demo",
            "canvas_id": "default",
            "node_id": "wm1",
            "video_url": "https://cdn.example.com/v.mp4",
        }
    )
    assert json.loads(out)["data"]["status"] == "ingested"
    call = calls[0]
    assert call["path"] == "/api/v1/projects/proj_demo/freezone/blender/ingest-white-model-video"
    assert call["body"]["url"] == "https://cdn.example.com/v.mp4"
    assert call["body"]["commit_slot"] is False


def test_missing_canvas_or_node_returns_error() -> None:
    out = dt._handle_commit_white_model_video({"project_id": "p", "node_id": "wm1"})
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "canvas_id" in parsed["error"]


def test_no_source_returns_error() -> None:
    out = dt._handle_commit_white_model_video(
        {"project_id": "p", "canvas_id": "default", "node_id": "wm1"}
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "from_blender" in parsed["error"] or "video_url" in parsed["error"]


def test_upstream_exception_is_not_swallowed(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("上游 500")

    monkeypatch.setattr(dt, "_request", boom)
    out = dt._handle_commit_white_model_video(
        {
            "project_id": "p",
            "canvas_id": "default",
            "node_id": "wm1",
            "video_url": "https://cdn.example.com/v.mp4",
        }
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "上游 500" in parsed["error"]
