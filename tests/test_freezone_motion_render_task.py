# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效大片渲染任务（freezone_motion_render）后端契约测试。
"""覆盖三层：入队走 video 队列、路由的提交前闸门（空页面 / 本机依赖缺失）、
runner 的编排与产物形状（时长换算、拍点透传、节点历史、资产归档）。

渲染引擎自身的确定性、逐帧调用形状与编码命令在
``test_freezone_motion_render.py`` 已单测；本文件只守「任务链路」这一层，
Chrome 与 ffmpeg 全部换成假对象，绝不起真浏览器。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import motion_audio, motion_render
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import freezone as freezone_runner

_PAGE = "<!doctype html><html><head></head><body><canvas id=c></canvas></body></html>"
# 文本模型按契约产出的"好页面"：带 window.render(t) 与 window.DUR，且裹在代码围栏里
# （模型实际就是这么回的），用于验证 runner 会先把源码收出来。
_GOOD_PAGE_REPLY = (
    "好的，下面是完整页面：\n```html\n<!doctype html><html><body><canvas id=c></canvas>"
    "<script>window.DUR=2.0;window.render=function(t){};</script></body></html>\n```\n"
    "以上就是单文件自包含的实现。"
)


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

    def update_progress_for_project(self, *_args: Any, **kwargs: Any) -> None:
        self.updates.append(kwargs)


# --------------------------------------------------------------- 1) 入队


@pytest.mark.asyncio
async def test_motion_render_enqueue_uses_video_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    result = await freezone_routes._enqueue_or_start_freezone_motion_render(
        ctx=SimpleNamespace(project_id="project_59", requester_user_id="user-1"),
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="mg_job",
        payload={"html": _PAGE, "fps": 24},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_motion_render"
    assert captured["queue_kind"] == "video"
    assert captured["payload"]["job_id"] == "mg_job"
    assert captured["payload"]["project_dir"] == str(tmp_path)
    assert captured["payload"]["html"] == _PAGE


# --------------------------------------------------------------- 2) 路由


def _route_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, preflight_error: str = ""
) -> tuple[TestClient, list[dict]]:
    from novelvideo.api.routes.freezone import motion as motion_routes

    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    ctx = _project_context(tmp_path)

    async def fake_resolve(*_args, **_kwargs):
        return (ctx, "admin", "project", tmp_path / "output", str(tmp_path / "output"))

    submitted: list[dict] = []

    async def fake_enqueue(**kwargs):
        submitted.append(kwargs)
        return {"ok": True, "data": {"job_id": kwargs["job_id"]}}

    def fake_preflight() -> None:
        if preflight_error:
            raise motion_render.MotionRenderError(preflight_error)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone_routes, "_enqueue_or_start_freezone_motion_render", fake_enqueue)
    monkeypatch.setattr(motion_routes, "render_preflight", fake_preflight)
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    return TestClient(app), submitted


def _post(client: TestClient, body: dict) -> Any:
    return client.post("/api/v1/projects/59/freezone/motion/render", json=body)


def test_route_rejects_an_empty_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(client, {"html": "   "})

    assert response.status_code == 400
    assert "MG 页面内容为空" in response.json()["detail"]
    assert submitted == []


def test_route_refuses_to_queue_when_local_deps_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(
        monkeypatch, tmp_path, preflight_error="本机找不到可用的 Chrome：桌面版请用自带 Chrome 的安装包"
    )

    response = _post(client, {"html": _PAGE})

    assert response.status_code == 409
    assert "Chrome" in response.json()["detail"]
    # 注定失败的任务不许排进队列（用户当场拿到出路，而不是任务列表里的红点）。
    assert submitted == []


def test_route_forwards_the_render_choices_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(
        client,
        {
            "html": _PAGE,
            "case_id": "M43",
            "title": "产品宣传片",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "fps": 25,
            "duration_seconds": 6,
            "node_id": "node_7",
            "archive_to_library": True,
            "episode": 2,
            "beat_number": 5,
        },
    )

    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["case_id"] == "M43"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "1080p"
    assert payload["fps"] == 25
    assert payload["duration_seconds"] == 6
    assert payload["node_id"] == "node_7"
    assert payload["archive_to_library"] is True
    # 集号/镜号要原样进 payload：导出剪映草稿靠它们把成片归位到对应镜头。
    assert payload["episode"] == 2
    assert payload["beat_number"] == 5
    # 配乐默认开着；false 是"只要画面"，任务里就不许起 ffmpeg 排乐。
    assert payload["music"] is True

    assert _post(client, {"html": _PAGE, "music": False}).status_code == 200
    assert submitted[1]["payload"]["music"] is False


def test_route_forwards_a_case_only_submission_for_page_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """没贴页面、只给案例 + 镜头文案：路由不许拦，并把两个字段原样交给任务。"""
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(
        client,
        {"case_id": "M43", "subject": "黑底白字开场", "model_text": "gpt-x"},
    )

    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["html"] == ""
    assert payload["case_id"] == "M43"
    assert payload["subject"] == "黑底白字开场"
    assert payload["model_text"] == "gpt-x"


# --------------------------------------------------------------- 3) runner


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    frames: int = 60,
    duration: float = 2.0,
    page_duration: float = 2.0,
    events: tuple[dict, ...] = ({"t": 1.0, "type": "swap"},),
    width: int = 1280,
    height: int = 720,
    reply: str = _GOOD_PAGE_REPLY,
    score_error: str = "",
) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "render": [],
        "archived": [],
        "text": [],
        "score": [],
        # 模型回复可被单个用例改写：默认给一页合规的 MG 源码。
        "reply": reply,
        "score_error": score_error,
    }
    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    def fake_archive(**kwargs):
        # 复刻真实 helper 的口径：runner 无条件调用，是否入库由 payload 的标记决定；
        # 登记成功后就地给 result 打 archived_to_library 标记。
        if not kwargs["payload"].get("archive_to_library"):
            return
        calls["archived"].append(kwargs)
        kwargs["result"]["archived_to_library"] = True
        kwargs["result"]["library_item_id"] = "tool-mg_job"

    monkeypatch.setattr(freezone_runner, "_archive_tool_output_to_library", fake_archive)

    async def fake_render(html, out_path, **kwargs):
        on_progress = kwargs.get("on_progress")
        calls["render"].append({"html": html, "chars": len(html), **kwargs})
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"mp4")
        target.with_suffix(".html").write_text(html, encoding="utf-8")
        motion_render.page_source_path(target.with_suffix(".html")).write_text(
            html, encoding="utf-8"
        )
        if on_progress is not None:
            on_progress(frames, frames)
        return motion_render.MotionRenderResult(
            path=target,
            width=width,
            height=height,
            plan=motion_render.FramePlan(
                frames=frames,
                fps=kwargs.get("fps") or 30,
                duration_seconds=duration,
                page_duration_seconds=page_duration,
            ),
            events=events,
        )

    monkeypatch.setattr(motion_render, "render_motion_video", fake_render)

    # 编曲那一段在测试里只留「参数与产物形状」：真 ffmpeg/loudnorm 由
    # test_freezone_motion_audio_ffmpeg.py 单独守，这里不许起子进程。
    async def fake_score(video_path: Path, **kwargs):
        calls["score"].append({"video_path": str(video_path), **kwargs})
        if calls["score_error"]:
            raise motion_audio.MotionAudioError(calls["score_error"])
        score_path = Path(video_path).with_suffix(".score.wav")
        score_path.write_bytes(b"RIFF")
        return motion_audio.MotionScore(
            score_path=score_path,
            bpm=120.0,
            grid_source="events",
            style="pulse",
            notes=48,
            duration_seconds=float(kwargs["duration_seconds"]),
            sample_rate=44100,
            loudness=motion_audio.LoudnessReport(-18.2, -3.1, 9.4, -25.0, 0.6),
        )

    monkeypatch.setattr(motion_audio, "score_and_attach", fake_score)

    async def fake_generate_text(*_args, **kwargs):
        calls["text"].append(kwargs)
        return ("mg-text-model", calls["reply"])

    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    calls["task_manager"] = task_manager
    return calls


async def _run(tmp_path: Path, payload: dict) -> dict:
    envelope = {"payload": {"job_id": "mg_job", "project_dir": str(tmp_path), **payload}}
    return await freezone_runner._run_freezone_motion_render_async(
        envelope, _project_context(tmp_path)
    )


@pytest.mark.asyncio
async def test_runner_rejects_empty_or_oversized_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="MG 页面内容为空"):
        await _run(tmp_path, {"html": "  "})
    with pytest.raises(RuntimeError, match="MG 页面过大"):
        await _run(tmp_path, {"html": "x" * (motion_render.MAX_PAGE_CHARS + 1)})

    assert calls["render"] == []


@pytest.mark.asyncio
async def test_runner_renders_and_reports_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path, frames=150, duration=5.0, page_duration=29.0)

    result = await _run(
        tmp_path,
        {
            "html": _PAGE,
            "case_id": "M43",
            "fps": 30,
            "duration_seconds": 5,
            "resolution": "720p",
            "title": "开场",
            "episode": 3,
            "beat_number": 7,
        },
    )

    assert result["media_type"] == "video"
    assert result["engine"] == "chrome-cdp"
    assert result["frames"] == 150
    assert result["duration_seconds"] == 5.0
    assert result["page_duration_seconds"] == 29.0
    assert result["requested_duration_seconds"] == 5.0
    assert result["beats"] == [{"t": 1.0, "type": "swap"}]
    # 贴页面的任务不出网：page_source 要如实记成 pasted。
    assert result["page_source"] == "pasted"
    assert calls["text"] == []
    assert result["output_url"] == "/static/freezone/_outputs/freezone_motion_render/mg_job.mp4"
    # 页面与成片同名同目录留档：出片后还能打开 .html 复现每一帧。
    assert result["page_url"].endswith("mg_job.html")
    # 另一份不带引导脚本的原始源码：改完再渲就从这里取。
    assert result["page_source_url"].endswith("mg_job.source.html")
    # 渲染元数据要同名落一份 sidecar：草稿导出靠它取画幅/时长/拍点。
    sidecar = Path(result["sidecar_path"])
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "job_id": "mg_job",
        "case_id": "M43",
        # 按风格出片时 case_id 是空的，style_id 才是这一镜唯一的出处；两栏都要落盘。
        "style_id": "",
        # 片名/集号/镜号一起落盘：导出草稿时要把成片按镜归位并显示成认得出的名字。
        "title": "开场",
        "episode": 3,
        "beat_number": 7,
        "video_path": result["output_path"],
        "page_path": str(Path(result["output_path"]).with_suffix(".html")),
        "width": 1280,
        "height": 720,
        "fps": 30,
        "frames": 150,
        "duration_seconds": 5.0,
        "aspect_ratio": "16:9",
        "beats": [{"t": 1.0, "type": "swap"}],
        # 配乐口径也要落 sidecar：草稿任务靠它决定成片自带的那条分轨要不要让位。
        "audio": {
            "engine": "code-arranged",
            "score_path": str(Path(result["output_path"]).with_suffix(".score.wav")),
            "bpm": 120.0,
            "grid_source": "events",
            "style": "pulse",
            "notes": 48,
            "duration_seconds": 5.0,
            "sample_rate": 44100,
            "loudness": {
                "measured_input_lufs": -18.2,
                "measured_input_true_peak_db": -3.1,
                "measured_input_lra": 9.4,
                "measured_input_threshold": -25.0,
                "target_offset": 0.6,
                "target_lufs": -14.0,
                "target_true_peak_db": -1.0,
            },
        },
    }

    sent = calls["render"][0]
    assert sent["width"] == 1280 and sent["height"] == 720
    assert sent["fps"] == 30
    assert sent["duration_seconds"] == 5.0

    # 编曲收到的拍点就是页面自报的那一份，时长/帧率取渲染结果而不是请求参数。
    asked = calls["score"][0]
    assert asked["events"] == [{"t": 1.0, "type": "swap"}]
    assert asked["duration_seconds"] == 5.0
    assert asked["fps"] == 30
    assert asked["case_category"]  # M43 在册：分类决定编曲档位
    # 分轨干声要能直接播：听配乐对不对拍点不该逼用户先把整片下下来。
    assert result["score_url"] == "/static/freezone/_outputs/freezone_motion_render/mg_job.score.wav"

    progresses = [update["progress"] for update in calls["task_manager"].updates]
    assert progresses[0] == 0.12
    assert progresses[1] == pytest.approx(0.94)
    # 配乐这一步的进度也要单独动：几秒的片子跑两遍 loudnorm 是真会卡住的地方。
    assert progresses[2] == 0.95
    assert progresses[3] == 0.97


@pytest.mark.asyncio
async def test_runner_maps_aspect_and_resolution_onto_the_canvas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path, width=1080, height=1920)

    result = await _run(
        tmp_path, {"html": _PAGE, "aspect_ratio": "9:16", "resolution": "1080p", "fps": 90}
    )

    sent = calls["render"][0]
    assert (sent["width"], sent["height"]) == (1080, 1920)
    # 帧率越界由引擎侧夹到上限，路由的 1-60 只是第一道闸。
    assert sent["fps"] == 60
    assert result["aspect_ratio"] == "9:16"
    assert result["resolution"] == "1080p"


@pytest.mark.asyncio
async def test_runner_archives_only_when_the_submitter_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    await _run(tmp_path, {"html": _PAGE, "case_id": "M43"})
    assert calls["archived"] == []

    result = await _run(
        tmp_path, {"html": _PAGE, "case_id": "M43", "title": "产品宣传片", "archive_to_library": True}
    )

    assert len(calls["archived"]) == 1
    entry = calls["archived"][0]
    assert entry["task_type"] == "freezone_motion_render"
    assert entry["media"] == "video"
    assert entry["stem"] == "产品宣传片"
    assert entry["url"] == result["output_url"]
    assert result["archived_to_library"] is True


@pytest.mark.asyncio
async def test_runner_writes_the_canvas_node_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    history: list[dict] = []

    def fake_append(**kwargs):
        history.append(kwargs)
        return {"id": "rec_1"}

    monkeypatch.setattr(freezone_runner, "_append_node_history", fake_append)

    result = await _run(tmp_path, {"html": _PAGE, "node_id": "node_7"})

    assert history[0]["media_type"] == "video"
    assert result["generation_history_record"] == {"id": "rec_1"}


# --------------------------------------------------------------- 4) 配乐接线


@pytest.mark.asyncio
async def test_runner_skips_the_score_when_the_submitter_asked_for_picture_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``music=false`` 是一等选项：只要画面就不许起 ffmpeg，sidecar 要如实记成 off。"""
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(tmp_path, {"html": _PAGE, "music": False})

    assert calls["score"] == []
    assert result["audio"] == {"engine": "off"}
    assert "score_url" not in result


@pytest.mark.asyncio
async def test_runner_delivers_a_silent_film_when_the_score_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编曲/响度归一失败不许判整单失败：画面是主产物，但原因必须写进结果和 sidecar。"""
    calls = _patch_runner(monkeypatch, tmp_path, score_error="本机没有 ffmpeg，声音响度归一")

    result = await _run(tmp_path, {"html": _PAGE})

    assert len(calls["score"]) == 1
    assert Path(result["output_path"]).is_file()
    assert result["audio"]["engine"] == "silent"
    assert "本机没有 ffmpeg" in result["audio"]["error"]
    assert "score_url" not in result
    sidecar = json.loads(Path(result["sidecar_path"]).read_text(encoding="utf-8"))
    assert sidecar["audio"] == result["audio"]


# --------------------------------------------------- 5) 页面生成阶段（html 留空）


@pytest.mark.asyncio
async def test_runner_generates_the_page_when_nothing_is_pasted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """逐镜一键出片：没贴页面时，先按案例契约让模型写页面，再渲染那一页。"""
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(
        tmp_path,
        {"case_id": "M43", "subject": "黑底白字开场，字标从遮罩线升起", "model_text": "gpt-x"},
    )

    asked = calls["text"][0]
    assert asked["model"] == "gpt-x"
    # 提示词里既要有图册那份契约（compose_motion_prompt 的产物），也要有本镜头文案
    # 和这次任务的画幅 —— 三者缺一个，出来的页面就不是这个镜头的。
    assert "window.DUR" in asked["prompt"]
    assert "黑底白字开场" in asked["prompt"]
    assert "1280x720" in asked["prompt"]

    # 渲染收到的是收出来的源码，不是带解说的模型原文。
    assert calls["render"][0]["html"].startswith("<!doctype html>")
    assert "好的，下面是完整页面" not in calls["render"][0]["html"]
    assert result["page_source"] == "generated"
    assert result["model"] == "mg-text-model"
    # 两段进度都要动：生成 0.04 → 起浏览器 0.12 → 收尾 0.97。
    progresses = [update["progress"] for update in calls["task_manager"].updates]
    assert progresses[0] == 0.04
    assert 0.04 < progresses[1] <= 0.12


@pytest.mark.asyncio
async def test_runner_fails_before_rendering_when_the_reply_is_not_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path, reply="抱歉，我无法完成这个请求。")

    with pytest.raises(RuntimeError, match="MG 页面生成失败"):
        await _run(tmp_path, {"case_id": "M43"})

    # 契约预检在起浏览器之前：失败要快，且不许留下半个成片任务。
    assert calls["render"] == []


@pytest.mark.asyncio
async def test_runner_names_an_unknown_case_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="不在本机的图册清单里"):
        await _run(tmp_path, {"case_id": "M999"})

    assert calls["text"] == []
    assert calls["render"] == []


# ------------------------------------------------- 6) 按画风出片（style_id 入口）


@pytest.mark.asyncio
async def test_runner_generates_a_page_from_a_style_and_scores_by_its_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画风图册的第二个动作：没贴页面、只给风格 + 镜头文案，页面按那条风格生成。

    发给模型的提示词必须是「风格规格 + 案例那份渲染契约」，并带上本镜头文案与画幅；
    缺契约就画不出能被逐帧截的页面，缺文案则这一镜与下一镜没有区别。
    """
    from novelvideo.freezone.motion_styles import get_motion_style

    style = get_motion_style("ink-wash") or {}
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(tmp_path, {"style_id": "ink-wash", "subject": "宣纸上晕开的山影"})

    asked = calls["text"][0]
    assert style["label"] in asked["prompt"]
    assert "【代码技法】" in asked["prompt"]
    assert "window.DUR" in asked["prompt"]
    assert "宣纸上晕开的山影" in asked["prompt"]
    assert calls["render"][0]["html"].startswith("<!doctype html>")
    # 编曲档位按风格的分类挑：水墨走安静档，音乐 MV 那种分类才会推到 drive。
    assert calls["score"][0]["case_category"] == style["category"]
    # 出处要留在结果与 sidecar 里：按风格出的片没有案例可查，style_id 是唯一线索。
    assert result["case_id"] == ""
    assert result["style_id"] == "ink-wash"
    sidecar = json.loads(Path(result["sidecar_path"]).read_text(encoding="utf-8"))
    assert sidecar["style_id"] == "ink-wash"
    assert sidecar["case_id"] == ""


@pytest.mark.asyncio
async def test_runner_names_an_unknown_style_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="不在本机的画风图册清单里"):
        await _run(tmp_path, {"style_id": "no_such_style"})

    assert calls["text"] == []
    assert calls["render"] == []


@pytest.mark.asyncio
async def test_runner_refuses_a_task_that_carries_both_a_case_and_a_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两套规格同时给会让模型在冲突的色板与禁则之间取平均：任务层自己也要挡。"""
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="案例与风格只能选一个"):
        await _run(tmp_path, {"case_id": "M43", "style_id": "ink-wash"})

    assert calls["text"] == []
    assert calls["render"] == []


def test_route_rejects_a_case_and_a_style_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(client, {"html": _PAGE, "case_id": "M43", "style_id": "ink-wash"})

    assert response.status_code == 400
    assert "案例与风格只能选一个" in response.json()["detail"]
    assert submitted == []


def test_route_forwards_a_style_only_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(client, {"style_id": "ink-wash", "subject": "宣纸上晕开的山影"})

    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["style_id"] == "ink-wash"
    assert payload["case_id"] == ""
    assert payload["subject"] == "宣纸上晕开的山影"
