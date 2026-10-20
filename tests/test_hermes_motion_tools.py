"""[aipdd fork delta] Tests for the P1 MCP coverage: chapters / motion / exports.

These tools close the last gaps where the Agent could only guess REST paths:
① novel chapter read/save + start-ingest after upload; ② MG motion catalog,
renders, render/stills/draft submission; ③ SRT text and binary episode exports.
The HTTP routes are pinned elsewhere; here we check the MCP layer stays a thin,
faithful delegation and that binary exports never go through the UTF-8 path.
"""

from __future__ import annotations

import copy
import importlib.util
import io
from pathlib import Path

CHAPTERS = "aipdd_drama_get_chapters"
GET_STORY_SOURCE = "aipdd_drama_get_story_source"
SAVE_STORY_SOURCE = "aipdd_drama_save_story_source"
UPDATE_CHAPTER = "aipdd_drama_update_chapter"
START_INGEST = "aipdd_drama_start_ingest"
MOTION_CASES = "aipdd_drama_motion_cases"
MOTION_STYLES = "aipdd_drama_motion_styles"
MOTION_RENDERS = "aipdd_drama_motion_renders"
MOTION_RENDER = "aipdd_drama_motion_render"
MOTION_STILLS = "aipdd_drama_motion_stills"
MOTION_DRAFT = "aipdd_drama_motion_draft"
SUBTITLES = "aipdd_drama_get_subtitles"
EXPORT_EPISODE = "aipdd_drama_export_episode"

NEW_TOOLS = {
    CHAPTERS,
    GET_STORY_SOURCE,
    SAVE_STORY_SOURCE,
    UPDATE_CHAPTER,
    START_INGEST,
    MOTION_CASES,
    MOTION_STYLES,
    MOTION_RENDERS,
    MOTION_RENDER,
    MOTION_STILLS,
    MOTION_DRAFT,
    SUBTITLES,
    EXPORT_EPISODE,
}


def _load_impl():
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_motion_tools_impl", impl_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


def _fake_request(monkeypatch, plugin, routes=None):
    calls: list[dict] = []
    routes = routes or {}

    def _fake(method, path, *, query=None, body=None, timeout=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
                "timeout": timeout,
            }
        )
        # 路由响应每次请求都是新对象；复制一份，避免被 handler 就地改写后串到下一个用例。
        return copy.deepcopy(routes.get((method, path), {"ok": True, "data": {}}))
    monkeypatch.setattr(plugin, "_request", _fake)
    return calls


def _handlers(plugin):
    return {name: handler for name, _schema, handler in plugin.TOOLS}


# ---------------------------------------------------------------------------
# 注册与 schema 自洽
# ---------------------------------------------------------------------------


def test_new_tools_registered_with_self_consistent_schemas():
    plugin = _load_impl()
    tools = [item for item in plugin.TOOLS if item[0] in NEW_TOOLS]
    assert {item[0] for item in tools} == NEW_TOOLS
    for name, schema, handler in tools:
        assert schema["name"] == name
        assert schema["description"].strip()
        assert callable(handler)
        assert schema["parameters"]["type"] == "object"


def test_export_tool_paths_stay_inside_api_surface():
    plugin = _load_impl()
    schemas = {name: schema for name, schema, _handler in plugin.TOOLS}
    assert "export/video" in schemas[EXPORT_EPISODE]["description"]
    assert "export/zip" in schemas[EXPORT_EPISODE]["description"]
    assert "freezone/motion/render" in schemas[MOTION_RENDER]["description"]
    assert "freezone_motion_draft" in schemas[MOTION_DRAFT]["description"]


def test_motion_page_source_constraints_are_declared_in_schema():
    """html / case_id / style_id 三者必居其一、两个 id 互斥 —— 与运行时校验同口径。"""
    plugin = _load_impl()
    schemas = {name: schema for name, schema, _handler in plugin.TOOLS}
    for name in (MOTION_RENDER, MOTION_STILLS):
        params = schemas[name]["parameters"]
        assert params["anyOf"] == [
            {"required": ["html"]},
            {"required": ["case_id"]},
            {"required": ["style_id"]},
        ], name
        assert params["not"] == {"required": ["case_id", "style_id"]}, name


# ---------------------------------------------------------------------------
# 故事源：用户在 Agent 里直接输入整本小说
# ---------------------------------------------------------------------------


def test_save_story_source_puts_full_text(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    novel = "第一章\n夜雨。\n第二章\n天晴。"
    result = _handlers(plugin)[SAVE_STORY_SOURCE](
        {"project_id": "alpha", "text": novel}
    )

    assert result["ok"] is True
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "/api/v1/projects/alpha/story/source"
    assert calls[0]["body"] == {"text": novel}
    assert calls[0]["timeout"] == 120


def test_save_story_source_rejects_blank_text(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[SAVE_STORY_SOURCE](
        {"project_id": "alpha", "text": " \n "}
    )
    assert isinstance(result, str)
    assert "text is required" in result
    assert calls == []


def test_get_story_source_returns_route_body(monkeypatch):
    plugin = _load_impl()
    _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", "/api/v1/projects/alpha/story/source"): {
                "ok": True,
                "data": {"text": "整本小说", "billable_chars": 4, "filename": "novel.txt"},
            }
        },
    )
    result = _handlers(plugin)[GET_STORY_SOURCE]({"project_id": "alpha"})
    assert result["data"]["text"] == "整本小说"
    assert result["data"]["billable_chars"] == 4
    assert result["data"]["filename"] == "novel.txt"


# ---------------------------------------------------------------------------
# 章节：默认元数据 / 单章正文 / 保存
# ---------------------------------------------------------------------------

CHAPTERS_RESPONSE = {
    "ok": True,
    "data": {
        "total_chars": 120,
        "billable_chars": 100,
        "count": 2,
        "chapters": [
            {
                "number": 1,
                "title": "第一章",
                "word_count": 50,
                "content": "第一章\n正文一",
                "scene_blocks": [{"index": 1}],
            },
            {
                "number": 2,
                "title": "第二章",
                "word_count": 60,
                "content": "第二章\n正文二",
                "scene_blocks": [],
            },
        ],
    },
}


def test_get_chapters_defaults_to_metadata_only(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("GET", "/api/v1/projects/alpha/chapters"): CHAPTERS_RESPONSE},
    )
    result = _handlers(plugin)[CHAPTERS]({"project_id": "alpha"})

    assert calls[0]["query"] is None
    assert result["data"]["returned"] == 2
    assert result["data"]["count"] == 2
    for chapter in result["data"]["chapters"]:
        assert "content" not in chapter
        assert "scene_blocks" not in chapter
    assert "chapter=N" in result["hint"]


def test_get_chapters_single_chapter_returns_content(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("GET", "/api/v1/projects/alpha/chapters"): CHAPTERS_RESPONSE},
    )
    result = _handlers(plugin)[CHAPTERS](
        {"project_id": "alpha", "chapter": 2, "spine_template": "narrated"}
    )

    assert calls[0]["query"] == {"spine_template": "narrated"}
    assert [row["number"] for row in result["data"]["chapters"]] == [2]
    assert result["data"]["chapters"][0]["content"] == "第二章\n正文二"


def test_get_chapters_unknown_chapter_errors_before_returning_data(monkeypatch):
    plugin = _load_impl()
    _fake_request(
        monkeypatch,
        plugin,
        {("GET", "/api/v1/projects/alpha/chapters"): CHAPTERS_RESPONSE},
    )
    result = _handlers(plugin)[CHAPTERS]({"project_id": "alpha", "chapter": 9})
    assert isinstance(result, str)
    assert "chapter 9 not found" in result


def test_update_chapter_puts_body_only(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[UPDATE_CHAPTER](
        {"project_id": "alpha", "chapter": 3, "body": "新的正文"}
    )

    assert result["ok"] is True
    assert calls == [
        {
            "method": "PUT",
            "path": "/api/v1/projects/alpha/chapters/3",
            "query": None,
            "body": {"body": "新的正文"},
            "timeout": None,
        }
    ]


def test_update_chapter_rejects_blank_body_without_request(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[UPDATE_CHAPTER](
        {"project_id": "alpha", "chapter": 3, "body": "   "}
    )
    assert isinstance(result, str)
    assert "body is required" in result
    assert calls == []


def test_start_ingest_posts_filename_and_flags(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[START_INGEST](
        {
            "project_id": "alpha",
            "filename": "novel.txt",
            "rebuild": True,
            "spine_template": "drama",
        }
    )

    assert result["ok"] is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/v1/projects/alpha/ingest/start"
    assert calls[0]["body"] == {
        "filename": "novel.txt",
        "rebuild": True,
        "spine_template": "drama",
    }
    assert calls[0]["timeout"] == 60


def test_start_ingest_requires_filename_without_request(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[START_INGEST]({"project_id": "alpha"})
    assert isinstance(result, str)
    assert "filename is required" in result
    assert calls == []


# ---------------------------------------------------------------------------
# 动效大片目录 / 成片清单
# ---------------------------------------------------------------------------

CASES_RESPONSE = {
    "ok": True,
    "data": [
        {"id": "M43", "label": "产品短片", "category": "产品宣传", "brief": "液态变形", "tags": ["暖白"]},
        {"id": "M12", "label": "标题动效", "category": "标题", "brief": "文字入场", "tags": ["快节奏"]},
    ],
    "categories": ["产品宣传", "标题"],
}

STYLES_RESPONSE = {
    "ok": True,
    "data": [
        {"id": "ink-wash", "label": "中国水墨", "category": "手绘插画", "brief": "纸墨", "tags": ["文化"]},
        {"id": "swiss", "label": "瑞士排版", "label_en": "Swiss", "category": "信息发布", "brief": "网格", "tags": []},
    ],
    "categories": ["手绘插画", "信息发布"],
}


def test_motion_cases_filters_and_limits(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("GET", "/api/v1/projects/alpha/freezone/motion/cases"): CASES_RESPONSE},
    )
    result = _handlers(plugin)[MOTION_CASES](
        {"project_id": "alpha", "category": "产品宣传", "query": "液态"}
    )

    assert calls[0]["method"] == "GET"
    assert [row["id"] for row in result["data"]] == ["M43"]
    assert result["total"] == 1


def test_motion_cases_by_id_returns_prompt(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", "/api/v1/projects/alpha/freezone/motion/cases/M43/prompt"): {
                "ok": True,
                "data": {"case_id": "M43", "prompt": "规格..."},
            }
        },
    )
    result = _handlers(plugin)[MOTION_CASES]({"project_id": "alpha", "case_id": "M43"})
    assert calls[0]["path"].endswith("/motion/cases/M43/prompt")
    assert result["data"]["prompt"] == "规格..."


def test_motion_styles_by_id_and_filter(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", "/api/v1/projects/alpha/freezone/motion/styles"): STYLES_RESPONSE,
            ("GET", "/api/v1/projects/alpha/freezone/motion/styles/ink-wash/prompt"): {
                "ok": True,
                "data": {"style_id": "ink-wash", "prompt": "水墨规格"},
            },
        },
    )
    listed = _handlers(plugin)[MOTION_STYLES](
        {"project_id": "alpha", "query": "swiss"}
    )
    assert [row["id"] for row in listed["data"]] == ["swiss"]

    prompt = _handlers(plugin)[MOTION_STYLES](
        {"project_id": "alpha", "style_id": "ink-wash"}
    )
    assert prompt["data"]["prompt"] == "水墨规格"
    assert calls[-1]["path"].endswith("/motion/styles/ink-wash/prompt")


def test_motion_renders_passes_episode_filter(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", "/api/v1/projects/alpha/freezone/motion/renders"): {
                "ok": True,
                "data": [{"path": "a.mp4", "episode": 3}],
                "total": 1,
            }
        },
    )
    result = _handlers(plugin)[MOTION_RENDERS]({"project_id": "alpha", "episode": 3})
    assert calls[0]["query"] == {"episode": 3}
    assert result["data"][0]["path"] == "a.mp4"


# ---------------------------------------------------------------------------
# 动效大片提交：render / stills / draft
# ---------------------------------------------------------------------------


def test_motion_render_passes_page_and_workbench_fields(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[MOTION_RENDER](
        {
            "project_id": "alpha",
            "html": "<html>window.DUR=2</html>",
            "aspect_ratio": "9:16",
            "fps": 24,
            "episode": 2,
            "beat_number": 7,
            "music": False,
        }
    )

    assert result["ok"] is True
    assert calls[0]["path"] == "/api/v1/projects/alpha/freezone/motion/render"
    assert calls[0]["body"] == {
        "html": "<html>window.DUR=2</html>",
        "aspect_ratio": "9:16",
        "fps": 24,
        "episode": 2,
        "beat_number": 7,
        "music": False,
    }


def test_motion_render_requires_a_page_source(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[MOTION_RENDER]({"project_id": "alpha"})
    assert isinstance(result, str)
    assert "html, case_id or style_id is required" in result
    assert calls == []

    exclusive = _handlers(plugin)[MOTION_RENDER](
        {"project_id": "alpha", "case_id": "M43", "style_id": "ink-wash"}
    )
    assert isinstance(exclusive, str)
    assert "mutually exclusive" in exclusive
    assert calls == []


def test_motion_stills_passes_max_stills(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[MOTION_STILLS](
        {"project_id": "alpha", "case_id": "M43", "subject": "一杯咖啡", "max_stills": 8}
    )
    assert result["ok"] is True
    assert calls[0]["path"] == "/api/v1/projects/alpha/freezone/motion/stills"
    assert calls[0]["body"] == {
        "case_id": "M43",
        "subject": "一杯咖啡",
        "max_stills": 8,
    }


def test_motion_draft_requires_clips_with_source(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    empty = _handlers(plugin)[MOTION_DRAFT]({"project_id": "alpha"})
    assert isinstance(empty, str)
    assert "clips is required" in empty
    assert calls == []

    no_source = _handlers(plugin)[MOTION_DRAFT](
        {"project_id": "alpha", "clips": [{"name": "镜头1"}]}
    )
    assert isinstance(no_source, str)
    assert "needs a source" in no_source
    assert calls == []


def test_motion_draft_passes_timeline_payload(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    clips = [{"source": "outputs/a.mp4", "caption": "第一镜"}]
    result = _handlers(plugin)[MOTION_DRAFT](
        {
            "project_id": "alpha",
            "title": "第 1 集",
            "clips": clips,
            "bgm": {"source": "bgm.mp3", "gain": 0.2},
            "deploy": False,
        }
    )

    assert result["ok"] is True
    assert calls[0]["path"] == "/api/v1/projects/alpha/freezone/motion/draft"
    assert calls[0]["body"] == {
        "title": "第 1 集",
        "clips": clips,
        "bgm": {"source": "bgm.mp3", "gain": 0.2},
        "deploy": False,
    }


# ---------------------------------------------------------------------------
# 字幕 / 导出
# ---------------------------------------------------------------------------

SRT = "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:02,000 --> 00:00:03,500\n再见\n"


def test_get_subtitles_wraps_text_with_counts(monkeypatch):
    plugin = _load_impl()
    _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", "/api/v1/projects/alpha/episodes/2/export/srt"): {
                "ok": True,
                "status_code": 200,
                "data": SRT,
            }
        },
    )
    result = _handlers(plugin)[SUBTITLES]({"project_id": "alpha", "episode": 2})
    assert result["data"]["content"] == SRT
    assert result["data"]["cue_count"] == 2
    assert result["data"]["chars"] == len(SRT)


def test_export_episode_uses_binary_download(monkeypatch, tmp_path):
    plugin = _load_impl()
    _fake_request(monkeypatch, plugin)
    monkeypatch.setattr(plugin, "_project_output_dir", lambda: tmp_path)
    calls: list[dict] = []

    def _fake_download(method, path, destination, *, timeout=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "destination": destination,
                "timeout": timeout,
            }
        )
        return {"ok": True, "data": {"path": str(destination), "bytes": 10}}

    monkeypatch.setattr(plugin, "_download", _fake_download)

    zip_result = _handlers(plugin)[EXPORT_EPISODE](
        {"project_id": "alpha", "episode": 3, "kind": "zip"}
    )
    assert zip_result["data"]["kind"] == "zip"
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/v1/projects/alpha/episodes/3/export/zip"
    assert calls[0]["destination"] == tmp_path / "exports" / "alpha" / "ep003.zip"
    assert calls[0]["timeout"] == 600.0

    video_result = _handlers(plugin)[EXPORT_EPISODE](
        {"project_id": "alpha", "episode": 3, "kind": "video", "timeout_seconds": 120}
    )
    assert video_result["data"]["kind"] == "video"
    assert calls[1]["method"] == "GET"
    assert calls[1]["path"] == "/api/v1/projects/alpha/episodes/3/export/video"
    assert calls[1]["destination"] == tmp_path / "exports" / "alpha" / "ep003_final.mp4"
    assert calls[1]["timeout"] == 120.0


def test_export_episode_rejects_unknown_kind_without_download(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(monkeypatch, plugin)
    result = _handlers(plugin)[EXPORT_EPISODE](
        {"project_id": "alpha", "episode": 1, "kind": "pdf"}
    )
    assert isinstance(result, str)
    assert "kind must be video or zip" in result
    assert calls == []


class _FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, content_type: str, status: int = 200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def test_download_writes_binary_bytes_untouched(monkeypatch, tmp_path):
    plugin = _load_impl()
    monkeypatch.setattr(plugin, "_base_url", lambda: "http://127.0.0.1:8780")
    monkeypatch.setattr(plugin, "_api_headers", lambda accept="application/json": {})
    payload = b"\x00\x00\x00\x18ftypmp42\xff\xfe\x00"
    monkeypatch.setattr(
        plugin, "urlopen", lambda req, timeout=None: _FakeResponse(payload, "video/mp4")
    )
    destination = tmp_path / "ep001_final.mp4"

    result = plugin._download(
        "GET", "/api/v1/projects/alpha/episodes/1/export/video", destination
    )

    assert result["ok"] is True
    assert result["data"]["bytes"] == len(payload)
    assert destination.read_bytes() == payload
    assert not (tmp_path / "ep001_final.mp4.part").exists()


def test_download_preserves_json_errors(monkeypatch, tmp_path):
    plugin = _load_impl()
    monkeypatch.setattr(plugin, "_base_url", lambda: "http://127.0.0.1:8780")
    monkeypatch.setattr(plugin, "_api_headers", lambda accept="application/json": {})
    body = b'{"ok": false, "error": "Final video not found"}'
    monkeypatch.setattr(
        plugin,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(body, "application/json", status=404),
    )
    destination = tmp_path / "ep001_final.mp4"

    result = plugin._download(
        "GET", "/api/v1/projects/alpha/episodes/1/export/video", destination
    )

    assert result["ok"] is False
    assert result["error"] == "Final video not found"
    assert not destination.exists()
