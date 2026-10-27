"""[aipdd fork delta] 讲解类视频「台词草稿」（两段式出片）测试。

守三件事：

1. **契约**：``freezone/explainer_script.py`` 的建稿与解析——面板拿去编辑的那份 JSON
   和用户回传的锁定稿是同一个结构，句子编号 / 章边界一律重算，空镜与空幕按「删掉
   这一段」处理，超限明确报错（不静默截断）。
2. **草稿任务**：``freezone_explainer_script`` 只跑文本叶——讲解两次（解说词 + 分镜）、
   白板一次（逐幕切分）——不配音、不出图、不渲染，产物落成可直接回传的 JSON。
3. **锁定稿分支**：两个成片 runner 收到合法 ``locked_script`` 后**不再调用文本模型**，
   台词、章名、幕数全部来自草稿；锁定稿不合法时直接失败，不回退模型重写。

全程不触网：所有生成叶与渲染/编码换成显式签名假对象。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from novelvideo.freezone.explainer import (
    DEFAULT_MOTION,
    DEFAULT_PRIMITIVE,
    HEADLINE_MAX_CHARS,
    MAX_CHAPTERS,
    DEFAULT_DURATION_S,
    parse_explainer_narration,
    parse_explainer_storyboard,
)
from novelvideo.freezone.explainer_script import (
    MAX_SCRIPT_CHARS,
    MAX_SENTENCE_CHARS,
    SCRIPT_DRAFT_VERSION,
    build_explainer_script_draft,
    build_whiteboard_script_draft,
    estimate_script_seconds,
    parse_explainer_script_draft,
    parse_whiteboard_script_draft,
)
from novelvideo.freezone.whiteboard import WHITEBOARD_MAX_SCENES
from novelvideo.project_context import ProjectContext

_NARRATION = (
    "# CHAPTER 1 什么是 RAG\n"
    "RAG 先把文档切块入库。检索时取回最相关的几段。\n"
    "# CHAPTER 2 为什么有效\n"
    "模型不必记住全部知识。答案可以追溯到出处。"
)
_STORYBOARD = json.dumps(
    {
        "title": {
            "big": "RAG",
            "rest": "检索增强生成",
            "tagline": "让模型带着资料回答",
        },
        "shots": [
            {
                "sentence": 1,
                "primitive": "flow",
                "headline": "四步走",
                "items": ["切块", "入库", "检索", "生成"],
                "motion": "travel",
            },
            {
                "sentence": 3,
                "primitive": "list",
                "headline": "两个好处",
                "items": ["不用背知识", "可溯源"],
                "motion": "lightup",
            },
        ],
    },
    ensure_ascii=False,
)


def _model_draft(**overrides: Any) -> dict[str, Any]:
    """模型路径产出的草稿：与 runner 里 build_explainer_script_draft 同一口径。"""
    chapters = parse_explainer_narration(_NARRATION)
    title, shots = parse_explainer_storyboard(_STORYBOARD, chapters=chapters)
    draft = build_explainer_script_draft(
        title, chapters, shots, duration_s=int(overrides.get("duration_s", 180))
    )
    draft.update({k: v for k, v in overrides.items() if k != "duration_s"})
    return draft


# ============================================================
# 1) 契约：科普讲解草稿
# ============================================================


def test_estimate_seconds_weights_cjk_more_than_latin() -> None:
    # 同样五个字符，CJK 按整字计权、拉丁按 0.55 计：面板上的「约 N 秒」要跟成片同档。
    cjk = estimate_script_seconds(["中文五个字"])
    latin = estimate_script_seconds(["hello"])
    assert cjk > latin
    assert estimate_script_seconds(["中文五个字", "hello"]) == pytest.approx(
        round((5 + 5 * 0.55) / 4.2, 1)
    )
    # 空输入不抛错（空草稿面板仍要能显示）。
    assert estimate_script_seconds([]) == 0.0
    assert estimate_script_seconds([None, ""]) == 0.0


def test_build_draft_hangs_chapter_names_on_shots() -> None:
    draft = _model_draft()

    assert draft["draft_version"] == SCRIPT_DRAFT_VERSION
    assert draft["kind"] == "explainer"
    assert draft["duration_s"] == 180
    assert draft["title"]["big"] == "RAG"
    # 章名挂在镜头上：面板里增删一镜只动这一镜，不需要在两个列表间对齐编号。
    assert [shot["chapter"] for shot in draft["shots"]] == ["什么是 RAG", "为什么有效"]
    assert draft["shots"][0]["sentences"] == [
        "RAG 先把文档切块入库。",
        "检索时取回最相关的几段。",
    ]
    assert draft["shots"][0]["primitive"] == "flow"
    assert draft["shots"][0]["items"] == ["切块", "入库", "检索", "生成"]
    assert draft["estimated_seconds"] == estimate_script_seconds(
        [
            "RAG 先把文档切块入库。",
            "检索时取回最相关的几段。",
            "模型不必记住全部知识。",
            "答案可以追溯到出处。",
        ]
    )


def test_parse_draft_renumbers_sentences_and_chapters() -> None:
    parsed = parse_explainer_script_draft(_model_draft())

    assert parsed.title.big == "RAG"
    assert [chapter.name for chapter in parsed.chapters] == [
        "什么是 RAG",
        "为什么有效",
    ]
    # 编号一律重算：面板允许增删句子，沿用提交值会在第二镜之后整体错位。
    assert [(shot.sentence_start, shot.sentence_end) for shot in parsed.shots] == [
        (1, 2),
        (3, 4),
    ]
    assert [shot.chapter_index for shot in parsed.shots] == [0, 1]
    assert parsed.narration_text() == (
        "RAG 先把文档切块入库。检索时取回最相关的几段。"
        "模型不必记住全部知识。答案可以追溯到出处。"
    )
    assert parsed.estimated_seconds == _model_draft()["estimated_seconds"]


def test_parse_draft_handles_edited_shots_locally() -> None:
    raw = {
        "draft_version": SCRIPT_DRAFT_VERSION,
        "kind": "explainer",
        "title": {"big": "改过的标题"},
        "shots": [
            {"chapter": "第一章", "sentences": ["第一句。", "第二句。"]},
            # 章名留空 = 沿用上一章（用户合并两章的自然写法）。
            {"chapter": "", "sentences": ["第三句。"]},
            {"chapter": "第二章", "sentences": ["第四句。"]},
            # 整镜删空 = 删掉这一段，不是报错。
            {"chapter": "第二章", "sentences": ["   ", ""]},
        ],
    }
    parsed = parse_explainer_script_draft(raw)

    assert parsed.title.big == "改过的标题"
    assert [chapter.name for chapter in parsed.chapters] == ["第一章", "第二章"]
    assert [chapter.sentences for chapter in parsed.chapters] == [
        ("第一句。", "第二句。", "第三句。"),
        ("第四句。",),
    ]
    assert [(shot.sentence_start, shot.sentence_end) for shot in parsed.shots] == [
        (1, 2),
        (3, 3),
        (4, 4),
    ]


def test_parse_draft_merges_chapter_overflow() -> None:
    raw = {
        "shots": [
            {"chapter": f"第{index}章", "sentences": [f"第{index}句。"]}
            for index in range(1, 7)
        ]
    }
    parsed = parse_explainer_script_draft(raw)

    assert len(parsed.chapters) == MAX_CHAPTERS
    assert sum(len(chapter.sentences) for chapter in parsed.chapters) == 6
    # 多出来的章并回最后一章，名字不变，句子顺序不受影响。
    assert parsed.chapters[-1].name == f"第{MAX_CHAPTERS}章"
    assert len(parsed.chapters[-1].sentences) == 6 - MAX_CHAPTERS + 1


def test_parse_draft_normalizes_vocabulary_and_clips_payload() -> None:
    raw = {
        "shots": [
            {
                "sentences": ["一句话。"],
                "primitive": "HOLOGRAM",
                "motion": "spin",
                "headline": "标" * (HEADLINE_MAX_CHARS + 10),
                "items": ["甲", "乙"],
                "emphasis": 5,
            }
        ]
    }
    parsed = parse_explainer_script_draft(raw)

    shot = parsed.shots[0]
    # 词表外的图元/动效归一化：渲染器永远拿不到画不出来的规格。
    assert shot.primitive == DEFAULT_PRIMITIVE
    assert shot.motion == DEFAULT_MOTION
    assert len(shot.headline) <= HEADLINE_MAX_CHARS
    # 强调下标越界：有 items 时归 0（强调第一条），渲染器不会拿到越界下标。
    assert shot.emphasis == 0
    # 章名缺失时给一个可读的默认章，而不是空字符串。
    assert parsed.chapters[0].name == "讲解"


def test_parse_draft_rejects_unusable_handoffs() -> None:
    with pytest.raises(ValueError, match="台词草稿为空"):
        parse_explainer_script_draft(None)
    with pytest.raises(ValueError, match="更新的版本"):
        parse_explainer_script_draft({"draft_version": SCRIPT_DRAFT_VERSION + 1})
    with pytest.raises(ValueError, match="类型不对"):
        parse_explainer_script_draft({"kind": "whiteboard", "shots": []})
    with pytest.raises(ValueError, match="没有任何镜头"):
        parse_explainer_script_draft({"shots": []})
    with pytest.raises(ValueError, match="没有任何句子"):
        parse_explainer_script_draft({"shots": [{"sentences": ["  "]}]})


def test_parse_draft_rejects_oversize_script_instead_of_truncating() -> None:
    long_sentence = "字" * (MAX_SENTENCE_CHARS + 1)
    with pytest.raises(ValueError, match=str(MAX_SENTENCE_CHARS)):
        parse_explainer_script_draft({"shots": [{"sentences": [long_sentence]}]})

    # 单句都不超限，但总量超了：截断会静默丢台词，所以按「太长」报错。
    sentences = ["字" * MAX_SENTENCE_CHARS] * (
        MAX_SCRIPT_CHARS // MAX_SENTENCE_CHARS + 1
    )
    with pytest.raises(ValueError, match=str(MAX_SCRIPT_CHARS)):
        parse_explainer_script_draft({"shots": [{"sentences": sentences}]})


def test_parse_draft_falls_back_to_requested_duration_without_text() -> None:
    # 空稿已在上面拦下；这里守「没算出估时」不出 None（面板要拿去格式化）。
    parsed = parse_explainer_script_draft(
        {"shots": [{"sentences": ["短。"]}]}, fallback_duration_s=240
    )
    assert parsed.estimated_seconds > 0


# ============================================================
# 2) 契约：白板动画草稿
# ============================================================


def test_build_whiteboard_draft_drops_weight_and_empty_scenes() -> None:
    draft = build_whiteboard_script_draft(
        [
            {
                "text": " 第一幕 旁白。 ",
                "keyText": "重" * 40,
                "scenePrompt": "画一条时间线",
            },
            {"text": "", "keyText": "", "scenePrompt": ""},
        ]
    )

    assert draft["kind"] == "whiteboard"
    assert draft["draft_version"] == SCRIPT_DRAFT_VERSION
    assert len(draft["scenes"]) == 1
    scene = draft["scenes"][0]
    assert scene["text"] == "第一幕 旁白。"
    assert len(scene["keyText"]) <= 12
    # 权重不进草稿：改文案后沿用模型当初给的权重就与朗读量脱钩了。
    assert "weight" not in scene
    assert draft["estimated_seconds"] > 0


def test_parse_whiteboard_draft_recomputes_weight_and_backfills_prompt() -> None:
    parsed = parse_whiteboard_script_draft(
        {
            "kind": "whiteboard",
            "scenes": [
                {"text": "第一幕。", "keyText": "", "scenePrompt": ""},
                {"text": "   ", "scenePrompt": "空幕会被丢掉"},
                {"text": "第二幕。", "keyText": "重点", "scenePrompt": "画两个方块"},
            ],
        }
    )

    assert [scene["text"] for scene in parsed.scenes] == ["第一幕。", "第二幕。"]
    # 只改文案、不管画面时也要出图，否则成片会少一段。
    assert parsed.scenes[0]["scenePrompt"].startswith("围绕这一段讲解画一张简笔示意线稿")
    assert parsed.scenes[1]["scenePrompt"] == "画两个方块"
    assert parsed.scenes[0]["weight"] == float(max(1, len("第一幕。")))
    # 配音一次读完，所以旁白由逐幕文案拼回。
    assert parsed.script == "第一幕。第二幕。"
    assert parsed.estimated_seconds > 0


def test_parse_whiteboard_draft_rejects_unusable_handoffs() -> None:
    with pytest.raises(ValueError, match="台词草稿为空"):
        parse_whiteboard_script_draft(None)
    with pytest.raises(ValueError, match="类型不对"):
        parse_whiteboard_script_draft({"kind": "explainer", "scenes": []})
    with pytest.raises(ValueError, match="没有任何分幕"):
        parse_whiteboard_script_draft({"scenes": []})
    with pytest.raises(ValueError, match="没有任何文案"):
        parse_whiteboard_script_draft({"scenes": [{"text": " "}]})


def test_parse_whiteboard_draft_rejects_too_many_scenes_and_chars() -> None:
    too_many = [
        {"text": f"第{index}幕。", "scenePrompt": "画点什么"}
        for index in range(WHITEBOARD_MAX_SCENES + 1)
    ]
    with pytest.raises(ValueError, match=str(WHITEBOARD_MAX_SCENES)):
        parse_whiteboard_script_draft({"scenes": too_many})

    long_scenes = [
        {"text": "字" * (MAX_SCRIPT_CHARS // 3 + 1), "scenePrompt": "画点什么"}
        for _ in range(3)
    ]
    with pytest.raises(ValueError, match=str(MAX_SCRIPT_CHARS)):
        parse_whiteboard_script_draft({"scenes": long_scenes})


# ============================================================
# 3) 草稿任务 runner
# ============================================================


def _project_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="project-1",
        project_name="project",
        owner_type="user",
        owner_id="user-1",
        owner_username="user",
        requester_user_id="user-1",
        requester_username="user",
        requester_principals=(("user", "user-1"),),
        effective_role="editor",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


class _TaskManager:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_progress_for_project(self, *_args, **kwargs) -> None:
        self.updates.append(kwargs)


def _patch_draft_leaves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    text_responses: list[str],
) -> dict[str, Any]:
    """草稿任务只该碰文本叶：其余叶一律记「被调用了」以便断言没被调用。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {
        "text": [],
        "speech": [],
        "gen": [],
        "archived": [],
    }

    async def fake_generate_text(*, prompt, model=None):
        calls["text"].append({"prompt": prompt, "model": model})
        index = min(len(calls["text"]) - 1, len(text_responses) - 1)
        return "DC-llm-custom", text_responses[index]

    async def fake_audio_speech(**kwargs):
        calls["speech"].append(kwargs)
        raise AssertionError("草稿任务不该配音")

    async def fake_gen(**kwargs):
        calls["gen"].append(kwargs)
        raise AssertionError("草稿任务不该出图")

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_audio_speech,
    )
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_gen", fake_gen)
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: calls["archived"].append(kwargs),
    )
    calls["task_manager"] = task_manager
    return calls


@pytest.mark.asyncio
async def test_explainer_draft_runner_runs_two_text_leaves_and_writes_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_draft_leaves(
        monkeypatch, tmp_path, text_responses=[_NARRATION, _STORYBOARD]
    )

    result = await freezone_runner._run_freezone_explainer_script_async(
        {
            "task_type": "freezone_explainer_script",
            "payload": {
                "job_id": "draft_job",
                "project_dir": str(project_dir),
                "kind": "explainer",
                "reference_content": "RAG 先把文档切块入库。",
                "title": "RAG 是什么",
                "duration_s": 240,
                "model_text": "DC-llm-custom",
            },
        },
        _project_context(tmp_path),
    )

    assert result["kind"] == "explainer"
    assert result["output_format"] == "json"
    assert result["draft_version"] == SCRIPT_DRAFT_VERSION
    assert result["shot_count"] == 2
    # 章名按镜头顺序去重（集合去重会把导航顺序弄丢）。
    assert result["chapter_names"] == ["什么是 RAG", "为什么有效"]
    assert result["model"] == "DC-llm-custom"

    draft_path = Path(result["output_path"])
    assert draft_path == project_dir / "freezone" / "_outputs" / "freezone_explainer_script" / "draft_job.json"
    assert json.loads(draft_path.read_text(encoding="utf-8")) == result["draft"]
    assert result["output_url"] == "/static/freezone/_outputs/freezone_explainer_script/draft_job.json"

    # 两次文本叶：解说词（带参考资料与目标时长）+ 分镜（带编号句子）；模型选型原样透传。
    assert len(calls["text"]) == 2
    assert "RAG 先把文档切块入库" in calls["text"][0]["prompt"]
    assert "240 秒" in calls["text"][0]["prompt"]
    assert "1. RAG 先把文档切块入库。" in calls["text"][1]["prompt"]
    assert all(item["model"] == "DC-llm-custom" for item in calls["text"])
    # 草稿阶段不配音、不出图、不入库。
    assert calls["speech"] == []
    assert calls["gen"] == []
    assert calls["archived"] == []


@pytest.mark.asyncio
async def test_whiteboard_draft_runner_runs_one_text_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    scenes = json.dumps(
        [
            {"text": "第一幕旁白。", "keyText": "第一幕", "scenePrompt": "画一条时间线"},
            {"text": "第二幕旁白。", "keyText": "", "scenePrompt": "画两个方块"},
        ],
        ensure_ascii=False,
    )
    calls = _patch_draft_leaves(monkeypatch, tmp_path, text_responses=[scenes])

    result = await freezone_runner._run_freezone_explainer_script_async(
        {
            "task_type": "freezone_explainer_script",
            "payload": {
                "job_id": "draft_job",
                "project_dir": str(project_dir),
                "kind": "whiteboard",
                "script": "第一幕旁白。第二幕旁白。",
                "max_scenes": 6,
            },
        },
        _project_context(tmp_path),
    )

    assert result["kind"] == "whiteboard"
    assert result["scene_count"] == 2
    assert [scene["text"] for scene in result["draft"]["scenes"]] == [
        "第一幕旁白。",
        "第二幕旁白。",
    ]
    # 白板草稿只切幕：一次文本叶，且 prompt 带幕数上限。
    assert len(calls["text"]) == 1
    assert "6 个" in calls["text"][0]["prompt"]
    assert calls["speech"] == []


@pytest.mark.asyncio
async def test_draft_runner_rejects_bad_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_draft_leaves(monkeypatch, tmp_path, text_responses=[_NARRATION])
    ctx = _project_context(tmp_path)

    def _envelope(**payload: Any) -> dict[str, Any]:
        base = {"job_id": "draft_job", "project_dir": str(project_dir)}
        base.update(payload)
        return {"task_type": "freezone_explainer_script", "payload": base}

    # 未知 kind：夹具是前端契约，跑错族只会产出对不上的草稿。
    with pytest.raises(ValueError, match="unsupported explainer script kind"):
        await freezone_runner._run_freezone_explainer_script_async(
            _envelope(kind="clip"), ctx
        )
    # 白板缺文案 / 讲解缺参考资料：都不该白跑一次文本叶。
    with pytest.raises(ValueError, match="白板台词草稿需要口播文案"):
        await freezone_runner._run_freezone_explainer_script_async(
            _envelope(kind="whiteboard", script="   "), ctx
        )
    with pytest.raises(ValueError, match="required"):
        await freezone_runner._run_freezone_explainer_script_async(
            _envelope(kind="explainer", reference_content="  "), ctx
        )
    assert calls["text"] == []


# ============================================================
# 4) 成片 runner 的锁定稿分支
# ============================================================


def _patch_video_leaves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    narration_text: str = _NARRATION,
    storyboard_text: str = _STORYBOARD,
    narration_ms: int = 12000,
) -> dict[str, Any]:
    """讲解成片的三个叶 + 渲染换成假对象；渲染走 Pillow 分支（不依赖渲染服务）。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {
        "text": [],
        "speech": [],
        "render": [],
        "archived": [],
    }
    voice_path = tmp_path / "voice.mp3"
    voice_path.write_bytes(b"mp3")
    responses = [narration_text, storyboard_text]

    async def fake_generate_text(*, prompt, model=None):
        calls["text"].append({"prompt": prompt, "model": model})
        index = min(len(calls["text"]) - 1, len(responses) - 1)
        return "DC-llm-custom", responses[index]

    async def fake_audio_speech(**kwargs):
        calls["speech"].append(kwargs)
        return SimpleNamespace(
            audio_path=str(voice_path),
            duration_ms=narration_ms,
            model="newapi-tts-1",
        )

    def fake_render(
        *,
        timeline,
        out_path,
        width,
        height,
        fps,
        background,
        narration_path,
        bgm_path,
        bgm_volume,
        on_progress=None,
    ):
        calls["render"].append(
            {
                "scene_count": len(timeline.scenes),
                "narration_path": narration_path,
                "background": background,
            }
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")
        if on_progress is not None:
            on_progress(1.0, "编码完成")
        return out_path

    async def fake_make_store(_ctx):
        return None

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_audio_speech,
    )
    monkeypatch.setattr(
        "novelvideo.freezone.explainer_motion.render_explainer_video", fake_render
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_sqlite_store_for_context", fake_make_store
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: calls["archived"].append(kwargs),
    )
    calls["task_manager"] = task_manager
    return calls


def _video_payload(project_dir: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": "ex_job",
        "project_dir": str(project_dir),
        "reference_content": "RAG 先把文档切块入库，检索时取回最相关的几段。",
        "title": "RAG 是什么",
        "duration_s": 180,
        "model_text": "DC-llm-custom",
        "model": "newapi-tts-1",
        "background": "dots",
        "resolution": "720p",
        "archive_to_library": True,
        "source_tool": "explainer-video",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_explainer_video_uses_locked_script_without_text_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_video_leaves(monkeypatch, tmp_path)
    draft = _model_draft()

    result = await freezone_runner._run_freezone_explainer_video_async(
        {
            "task_type": "freezone_explainer_video",
            "payload": _video_payload(project_dir, locked_script=draft),
        },
        _project_context(tmp_path),
    )

    # 用户确认过的稿子：两次文本调用全部跳过，配音读的就是草稿里的句子。
    assert calls["text"] == []
    assert result["script_source"] == "draft"
    assert result["narration_model"] == "explainer-script-draft"
    assert result["storyboard_model"] == "explainer-script-draft"
    assert result["narration"] == (
        "RAG 先把文档切块入库。检索时取回最相关的几段。"
        "模型不必记住全部知识。答案可以追溯到出处。"
    )
    assert result["chapters"] == ["什么是 RAG", "为什么有效"]
    assert result["title"] == "RAG"
    assert result["shot_count"] == 2
    assert result["script_estimated_seconds"] == pytest.approx(
        draft["estimated_seconds"]
    )
    assert calls["speech"][0]["text"] == result["narration"]
    assert len(calls["render"]) == 1


@pytest.mark.asyncio
async def test_explainer_video_accepts_edited_locked_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_video_leaves(monkeypatch, tmp_path)
    draft = _model_draft()
    # 面板里的典型编辑：改一句、删一镜、加一句到末镜。
    draft["shots"][0]["sentences"] = ["RAG 先把资料切块入库。"]
    draft["shots"][1]["sentences"] = [
        "模型不必背下全部知识。",
        "答案能追溯到出处。",
    ]

    result = await freezone_runner._run_freezone_explainer_video_async(
        {
            "task_type": "freezone_explainer_video",
            "payload": _video_payload(project_dir, locked_script=draft),
        },
        _project_context(tmp_path),
    )

    assert calls["text"] == []
    assert result["narration"] == (
        "RAG 先把资料切块入库。模型不必背下全部知识。答案能追溯到出处。"
    )


@pytest.mark.asyncio
async def test_explainer_video_rejects_invalid_locked_script_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_video_leaves(monkeypatch, tmp_path)

    # 草稿类型不对：宁可失败也不偷偷换成模型现写的台词。
    with pytest.raises(ValueError, match="类型不对"):
        await freezone_runner._run_freezone_explainer_video_async(
            {
                "task_type": "freezone_explainer_video",
                "payload": _video_payload(
                    project_dir,
                    locked_script={
                        "kind": "whiteboard",
                        "scenes": [{"text": "第一幕。", "scenePrompt": "画线"}],
                    },
                ),
            },
            _project_context(tmp_path),
        )
    assert calls["text"] == []
    assert calls["speech"] == []

    # 台词被删空：同样失败，不回退。
    with pytest.raises(ValueError, match="没有任何句子"):
        await freezone_runner._run_freezone_explainer_video_async(
            {
                "task_type": "freezone_explainer_video",
                "payload": _video_payload(
                    project_dir,
                    locked_script={"kind": "explainer", "shots": [{"sentences": []}]},
                ),
            },
            _project_context(tmp_path),
        )
    assert calls["text"] == []


def _patch_whiteboard_leaves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Any]:
    """白板成片的四类叶（文本/图像/配音/混剪）+ 本地叠加与逐笔渲染换成假对象。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {"text": [], "speech": [], "gen": [], "mix": [], "archived": []}
    voice_path = tmp_path / "voice.mp3"
    voice_path.write_bytes(b"mp3")

    async def fake_generate_text(*, prompt, model=None):
        calls["text"].append({"prompt": prompt, "model": model})
        raise AssertionError("锁定稿不该再调用文本模型")

    async def fake_audio_speech(**kwargs):
        calls["speech"].append(kwargs)
        return SimpleNamespace(
            audio_path=str(voice_path),
            duration_ms=9000,
            model="newapi-tts-1",
        )

    async def fake_gen(**kwargs):
        calls["gen"].append(kwargs)
        out = tmp_path / f"{kwargs['job_id']}.png"
        out.write_bytes(b"png")
        return str(out)

    def fake_overlay(*, image_path, out_path, text, width, height):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"png")

    def fake_render_scene(*, image_path, out_path, duration_ms, width, height, fps, pen_cursor):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")

    async def fake_mix(**kwargs):
        calls["mix"].append(kwargs)
        output_path = Path(kwargs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        progress = kwargs.get("on_progress")
        if progress is not None:
            progress(1.0, "拼接完成")
        return SimpleNamespace(output_path=str(output_path))

    async def fake_make_store(_ctx):
        return None

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_gen", fake_gen
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_mix_render", fake_mix
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_audio_speech,
    )
    monkeypatch.setattr(
        "novelvideo.freezone.whiteboard.overlay_key_text", fake_overlay
    )
    monkeypatch.setattr(
        "novelvideo.freezone.whiteboard.render_whiteboard_scene", fake_render_scene
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_sqlite_store_for_context", fake_make_store
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: calls["archived"].append(kwargs),
    )
    calls["task_manager"] = task_manager
    return calls


@pytest.mark.asyncio
async def test_whiteboard_video_uses_locked_script_scenes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_whiteboard_leaves(monkeypatch, tmp_path)
    locked = build_whiteboard_script_draft(
        [
            {"text": "第一幕旁白。", "keyText": "第一幕", "scenePrompt": "画一条时间线"},
            {"text": "第二幕旁白。", "keyText": "", "scenePrompt": "画两个方块"},
        ]
    )

    result = await freezone_runner._run_freezone_whiteboard_animation_async(
        {
            "task_type": "freezone_whiteboard_animation",
            "payload": {
                "job_id": "wb_job",
                "project_dir": str(project_dir),
                "script": "",
                "locked_script": locked,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "archive_to_library": True,
                "source_tool": "whiteboard-animation",
            },
        },
        _project_context(tmp_path),
    )

    # 幕数与逐幕文案都来自草稿：分镜规划那次文本调用整个跳过。
    assert calls["text"] == []
    assert result["script_source"] == "draft"
    assert result["plan_model"] == "whiteboard-script-draft"
    assert result["scene_count"] == 2
    assert result["script"] == "第一幕旁白。第二幕旁白。"
    assert calls["speech"][0]["text"] == result["script"]
    # 每幕各出一张线稿（重点字那幕还会叠字），时长按字数分配。
    assert len(calls["gen"]) == 2
    assert len(calls["mix"][0]["material_paths"]) == 2


# ============================================================
# 5) 草稿端点
# ============================================================


def _patch_script_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Any]:
    from novelvideo.api.routes import freezone as freezone_routes
    from novelvideo.api.routes.freezone import explainer_script_ops

    captured: dict[str, Any] = {}

    async def _fake_resolve(project: str, user: dict, **_kwargs):
        del user
        ctx = SimpleNamespace(requester_user_id="user-1", project_id=f"project_{project}")
        return ctx, "admin", project, tmp_path, str(tmp_path)

    async def _fake_projection(**kwargs):
        captured["projection"] = kwargs
        return {"projection": {"voice_registry": {}}}

    async def _fake_enqueue(**kwargs):
        captured["enqueue"] = kwargs
        return {"ok": True, "data": {"task_id": "task_1", "task_type": "freezone_explainer_script"}}

    monkeypatch.setattr(
        explainer_script_ops._freezone, "_resolve_freezone_project", _fake_resolve
    )
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", _fake_projection)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "draft_job")
    monkeypatch.setattr(
        freezone_routes,
        "_enqueue_or_start_freezone_explainer_script",
        _fake_enqueue,
    )
    return captured


@pytest.mark.asyncio
async def test_script_route_enqueues_explainer_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.api.routes.freezone import explainer_script_ops
    from novelvideo.api.schemas import FreezoneExplainerScriptRequest

    captured = _patch_script_route(monkeypatch, tmp_path)

    result = await explainer_script_ops.freezone_explainer_script(
        "59",
        FreezoneExplainerScriptRequest(
            kind="explainer",
            reference_content="  RAG 把文档切块入库。  ",
            title="  RAG 是什么  ",
            duration_s=240,
            model_text="  DC-llm-custom  ",
            source_tool="explainer-video",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    payload = captured["enqueue"]["payload"]
    assert payload["kind"] == "explainer"
    # 清洗与闸门复用成片那一份：草稿放行的资料，成片也一定放行。
    assert payload["reference_content"] == "RAG 把文档切块入库。"
    assert payload["script"] == ""
    assert payload["title"] == "RAG 是什么"
    assert payload["duration_s"] == 240
    assert payload["model_text"] == "DC-llm-custom"
    assert payload["source_tool"] == "explainer-video"
    assert captured["enqueue"]["job_id"] == "draft_job"
    assert captured["projection"]["task_type"] == "freezone_explainer_script"


@pytest.mark.asyncio
async def test_script_route_enqueues_whiteboard_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.api.routes.freezone import explainer_script_ops
    from novelvideo.api.schemas import FreezoneExplainerScriptRequest

    captured = _patch_script_route(monkeypatch, tmp_path)

    await explainer_script_ops.freezone_explainer_script(
        "59",
        FreezoneExplainerScriptRequest(
            kind="whiteboard",
            # 两个族的字段互不越界：白板不读参考资料，讲解不读文案。
            reference_content="这段资料应该被忽略",
            script="  第一幕旁白。第二幕旁白。  ",
            max_scenes=6,
            source_tool="whiteboard-animation",
        ),
        user={"username": "admin"},
    )

    payload = captured["enqueue"]["payload"]
    assert payload["kind"] == "whiteboard"
    assert payload["script"] == "第一幕旁白。第二幕旁白。"
    assert payload["reference_content"] == ""
    assert payload["max_scenes"] == 6


@pytest.mark.asyncio
async def test_script_route_rejects_bad_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.api.routes.freezone import explainer_script_ops
    from novelvideo.api.schemas import FreezoneExplainerScriptRequest

    captured = _patch_script_route(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as whiteboard:
        await explainer_script_ops.freezone_explainer_script(
            "59",
            FreezoneExplainerScriptRequest(kind="whiteboard", script="   "),
            user={"username": "admin"},
        )
    assert whiteboard.value.status_code == 400
    assert "script is required" in str(whiteboard.value.detail)

    with pytest.raises(HTTPException) as too_long:
        await explainer_script_ops.freezone_explainer_script(
            "59",
            FreezoneExplainerScriptRequest(
                kind="whiteboard",
                script="字" * (explainer_script_ops.MAX_WHITEBOARD_SCRIPT_CHARS + 1),
            ),
            user={"username": "admin"},
        )
    assert too_long.value.status_code == 400

    with pytest.raises(HTTPException) as no_reference:
        await explainer_script_ops.freezone_explainer_script(
            "59",
            FreezoneExplainerScriptRequest(kind="explainer", reference_content="   "),
            user={"username": "admin"},
        )
    assert no_reference.value.status_code == 400

    # kind 的白名单校验在路由里（schema 的 Literal 之外再兜一层，防内部调用绕过）。
    with pytest.raises(HTTPException) as bad_kind:
        await explainer_script_ops.freezone_explainer_script(
            "59",
            SimpleNamespace(
                kind="clip",
                script="",
                reference_content="资料",
                title="",
                duration_s=180,
                max_scenes=8,
                model_text="",
                source_tool="",
            ),
            user={"username": "admin"},
        )
    assert bad_kind.value.status_code == 400

    # 上面四次全部在入队之前被拦下：不会留下空跑的任务。
    assert "enqueue" not in captured


def test_script_output_path_matches_task_bucket(tmp_path: Path) -> None:
    from novelvideo.api.routes.freezone.explainer_script_ops import (
        _explainer_script_output_path,
    )

    path = _explainer_script_output_path(tmp_path, "draft_job")
    # 与 runner 落盘位置一致：jobs 子模块按同一份口径回读产物。
    assert path == (
        tmp_path / "freezone" / "_outputs" / "freezone_explainer_script" / "draft_job.json"
    )
    assert path.suffix == ".json"
    assert DEFAULT_DURATION_S > 0
