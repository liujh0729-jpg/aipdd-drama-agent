"""[aipdd fork delta] Tests for the canvas image/video/audio/text/resource MCP tool groups.

从画布与工具页提炼而来的 26 个结构化工具：生图 9（generate/edit/upscale/outpaint/
relight/multi-view/template-edit/redraw/reverse-prompt）、生视频 6（generate/i2v/
omni-gen/upscale/edit/extend）、音频 2（separate/music）、文本脚本 5（generate/
translate/story-script/video-script-reverse/media-copywrite）、资源目录读 4（assets/
character-library/audio-references/models）。钉住注册、门禁、必填校验、上游请求体
形状与 GET 路径映射。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_impl():
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_canvas_mcp_impl", impl_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


IMAGE_TOOL_NAMES = {
    "aipdd_drama_image_generate",
    "aipdd_drama_image_edit",
    "aipdd_drama_image_upscale",
    "aipdd_drama_image_outpaint",
    "aipdd_drama_image_relight",
    "aipdd_drama_image_multi_view",
    "aipdd_drama_image_template_edit",
    "aipdd_drama_image_redraw",
    "aipdd_drama_image_reverse_prompt",
}
VIDEO_TOOL_NAMES = {
    "aipdd_drama_video_generate",
    "aipdd_drama_video_i2v",
    "aipdd_drama_video_omni_gen",
    "aipdd_drama_video_upscale",
    "aipdd_drama_video_edit",
    "aipdd_drama_video_extend",
}
AUDIO_TOOL_NAMES = {"aipdd_drama_audio_separate", "aipdd_drama_audio_music"}
TEXT_TOOL_NAMES = {
    "aipdd_drama_text_generate",
    "aipdd_drama_text_translate",
    "aipdd_drama_story_script_generate",
    "aipdd_drama_video_script_reverse",
    "aipdd_drama_media_copywrite",
}
RESOURCE_TOOL_NAMES = {
    "aipdd_drama_list_assets",
    "aipdd_drama_get_character_library",
    "aipdd_drama_get_audio_references",
    "aipdd_drama_get_models",
}

ALL_NEW = IMAGE_TOOL_NAMES | VIDEO_TOOL_NAMES | AUDIO_TOOL_NAMES | TEXT_TOOL_NAMES | RESOURCE_TOOL_NAMES

# 只有这些工具的端点接受 archive_to_library，其余（纯生成/读）不入库。
ARCHIVING_TOOL_NAMES = {
    "aipdd_drama_image_edit",
    "aipdd_drama_video_omni_gen",
    "aipdd_drama_video_edit",
}


def test_all_new_tools_registered_with_self_consistent_schemas():
    plugin = _load_impl()
    tools = [item for item in plugin.TOOLS if item[0] in ALL_NEW]

    assert {item[0] for item in tools} == ALL_NEW
    assert len(tools) == len(ALL_NEW) == 26
    for name, schema, handler in tools:
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} not in properties"
        if name in ARCHIVING_TOOL_NAMES:
            assert "archive_to_library" in params["properties"], name


def test_new_tools_require_main_api_credentials():
    plugin = _load_impl()
    registered = []

    class FakeCtx:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(FakeCtx())

    matched = [kwargs for kwargs in registered if kwargs["name"] in ALL_NEW]
    assert {kwargs["name"] for kwargs in matched} == ALL_NEW
    for kwargs in matched:
        assert kwargs["check_fn"] is plugin._available
        assert kwargs["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]


def test_every_handler_requires_project(monkeypatch):
    plugin = _load_impl()
    monkeypatch.delenv("DRAMACLAW_PROJECT_ID", raising=False)
    handlers = {
        "image_generate": plugin._handle_image_generate,
        "video_generate": plugin._handle_video_generate,
        "list_assets": plugin._handle_list_assets,
        "get_models": plugin._handle_get_models,
        "media_copywrite": plugin._handle_media_copywrite,
    }
    for label, fn in handlers.items():
        # Provide the minimal required args so project resolution is the first failure.
        args = {"prompt": "hi", "kind": "image", "source_type": "link", "url": "/x"}
        result = fn(args)
        assert "project_id is required" in result, label


def _capture(plugin, monkeypatch):
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    return calls


def test_image_generate_fills_model_id_and_trims_references(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)

    plugin._handle_image_generate(
        {
            "project_id": "demo",
            "prompt": "a cat",
            "model": "gpt-image-2",
            "reference_urls": ["  /static/admin/demo/a.png  ", "", "/static/admin/demo/b.png"],
        }
    )

    method, path, _q, body = calls[0]
    assert method == "POST"
    assert path == "/api/v1/projects/demo/freezone/gen"
    assert body["prompt"] == "a cat"
    assert body["model"] == "gpt-image-2"
    assert body["model_id"] == "gpt-image-2"
    assert body["reference_urls"] == ["/static/admin/demo/a.png", "/static/admin/demo/b.png"]


def test_image_generate_rejects_empty_prompt(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_image_generate({"project_id": "demo", "prompt": "  "})
    assert "prompt is required" in result


def test_image_edit_defaults_archive_true_and_validates_prompt(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)

    plugin._handle_image_edit(
        {
            "project_id": "demo",
            "base_url": "/static/admin/demo/base.png",
            "prompt": "clean it",
            "extra_reference_urls": ["/static/admin/demo/ref.png"],
        }
    )
    _m, path, _q, body = calls[0]
    assert path == "/api/v1/projects/demo/freezone/edit"
    assert body["base_url"] == "/static/admin/demo/base.png"
    assert body["archive_to_library"] is True
    assert body["extra_reference_urls"] == ["/static/admin/demo/ref.png"]

    # prompt missing → error, no second request fired.
    err = plugin._handle_image_edit({"project_id": "demo", "base_url": "/static/x.png"})
    assert "prompt is required" in err


def test_image_edit_requires_base_url(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_image_edit({"project_id": "demo", "prompt": "p"})
    assert "base_url is required" in result


def test_template_edit_requires_mode(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_image_template_edit({"project_id": "demo", "source_url": "/static/a.png"})
    assert "mode is required" in result


def test_image_upscale_and_outpaint_hit_expected_paths(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)

    plugin._handle_image_upscale({"project_id": "demo", "source_url": "/static/a.png", "scale_factor": 4})
    plugin._handle_image_outpaint(
        {"project_id": "demo", "source_url": "/static/a.png", "target_aspect_ratio": "16:9"}
    )

    assert calls[0][1] == "/api/v1/projects/demo/freezone/upscale"
    assert calls[0][3]["scale_factor"] == 4
    assert calls[1][1] == "/api/v1/projects/demo/freezone/outpaint"
    assert calls[1][3]["target_aspect_ratio"] == "16:9"


def test_image_redraw_and_reverse_prompt_paths(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)

    plugin._handle_image_redraw({"project_id": "demo", "source_url": "/static/a.png", "mask_url": "/static/m.png"})
    plugin._handle_image_reverse_prompt({"project_id": "demo", "source_url": "/static/a.png"})

    assert calls[0][1] == "/api/v1/projects/demo/freezone/redraw"
    assert calls[0][3]["mask_url"] == "/static/m.png"
    assert calls[1][1] == "/api/v1/projects/demo/freezone/image/reverse-prompt"


def test_video_generate_and_extend_require_prompt(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    assert "prompt is required" in plugin._handle_video_generate({"project_id": "demo"})
    assert (
        "prompt is required"
        in plugin._handle_video_extend({"project_id": "demo", "video_url": "/static/a.mp4"})
    )


def test_video_generate_posts_body(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_video_generate(
        {"project_id": "demo", "prompt": "sunset", "resolution": "1080p", "model": "seedance"}
    )
    _m, path, _q, body = calls[0]
    assert path == "/api/v1/projects/demo/freezone/video/gen"
    assert body["prompt"] == "sunset"
    assert body["resolution"] == "1080p"
    assert body["model"] == "seedance"


def test_video_i2v_rejects_bad_gen_mode(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_video_i2v({"project_id": "demo", "gen_mode": "weird"})
    assert "gen_mode must be imageToVideo or imageReference" in result


def test_video_i2v_defaults_gen_mode(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_video_i2v(
        {"project_id": "demo", "image_urls": ["/static/a.png"], "prompt": "move"}
    )
    _m, path, _q, body = calls[0]
    assert path == "/api/v1/projects/demo/freezone/video/i2v"
    assert body["gen_mode"] == "imageToVideo"
    assert body["image_urls"] == ["/static/a.png"]


def test_video_omni_gen_requires_references(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    assert "references is required" in plugin._handle_video_omni_gen({"project_id": "demo", "prompt": "p"})
    assert (
        "each carry a non-empty url"
        in plugin._handle_video_omni_gen(
            {"project_id": "demo", "prompt": "p", "references": [{"type": "image"}]}
        )
    )


def test_video_omni_gen_posts_omni_body(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_video_omni_gen(
        {
            "project_id": "demo",
            "prompt": "p",
            "model": "seedance",
            "references": [{"type": "video", "url": "/static/a.mp4"}],
        }
    )
    _m, path, _q, body = calls[0]
    assert path == "/api/v1/projects/demo/freezone/video/omni-gen"
    assert body["gen_mode"] == "allReference"
    assert body["model_id"] == "seedance"
    assert body["references"] == [{"type": "video", "url": "/static/a.mp4"}]


def test_video_edit_and_upscale_paths(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_video_edit({"project_id": "demo", "video_url": "/static/a.mp4", "audio_setting": "origin"})
    plugin._handle_video_upscale({"project_id": "demo", "source_url": "/static/a.mp4", "resolution": "4k"})

    assert calls[0][1] == "/api/v1/projects/demo/freezone/video/video-edit"
    assert calls[0][3]["audio_setting"] == "origin"
    assert calls[0][3]["archive_to_library"] is True
    assert calls[1][1] == "/api/v1/projects/demo/freezone/video/upscale"
    assert calls[1][3]["resolution"] == "4k"


def test_audio_separate_and_music_paths(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_audio_separate({"project_id": "demo", "source_url": "/static/a.mp4"})
    plugin._handle_audio_music({"project_id": "demo", "input": "calm piano"})

    assert calls[0][1] == "/api/v1/projects/demo/freezone/video/audio-separate"
    assert calls[1][1] == "/api/v1/projects/demo/freezone/audio/eleven-music"
    assert calls[1][3]["input"] == "calm piano"


def test_text_generate_translate_story_paths(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    plugin._handle_text_generate({"project_id": "demo", "prompt": "write"})
    plugin._handle_text_translate({"project_id": "demo", "text": "hello", "node_type": "video"})
    plugin._handle_story_script_generate({"project_id": "demo", "source_text": "script"})

    assert calls[0][1] == "/api/v1/projects/demo/freezone/text/generate"
    assert calls[1][1] == "/api/v1/projects/demo/freezone/text/translate"
    assert calls[1][3]["node_type"] == "video"
    assert calls[2][1] == "/api/v1/projects/demo/freezone/text/story-script"
    assert calls[2][3]["source_text"] == "script"


def test_story_script_requires_one_source(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_story_script_generate({"project_id": "demo"})
    assert "source_text / source_url / video_url is required" in result


def test_video_script_reverse_requires_video_url(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    assert "video_url is required" in plugin._handle_video_script_reverse({"project_id": "demo"})
    plugin._handle_video_script_reverse(
        {"project_id": "demo", "video_url": "/static/a.mp4", "model": "gemini"}
    )
    assert calls[-1][1] == "/api/v1/projects/demo/freezone/video-script-reverse"
    assert calls[-1][3]["model"] == "gemini"


def test_media_copywrite_validates_source_type(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)
    bad = plugin._handle_media_copywrite({"project_id": "demo", "source_type": "weird", "url": "/x"})
    assert "source_type must be" in bad
    plugin._handle_media_copywrite(
        {"project_id": "demo", "source_type": "link", "url": "https://v.douyin.com/x", "platform": "douyin"}
    )
    assert calls[-1][1] == "/api/v1/projects/demo/freezone/media-copywrite"
    assert calls[-1][3]["url"] == "https://v.douyin.com/x"


def test_resource_reads_use_get_and_kind_mapping(monkeypatch):
    plugin = _load_impl()
    calls = _capture(plugin, monkeypatch)

    plugin._handle_list_assets({"project_id": "demo", "query": {"page": 1}})
    plugin._handle_get_character_library({"project_id": "demo"})
    plugin._handle_get_audio_references({"project_id": "demo"})
    plugin._handle_get_models({"project_id": "demo", "kind": "image"})
    plugin._handle_get_models({"project_id": "demo", "kind": "vision"})

    assert calls[0][:3] == ("GET", "/api/v1/projects/demo/freezone/assets", {"page": 1})
    assert calls[1][:2] == ("GET", "/api/v1/projects/demo/freezone/video/character-library")
    assert calls[2][:2] == ("GET", "/api/v1/projects/demo/freezone/audio/references")
    assert calls[3][:2] == ("GET", "/api/v1/projects/demo/freezone/image/models")
    # text/vision catalogs are global (no project prefix).
    assert calls[4][:2] == ("GET", "/api/v1/model-gateway/vision-models")


def test_get_models_rejects_unknown_kind(monkeypatch):
    plugin = _load_impl()
    _capture(plugin, monkeypatch)
    result = plugin._handle_get_models({"project_id": "demo", "kind": "hologram"})
    assert "kind must be one of" in result
