# SPDX-License-Identifier: Elastic-2.0
"""[aipdd fork delta] MCP 自愈工具 ``aipdd_drama_self_heal`` 的 schema 与路由映射。"""

from __future__ import annotations

import json

from novelvideo.chat import dramaclaw_tools as dt

TOOL_NAME = "aipdd_drama_self_heal"


def _tool_entry():
    for entry in dt.TOOLS:
        if entry[0] == TOOL_NAME:
            return entry
    raise AssertionError(f"{TOOL_NAME} 未注册到 TOOLS")


def test_tool_registered_with_schema() -> None:
    name, schema, handler = _tool_entry()
    assert name == TOOL_NAME
    assert callable(handler)
    props = schema["parameters"]["properties"]
    assert {"symptom", "tool_name", "error", "scope", "mode", "model", "allow_heavy"} <= set(props)
    assert schema["parameters"]["required"] == ["symptom"]
    assert props["mode"]["enum"] == ["diagnose", "repair"]
    assert props["scope"]["enum"] == ["auto", "environment", "code"]


def test_diagnose_posts_symptom_and_defaults(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(
        method, path, *, query=None, body=None, timeout=None, self_heal_hint=True
    ):
        calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "timeout": timeout,
                "self_heal_hint": self_heal_hint,
            }
        )
        return {"ok": True, "data": {"degraded": False}}

    monkeypatch.setattr(dt, "_request", fake_request)
    out = json.loads(
        dt._handle_self_heal(
            {
                "symptom": "生成草图返回 HTTP 500",
                "tool_name": "aipdd_drama_generate_sketches",
                "error": "Traceback ...",
            }
        )
    )

    assert out["ok"] is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/v1/admin/self-heal"
    assert calls[0]["body"] == {
        "symptom": "生成草图返回 HTTP 500",
        "mode": "diagnose",
        "scope": "auto",
        "tool_name": "aipdd_drama_generate_sketches",
        "error": "Traceback ...",
    }
    assert calls[0]["timeout"] == 180
    assert calls[0]["self_heal_hint"] is False


def test_repair_mode_and_allow_heavy_are_forwarded(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_request(
        method, path, *, query=None, body=None, timeout=None, self_heal_hint=True
    ):
        seen.append(body)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(dt, "_request", fake_request)
    dt._handle_self_heal(
        {
            "symptom": "环境缺 world",
            "mode": "repair",
            "scope": "environment",
            "allow_heavy": True,
            "model": "custom-text-model",
        }
    )

    assert seen[0]["mode"] == "repair"
    assert seen[0]["scope"] == "environment"
    assert seen[0]["allow_heavy"] is True
    assert seen[0]["model"] == "custom-text-model"


def test_allow_heavy_string_false_is_not_enabled(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_request(
        method, path, *, query=None, body=None, timeout=None, self_heal_hint=True
    ):
        seen.append(body)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(dt, "_request", fake_request)
    dt._handle_self_heal({"symptom": "环境缺 world", "mode": "repair", "allow_heavy": "false"})
    assert "allow_heavy" not in seen[0]


def test_allow_heavy_omitted_by_default(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_request(
        method, path, *, query=None, body=None, timeout=None, self_heal_hint=True
    ):
        seen.append(body)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(dt, "_request", fake_request)
    dt._handle_self_heal({"symptom": "失败"})
    assert "allow_heavy" not in seen[0]


def test_missing_symptom_is_structured_error() -> None:
    out = json.loads(dt._handle_self_heal({}))
    assert out["ok"] is False
    assert "symptom" in out["error"].lower()


def test_invalid_mode_and_scope_are_structured_errors() -> None:
    mode = json.loads(dt._handle_self_heal({"symptom": "x", "mode": "bogus"}))
    assert mode["ok"] is False
    assert "mode" in mode["error"].lower()

    scope = json.loads(dt._handle_self_heal({"symptom": "x", "scope": "bogus"}))
    assert scope["ok"] is False
    assert "scope" in scope["error"].lower()


def test_upstream_failure_passes_through(monkeypatch) -> None:
    monkeypatch.setattr(
        dt,
        "_request",
        lambda *args, **kwargs: {"ok": False, "status_code": 502, "error": "bad gateway"},
    )
    out = json.loads(dt._handle_self_heal({"symptom": "失败"}))
    assert out["ok"] is False
    assert out["status_code"] == 502
    assert out["error"] == "bad gateway"


def test_failure_hint_points_to_self_heal() -> None:
    out = dt._with_chat_error_hints({"ok": False, "status_code": 500, "error": "boom"})
    assert out["self_heal"]["tool"] == TOOL_NAME
    assert "aipdd_drama_self_heal" in out["self_heal"]["instruction"]

    ok = dt._with_chat_error_hints({"ok": True, "data": {"result": 1}})
    assert "self_heal" not in ok


def test_nested_failures_do_not_duplicate_self_heal_hint() -> None:
    out = dt._with_chat_error_hints(
        {
            "ok": False,
            "status_code": 500,
            "error": "outer",
            "data": [{"ok": False, "status_code": 500, "error": "inner"}],
        }
    )

    assert "self_heal" in out
    assert "self_heal" not in out["data"][0]


def test_prereq_failure_prefers_user_action_over_self_heal() -> None:
    out = dt._with_chat_error_hints(
        {
            "ok": False,
            "status_code": 200,
            "code": "voice_prereq_required",
            "error": "Beat 03 解说声线缺失",
        }
    )

    assert "chat_error" in out
    assert "self_heal" not in out


def test_auth_failure_is_not_offered_to_self_heal() -> None:
    out = dt._with_chat_error_hints(
        {"ok": False, "status_code": 401, "error": "invalid token"}
    )
    assert "self_heal" not in out


def test_vectcut_success_false_counts_as_failure() -> None:
    assert dt._looks_like_failure({"success": False, "error": "save_draft failed"}) is True
    out = dt._with_chat_error_hints(
        {"success": False, "error": "save_draft failed: media file missing"}
    )
    assert out["self_heal"]["tool"] == TOOL_NAME


def test_caller_fixable_4xx_is_not_offered_to_self_heal() -> None:
    for code in (400, 404, 409, 422):
        out = dt._with_chat_error_hints(
            {"ok": False, "status_code": code, "error": f"HTTP {code}"}
        )
        assert "self_heal" not in out, code


def test_rate_limit_and_server_errors_still_offer_self_heal() -> None:
    rate_limited = dt._with_chat_error_hints(
        {"ok": False, "status_code": 429, "error": "quota exhausted"}
    )
    assert rate_limited["self_heal"]["tool"] == TOOL_NAME

    server_error = dt._with_chat_error_hints(
        {"ok": False, "status_code": 503, "error": "upstream down"}
    )
    assert server_error["self_heal"]["tool"] == TOOL_NAME


def test_network_error_points_to_service_not_self_heal() -> None:
    out = dt._with_chat_error_hints(
        {"ok": False, "error": "network_error: [WinError 10061] refused"}
    )

    assert "service_unreachable" in out
    assert "self_heal" not in out
    assert TOOL_NAME in out["service_unreachable"]["instruction"]


def test_tool_error_output_gets_self_heal_hint() -> None:
    text = dt.tool_error("HTTP 500: backend exploded")
    out = json.loads(dt.hint_tool_output(text, tool_name="aipdd_drama_get"))

    assert out["self_heal"]["tool"] == TOOL_NAME


def test_self_heal_own_error_gets_no_recursive_hint() -> None:
    text = dt.tool_error("默认文本模型不可用")
    out = json.loads(dt.hint_tool_output(text, tool_name=TOOL_NAME))

    assert "self_heal" not in out


def test_hint_tool_output_leaves_non_json_untouched() -> None:
    assert dt.hint_tool_output("plain text", tool_name="aipdd_drama_get") == "plain text"
