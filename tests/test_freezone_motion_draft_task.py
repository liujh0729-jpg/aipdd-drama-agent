# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效剪映草稿导出任务（freezone_motion_draft）后端契约测试。
"""三层：入队走 video 队列、路由的提交前闸门（没有成片 / 随包模板缺失）、
runner 的编排与产物形状（落盘位置优先级、拍点计数、部署复制、报错口径）。

草稿字典本身与目录结构在 ``test_freezone_motion_draft.py`` 已单测；本文件只守「任务链路」
这一层，剪映本体一次都不启动。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import motion_draft
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import freezone as freezone_runner

_JOB = "mg_draft_job"


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


def _make_render_output(
    root: Path,
    *,
    name: str,
    seconds: float,
    beats: list[float],
    extra: dict[str, Any] | None = None,
) -> Path:
    """伪造一次渲染的产物：mp4 + 同名 ``*.motion.json`` 侧车。"""
    video = root / f"{name}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")
    sidecar: dict[str, Any] = {
        "job_id": name,
        "video_path": str(video).replace("\\", "/"),
        "width": 1280,
        "height": 720,
        "fps": 30,
        "duration_seconds": seconds,
        "beats": [{"t": beat, "type": "hit"} for beat in beats],
    }
    sidecar.update(extra or {})
    video.with_suffix(".motion.json").write_text(
        json.dumps(sidecar, ensure_ascii=False),
        encoding="utf-8",
    )
    return video


# --------------------------------------------------------------- 1) 入队


@pytest.mark.asyncio
async def test_motion_draft_enqueue_uses_video_queue(
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
    result = await freezone_routes._enqueue_or_start_freezone_motion_draft(
        ctx=SimpleNamespace(project_id="project_59", requester_user_id="user-1"),
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id=_JOB,
        payload={"clips": [{"source": "a.mp4"}], "deploy": True},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_motion_draft"
    assert captured["queue_kind"] == "video"
    assert captured["payload"]["job_id"] == _JOB
    assert captured["payload"]["project_dir"] == str(tmp_path)
    assert captured["payload"]["clips"] == [{"source": "a.mp4"}]


# --------------------------------------------------------------- 2) 路由


def _route_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    template_missing: bool = False,
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

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone_routes, "_enqueue_or_start_freezone_motion_draft", fake_enqueue)
    monkeypatch.setattr(
        motion_routes, "template_dir", lambda: None if template_missing else Path("template")
    )
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    return TestClient(app), submitted


def _post(client: TestClient, body: dict) -> Any:
    return client.post("/api/v1/projects/59/freezone/motion/draft", json=body)


def test_route_requires_at_least_one_clip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)
    response = _post(client, {"clips": []})
    assert response.status_code == 400
    assert "没有任何 MG 成片" in response.json()["detail"]
    assert submitted == []


def test_route_blocks_when_bundled_template_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path, template_missing=True)
    response = _post(client, {"clips": [{"source": "E:/out/a.mp4", "duration_seconds": 2}]})
    assert response.status_code == 409
    assert "剪映 10.2 草稿模板" in response.json()["detail"]
    assert submitted == []


def test_route_maps_body_onto_the_task_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)
    response = _post(
        client,
        {
            "title": "苹果风成片",
            "clips": [
                {
                    "source": "E:/out/a.mp4",
                    "name": "镜1",
                    "beats": [0.5],
                    "narration": {"source": "E:/out/vo1.mp3", "duration_seconds": 1.5},
                    "caption": "这一镜的字",
                },
                {"source": "E:/out/b.mp4", "duration_seconds": 3.5},
            ],
            "narration": {"source": "E:/out/vo.mp3"},
            "bgm": {"source": "E:/out/bgm.mp3", "gain": 0.2},
            "subtitles": [{"text": "手写一条", "start_seconds": 1.0}],
            "text_style": {"size": 9.5, "bold": False, "y_ratio": 0.2},
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "fps": 24,
            "deploy": False,
        },
    )
    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["title"] == "苹果风成片"
    assert [clip["source"] for clip in payload["clips"]] == ["E:/out/a.mp4", "E:/out/b.mp4"]
    assert payload["clips"][0]["beats"] == [0.5]
    # 逐镜旁白跟着片段走，不能被 model_dump 丢掉。
    assert payload["clips"][0]["narration"]["source"] == "E:/out/vo1.mp3"
    assert payload["clips"][1]["narration"] is None
    # 字幕也一样：逐镜的跟着片段，整片的走请求级字段。
    assert payload["clips"][0]["caption"] == "这一镜的字"
    assert payload["clips"][1]["caption"] == ""
    assert payload["subtitles"] == [{"text": "手写一条", "start_seconds": 1.0, "duration_seconds": None}]
    assert payload["text_style"]["size"] == 9.5
    assert payload["narration"]["source"] == "E:/out/vo.mp3"
    assert payload["bgm"]["gain"] == 0.2
    assert payload["aspect_ratio"] == "9:16"
    assert payload["fps"] == 24
    assert payload["deploy"] is False


def test_route_defaults_the_subtitle_fields_to_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, submitted = _route_client(monkeypatch, tmp_path)
    response = _post(client, {"clips": [{"source": "E:/out/a.mp4"}]})
    assert response.status_code == 200
    payload = submitted[0]["payload"]
    assert payload["subtitles"] == []
    assert payload["text_style"] is None


# --------------------------------------------------------------- 2b) 成片清单路由


def _renders_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TestClient, Path]:
    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    ctx = _project_context(tmp_path)

    async def fake_resolve(*_args, **_kwargs):
        return (ctx, "admin", "project", tmp_path / "output", str(tmp_path / "output"))

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(
        freezone_routes,
        "make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    return TestClient(app), outputs_render_dir(tmp_path)


def outputs_render_dir(tmp_path: Path) -> Path:
    return outputs_dir(tmp_path / "output", "freezone_motion_render")


def test_renders_route_lists_clips_and_filters_by_episode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, root = _renders_client(monkeypatch, tmp_path)
    tagged = _make_render_output(
        root,
        name="job_tagged",
        seconds=2.0,
        beats=[0.5, 1.5],
        extra={"title": "开场", "episode": 3, "beat_number": 7, "case_id": "M43"},
    )
    plain = _make_render_output(root, name="job_plain", seconds=3.0, beats=[])
    # 清单顺序 = 时间轴顺序，所以渲染先后必须钉死，不能靠文件系统时间精度。
    os.utime(tagged, (1_700_000_100.0, 1_700_000_100.0))
    os.utime(plain, (1_700_000_200.0, 1_700_000_200.0))

    response = client.get("/api/v1/projects/59/freezone/motion/renders")
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["job_id"] for item in data] == ["job_tagged", "job_plain"]
    assert data[0]["name"] == "开场"
    assert (data[0]["episode"], data[0]["beat_number"]) == (3, 7)
    assert data[0]["beats"] == [{"t": 0.5, "type": "hit"}, {"t": 1.5, "type": "hit"}]
    assert data[1]["has_sidecar"] is True
    assert data[1]["name"] == "job_plain"
    # 只回同源 URL：本机绝对路径不进接口。
    assert data[0]["url"] == "/static/freezone/_outputs/freezone_motion_render/job_tagged.mp4"
    assert "video_path" not in data[0]

    filtered = client.get("/api/v1/projects/59/freezone/motion/renders?episode=3")
    assert [item["job_id"] for item in filtered.json()["data"]] == ["job_tagged"]


def test_renders_route_returns_nothing_for_an_empty_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _root = _renders_client(monkeypatch, tmp_path)
    response = client.get("/api/v1/projects/59/freezone/motion/renders")
    assert response.status_code == 200
    assert response.json()["data"] == []


# --------------------------------------------------------------- 3) runner


def _patch_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _TaskManager:
    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    # 「设置 → 剪映草稿目录」在测试里一律未配置，让落盘回退到项目输出目录。
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_vectcut_draft_dir", lambda: ""
    )
    return task_manager


async def _run(tmp_path: Path, payload: dict) -> dict:
    envelope = {"payload": {"job_id": _JOB, "project_dir": str(tmp_path), **payload}}
    return await freezone_runner._run_freezone_motion_draft_async(
        envelope, _project_context(tmp_path)
    )


@pytest.mark.asyncio
async def test_runner_lands_a_readable_draft_in_the_project_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_manager = _patch_runner(monkeypatch, tmp_path)
    first = _make_render_output(tmp_path, name="shot1", seconds=2.0, beats=[0.5, 1.25])
    second = _make_render_output(tmp_path, name="shot2", seconds=3.0, beats=[])
    vo = tmp_path / "vo.mp3"
    vo.write_bytes(b"mp3")

    result = await _run(
        tmp_path,
        {
            "title": "两镜草稿",
            "clips": [{"source": str(first)}, {"source": str(second)}],
            "narration": {"source": str(vo)},
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "fps": 30,
        },
    )

    assert result["media_type"] == "draft"
    assert result["name"] == "两镜草稿"
    assert result["clips"] == 2
    assert result["duration_seconds"] == 5.0
    assert result["beat_count"] == 2
    assert result["has_narration"] is True
    assert result["has_bgm"] is False
    # 没配草稿目录、也没让部署：工程就落在项目输出目录里，deployed_dir 留空。
    assert Path(result["draft_dir"]).parent == tmp_path / "freezone" / "_outputs" / "freezone_motion_draft"
    assert result["deployed_dir"] == ""
    assert result["timeline_id"]

    # 落盘的确实是那两镜 + 一条旁白，且剪映读的那份镜像与本体一致。
    doc = json.loads((Path(result["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
    assert [track["type"] for track in doc["tracks"]] == ["video", "audio"]
    assert len(doc["tracks"][0]["segments"]) == 2
    mirror = Path(result["draft_dir"]) / "Timelines" / result["timeline_id"] / "draft_content.json"
    assert json.loads(mirror.read_text(encoding="utf-8")) == doc

    progresses = [update["progress"] for update in task_manager.updates]
    assert progresses[0] == 0.08
    assert progresses[-1] == 0.95


@pytest.mark.asyncio
async def test_runner_lands_written_subtitles_on_a_text_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """字幕不是「写在画面上」，而是剪映里一条独立的文字轨：轨道、素材、层级都要齐。"""
    _patch_runner(monkeypatch, tmp_path)
    first = _make_render_output(tmp_path, name="shot1", seconds=2.0, beats=[0.5])
    second = _make_render_output(tmp_path, name="shot2", seconds=3.0, beats=[])

    result = await _run(
        tmp_path,
        {
            "title": "带字幕的两镜",
            "clips": [{"source": str(first)}, {"source": str(second)}],
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "fps": 30,
            "subtitles": [
                {"text": "第一条", "start_seconds": 0.0},
                {"text": "第二条", "start_seconds": 2.5},
            ],
            "text_style": {"size": 9.0, "bold": True, "y_ratio": 0.8},
        },
    )

    assert result["text_clips"] == 2
    doc = json.loads(
        (Path(result["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8")
    )
    assert [track["type"] for track in doc["tracks"]] == ["video", "text"]
    segments = doc["tracks"][1]["segments"]
    # 第一条没写时长 → 垫到第二条起点；第二条垫到全片结束（5.0s）。
    assert [segment["target_timerange"] for segment in segments] == [
        {"duration": 2_500_000, "start": 0},
        {"duration": 2_500_000, "start": 2_500_000},
    ]
    assert [segment["clip"]["transform"]["y"] for segment in segments] == [
        pytest.approx(-0.6),
        pytest.approx(-0.6),
    ]
    assert all(segment["render_index"] > 0 for segment in segments)
    texts = [json.loads(item["content"]) for item in doc["materials"]["texts"]]
    assert [item["text"] for item in texts] == ["第一条", "第二条"]
    assert [item["styles"][0]["size"] for item in texts] == [9.0, 9.0]


@pytest.mark.asyncio
async def test_runner_refuses_captions_and_written_subtitles_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    video = _make_render_output(tmp_path, name="shot", seconds=2.0, beats=[])

    with pytest.raises(RuntimeError, match="字幕只能选一种给法"):
        await _run(
            tmp_path,
            {
                "clips": [{"source": str(video), "caption": "这一镜的字"}],
                "subtitles": [{"text": "还要手写", "start_seconds": 0.5}],
            },
        )


@pytest.mark.asyncio
async def test_runner_honours_an_explicit_draft_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])
    target = tmp_path / "草稿盘"

    result = await _run(
        tmp_path,
        {"clips": [{"source": str(video)}], "draft_dir": str(target)},
    )

    assert Path(result["draft_dir"]) == target / _JOB
    assert (target / _JOB / "draft_content.json").is_file()


@pytest.mark.asyncio
async def test_runner_falls_back_to_the_configured_draft_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    configured = tmp_path / "剪映草稿"
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_vectcut_draft_dir", lambda: str(configured)
    )
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])

    result = await _run(tmp_path, {"clips": [{"source": str(video)}]})
    assert Path(result["draft_dir"]) == configured / _JOB


@pytest.mark.asyncio
@pytest.mark.parametrize("draft_dir", ["../../草稿盘", "relative/dir", "/drafts/../etc"])
async def test_runner_refuses_a_draft_dir_that_is_not_an_absolute_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draft_dir: str
) -> None:
    """请求体里的 ``draft_dir`` 得过与「设置」同一道闸门。

    不设限的话，任何能提交这个任务的人都能让服务端在自己有写权限的任意位置建目录、
    写一整盘草稿文件。
    """
    _patch_runner(monkeypatch, tmp_path)
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])

    with pytest.raises(RuntimeError, match="剪映草稿落盘目录不合法"):
        await _run(tmp_path, {"clips": [{"source": str(video)}], "draft_dir": draft_dir})


@pytest.mark.asyncio
async def test_runner_reports_the_deploy_copy_when_jianying_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    projects = tmp_path / "com.lveditor.draft"
    projects.mkdir()
    monkeypatch.setattr(motion_draft.writer, "jianying_projects_dir", lambda: projects)
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])

    result = await _run(
        tmp_path,
        {"title": "上线片", "clips": [{"source": str(video)}], "deploy": True},
    )

    assert Path(result["deployed_dir"]) == projects / "上线片"
    assert (projects / "上线片" / "draft_content.json").is_file()


@pytest.mark.asyncio
async def test_runner_renames_instead_of_deleting_someone_elses_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """剪映工程目录里已有同名工程：那是用户的活，删它等于删用户的草稿。"""
    _patch_runner(monkeypatch, tmp_path)
    projects = tmp_path / "com.lveditor.draft"
    projects.mkdir()
    foreign = projects / "复制不了的片"
    foreign.mkdir()
    (foreign / "draft_content.json").write_text("{}", encoding="utf-8")
    (foreign / "draft_meta_info.json").write_text(
        json.dumps({"draft_id": "别人的工程ID"}), encoding="utf-8"
    )
    monkeypatch.setattr(motion_draft.writer, "jianying_projects_dir", lambda: projects)
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])

    result = await _run(
        tmp_path,
        {"title": "复制不了的片", "clips": [{"source": str(video)}], "deploy": True},
    )

    assert Path(result["deployed_dir"]) == projects / f"复制不了的片-{_JOB}"
    assert json.loads((foreign / "draft_meta_info.json").read_text(encoding="utf-8"))[
        "draft_id"
    ] == "别人的工程ID"
    assert "落成为" in result["warning"]


@pytest.mark.asyncio
async def test_runner_surfaces_a_broken_deploy_target_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """剪映工程目录写不进去 ≠ 草稿没生成：要把路径说清楚，但仍算成功。"""
    _patch_runner(monkeypatch, tmp_path)
    projects = tmp_path / "com.lveditor.draft"
    projects.mkdir()
    monkeypatch.setattr(motion_draft.writer, "jianying_projects_dir", lambda: projects)

    real_copytree = motion_draft.writer.shutil.copytree

    def _only_deploy_fails(*args: object, **kwargs: object) -> object:
        # 铺骨架那一次要照常成功，只有「复制进剪映工程目录」这一步模拟写不进去。
        # copytree 的递归是回调模块全局的 copytree，所以参数得原样转手。
        if len(args) > 1 and str(args[1]).startswith(str(projects)):
            raise OSError("磁盘不可用")
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(motion_draft.writer.shutil, "copytree", _only_deploy_fails)
    video = _make_render_output(tmp_path, name="shot", seconds=1.0, beats=[])

    result = await _run(
        tmp_path,
        {"title": "复制不了的片", "clips": [{"source": str(video)}], "deploy": True},
    )
    assert result["deployed_dir"] == ""
    assert "复制进剪映工程目录失败" in result["warning"]
    assert Path(result["draft_dir"], "draft_content.json").is_file()


@pytest.mark.asyncio
async def test_runner_refuses_a_missing_clip_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="素材文件不存在"):
        await _run(tmp_path, {"clips": [{"source": str(tmp_path / "没有这个.mp4")}]})


@pytest.mark.asyncio
async def test_runner_refuses_clips_without_any_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    video = tmp_path / "bare.mp4"
    video.write_bytes(b"mp4")
    with pytest.raises(RuntimeError, match="取不到时长"):
        await _run(tmp_path, {"clips": [{"source": str(video)}]})


@pytest.mark.asyncio
async def test_runner_refuses_external_clip_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="外链"):
        await _run(tmp_path, {"clips": [{"source": "https://cdn.example/a.mp4"}]})
