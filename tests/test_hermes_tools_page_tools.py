"""[aipdd fork delta] Tests for the tools-page tool group in the dramaclaw MCP plugin.

与前端「工具」栏目面板一一对应：视频复刻 / 混剪短视频 / 白板动画视频 / 科普讲解视频 / 一句话生成
短视频 / 人声复刻 / 音乐创作 / 对口型视频 / 替换视频人物 / 替换商品 / 视频去水印 / 图片去水印 /
抖音文案提取。这些测试钉住插件侧契约：注册、可用性门禁、默认归档口径、上游请求体
形状与错误分支。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_plugin_module():
    # P5 薄壳化：.hermes/plugins/dramaclaw 现在是薄壳（Hermes 发现面），
    # 先验证其可独立加载、工具表可用。
    shell_path = (
        Path(__file__).resolve().parents[1]
        / ".hermes" / "plugins" / "dramaclaw" / "__init__.py"
    )
    shell_spec = importlib.util.spec_from_file_location("test_tools_page_shell", shell_path)
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
    spec = importlib.util.spec_from_file_location("test_tools_page_plugin", impl_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


TOOLS_PAGE_TOOL_NAMES = {
    "aipdd_drama_video_clone",
    "aipdd_drama_video_mix",
    "aipdd_drama_whiteboard_animation",
    "aipdd_drama_explainer_video",
    "aipdd_drama_video_idea",
    "aipdd_drama_voice_clone",
    # [aipdd fork delta req46] 音乐创作（MiniMax Music 3.0 五模式）。
    "aipdd_drama_music_create",
    "aipdd_drama_lip_sync",
    "aipdd_drama_replace_person",
    "aipdd_drama_replace_product",
    "aipdd_drama_video_watermark",
    "aipdd_drama_image_watermark",
    "aipdd_drama_douyin_copywrite",
}

# [aipdd fork delta req98] 资产库新增文本载体后，工具页全部工具（含抖音文案
# 提取的 txt 产物）都按「生成就入库」默认归档，不再有例外。
def test_tools_page_group_registered_with_self_consistent_schemas():
    plugin = _load_plugin_module()
    tools = [item for item in plugin.TOOLS if item[0] in TOOLS_PAGE_TOOL_NAMES]

    assert {item[0] for item in tools} == TOOLS_PAGE_TOOL_NAMES
    for name, schema, handler in tools:
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} not in properties"
        # 工具页口径：产物默认自动入库，显式 false 才关闭。
        assert "archive_to_library" in params["properties"], name


def test_tools_page_tools_require_main_api_credentials():
    plugin = _load_plugin_module()
    registered = []

    class FakeCtx:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(FakeCtx())

    matched = [kwargs for kwargs in registered if kwargs["name"] in TOOLS_PAGE_TOOL_NAMES]
    assert {kwargs["name"] for kwargs in matched} == TOOLS_PAGE_TOOL_NAMES
    for kwargs in matched:
        assert kwargs["check_fn"] is plugin._available
        assert kwargs["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]


def test_tools_page_archive_flag_defaults_true_and_only_explicit_false_opts_out():
    plugin = _load_plugin_module()

    assert plugin._tools_page_archive_flag({}) is True
    assert plugin._tools_page_archive_flag({"archive_to_library": None}) is True
    assert plugin._tools_page_archive_flag({"archive_to_library": "true"}) is True
    assert plugin._tools_page_archive_flag({"archive_to_library": True}) is True
    for raw in ("0", "false", "NO", "off", " false "):
        assert plugin._tools_page_archive_flag({"archive_to_library": raw}) is False
    assert plugin._tools_page_archive_flag({"archive_to_library": False}) is False


def test_tools_page_handlers_require_project(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.delenv("DRAMACLAW_PROJECT_ID", raising=False)

    result = plugin._handle_video_clone({"video_url": "/static/x/a.mp4"})

    assert "project_id is required" in result


def test_video_clone_handler_posts_remake_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_video_clone(
        {
            "project_id": "demo",
            "video_url": "/static/admin/demo/freezone/_uploads/ref.mp4",
            "prompt": "更冷冽",
            "reference_urls": [
                "/static/admin/demo/freezone/_uploads/ref-a.png",
                "  /static/admin/demo/freezone/_uploads/ref-b.png  ",
            ],
            "model": "cat-fast",
            "reference_mode": "frames",
            "audio_mode": "source",
            "max_frames": 16,
            "archive_to_library": False,
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    assert calls == [
        (
            "POST",
            "/api/v1/projects/demo/freezone/video/clone",
            {
                "video_url": "/static/admin/demo/freezone/_uploads/ref.mp4",
                "archive_to_library": False,
                "prompt": "更冷冽",
                "reference_urls": [
                    "/static/admin/demo/freezone/_uploads/ref-a.png",
                    "/static/admin/demo/freezone/_uploads/ref-b.png",
                ],
                "model": "cat-fast",
                "reference_mode": "frames",
                "audio_mode": "source",
                "max_frames": 16,
            },
        )
    ]


def test_video_clone_handler_reference_urls_validation(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append(body or {})
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    # 全空/空白列表：不发 reference_urls 字段。
    plugin._handle_video_clone(
        {
            "project_id": "demo",
            "video_url": "/static/x/a.mp4",
            "reference_urls": ["  ", ""],
        }
    )
    assert "reference_urls" not in calls[0]
    # 非列表：直接报错且不再发请求。
    result = plugin._handle_video_clone(
        {
            "project_id": "demo",
            "video_url": "/static/x/a.mp4",
            "reference_urls": "/static/x/a.png",
        }
    )
    assert "reference_urls must be a list" in result
    assert len(calls) == 1


def test_video_clone_handler_requires_video_url(monkeypatch):
    plugin = _load_plugin_module()

    def fake_request(*_args, **_kwargs):
        raise AssertionError("must not call the API without video_url")

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_video_clone({"project_id": "demo", "video_url": "  "})

    assert "video_url is required" in result


def test_video_mix_handler_posts_pool_remix_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_video_mix(
        {
            "project_id": "demo",
            "material_urls": [" /static/x/a.jpg ", "/static/x/b.mp4"],
            "subject": " 春日露营 ",
            "model": "ap-indextts-2",
            "bgm_url": "/static/x/bgm.mp3",
            "bgm_volume": 0.3,
            "clip_duration": 4,
            "voice_scope": "user_custom",
            "voice_id": "voice-1",
            "archive_to_library": False,
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/video/mix")
    # 素材池按顺序透传，字符串项去空白。
    assert body["material_urls"] == ["/static/x/a.jpg", "/static/x/b.mp4"]
    assert body["subject"] == "春日露营"
    assert body["script"] == ""
    assert body["audio_mode"] == "generate"
    assert body["archive_to_library"] is False
    assert body["bgm_volume"] == 0.3
    assert body["clip_duration"] == 4
    assert body["voice_ref"]["scope"] == "user_custom"
    assert body["voice_ref"]["voice_id"] == "voice-1"
    # 未传的可选项不注入默认值——后端 pydantic 默认兜底。
    assert "subtitle_enabled" not in body
    assert "transition" not in body


def test_video_mix_handler_validates_pool_mode_and_voice(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    # 素材池必填：缺失、空列表、全空白项都直接报错，不发请求。
    assert "material_urls is required" in plugin._handle_video_mix({"project_id": "demo"})
    assert "material_urls is required" in plugin._handle_video_mix(
        {"project_id": "demo", "material_urls": []}
    )
    assert "material_urls is required" in plugin._handle_video_mix(
        {"project_id": "demo", "material_urls": [" ", ""]}
    )
    assert len(calls) == 0

    # 配音模式必须给主题或文案；静音模式不需要。
    assert "subject or script is required" in plugin._handle_video_mix(
        {"project_id": "demo", "material_urls": ["/static/x/a.mp4"]}
    )
    result = plugin._handle_video_mix(
        {
            "project_id": "demo",
            "material_urls": ["/static/x/a.mp4"],
            "audio_mode": "mute",
            "max_duration_seconds": 10,
        }
    )
    assert result == {"ok": True, "data": {}}
    assert calls[-1][2]["audio_mode"] == "mute"
    assert calls[-1][2]["max_duration_seconds"] == 10

    # 未知音频模式直接拒绝。
    assert "audio_mode must be generate or mute" in plugin._handle_video_mix(
        {
            "project_id": "demo",
            "material_urls": ["/static/x/a.mp4"],
            "audio_mode": "sing",
        }
    )

    # 素材池超过 60 项：与后端 _MIX_MAX_MATERIALS 同口径。
    over = [f"/static/x/{index}.jpg" for index in range(61)]
    assert "at most 60 entries" in plugin._handle_video_mix(
        {"project_id": "demo", "material_urls": over, "subject": "x"}
    )

    # 带了 voice_* 却没给 scope → 明确报错，不发请求。
    assert "voice_scope is required" in plugin._handle_video_mix(
        {
            "project_id": "demo",
            "material_urls": ["/static/x/a.mp4"],
            "subject": "春日露营",
            "voice_id": "voice-1",
        }
    )
    assert len(calls) == 1


def test_whiteboard_animation_handler_posts_script_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_whiteboard_animation(
        {
            "project_id": "demo",
            "script": "  为什么你的视频留不住人？  ",
            "model_text": "DC-llm-custom",
            "model_image": "img-catalog",
            "image_model_params": {"size": "2K"},
            "model": "audio-catalog",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "max_scenes": 6,
            "pen_cursor": False,
            "subtitle_enabled": True,
            "subtitle_font_size": "large",
            "emotion_prompt": "轻松、亲切",
            "voice_scope": "user_custom",
            "voice_id": "voice-1",
            "bgm_url": "/static/x/bgm.mp3",
            "bgm_volume": 0.25,
            "archive_to_library": False,
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    method, path, body = calls[-1]
    assert (method, path) == (
        "POST",
        "/api/v1/projects/demo/freezone/whiteboard-animation",
    )
    # 口播文案去空白后透传，三类模型与渲染参数原样上交后端定型。
    assert body["script"] == "为什么你的视频留不住人？"
    assert body["model_text"] == "DC-llm-custom"
    assert body["model_image"] == "img-catalog"
    assert body["image_model_params"] == {"size": "2K"}
    assert body["model"] == "audio-catalog"
    assert body["aspect_ratio"] == "9:16"
    assert body["resolution"] == "1080p"
    assert body["max_scenes"] == 6
    assert body["pen_cursor"] is False
    assert body["subtitle_enabled"] is True
    assert body["subtitle_font_size"] == "large"
    assert body["emotion_prompt"] == "轻松、亲切"
    assert body["bgm_url"] == "/static/x/bgm.mp3"
    assert body["bgm_volume"] == 0.25
    assert body["voice_ref"] == {
        "scope": "user_custom",
        "character_name": "",
        "identity_id": "",
        "slot": "",
        "voice_id": "voice-1",
    }
    assert body["archive_to_library"] is False


def test_whiteboard_animation_handler_defaults_and_validation(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    # 文案必填：缺失/全空白都直接报错，不发请求。
    assert "script is required" in plugin._handle_whiteboard_animation(
        {"project_id": "demo"}
    )
    assert "script is required" in plugin._handle_whiteboard_animation(
        {"project_id": "demo", "script": "   "}
    )
    # 超长文案与后端 10000 字上限同口径。
    assert "script must be <= 10000 characters" in plugin._handle_whiteboard_animation(
        {"project_id": "demo", "script": "字" * 10_001}
    )
    assert calls == []

    # 只给文案：可选项不注入默认值，画布/模型/逐笔揭示全部由后端默认兜底。
    result = plugin._handle_whiteboard_animation(
        {"project_id": "demo", "script": "三个提高完播率的方法"}
    )
    assert result == {"ok": True, "data": {}}
    assert calls[-1][2] == {
        "script": "三个提高完播率的方法",
        "archive_to_library": True,
    }

    # 带了 voice_* 却没给 scope → 明确报错，不发请求。
    assert "voice_scope is required" in plugin._handle_whiteboard_animation(
        {"project_id": "demo", "script": "x", "voice_id": "voice-1"}
    )
    assert len(calls) == 1


def test_video_idea_handler_posts_idea_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_video_idea(
        {
            "project_id": "demo",
            "idea": "  一只流浪猫在雨夜救下迷路的小女孩  ",
            "style": "Cinematic.",
            "user_requirement": "适合儿童",
            "max_scenes": 2,
            "max_shots_per_scene": 4,
            "shot_duration": 6.0,
            "portrait_mode": "front",
            "model_image": "img-catalog",
            "model_video": "video-catalog",
            "generate_audio": False,
            "archive_to_library": False,
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/video/idea")
    # 创意去空白，其余透传。
    assert body["idea"] == "一只流浪猫在雨夜救下迷路的小女孩"
    assert body["style"] == "Cinematic."
    assert body["user_requirement"] == "适合儿童"
    assert body["max_scenes"] == 2
    assert body["max_shots_per_scene"] == 4
    assert body["shot_duration"] == 6.0
    assert body["portrait_mode"] == "front"
    assert body["model_image"] == "img-catalog"
    assert body["model_video"] == "video-catalog"
    assert body["generate_audio"] is False
    assert body["archive_to_library"] is False
    # 未传的可选项不注入默认值——后端 pydantic 默认兜底。
    assert "human_review" not in body
    assert "scene_optimize" not in body


def test_video_idea_handler_validates_idea_and_portrait_mode(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    # 创意必填；缺失/全空白都直接报错，不发请求。
    assert "idea is required" in plugin._handle_video_idea({"project_id": "demo"})
    assert "idea is required" in plugin._handle_video_idea({"project_id": "demo", "idea": "   "})
    assert len(calls) == 0

    # 超长创意与后端 2000 字上限同口径。
    assert "idea must be <= 2000 characters" in plugin._handle_video_idea(
        {"project_id": "demo", "idea": "猫" * 2001}
    )

    # 未知肖像策略直接拒绝。
    assert "portrait_mode must be three or front" in plugin._handle_video_idea(
        {"project_id": "demo", "idea": "一只猫", "portrait_mode": "quad"}
    )

    # 默认三视图；显式 front 透传。
    result = plugin._handle_video_idea({"project_id": "demo", "idea": "一只猫"})
    assert result == {"ok": True, "data": {}}
    assert calls[-1][2]["portrait_mode"] == "three"
    assert calls[-1][2]["archive_to_library"] is True


def test_voice_clone_handler_builds_voice_ref_and_validates_scope(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    plugin._handle_voice_clone(
        {
            "project_id": "demo",
            "text": "  第一句台词  ",
            "voice_scope": "character_default",
            "voice_character_name": "阿离",
        }
    )
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/audio/speech")
    assert body["text"] == "第一句台词"
    assert body["archive_to_library"] is True
    assert body["voice_ref"] == {
        "scope": "character_default",
        "character_name": "阿离",
        "identity_id": "",
        "slot": "",
        "voice_id": "",
    }

    # 不带任何 voice_* 字段 → 不传 voice_ref，使用项目默认旁白声线。
    plugin._handle_voice_clone({"project_id": "demo", "text": "hello"})
    assert "voice_ref" not in calls[-1][2]

    # 带了 voice_* 却没给 scope → 明确报错，不发请求。
    result = plugin._handle_voice_clone(
        {"project_id": "demo", "text": "hello", "voice_character_name": "阿离"}
    )
    assert "voice_scope is required" in result
    assert len(calls) == 2


def test_lip_sync_handler_builds_all_reference_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_lip_sync(
        {
            "project_id": "demo",
            "video_url": "/static/x/base.mp4",
            "audio_url": "/static/x/voice.mp3",
            "model": "cat-omni",
            "duration_seconds": 6,
        }
    )

    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/video/omni-gen")
    assert body["gen_mode"] == "allReference"
    # model 与 model_id 同传目录 id：后端 identifiers 两个都认。
    assert body["model"] == "cat-omni"
    assert body["model_id"] == "cat-omni"
    assert body["duration_seconds"] == 6
    assert body["archive_to_library"] is True
    assert body["references"] == [
        {"type": "video", "url": "/static/x/base.mp4", "role": "", "label": ""},
        {"type": "audio", "url": "/static/x/voice.mp3", "role": "", "label": ""},
    ]

    result = plugin._handle_lip_sync(
        {"project_id": "demo", "video_url": "/static/x/base.mp4"}
    )
    assert "audio_url is required" in result
    assert len(calls) == 1


def test_replace_person_handler_builds_image_reference(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_replace_person(
        {
            "project_id": "demo",
            "video_url": "/static/x/source.mp4",
            "image_url": "/static/x/person.png",
        }
    )

    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/video/omni-gen")
    assert body["gen_mode"] == "allReference"
    assert body["references"] == [
        {"type": "video", "url": "/static/x/source.mp4", "role": "", "label": ""},
        {"type": "image", "url": "/static/x/person.png", "role": "", "label": ""},
    ]


def test_replace_product_handler_builds_image_reference(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_replace_product(
        {
            "project_id": "demo",
            "video_url": "/static/x/source.mp4",
            "image_url": "/static/x/product.png",
        }
    )

    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/video/omni-gen")
    assert body["gen_mode"] == "allReference"
    assert body["archive_to_library"] is True
    assert body["references"] == [
        {"type": "video", "url": "/static/x/source.mp4", "role": "", "label": ""},
        {"type": "image", "url": "/static/x/product.png", "role": "", "label": ""},
    ]


def test_video_watermark_local_defaults_to_smart_subtitle(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_video_watermark({"project_id": "demo", "source_url": "/static/x/a.mp4"})

    assert calls[-1] == (
        "POST",
        "/api/v1/projects/demo/freezone/video/erase",
        {
            "source_url": "/static/x/a.mp4",
            "mode": "smart_subtitle",
            "engine": "local",
            "archive_to_library": True,
        },
    )

    # box 模式携带归一化框选参数。
    plugin._handle_video_watermark(
        {
            "project_id": "demo",
            "source_url": "/static/x/a.mp4",
            "mode": "box",
            "engine": "mediakit",
            "box_x": 0.1,
            "box_y": 0.8,
            "box_width": 0.6,
            "box_height": 0.1,
        }
    )
    assert calls[-1][2] == {
        "source_url": "/static/x/a.mp4",
        "mode": "box",
        "engine": "mediakit",
        "archive_to_library": True,
        "box_x": 0.1,
        "box_y": 0.8,
        "box_width": 0.6,
        "box_height": 0.1,
    }


def test_video_watermark_rejects_unknown_engine(monkeypatch):
    plugin = _load_plugin_module()

    def fake_request(*_args, **_kwargs):
        raise AssertionError("must not call the API for an unknown engine")

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_video_watermark(
        {"project_id": "demo", "source_url": "/static/x/a.mp4", "engine": "magic"}
    )

    assert "engine must be one of local/mediakit/seedance" in result


def test_video_watermark_seedance_requires_model_then_posts_video_edit(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._handle_video_watermark(
        {"project_id": "demo", "source_url": "/static/x/a.mp4", "engine": "seedance"}
    )
    assert "model is required when engine=seedance" in result
    assert calls == []

    plugin._handle_video_watermark(
        {
            "project_id": "demo",
            "source_url": "/static/x/a.mp4",
            "engine": "seedance",
            "model": "cat-mini",
            "resolution": "1080p",
        }
    )
    method, path, body = calls[-1]
    assert (method, path) == (
        "POST",
        "/api/v1/projects/demo/freezone/video/video-edit",
    )
    assert body["video_url"] == "/static/x/a.mp4"
    assert body["gen_mode"] == "videoEdit"
    # 生成式重绘必须保留原声，与工具页 AI 编辑路径一致。
    assert body["audio_setting"] == "origin"
    assert body["model"] == "cat-mini"
    assert body["model_id"] == "cat-mini"
    assert body["resolution"] == "1080p"
    assert "去除视频中的水印" in body["prompt"]
    assert body["archive_to_library"] is True


def test_image_watermark_defaults_to_original_ratio(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_image_watermark({"project_id": "demo", "base_url": "/static/x/img.png"})

    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/projects/demo/freezone/edit")
    assert body["base_url"] == "/static/x/img.png"
    # 默认保持原图比例——工具页图片去水印不裁切构图。
    assert body["aspect_ratio"] == "original"
    assert "去除图片中的水印" in body["prompt"]
    assert body["archive_to_library"] is True

    result = plugin._handle_image_watermark({"project_id": "demo"})
    assert "base_url is required" in result
    assert len(calls) == 1


def test_douyin_copywrite_handler_posts_link_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_douyin_copywrite(
        {
            "project_id": "demo",
            "video": "  7.53 复制打开抖音 https://v.douyin.com/abc123/  ",
            "model": "gateway:volc:doubao-seed-asr",
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    method, path, body = calls[-1]
    assert (method, path) == (
        "POST",
        "/api/v1/projects/demo/freezone/douyin-copywrite",
    )
    # 分享文案去空白后整体透传（后端从中解析链接）；选定模型原样回传。
    assert body["video"] == "7.53 复制打开抖音 https://v.douyin.com/abc123/"
    assert body["model"] == "gateway:volc:doubao-seed-asr"
    # 产物是文案 txt：工具页默认生成就入库（资产库「文本」载体）。
    assert body["archive_to_library"] is True

    # 未指定模型：body 不带 model，由后端默认（FunASR）。
    plugin._handle_douyin_copywrite(
        {"project_id": "demo", "video": "https://v.douyin.com/abc123/"}
    )
    assert calls[-1][2] == {
        "video": "https://v.douyin.com/abc123/",
        "archive_to_library": True,
    }

    # 显式关闭：仅影响归档开关，其余字段不变。
    body: dict[str, object] = {
        "project_id": "demo",
        "video": "https://v.douyin.com/abc123/",
    }
    plugin._handle_douyin_copywrite({**body, "archive_to_library": False})
    assert calls[-1][2]["archive_to_library"] is False


def test_douyin_copywrite_handler_requires_video(monkeypatch):
    plugin = _load_plugin_module()

    def fake_request(*_args, **_kwargs):
        raise AssertionError("must not call the API without video")

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_douyin_copywrite({"project_id": "demo", "video": "   "})

    assert "video is required" in result

def test_explainer_video_handler_posts_reference_body(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_1"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_explainer_video(
        {
            "project_id": "demo",
            "reference_content": "  RAG 先把文档切块入库，检索时取回最相关的几段。  ",
            "title": "RAG 是什么",
            "duration_s": 240,
            "model_text": "DC-llm-custom",
            "model": "audio-catalog",
            "background": "stars",
            "resolution": "1080p",
            "emotion_prompt": "沉稳、克制",
            "voice_scope": "user_custom",
            "voice_id": "voice-1",
            "bgm_url": "/static/x/bgm.mp3",
            "bgm_volume": 0.25,
            "archive_to_library": False,
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_1"}}
    method, path, body = calls[-1]
    assert (method, path) == (
        "POST",
        "/api/v1/projects/demo/freezone/explainer-video",
    )
    # 参考资料去空白后透传，文本/音频模型与渲染参数原样上交后端定型。
    assert body["reference_content"] == "RAG 先把文档切块入库，检索时取回最相关的几段。"
    assert body["title"] == "RAG 是什么"
    assert body["duration_s"] == 240
    assert body["model_text"] == "DC-llm-custom"
    assert body["model"] == "audio-catalog"
    assert body["background"] == "stars"
    assert body["resolution"] == "1080p"
    assert body["emotion_prompt"] == "沉稳、克制"
    assert body["bgm_url"] == "/static/x/bgm.mp3"
    assert body["bgm_volume"] == 0.25
    assert body["voice_ref"] == {
        "scope": "user_custom",
        "character_name": "",
        "identity_id": "",
        "slot": "",
        "voice_id": "voice-1",
    }
    assert body["archive_to_library"] is False


def test_explainer_video_handler_defaults_and_validation(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    # 参考资料必填：缺失/全空白都直接报错，不发请求。
    assert "reference_content is required" in plugin._handle_explainer_video(
        {"project_id": "demo"}
    )
    assert "reference_content is required" in plugin._handle_explainer_video(
        {"project_id": "demo", "reference_content": "   "}
    )
    # 超长资料与后端 12000 字上限同口径。
    assert (
        "reference_content must be <= 12000 characters"
        in plugin._handle_explainer_video(
            {"project_id": "demo", "reference_content": "字" * 12_001}
        )
    )
    assert calls == []

    # 只给资料：可选项不注入默认值，时长/幕底/清晰度/模型全部由后端默认兜底。
    result = plugin._handle_explainer_video(
        {"project_id": "demo", "reference_content": "RAG 先把文档切块入库。"}
    )
    assert result == {"ok": True, "data": {}}
    assert calls[-1][2] == {
        "reference_content": "RAG 先把文档切块入库。",
        "archive_to_library": True,
    }

    # 带了 voice_* 却没给 scope → 明确报错，不发请求。
    assert "voice_scope is required" in plugin._handle_explainer_video(
        {"project_id": "demo", "reference_content": "x", "voice_id": "voice-1"}
    )
    assert len(calls) == 1


def test_explainer_video_schema_only_exposes_gateway_capabilities():
    plugin = _load_plugin_module()
    entry = next(item for item in plugin.TOOLS if item[0] == "aipdd_drama_explainer_video")
    schema = entry[1]
    properties = schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["reference_content"]
    # 科普讲解片的画面是本地逐帧绘制：不暴露 model_image / model_video，
    # 模型侧只有网关文本与音频两类既有能力。
    assert "model_image" not in properties
    assert "model_video" not in properties
    assert {"reference_content", "duration_s", "model_text", "model", "background"} <= set(
        properties
    )


def test_music_create_handler_posts_humming_two_step_body(monkeypatch):
    """[aipdd fork delta req46] 哼唱两步：特征 id + 改过的词直达提交端点，默认归档。"""
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"job_id": "job_music"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    lyrics = "[Verse]\n改过的第一行\n\n[Chorus]\n改过的副歌"
    result = plugin._handle_music_create(
        {
            "project_id": "demo",
            "mode": "humming",
            "prompt": "把这段哼唱做成轻快的民谣翻唱版本",
            "lyrics": lyrics,
            "model": "music-cover",
            "provider": "minimax_cn",
            "cover_feature_id": "cf-2026",
            "sample_rate": 44100,
            "bitrate": 256000,
            "audio_format": "wav",
            "source_name": "hum.wav",
        }
    )

    assert result == {"ok": True, "data": {"job_id": "job_music"}}
    assert calls == [
        (
            "POST",
            "/api/v1/projects/demo/freezone/audio/music-create",
            {
                "mode": "humming",
                "prompt": "把这段哼唱做成轻快的民谣翻唱版本",
                "lyrics": lyrics,
                "model": "music-cover",
                "provider": "minimax_cn",
                "cover_feature_id": "cf-2026",
                "sample_rate": 44100,
                "bitrate": 256000,
                "audio_format": "wav",
                "source_name": "hum.wav",
                "archive_to_library": True,
                "source_tool": "music-create",
            },
        )
    ]


def test_music_create_handler_passes_reference_audio_and_ignores_empty_strings(monkeypatch):
    """翻唱用参考音频；空字符串字段一律不发（后端按缺省处理，多发只会盖掉默认值）。"""
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True}

    monkeypatch.setattr(plugin, "_request", fake_request)
    plugin._handle_music_create(
        {
            "project_id": "demo",
            "mode": "cover",
            "prompt": "把这首歌唱成摇滚版本，鼓点更重",
            "reference_audio": "/static/demo/freezone/_uploads/origin.mp3",
            "lyrics": "",
            "model": "",
            "cover_feature_id": "",
            "archive_to_library": False,
        }
    )

    _method, _path, body = calls[0]
    assert body["reference_audio"] == "/static/demo/freezone/_uploads/origin.mp3"
    for absent in ("lyrics", "model", "cover_feature_id", "provider", "source_name"):
        assert absent not in body
    assert body["archive_to_library"] is False


def test_music_create_handler_validates_mode_without_calling_the_api(monkeypatch):
    """模式是唯一的必填项：缺省与越界都在本地拦住，不去打提交端点。"""
    plugin = _load_plugin_module()
    monkeypatch.delenv("DRAMACLAW_PROJECT_ID", raising=False)

    def fake_request(*_args, **_kwargs):
        raise AssertionError("must not call the API without a valid mode")

    monkeypatch.setattr(plugin, "_request", fake_request)

    assert "mode is required" in plugin._handle_music_create({"project_id": "demo"})
    assert "mode is required" in plugin._handle_music_create(
        {"project_id": "demo", "mode": "karaoke"}
    )
    assert "project_id is required" in plugin._handle_music_create(
        {"mode": "one_sentence"}
    )


def test_music_create_schema_pins_mode_enum_and_direct_endpoint():
    plugin = _load_plugin_module()
    entry = next(item for item in plugin.TOOLS if item[0] == "aipdd_drama_music_create")
    schema = entry[1]
    properties = schema["parameters"]["properties"]

    assert schema["parameters"]["required"] == ["mode"]
    assert properties["mode"]["enum"] == list(plugin.MUSIC_CREATE_MODES)
    assert properties["sample_rate"]["enum"] == [16000, 24000, 32000, 44100]
    assert properties["audio_format"]["enum"] == ["mp3", "wav", "pcm"]
    # 音乐不经网关：目录 / 站点渠道 / 两步翻唱特征 id 都是 schema 的显式入口。
    for field in ("model", "provider", "cover_feature_id", "reference_audio", "archive_to_library"):
        assert field in properties
    assert "freezone_music_generate" in schema["description"]
