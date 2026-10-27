"""[aipdd fork delta] 科普讲解视频（freezone_explainer_video）后端契约测试。

覆盖三层：
1. 入队：走 video 队列、载荷原样透传（只补 job_id / project_dir）；
2. 路由：参考资料闸门、配音音频目录定型为内部执行身份、时长/幕底/清晰度/画幅
   定型透传，以及各类 4xx；
3. runner 全链：参考资料 → 解说词 leaf → 分镜 leaf → 配音 leaf → 本地时间轴 →
   逐帧渲染（fake）→ 归档。

逐帧绘制的确定性与编码命令在 ``test_freezone_explainer_motion.py`` 已有单测；
本文件守「链路编排与产物形状」，三个生成叶与渲染全部换成显式签名假对象。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.explainer import MIN_SHOT_FRAMES
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


# ---------------------------------------------------------------
# 1) 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_explainer_enqueue_uses_video_queue_and_verbatim_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_enqueue_project_task(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task_1"),
            backend="celery",
            queue="node.video",
        )

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )

    ctx = SimpleNamespace(project_id="project_59", requester_user_id="user-1")
    result = await freezone_routes._enqueue_or_start_freezone_explainer_video(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="ex_job",
        payload={"reference_content": "RAG 先把文档切块入库。", "duration_s": 240},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_explainer_video"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_explainer_video"
    # 画面是长任务（逐帧绘制 + 编码），必须落 video 队列而不是 default。
    assert captured["queue_kind"] == "video"
    assert captured["episode"] == 0
    assert captured["scope"] == "ex_job"
    assert captured["payload"] == {
        "job_id": "ex_job",
        "project_dir": str(tmp_path),
        "reference_content": "RAG 先把文档切块入库。",
        "duration_s": 240,
    }


@pytest.mark.asyncio
async def test_explainer_enqueue_requires_project_context() -> None:
    with pytest.raises(HTTPException) as info:
        await freezone_routes._enqueue_or_start_freezone_explainer_video(
            ctx=None,
            username="admin",
            project="59",
            project_dir=Path("/tmp"),
            output_dir="/tmp",
            job_id="ex_job",
            payload={},
        )
    assert info.value.status_code == 503


# ---------------------------------------------------------------
# 2) 路由
# ---------------------------------------------------------------


def _ex_ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )


def _patch_ex_route_resolve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ctx: SimpleNamespace,
    mapping: dict[str, Path],
) -> None:
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(
        freezone_routes, "resolve_static_url_to_path", lambda url, _dir: mapping[url]
    )
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "ex_job")


def _patch_ex_catalogs(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
) -> list[tuple]:
    catalog_calls: list[tuple] = []

    async def fake_audio_catalog(media_type, *, requester_user_id):
        catalog_calls.append((media_type, requester_user_id))
        return [{"id": "tts-a", "apiModel": "newapi-tts-1"}]

    async def fake_projection(**kwargs):
        captured["projection_task_type"] = kwargs["task_type"]
        captured["projection_extra_config"] = kwargs["extra_config"]
        return {"projection": {"voice_registry": {}}}

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", fake_audio_catalog)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    return catalog_calls


def _patch_ex_enqueue(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]):
    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_explainer_video",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_explainer_video:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_explainer_video", fake_enqueue
    )


@pytest.mark.asyncio
async def test_explainer_route_pins_voice_model_and_render_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"mp3")
    ctx = _ex_ctx(tmp_path)
    bgm_url = "/static/admin/59/freezone/_uploads/bgm.mp3"
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {bgm_url: bgm})

    captured: dict[str, object] = {}
    catalog_calls = _patch_ex_catalogs(monkeypatch, captured)
    _patch_ex_enqueue(monkeypatch, captured)

    result = await freezone_routes.freezone_explainer_video(
        project="59",
        body=freezone_routes.FreezoneExplainerVideoRequest(
            reference_content="  RAG 先把文档切块入库，检索时取回最相关的几段。  ",
            title=" RAG 是什么 ",
            duration_s=1200,
            model_text="DC-llm-custom",
            model="tts-a",
            background="stars",
            resolution="1080p",
            emotion_prompt="沉稳、克制",
            voice_ref={"scope": "user_custom", "voice_id": "v-9"},
            bgm_url=bgm_url,
            bgm_volume=0.25,
            archive_to_library=True,
            source_tool="explainer-video",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    # 配音目录命中后定型为内部执行身份（apiModel），不是前端目录 id。
    assert ("audio", "user-1") in catalog_calls
    assert captured["model"] == "newapi-tts-1"
    assert captured["reference_content"] == "RAG 先把文档切块入库，检索时取回最相关的几段。"
    assert captured["title"] == "RAG 是什么"
    # 时长在路由层就夹进样片档，runner 不必再防越界。
    assert captured["duration_s"] == 330
    assert captured["model_text"] == "DC-llm-custom"
    assert captured["background"] == "stars"
    assert captured["resolution"] == "1080p"
    assert captured["emotion_prompt"] == "沉稳、克制"
    assert captured["voice_ref"] == {
        "scope": "user_custom",
        "character_name": "",
        "identity_id": "",
        "slot": "",
        "voice_id": "v-9",
    }
    assert captured["bgm_path"] == bgm.as_posix()
    assert captured["bgm_volume"] == 0.25
    assert captured["archive_to_library"] is True
    assert captured["source_tool"] == "explainer-video"
    assert captured["projection_task_type"] == "freezone_explainer_video"


@pytest.mark.asyncio
async def test_explainer_route_forwards_locked_script_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两段式第二段：路由必须把确认过的整份草稿塞进 payload。

    守的是 HTTP 接线层——runner 侧收到 ``payload["locked_script"]`` 才跳过文本模型。
    只测 runner 的会话漏掉这一层，会让「改完白改」（静默重新生成解说词）在无报错下复发。
    """
    ctx = _ex_ctx(tmp_path)
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {})
    captured: dict[str, object] = {}
    _patch_ex_catalogs(monkeypatch, captured)
    _patch_ex_enqueue(monkeypatch, captured)

    locked = {
        "draft_version": 1,
        "kind": "explainer",
        "shots": [{"sentences": ["用户改过的解说词。"], "primitive": "text"}],
    }

    await freezone_routes.freezone_explainer_video(
        project="59",
        body=freezone_routes.FreezoneExplainerVideoRequest(
            reference_content="一份参考资料。",
            locked_script=locked,
        ),
        user={"username": "admin"},
    )

    assert captured["locked_script"] == locked


@pytest.mark.asyncio
async def test_explainer_route_defaults_and_skips_audio_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ex_ctx(tmp_path)
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {})
    captured: dict[str, object] = {}
    catalog_calls = _patch_ex_catalogs(monkeypatch, captured)
    _patch_ex_enqueue(monkeypatch, captured)

    await freezone_routes.freezone_explainer_video(
        project="59",
        body=freezone_routes.FreezoneExplainerVideoRequest(
            reference_content="一句足够短的说明。",
        ),
        user={"username": "admin"},
    )
    # 没传 model 就不查音频目录（默认语音引擎无需定型）。
    assert catalog_calls == []
    assert captured["model"] == ""
    assert captured["duration_s"] == 180
    assert captured["background"] == "dots"
    assert captured["resolution"] == "720p"
    assert captured["bgm_path"] == ""
    assert captured["voice_ref"] is None


@pytest.mark.asyncio
async def test_explainer_route_rejects_empty_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ex_ctx(tmp_path)
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {})
    with pytest.raises(HTTPException) as info:
        await freezone_routes.freezone_explainer_video(
            project="59",
            body=freezone_routes.FreezoneExplainerVideoRequest(reference_content="   "),
            user={"username": "admin"},
        )
    assert info.value.status_code == 400


@pytest.mark.asyncio
async def test_explainer_route_rejects_unknown_audio_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ex_ctx(tmp_path)
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {})
    captured: dict[str, object] = {}
    _patch_ex_catalogs(monkeypatch, captured)
    with pytest.raises(HTTPException) as info:
        await freezone_routes.freezone_explainer_video(
            project="59",
            body=freezone_routes.FreezoneExplainerVideoRequest(
                reference_content="一段资料。", model="tts-missing"
            ),
            user={"username": "admin"},
        )
    assert info.value.status_code == 409


@pytest.mark.asyncio
async def test_explainer_route_rejects_missing_bgm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ex_ctx(tmp_path)
    url = "/static/admin/59/freezone/_uploads/gone.mp3"
    _patch_ex_route_resolve(monkeypatch, tmp_path, ctx, {url: tmp_path / "gone.mp3"})
    captured: dict[str, object] = {}
    _patch_ex_catalogs(monkeypatch, captured)
    with pytest.raises(HTTPException) as info:
        await freezone_routes.freezone_explainer_video(
            project="59",
            body=freezone_routes.FreezoneExplainerVideoRequest(
                reference_content="一段资料。", bgm_url=url
            ),
            user={"username": "admin"},
        )
    assert info.value.status_code == 404

# ---------------------------------------------------------------
# 3) runner 全链
# ---------------------------------------------------------------


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


_NARRATION = (
    "# CHAPTER 1 什么是 RAG\n"
    "RAG 先把文档切块入库。检索时取回最相关的几段。\n"
    "# CHAPTER 2 为什么有效\n"
    "模型不必记住全部知识。答案可以追溯到出处。"
)
_STORYBOARD = json.dumps(
    {
        "title": {"big": "RAG", "rest": "检索增强生成", "tagline": "让模型带着资料回答"},
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
_NARRATION_SCRIPT = (
    "RAG 先把文档切块入库。检索时取回最相关的几段。"
    "模型不必记住全部知识。答案可以追溯到出处。"
)


def _ex_payload(project_dir: Path, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "job_id": "ex_job",
        "project_dir": str(project_dir),
        "reference_content": "RAG 先把文档切块入库，检索时取回最相关的几段。",
        "title": "RAG 是什么",
        "duration_s": 180,
        "model_text": "DC-llm-custom",
        "model": "newapi-tts-1",
        "background": "dots",
        "resolution": "720p",
        "emotion_prompt": "沉稳、克制",
        "voice_ref": None,
        "bgm_path": "",
        "bgm_volume": None,
        "account_voice_username": "",
        "archive_to_library": True,
        "source_tool": "explainer-video",
    }
    payload.update(overrides)
    return payload


def _patch_ex_runner_leaves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    narration_text: str = _NARRATION,
    storyboard_text: str = _STORYBOARD,
    narration_ms: int = 12000,
) -> dict[str, Any]:
    """三个生成叶与渲染全部换成显式签名假对象：本文件只守链路编排与产物形状。"""
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
                "total_frames": timeline.total_frames,
                "scene_count": len(timeline.scenes),
                "out_path": out_path,
                "width": width,
                "height": height,
                "fps": fps,
                "background": background,
                "narration_path": narration_path,
                "bgm_path": bgm_path,
                "bgm_volume": bgm_volume,
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


@pytest.mark.asyncio
async def test_explainer_runner_narrates_plans_dubs_and_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_ex_runner_leaves(monkeypatch, tmp_path)

    result = await freezone_runner._run_freezone_explainer_video_async(
        {
            "task_type": "freezone_explainer_video",
            "payload": _ex_payload(project_dir),
        },
        _project_context(tmp_path),
    )

    assert result["job_id"] == "ex_job"
    assert result["media_type"] == "video"
    assert result["narration"] == _NARRATION_SCRIPT
    assert result["chapters"] == ["什么是 RAG", "为什么有效"]
    assert result["title"] == "RAG"
    assert result["narration_model"] == "DC-llm-custom"
    assert result["storyboard_model"] == "DC-llm-custom"
    assert result["model"] == "newapi-tts-1"
    assert result["shot_count"] == 2
    assert result["narration_duration_ms"] == 12000
    assert result["requested_duration_s"] == 180
    assert result["fps"] == 30
    assert result["background"] == "dots"
    # 讲解片固定横屏，不受入参影响。
    assert result["aspect_ratio"] == "16:9"
    assert result["resolution"] == "720p"
    assert len(result["chapter_starts_seconds"]) == 2
    assert result["chapter_starts_seconds"][1] > 0
    assert result["duration_seconds"] == pytest.approx(12.0, abs=0.1)

    # 两次文本 leaf：解说词 prompt 带参考资料，分镜 prompt 带句子编号；模型选型原样传。
    assert len(calls["text"]) == 2
    assert "RAG 先把文档切块入库" in calls["text"][0]["prompt"]
    assert "CHAPTER" in calls["text"][0]["prompt"]
    assert "1. RAG 先把文档切块入库。" in calls["text"][1]["prompt"]
    assert all(item["model"] == "DC-llm-custom" for item in calls["text"])

    # 配音 leaf：独立 job 号、整段解说词直达、目录执行身份原样。
    assert len(calls["speech"]) == 1
    assert calls["speech"][0]["job_id"] == "ex_job_voice"
    assert calls["speech"][0]["text"] == _NARRATION_SCRIPT
    assert calls["speech"][0]["model"] == "newapi-tts-1"
    assert calls["speech"][0]["emotion_prompt"] == "沉稳、克制"

    # 渲染 leaf：16:9 720p 画布、配音与默认音量、无 BGM 传 None。
    assert len(calls["render"]) == 1
    render_call = calls["render"][0]
    assert render_call["width"] == 1280
    assert render_call["height"] == 720
    assert render_call["fps"] == 30
    assert render_call["background"] == "dots"
    assert render_call["narration_path"] == str(tmp_path / "voice.mp3")
    assert render_call["bgm_path"] is None
    assert render_call["bgm_volume"] == pytest.approx(0.2)
    # 每一镜都不短于最短镜时长（短镜头已在时间轴里并框）。
    assert render_call["total_frames"] >= render_call["scene_count"] * MIN_SHOT_FRAMES

    expected_output = outputs_dir(project_dir, "freezone_explainer_video") / "ex_job.mp4"
    assert render_call["out_path"] == expected_output
    assert result["output_path"] == str(expected_output)
    assert (
        result["output_url"]
        == "/static/freezone/_outputs/freezone_explainer_video/ex_job.mp4"
    )

    # 归档：一次提交一条成片。
    assert len(calls["archived"]) == 1
    assert calls["archived"][0]["media"] == "video"
    assert calls["archived"][0]["task_type"] == "freezone_explainer_video"
    assert calls["archived"][0]["url"] == result["output_url"]

    # 进度：解说词 / 分镜 / 配音 / 逐帧绘制固定四点 + 渲染轮询折算 + 归档。
    progresses = [update["progress"] for update in calls["task_manager"].updates]
    assert progresses[:4] == [0.05, 0.16, 0.26, 0.32]
    assert progresses[-1] == 0.97
    assert all(0.32 < value <= 0.94 for value in progresses[4:-1])


@pytest.mark.asyncio
async def test_explainer_runner_passes_bgm_and_explicit_zero_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"mp3")
    calls = _patch_ex_runner_leaves(monkeypatch, tmp_path)

    await freezone_runner._run_freezone_explainer_video_async(
        {
            "task_type": "freezone_explainer_video",
            "payload": _ex_payload(
                project_dir, background="stars", resolution="1080p",
                bgm_path=str(bgm), bgm_volume=0.0,
            ),
        },
        _project_context(tmp_path),
    )

    render_call = calls["render"][0]
    assert render_call["bgm_path"] == str(bgm)
    # 0 是用户显式关掉 BGM，不能被默认值覆盖。
    assert render_call["bgm_volume"] == 0.0
    assert render_call["background"] == "stars"
    assert render_call["width"] == 1920
    assert render_call["height"] == 1080


@pytest.mark.asyncio
async def test_explainer_runner_falls_back_to_reference_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_ex_runner_leaves(monkeypatch, tmp_path, narration_text="")

    result = await freezone_runner._run_freezone_explainer_video_async(
        {
            "task_type": "freezone_explainer_video",
            "payload": _ex_payload(
                project_dir,
                reference_content="RAG 先把文档切块入库。\n检索时取回最相关的几段。",
            ),
        },
        _project_context(tmp_path),
    )

    # 模型没按格式回解说词不致命：参考资料按行兜底，链路照旧出片。
    assert result["chapters"] == ["讲解"]
    assert result["narration"] == (
        "RAG 先把文档切块入库。检索时取回最相关的几段。"
    )
    assert result["shot_count"] >= 1
    # 分镜 prompt 里仍然是本地编号后的兜底解说词，链路两段文本 leaf 都跑。
    assert len(calls["text"]) == 2
    assert "1. RAG 先把文档切块入库。" in calls["text"][1]["prompt"]


@pytest.mark.asyncio
async def test_explainer_runner_rejects_invalid_narration_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    _patch_ex_runner_leaves(monkeypatch, tmp_path, narration_ms=0)

    with pytest.raises(RuntimeError, match="explainer narration duration is invalid"):
        await freezone_runner._run_freezone_explainer_video_async(
            {
                "task_type": "freezone_explainer_video",
                "payload": _ex_payload(project_dir),
            },
            _project_context(tmp_path),
        )
