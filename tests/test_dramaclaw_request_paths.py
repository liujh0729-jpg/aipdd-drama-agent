# SPDX-License-Identifier: Elastic-2.0
"""MCP 工具出站路径：中文/空格必须转义，但已有编码与查询分隔符不能被破坏。"""

from __future__ import annotations

import json

import pytest

from novelvideo.chat import dramaclaw_tools as dt


def test_normalize_api_path_quotes_unicode_and_spaces() -> None:
    assert (
        dt._normalize_api_path("/api/v1/projects/我的 剧集/export")
        == "/api/v1/projects/%E6%88%91%E7%9A%84%20%E5%89%A7%E9%9B%86/export"
    )


def test_normalize_api_path_preserves_existing_percent_encoding() -> None:
    path = "/api/v1/projects/%E5%89%A7%E9%9B%86/export"
    assert dt._normalize_api_path(path) == path


def test_normalize_api_path_still_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        dt._normalize_api_path("/api/v1/projects/../secrets/export")


def test_export_dir_sanitizes_project_name() -> None:
    path = dt._export_dir("../../evil/name")
    assert path.parent.name == "exports"
    assert ".." not in path.parts
    assert path.name == "evil_name"


def test_generic_passthrough_truncates_oversized_bodies(monkeypatch) -> None:
    huge = {"ok": True, "data": {"rows": ["x" * 1000] * 400}}
    monkeypatch.setattr(dt, "_request", lambda *args, **kwargs: huge)

    out = json.loads(dt._handle_get({"path": "/api/v1/big"}))

    assert out["truncated"] is True
    assert out["ok"] is True
    assert out["truncated_chars"] > dt._GENERIC_BODY_MAX_CHARS
    assert len(out["preview"]) == dt._GENERIC_BODY_MAX_CHARS
    assert "limit" in out["notice"]


def test_truncated_generic_body_preserves_failure_status(monkeypatch) -> None:
    huge = {
        "ok": False,
        "status_code": 500,
        "error": "boom",
        "data": {"rows": ["y" * 1000] * 400},
    }
    monkeypatch.setattr(dt, "_request", lambda *args, **kwargs: huge)

    out = json.loads(dt._handle_get({"path": "/api/v1/big"}))

    assert out["truncated"] is True
    assert out["ok"] is False
    assert out["status_code"] == 500
    assert out["error"] == "boom"


def test_generic_passthrough_keeps_small_bodies_intact(monkeypatch) -> None:
    small = {"ok": True, "data": [1, 2, 3]}
    monkeypatch.setattr(dt, "_request", lambda *args, **kwargs: small)

    for handler in (dt._handle_get, dt._handle_post, dt._handle_patch, dt._handle_delete):
        assert json.loads(handler({"path": "/api/v1/small"})) == small
