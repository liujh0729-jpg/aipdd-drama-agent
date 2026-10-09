"""[aipdd fork delta] Tests for the vectcut_* tool group in the dramaclaw MCP plugin.

The VectCut editing service (services/vectcut) is exercised separately; these
tests pin the plugin-side contract: registration, availability gating, media
resolution, and the upstream request bodies built by each handler.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_plugin_module():
    # P5 薄壳化：.hermes/plugins/dramaclaw 现在是薄壳（Hermes 发现面），
    # 先验证其可独立加载、工具表可用。
    shell_path = (
        Path(__file__).resolve().parents[1]
        / ".hermes" / "plugins" / "dramaclaw" / "__init__.py"
    )
    shell_spec = importlib.util.spec_from_file_location("test_vectcut_shell", shell_path)
    assert shell_spec is not None and shell_spec.loader is not None
    shell = importlib.util.module_from_spec(shell_spec)
    shell_spec.loader.exec_module(shell)
    assert shell.TOOLS

    # 实现真身（与 MCP 桥同一份源码）：按路径独立装载为新模块对象，并把
    # tool_result/tool_error 还原为 identity 语义（历史上由 tools.registry
    # shim 注入；mock 成直通便于直接观测结构化结果）。
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_vectcut_plugin", impl_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


VECTCUT_TOOL_NAMES = {
    "vectcut_health",
    "vectcut_list_options",
    "vectcut_create_draft",
    "vectcut_add_video",
    "vectcut_add_image",
    "vectcut_add_audio",
    "vectcut_add_text",
    "vectcut_add_subtitle",
    "vectcut_add_effect",
    "vectcut_add_sticker",
    "vectcut_add_keyframe",
    "vectcut_get_duration",
    "vectcut_save_draft",
}


def test_vectcut_tool_group_registered_with_self_consistent_schemas():
    plugin = _load_plugin_module()
    vectcut_tools = [item for item in plugin.TOOLS if item[0].startswith("vectcut_")]

    assert {item[0] for item in vectcut_tools} == VECTCUT_TOOL_NAMES
    for name, schema, handler in vectcut_tools:
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} not in properties"


def test_vectcut_tools_bypass_main_api_credentials():
    plugin = _load_plugin_module()
    registered = []

    class FakeCtx:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(FakeCtx())

    by_name: dict[str, list[dict]] = {}
    for kwargs in registered:
        by_name.setdefault(kwargs["name"], []).append(kwargs)
    assert set(by_name) == {item[0] for item in plugin.TOOLS}

    for name, entries in by_name.items():
        for kwargs in entries:
            if name.startswith("vectcut_"):
                assert kwargs["check_fn"] is plugin._vectcut_available
                assert kwargs["requires_env"] == []
            else:
                assert kwargs["check_fn"] is plugin._available
                assert kwargs["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]


def test_vectcut_available_is_opt_out_only(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.delenv("VECTCUT_ENABLED", raising=False)
    assert plugin._vectcut_available() is True

    for raw in ("0", "false", "NO", "off"):
        monkeypatch.setenv("VECTCUT_ENABLED", raw)
        assert plugin._vectcut_available() is False


def test_vectcut_base_url_default_and_override(monkeypatch):
    """[req41] 地址只认配置：没配置不给 localhost 兜底，有配置则去掉尾斜杠。"""

    plugin = _load_plugin_module()
    monkeypatch.delenv("VECTCUT_BASE_URL", raising=False)
    assert plugin._vectcut_base_url() == ""

    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut:9001/")
    assert plugin._vectcut_base_url() == "http://vectcut:9001"


def test_vectcut_call_without_address_reports_not_configured(monkeypatch):
    """[req41] 未配地址时必须说“没配置”，不能把容器自己的 127.0.0.1:9001 当成服务。"""

    plugin = _load_plugin_module()
    monkeypatch.delenv("VECTCUT_BASE_URL", raising=False)
    result = plugin._vectcut_call("GET", "/aipdd/drafts")
    assert result["success"] is False
    assert "VECTCUT_BASE_URL" in result["error"]
    assert "127.0.0.1:9001" not in plugin._vectcut_base_url()


def test_vectcut_unreachable_hint_points_to_start_script(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut:9001")
    hint = plugin._vectcut_unreachable_hint()

    assert "cannot reach the VectCut service" in hint
    assert "start-vectcut.sh" in hint
    assert "VECTCUT_BASE_URL" in hint


def test_vectcut_body_resolves_media_and_drops_blank_values(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_parse_media(value, project_dir, *, allow_raw=False):
        calls.append((value, project_dir, allow_raw))
        return "/resolved/beat_01.png"

    monkeypatch.setattr(plugin, "_vectcut_parse_media", fake_parse_media)
    body = plugin._vectcut_body(
        {
            "image_url": "/static/projects/p/frames/ep001/beat_01.png",
            "draft_id": "dfd_1",
            "project_dir": "test",
            "start": None,
            "transition": "",
        },
        ("image_url",),
    )

    assert calls == [("/static/projects/p/frames/ep001/beat_01.png", "test", False)]
    assert body == {"image_url": "/resolved/beat_01.png", "draft_id": "dfd_1"}


def test_vectcut_parse_media_passes_raw_only_when_invalid(monkeypatch):
    plugin = _load_plugin_module()
    srt_text = "1\n00:00:00,000 --> 00:00:01,500\n你好"

    def fake_invalid(method, path, *, body=None):
        return {"success": False, "code": "invalid", "error": "not a media reference"}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_invalid)
    assert plugin._vectcut_parse_media(srt_text, None, allow_raw=True) == srt_text
    with pytest.raises(ValueError):
        plugin._vectcut_parse_media(srt_text, None)

    def fake_ambiguous(method, path, *, body=None):
        return {"success": False, "code": "ambiguous", "error": "ambiguous: 2 matches"}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_ambiguous)
    with pytest.raises(ValueError):
        plugin._vectcut_parse_media("/static/projects/p/a.png", None, allow_raw=True)


def test_vectcut_add_image_resolves_static_url_then_posts(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_call(method, path, *, body=None):
        calls.append((method, path, body))
        if path == "/aipdd/resolve_media":
            return {"success": True, "path": "/data/output/local/test/frames/ep001/beat_01.png"}
        return {"success": True, "output": {"draft_id": "dfd_1"}}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_call)
    plugin._handle_vectcut_add_image(
        {
            "image_url": "/static/projects/abc/frames/ep001/beat_01.png",
            "draft_id": "dfd_1",
            "start": 0,
            "end": 3.2,
        }
    )

    assert calls[0] == (
        "POST",
        "/aipdd/resolve_media",
        {"reference": "/static/projects/abc/frames/ep001/beat_01.png"},
    )
    assert calls[1] == (
        "POST",
        "/add_image",
        {
            "image_url": "/data/output/local/test/frames/ep001/beat_01.png",
            "draft_id": "dfd_1",
            "start": 0,
            "end": 3.2,
        },
    )

    plugin._handle_vectcut_add_image(
        {
            "image_url": "/static/projects/abc/frames/ep001/beat_01.png",
            "draft_id": "dfd_1",
            "project_dir": "test",
        }
    )
    assert calls[2] == (
        "POST",
        "/aipdd/resolve_media",
        {
            "reference": "/static/projects/abc/frames/ep001/beat_01.png",
            "project_dir": "test",
        },
    )


def test_vectcut_add_subtitle_passes_raw_srt_text(monkeypatch):
    plugin = _load_plugin_module()
    calls = []
    srt_text = "1\n00:00:00,000 --> 00:00:01,500\n你好"

    def fake_call(method, path, *, body=None):
        calls.append((method, path, body))
        if path == "/aipdd/resolve_media":
            return {"success": False, "code": "invalid", "error": "not a media reference"}
        return {"success": True, "output": {"draft_id": "dfd_1"}}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_call)
    plugin._handle_vectcut_add_subtitle({"srt": srt_text, "draft_id": "dfd_1"})

    assert calls[-1] == ("POST", "/add_subtitle", {"srt": srt_text, "draft_id": "dfd_1"})


def test_vectcut_add_sticker_requires_sticker_id():
    plugin = _load_plugin_module()
    result = plugin._handle_vectcut_add_sticker({"draft_id": "dfd_1", "start": 0, "end": 1})

    assert "missing required field(s): sticker_id" in result


def test_vectcut_add_keyframe_requires_property_type():
    plugin = _load_plugin_module()
    result = plugin._handle_vectcut_add_keyframe({"draft_id": "dfd_1"})

    assert "property_type" in result


def test_vectcut_save_draft_defaults_auto_deploy(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_call(method, path, *, body=None):
        calls.append((method, path, body))
        return {"success": True, "output": {"draft_url": "/drafts/dfd_1"}}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_call)
    plugin._handle_vectcut_save_draft({"draft_id": "dfd_1"})
    assert calls[-1] == ("POST", "/aipdd/save_draft", {"draft_id": "dfd_1", "auto_deploy": True})

    plugin._handle_vectcut_save_draft({"draft_id": "dfd_1", "auto_deploy": False})
    assert calls[-1][2]["auto_deploy"] is False


def test_vectcut_get_duration_maps_video_url_to_url(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_call(method, path, *, body=None):
        calls.append((method, path, body))
        if path == "/aipdd/resolve_media":
            return {"success": True, "path": "/data/output/local/test/videos/ep001/beat_01.mp4"}
        return {"success": True, "output": {"duration": 3.2}}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_call)
    plugin._handle_vectcut_get_duration(
        {"video_url": "/data/output/local/test/videos/ep001/beat_01.mp4"}
    )

    assert calls[-1] == (
        "POST",
        "/get_duration",
        {"url": "/data/output/local/test/videos/ep001/beat_01.mp4"},
    )


def test_vectcut_health_returns_service_payload(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setattr(
        plugin,
        "_vectcut_call",
        lambda method, path, *, body=None: {
            "success": True,
            "draft_dir": "/drafts",
            "platform": "linux",
        },
    )

    payload = plugin._handle_vectcut_health({})

    assert payload["draft_dir"] == "/drafts"


def test_vectcut_list_options_rejects_unknown_kind():
    plugin = _load_plugin_module()
    result = plugin._handle_vectcut_list_options({"kind": "not_a_kind"})

    assert "unknown kind" in result


def test_vectcut_list_options_maps_kind_to_endpoint(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_call(method, path, *, body=None):
        calls.append((method, path))
        return {"success": True, "output": []}

    monkeypatch.setattr(plugin, "_vectcut_call", fake_call)
    plugin._handle_vectcut_list_options({"kind": "Transition"})

    assert calls == [("GET", "/get_transition_types")]