# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] AIPDD Drama MCP 视频配音工具 ``aipdd_drama_video_dubbing``。

工具经内部 HTTP ``_request`` 打既有 freezone 视频配音路由，本测试零网络：打桩
``_request`` 记录 method/path/body 并回 canned dict，验证打对的路由、language/model/
asr_model 透传、参数缺失与上游异常的 ``{ok:false}`` 结构化错误，以及 schema 注册在列。
"""

from __future__ import annotations

import json

from novelvideo.chat import dramaclaw_tools as dt

TOOL_NAME = "aipdd_drama_video_dubbing"


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
    assert set(schema["parameters"]["required"]) >= {"source_url", "reference_audio_url"}
    props = schema["parameters"]["properties"]
    assert {
        "source_url",
        "reference_audio_url",
        "language",
        "model",
        "asr_model",
        "reference_name",
    } <= set(props)


def test_hits_video_dubbing_route(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"ok": True, "data": {"status": "queued", "job_id": "job-1"}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = dt._handle_video_dubbing(
        {
            "project_id": "proj_demo",
            "source_url": "/static/p/clip.mp4",
            "reference_audio_url": "/static/p/ref.wav",
            "language": "vi",
            "model": "doubao-seed-audio-1-0",
            "asr_model": "gateway:volc:whisper",
        }
    )
    assert json.loads(out)["data"]["job_id"] == "job-1"
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/v1/projects/proj_demo/freezone/audio/video-dubbing"
    body = call["body"]
    assert body["source_url"] == "/static/p/clip.mp4"
    assert body["reference_audio_url"] == "/static/p/ref.wav"
    assert body["language"] == "vi"
    assert body["model"] == "doubao-seed-audio-1-0"
    assert body["asr_model"] == "gateway:volc:whisper"
    assert body["source_tool"] == "video-dubbing"


def test_missing_source_returns_error() -> None:
    out = dt._handle_video_dubbing(
        {"project_id": "p", "reference_audio_url": "/static/p/ref.wav"}
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "source_url" in parsed["error"]


def test_missing_reference_returns_error() -> None:
    out = dt._handle_video_dubbing({"project_id": "p", "source_url": "/static/p/c.mp4"})
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "reference_audio_url" in parsed["error"]


def test_upstream_exception_is_not_swallowed(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("上游 500")

    monkeypatch.setattr(dt, "_request", boom)
    out = dt._handle_video_dubbing(
        {
            "project_id": "p",
            "source_url": "/static/p/c.mp4",
            "reference_audio_url": "/static/p/ref.wav",
        }
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "上游 500" in parsed["error"]
