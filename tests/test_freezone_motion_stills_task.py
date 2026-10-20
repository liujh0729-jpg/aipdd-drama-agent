# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效大片拍点预检（freezone_motion_stills）后端契约测试。
"""三层：入队走 video 队列（与成片抢的是同一个包内 Chrome，不许并发）、
路由的提交前闸门（空页面 / 本机没有 Chrome）、runner 的产物形状（一目录静帧的
清单与 URL、网格标记、页面源码链接、进度口径）。

拍点换算与逐拍出图在 ``test_freezone_motion_stills.py`` 已单测；本文件只守任务
链路这一层，Chrome 换成假对象，绝不起真浏览器。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import motion_render
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import freezone as freezone_runner

_PAGE = "<!doctype html><html><head></head><body><canvas id=c></canvas></body></html>"
_GOOD_PAGE_REPLY = (
    "好的，下面是完整页面：\n```html\n<!doctype html><html><body><canvas id=c></canvas>"
    "<script>window.DUR=2.0;window.render=function(t){};</script></body></html>\n```\n"
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

    def update_progress_for_project(self, *args: Any, **kwargs: Any) -> None:
        # task_type 在 `_update` 里是位置参数，一起记下来才检得出进度记在谁名下。
        self.updates.append({"args": args, **kwargs})


# --------------------------------------------------------------- 1) 入队


@pytest.mark.asyncio
async def test_motion_stills_enqueue_uses_video_queue(
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
    result = await freezone_routes._enqueue_or_start_freezone_motion_stills(
        ctx=SimpleNamespace(project_id="project_59", requester_user_id="user-1"),
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="mg_stills",
        payload={"html": _PAGE, "max_stills": 12},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_motion_stills"
    # 与成片同一个包内 Chrome：排在同一条队列里串行跑，别两处同时拉浏览器。
    assert captured["queue_kind"] == "video"
    assert captured["payload"]["job_id"] == "mg_stills"
    assert captured["payload"]["max_stills"] == 12


# --------------------------------------------------------------- 2) 路由


def _route_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    preflight_error: str = "",
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
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_motion_stills", fake_enqueue
    )
    monkeypatch.setattr(motion_routes, "stills_preflight", fake_preflight)
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    return TestClient(app), submitted


def _post(client: TestClient, body: dict) -> Any:
    return client.post("/api/v1/projects/59/freezone/motion/stills", json=body)


def test_route_rejects_an_empty_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(client, {"html": "   "})

    assert response.status_code == 400
    assert "MG 页面内容为空" in response.json()["detail"]
    assert submitted == []


def test_route_refuses_to_queue_without_a_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(
        monkeypatch,
        tmp_path,
        preflight_error="本机找不到可用的 Chrome：桌面版请用自带 Chrome 的安装包",
    )

    response = _post(client, {"html": _PAGE})

    assert response.status_code == 409
    assert "Chrome" in response.json()["detail"]
    assert submitted == []


def test_route_forwards_the_precheck_choices_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(
        client,
        {
            "case_id": "M43",
            "subject": "黑底白字开场",
            "model_text": "gpt-x",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "max_stills": 8,
            "source_tool": "motion-blockbuster",
        },
    )

    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["html"] == ""
    assert payload["case_id"] == "M43"
    assert payload["subject"] == "黑底白字开场"
    assert payload["model_text"] == "gpt-x"
    # 画幅沿用成片那一档：预检要检的就是这一镜成片会用的画布。
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "1080p"
    assert payload["max_stills"] == 8
    assert payload["source_tool"] == "motion-blockbuster"

    # 张数留空 = 不夹值，由引擎按默认档处理。
    assert _post(client, {"html": _PAGE}).status_code == 200
    assert submitted[1]["payload"]["max_stills"] is None


def test_route_rejects_an_out_of_range_still_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    assert _post(client, {"html": _PAGE, "max_stills": 0}).status_code == 422
    assert _post(client, {"html": _PAGE, "max_stills": 500}).status_code == 422
    assert submitted == []


# --------------------------------------------------------------- 3) runner


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    width: int = 1280,
    height: int = 720,
    events: tuple[dict, ...] = ({"t": 1.0, "type": "swap"},),
    beat_grid: bool = False,
    page_duration: float = 2.0,
    reply: str = _GOOD_PAGE_REPLY,
) -> dict[str, Any]:
    calls: dict[str, Any] = {"stills": [], "text": [], "archived": [], "reply": reply}
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    # 预检是检查用的中间产物：不许把它登记进资产库「待分类资产」。
    def fake_archive(**kwargs):
        calls["archived"].append(kwargs)

    monkeypatch.setattr(freezone_runner, "_archive_tool_output_to_library", fake_archive)

    async def fake_render(html, out_dir, **kwargs):
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        page = root / "page.html"
        page.write_text(html, encoding="utf-8")
        # 引擎同时落原始源码，leaf 才能报出「可编辑的那一份」地址。
        motion_render.page_source_path(page).write_text(html, encoding="utf-8")
        beats = [{"t": 0.0, "type": "open"}] + [
            {"t": float(entry["t"]), "type": str(entry.get("type") or "")}
            for entry in events
        ]
        on_progress = kwargs.get("on_progress")
        stills = []
        for index, entry in enumerate(beats):
            path = root / f"beat-{index + 1:02d}-{entry['t']:.3f}s.png"
            path.write_bytes(b"png")
            stills.append(
                motion_render.MotionStill(
                    path=path,
                    at_seconds=entry["t"],
                    event_type=entry["type"],
                )
            )
            if on_progress is not None:
                on_progress(index + 1, len(beats))
        calls["stills"].append({"html": html, "chars": len(html), **kwargs})
        return motion_render.MotionStillsResult(
            stills=tuple(stills),
            page_duration_seconds=page_duration,
            events=events,
            beat_grid=beat_grid,
        )

    monkeypatch.setattr(motion_render, "render_motion_stills", fake_render)

    async def fake_generate_text(*_args, **kwargs):
        calls["text"].append(kwargs)
        return ("mg-text-model", calls["reply"])

    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    return calls


async def _run(tmp_path: Path, payload: dict) -> dict:
    envelope = {
        "payload": {"job_id": "mg_stills", "project_dir": str(tmp_path), **payload},
        "__run_task_id": "task_9",
    }
    return await freezone_runner._run_freezone_motion_stills_async(
        envelope, _project_context(tmp_path)
    )


@pytest.mark.asyncio
async def test_runner_reports_every_beat_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(
        tmp_path,
        {"html": _PAGE, "case_id": "M43", "aspect_ratio": "9:16", "max_stills": 6},
    )

    assert result["media_type"] == "image"
    assert result["engine"] == "chrome-cdp"
    assert result["count"] == 2
    assert [item["at_seconds"] for item in result["stills"]] == [0.0, 1.0]
    assert result["stills"][0]["url"] == (
        "/static/freezone/_outputs/freezone_motion_stills/mg_stills/"
        "beat-01-0.000s.png"
    )
    # 预检是一目录图：没有单一成片，output_url 退到开场帧，通用产物解析不至于拿空地址。
    assert result["output_url"] == result["stills"][0]["url"]
    assert result["page_url"].endswith("mg_stills/page.html")
    # 可编辑的原始源码地址：前端「载入最近一次页面源码」取的就是这一份。
    assert result["page_source_url"].endswith("mg_stills/page.source.html")
    assert result["beats"] == [{"t": 1.0, "type": "swap"}]
    assert result["beat_grid"] is False
    assert result["page_duration_seconds"] == 2.0
    assert result["max_stills"] == 6
    assert result["aspect_ratio"] == "9:16"
    # 9:16 但清晰度留默认 720p：画布口径与渲染任务共用同一份 resolve_mix_canvas。
    assert (result["width"], result["height"]) == (720, 1280)
    assert result["page_source"] == "pasted"
    assert calls["text"] == []
    # 检查用的中间产物不进资产库，也不写画布节点历史（没有 node_id）。
    assert calls["archived"] == []

    sent = calls["stills"][0]
    assert sent["width"] == 720 and sent["height"] == 1280
    assert sent["max_stills"] == 6


@pytest.mark.asyncio
async def test_runner_reports_progress_under_its_own_task_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _TaskManager()
    _patch_runner(monkeypatch, tmp_path)
    # _patch_runner 先装一个丢弃型 manager，这里换成能被检阅的那一份。
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: manager)

    await _run(tmp_path, {"html": _PAGE})

    # 三条都要记在 freezone_motion_stills 名下：任务列表按 task_type 归组，
    # 进度写进渲染任务里，用户看到的就是一张永远不动的进度条。
    assert {update["args"][1] for update in manager.updates} == {
        "freezone_motion_stills"
    }
    progresses = [update["progress"] for update in manager.updates]
    assert progresses[0] == 0.12
    assert progresses[-1] == 0.97
    assert 0.15 < progresses[1] < 0.97


@pytest.mark.asyncio
async def test_runner_generates_the_page_when_nothing_is_pasted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(
        tmp_path, {"case_id": "M43", "subject": "黑底白字开场", "model_text": "gpt-x"}
    )

    asked = calls["text"][0]
    assert asked["model"] == "gpt-x"
    assert "window.DUR" in asked["prompt"]
    assert "黑底白字开场" in asked["prompt"]

    assert calls["stills"][0]["html"].startswith("<!doctype html>")
    assert result["page_source"] == "generated"
    assert result["model"] == "mg-text-model"


@pytest.mark.asyncio
async def test_runner_rejects_an_empty_or_oversized_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="MG 页面内容为空"):
        await _run(tmp_path, {"html": "  "})
    with pytest.raises(RuntimeError, match="MG 页面过大"):
        await _run(
            tmp_path,
            {"html": "x" * (motion_render.MAX_PAGE_CHARS + 1)},
        )

    assert calls["stills"] == []


@pytest.mark.asyncio
async def test_runner_fails_fast_when_the_reply_is_not_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path, reply="抱歉，我无法完成这个请求。")

    with pytest.raises(RuntimeError, match="MG 页面生成失败"):
        await _run(tmp_path, {"case_id": "M43"})

    assert calls["stills"] == []


@pytest.mark.asyncio
async def test_runner_gives_up_when_nothing_is_displayable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """假引擎一张图都不落：不许回「成功但没产物」，那前端只会显示一条空白任务。"""
    _patch_runner(monkeypatch, tmp_path)

    async def fake_render(html, out_dir, **kwargs):
        return motion_render.MotionStillsResult(
            stills=(),
            page_duration_seconds=1.0,
            events=(),
            beat_grid=True,
        )

    monkeypatch.setattr(motion_render, "render_motion_stills", fake_render)

    with pytest.raises(RuntimeError, match="没有产出可显示的图"):
        await _run(tmp_path, {"html": _PAGE})


# ------------------------------------------------- 4) 按画风预检（style_id 入口）


def test_route_rejects_a_case_and_a_style_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)

    response = _post(client, {"html": _PAGE, "case_id": "M43", "style_id": "ink-wash"})

    assert response.status_code == 400
    assert "案例与风格只能选一个" in response.json()["detail"]
    assert submitted == []


@pytest.mark.asyncio
async def test_runner_previews_a_page_generated_from_a_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预检与成片共用这条链路：只给风格也能先按拍点出图，出处记成 style_id。"""
    from novelvideo.freezone.motion_styles import get_motion_style

    style = get_motion_style("ink-wash") or {}
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await _run(tmp_path, {"style_id": "ink-wash", "subject": "宣纸上晕开的山影"})

    asked = calls["text"][0]
    assert style["label"] in asked["prompt"]
    # 契约照样接在风格规格后面：没有 window.render(t)/window.DUR 的页面截不出帧。
    assert "window.DUR" in asked["prompt"]
    assert "宣纸上晕开的山影" in asked["prompt"]
    assert result["page_source"] == "generated"
    assert result["case_id"] == ""
    assert result["style_id"] == "ink-wash"
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_runner_names_an_unknown_style_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="不在本机的画风图册清单里"):
        await _run(tmp_path, {"style_id": "no_such_style"})

    assert calls["text"] == []
    assert calls["stills"] == []
