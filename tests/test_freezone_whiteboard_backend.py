"""[aipdd fork delta] 白板动画视频（freezone_whiteboard_animation）后端契约测试。

覆盖三层：
1. 入队：走 video 队列、载荷原样透传（只补 job_id / project_dir）；
2. 路由：线稿图像目录与配音音频目录在提交前定型为内部执行身份，画幅/清晰度/
   幕数/笔尖光标/字幕/BGM 等渲染参数定型透传，以及各类 4xx；
3. runner 全链：文案 → 分镜规划 leaf → 配音 leaf → 逐幕出图 leaf → 本地叠字 →
   逐笔揭示渲染 → 同源混剪通道拼接/混音 → 归档。

逐笔揭示与 ffmpeg 编码在 ``test_freezone_whiteboard.py`` 里已有纯函数/渲染单测；
本文件守的是「链路编排与产物形状」，leaf、渲染与出图全部换成显式签名假对象。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


# ---------------------------------------------------------------
# 1) 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_whiteboard_enqueue_uses_video_queue_and_verbatim_payload(
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
    result = await freezone_routes._enqueue_or_start_freezone_whiteboard_animation(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="wb_job",
        payload={"script": "三个提高完播率的方法", "max_scenes": 6},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_whiteboard_animation"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_whiteboard_animation"
    assert captured["queue_kind"] == "video"
    assert captured["episode"] == 0
    assert captured["scope"] == "wb_job"
    # 载荷原样透传：入队侧只补 job_id / project_dir，不改字段。
    assert captured["payload"] == {
        "job_id": "wb_job",
        "project_dir": str(tmp_path),
        "script": "三个提高完播率的方法",
        "max_scenes": 6,
    }


# ---------------------------------------------------------------
# 2) 路由
# ---------------------------------------------------------------


def _patch_wb_route_resolve(
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
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "wb_job")


def _wb_ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )


def _patch_wb_catalogs(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
) -> list[tuple]:
    """图像目录走 _resolve_catalog_request，音频目录走 _scoped_media_model_catalog。"""
    catalog_calls: list[tuple] = []

    async def fake_resolve_catalog(media_type, model, model_params, **kwargs):
        catalog_calls.append((media_type, model, kwargs.get("requester_user_id")))
        return (
            {"template": "image-request"},
            {"size": "2K"},
            {
                "id": "img-cat",
                "providerId": "gateway",
                "apiModel": "newapi-image-1",
            },
        )

    async def fake_audio_catalog(media_type, *, requester_user_id):
        catalog_calls.append((media_type, requester_user_id))
        return [{"id": "tts-a", "apiModel": "newapi-tts-1"}]

    async def fake_projection(**kwargs):
        captured["projection_task_type"] = kwargs["task_type"]
        captured["projection_extra_config"] = kwargs["extra_config"]
        return {"projection": {"voice_registry": {}}}

    monkeypatch.setattr(freezone_routes, "_resolve_catalog_request", fake_resolve_catalog)
    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", fake_audio_catalog)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    return catalog_calls


@pytest.mark.asyncio
async def test_whiteboard_route_pins_models_and_render_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"mp3")
    ctx = _wb_ctx(tmp_path)
    bgm_url = "/static/admin/59/freezone/_uploads/bgm.mp3"
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {bgm_url: bgm})

    captured: dict[str, object] = {}
    catalog_calls = _patch_wb_catalogs(monkeypatch, captured)

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_whiteboard_animation",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_whiteboard_animation:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_whiteboard_animation", fake_enqueue
    )

    result = await freezone_routes.freezone_whiteboard_animation(
        project="59",
        body=freezone_routes.FreezoneWhiteboardAnimationRequest(
            script="  为什么你的视频留不住人？  ",
            model_text="DC-llm-custom",
            model_image="img-cat",
            image_model_params={"size": "2K"},
            model="tts-a",
            aspect_ratio="9:16",
            resolution="1080p",
            max_scenes=6,
            pen_cursor=False,
            subtitle_enabled=True,
            subtitle_font_size="large",
            emotion_prompt="轻松、亲切",
            bgm_url=bgm_url,
            bgm_volume=0.25,
            archive_to_library=True,
            source_tool="whiteboard-animation",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_whiteboard_animation"
    assert captured["job_id"] == "wb_job"
    assert captured["script"] == "为什么你的视频留不住人？"
    # 分镜规划模型选项原样下传（文本 leaf 侧再定型）。
    assert captured["model_text"] == "DC-llm-custom"
    # 线稿目录 id 提交前定型为 provider + 内部模型名，请求模板与参数一并定型。
    assert captured["image_provider"] == "gateway"
    assert captured["image_model"] == "newapi-image-1"
    assert captured["image_request_schema"] == {"template": "image-request"}
    assert captured["image_model_params"] == {"size": "2K"}
    # 音频目录 id 提交前定型为内部模型名；账号声线用户名随 requester 走。
    assert captured["model"] == "newapi-tts-1"
    assert captured["account_voice_username"] == "user-1"
    # 渲染参数定型：画幅/清晰度归一，幕数与开关原样。
    assert captured["aspect_ratio"] == "9:16"
    assert captured["resolution"] == "1080p"
    assert captured["max_scenes"] == 6
    assert captured["pen_cursor"] is False
    assert captured["subtitle_enabled"] is True
    assert captured["subtitle_font_size"] == "large"
    assert captured["emotion_prompt"] == "轻松、亲切"
    assert captured["bgm_path"] == bgm.as_posix()
    assert captured["bgm_volume"] == 0.25
    assert captured["voice_ref"] is None
    assert captured["archive_to_library"] is True
    assert captured["source_tool"] == "whiteboard-animation"
    # 配音链路把项目态一并定型（执行方不再回读项目库）。
    assert captured["projection"] == {"voice_registry": {}}
    assert captured["projection_task_type"] == "freezone_whiteboard_animation"
    assert captured["projection_extra_config"] == {"voice_ref": None}
    assert catalog_calls == [
        ("image", "img-cat", "user-1"),
        ("audio", "user-1"),
    ]


@pytest.mark.asyncio
async def test_whiteboard_route_forwards_locked_script_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两段式第二段：路由必须把确认过的整份草稿塞进 payload。

    守的是 HTTP 接线层——runner 侧收到 ``payload["locked_script"]`` 才跳过文本模型。
    只测 runner 的会话漏掉这一层，会让「改完白改」（静默重新生成台词）在无报错下复发。
    """
    ctx = _wb_ctx(tmp_path)
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {})
    captured: dict[str, object] = {}
    _patch_wb_catalogs(monkeypatch, captured)

    async def fake_enqueue(**kwargs):
        captured.update(kwargs["payload"])
        return {"ok": True, "data": {"task_type": "freezone_whiteboard_animation"}}

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_whiteboard_animation", fake_enqueue
    )

    locked = {
        "draft_version": 1,
        "kind": "whiteboard",
        "scenes": [
            {"text": "用户逐幕改过的旁白。", "keyText": "改", "scenePrompt": "画一条线"}
        ],
    }

    await freezone_routes.freezone_whiteboard_animation(
        project="59",
        body=freezone_routes.FreezoneWhiteboardAnimationRequest(
            script="原始文案会被 locked_script 覆盖",
            locked_script=locked,
        ),
        user={"username": "admin"},
    )

    assert captured["locked_script"] == locked


@pytest.mark.asyncio
async def test_whiteboard_route_defaults_horizontal_and_skips_audio_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _wb_ctx(tmp_path)
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {})

    captured: dict[str, object] = {}
    catalog_calls = _patch_wb_catalogs(monkeypatch, captured)

    async def fake_enqueue(**kwargs):
        captured.update(kwargs["payload"])
        return {"ok": True, "data": {"task_type": "freezone_whiteboard_animation"}}

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_whiteboard_animation", fake_enqueue
    )

    result = await freezone_routes.freezone_whiteboard_animation(
        project="59",
        body=freezone_routes.FreezoneWhiteboardAnimationRequest(script="一句话文案"),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    # 白板默认横屏 16:9 / 720p，幕数 8，笔尖光标默认开、字幕默认不烧录。
    assert captured["aspect_ratio"] == "16:9"
    assert captured["resolution"] == "720p"
    assert captured["max_scenes"] == 8
    assert captured["pen_cursor"] is True
    assert captured["subtitle_enabled"] is False
    assert captured["subtitle_font_size"] == "medium"
    assert captured["bgm_path"] == ""
    assert captured["bgm_volume"] is None
    assert captured["model"] == ""
    # 未指定配音模型时不查音频目录（默认语音引擎兜底）。
    assert catalog_calls == [("image", "", "user-1")]


@pytest.mark.asyncio
async def test_whiteboard_route_rejects_empty_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _wb_ctx(tmp_path)
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {})

    async def boom_enqueue(**_kwargs):
        raise AssertionError("must not enqueue with an empty script")

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_whiteboard_animation", boom_enqueue
    )

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_whiteboard_animation(
            project="59",
            body=freezone_routes.FreezoneWhiteboardAnimationRequest(script="   "),
            user={"username": "admin"},
        )

    assert exc.value.status_code == 400
    assert "script is required" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_whiteboard_route_rejects_unknown_audio_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _wb_ctx(tmp_path)
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {})

    captured: dict[str, object] = {}
    _patch_wb_catalogs(monkeypatch, captured)

    async def boom_enqueue(**_kwargs):
        raise AssertionError("must not enqueue when the audio model is unavailable")

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_whiteboard_animation", boom_enqueue
    )

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_whiteboard_animation(
            project="59",
            body=freezone_routes.FreezoneWhiteboardAnimationRequest(
                script="一句话文案",
                model="tts-missing",
            ),
            user={"username": "admin"},
        )

    assert exc.value.status_code == 409
    assert "音频模型" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_whiteboard_route_rejects_missing_bgm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _wb_ctx(tmp_path)
    missing = tmp_path / "ghost.mp3"
    bgm_url = "/static/admin/59/freezone/_uploads/ghost.mp3"
    _patch_wb_route_resolve(monkeypatch, tmp_path, ctx, {bgm_url: missing})

    captured: dict[str, object] = {}
    _patch_wb_catalogs(monkeypatch, captured)

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_whiteboard_animation(
            project="59",
            body=freezone_routes.FreezoneWhiteboardAnimationRequest(
                script="一句话文案",
                bgm_url=bgm_url,
            ),
            user={"username": "admin"},
        )

    assert exc.value.status_code == 404
    assert "bgm not found" in str(exc.value.detail)


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


def _wb_payload(project_dir: Path, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "job_id": "wb_job",
        "project_dir": str(project_dir),
        "script": "为什么你的视频留不住人？第一个原因是开头没有钩子。",
        "model_text": "DC-llm-custom",
        "image_provider": "gateway",
        "image_model": "newapi-image-1",
        "image_model_params": {"size": "2K"},
        "image_request_schema": {"template": "image-request"},
        "model": "newapi-tts-1",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "max_scenes": 6,
        "pen_cursor": True,
        "subtitle_enabled": False,
        "subtitle_font_size": "medium",
        "emotion_prompt": "轻松",
        "voice_ref": None,
        "bgm_path": "",
        "bgm_volume": None,
        "account_voice_username": "",
        "archive_to_library": True,
        "source_tool": "whiteboard-animation",
    }
    payload.update(overrides)
    return payload


def _patch_wb_runner_leaves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    plan_text: str,
    narration_ms: int = 6000,
) -> dict[str, object]:
    """把分镜/配音/出图/叠字/逐笔揭示/拼接渲染全部换成显式签名假对象。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {
        "text": [],
        "speech": [],
        "gen": [],
        "overlay": [],
        "reveal": [],
        "render": [],
        "archived": [],
    }
    voice_path = tmp_path / "voice.mp3"

    async def fake_generate_text(*, prompt, model=None):
        calls["text"].append({"prompt": prompt, "model": model})
        return "DC-plan", plan_text

    async def fake_audio_speech(**kwargs):
        calls["speech"].append(kwargs)
        return SimpleNamespace(
            audio_path=str(voice_path),
            duration_ms=narration_ms,
            model="newapi-tts-1",
        )

    async def fake_gen(**kwargs):
        calls["gen"].append(kwargs)
        raw = tmp_path / f"{kwargs['job_id']}.png"
        raw.write_bytes(b"png")
        return str(raw)

    def fake_overlay(*, image_path, out_path, text, width, height):
        calls["overlay"].append(
            {
                "image_path": str(image_path),
                "out_path": str(out_path),
                "text": text,
                "width": width,
                "height": height,
            }
        )
        Path(out_path).write_bytes(b"png")

    def fake_reveal(*, image_path, out_path, duration_ms, width, height, fps, pen_cursor):
        calls["reveal"].append(
            {
                "image_path": str(image_path),
                "out_path": str(out_path),
                "duration_ms": duration_ms,
                "width": width,
                "height": height,
                "fps": fps,
                "pen_cursor": pen_cursor,
            }
        )
        Path(out_path).write_bytes(b"mp4")

    async def fake_render(**kwargs):
        calls["render"].append(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress(0.5, "拼接中...")
        return output_path

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
        "novelvideo.freezone.jobs.run_freezone_video_mix_render", fake_render
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
        "novelvideo.freezone.whiteboard.render_whiteboard_scene", fake_reveal
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
async def test_whiteboard_runner_plans_dubs_reveals_and_stitches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    plan_text = json.dumps(
        [
            {"scenePrompt": "一个人划着手机刷视频", "keyText": "开头三秒", "weight": 1.0},
            {"scenePrompt": "上升的流失曲线", "keyText": "流失", "weight": 2.0},
        ],
        ensure_ascii=False,
    )
    calls = _patch_wb_runner_leaves(monkeypatch, tmp_path, plan_text=plan_text)

    envelope = {
        "task_type": "freezone_whiteboard_animation",
        "payload": _wb_payload(project_dir),
    }
    result = await freezone_runner._run_freezone_whiteboard_animation_async(
        envelope, _project_context(tmp_path)
    )

    assert result["job_id"] == "wb_job"
    assert result["media_type"] == "video"
    assert result["plan_model"] == "DC-plan"
    assert result["model"] == "newapi-tts-1"
    assert result["scene_count"] == 2
    assert result["narration_duration_ms"] == 6000
    assert result["aspect_ratio"] == "9:16"
    assert result["resolution"] == "720p"
    assert result["pen_cursor"] is True
    assert result["subtitle_enabled"] is False
    assert result["subtitle_font_size"] == "medium"

    # 分镜规划 leaf：文案进 prompt，幕数上限进 prompt，模型选型原样透传。
    assert len(calls["text"]) == 1
    assert "为什么你的视频留不住人？" in calls["text"][0]["prompt"]
    assert "不超过 6 个连续画面" in calls["text"][0]["prompt"]
    assert calls["text"][0]["model"] == "DC-llm-custom"

    # 配音 leaf：独立 job 号、全文直达、目录模型名原样传。
    assert len(calls["speech"]) == 1
    assert calls["speech"][0]["job_id"] == "wb_job_voice"
    assert calls["speech"][0]["model"] == "newapi-tts-1"
    assert calls["speech"][0]["emotion_prompt"] == "轻松"

    # 逐幕出图：每幕一次图像 leaf，线稿执行身份与画幅定型透传。
    assert len(calls["gen"]) == 2
    assert [item["job_id"] for item in calls["gen"]] == ["wb_job_s1", "wb_job_s2"]
    assert calls["gen"][0]["provider"] == "gateway"
    assert calls["gen"][0]["model"] == "newapi-image-1"
    assert calls["gen"][0]["model_params"] == {"size": "2K"}
    assert calls["gen"][0]["request_schema"] == {"template": "image-request"}
    assert calls["gen"][0]["aspect_ratio"] == "9:16"
    assert calls["gen"][0]["output_task_type"] == "freezone_whiteboard_animation"
    assert "画面内容：一个人划着手机刷视频" in calls["gen"][0]["prompt"]

    # 重点文字本地叠加（不进图像模型），叠字后的图才交给逐笔揭示。
    work_dir = outputs_dir(project_dir, "freezone_whiteboard_animation") / "wb_job_work"
    assert [item["text"] for item in calls["overlay"]] == ["开头三秒", "流失"]
    assert calls["overlay"][0]["width"] == 720
    assert calls["overlay"][0]["height"] == 1280
    assert calls["overlay"][0]["out_path"] == str(work_dir / "scene_01.png")

    # 逐笔揭示：每幕一条 clip，时长按 weight 分配（1:2 → 2000/4000ms）。
    assert [item["duration_ms"] for item in calls["reveal"]] == [2000, 4000]
    assert [item["image_path"] for item in calls["reveal"]] == [
        str(work_dir / "scene_01.png"),
        str(work_dir / "scene_02.png"),
    ]
    assert all(item["pen_cursor"] is True for item in calls["reveal"])
    assert all(item["fps"] == 30 for item in calls["reveal"])

    # 拼接：逐幕 clip 作视频素材，无转场、contain 适配、配音驱动。
    assert len(calls["render"]) == 1
    render_call = calls["render"][0]
    assert render_call["material_paths"] == [
        str(work_dir / "clip_01.mp4"),
        str(work_dir / "clip_02.mp4"),
    ]
    assert render_call["material_kinds"] == ["video", "video"]
    assert [item["duration"] for item in render_call["plan"]] == [2.0, 4.0]
    assert render_call["canvas_width"] == 720
    assert render_call["canvas_height"] == 1280
    assert render_call["fit_mode"] == "contain"
    assert render_call["transition"] == "none"
    assert render_call["audio_mode"] == "generate"
    assert render_call["bgm_path"] is None
    assert render_call["bgm_volume"] is None
    # 未勾选烧录 → 不产 SRT。
    assert render_call["subtitle_srt_text"] == ""

    expected_output = (
        outputs_dir(project_dir, "freezone_whiteboard_animation") / "wb_job.mp4"
    )
    assert render_call["output_path"] == expected_output
    assert result["output_path"] == str(expected_output)
    assert (
        result["output_url"]
        == "/static/freezone/_outputs/freezone_whiteboard_animation/wb_job.mp4"
    )

    # 归档：一次提交一条成片，媒体类型视频，走工具页自动入库口径。
    assert len(calls["archived"]) == 1
    assert calls["archived"][0]["media"] == "video"
    assert calls["archived"][0]["task_type"] == "freezone_whiteboard_animation"
    assert calls["archived"][0]["url"] == result["output_url"]

    # 进度覆盖规划 / 配音 / 逐幕揭示 / 拼接（含 on_progress 折算）/ 归档。
    task_manager = calls["task_manager"]
    assert [update["progress"] for update in task_manager.updates] == [
        0.05,
        0.15,
        0.3,
        0.525,
        0.8,
        0.89,
        0.97,
    ]


@pytest.mark.asyncio
async def test_whiteboard_runner_burns_subtitles_and_bgm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"mp3")
    plan_text = json.dumps(
        [{"scenePrompt": "漏斗示意图", "keyText": "", "weight": 1.0}],
        ensure_ascii=False,
    )
    calls = _patch_wb_runner_leaves(monkeypatch, tmp_path, plan_text=plan_text)

    result = await freezone_runner._run_freezone_whiteboard_animation_async(
        {
            "task_type": "freezone_whiteboard_animation",
            "payload": _wb_payload(
                project_dir,
                subtitle_enabled=True,
                subtitle_font_size="large",
                pen_cursor=False,
                bgm_path=str(bgm),
                bgm_volume=0.3,
            ),
        },
        _project_context(tmp_path),
    )

    assert result["subtitle_enabled"] is True
    assert result["subtitle_font_size"] == "large"
    assert result["pen_cursor"] is False

    # 无 keyText 的幕不叠字，但仍走逐笔揭示。
    assert calls["overlay"] == []
    assert len(calls["reveal"]) == 1
    assert calls["reveal"][0]["pen_cursor"] is False

    render_call = calls["render"][0]
    assert "00:00:00,000 -->" in render_call["subtitle_srt_text"]
    assert render_call["subtitle_font_size"] == "large"
    assert render_call["bgm_path"] == str(bgm)
    assert render_call["bgm_volume"] == 0.3


@pytest.mark.asyncio
async def test_whiteboard_runner_falls_back_to_single_scene_when_plan_unparsable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_wb_runner_leaves(
        monkeypatch, tmp_path, plan_text="抱歉，我不会输出 JSON。"
    )

    result = await freezone_runner._run_freezone_whiteboard_animation_async(
        {
            "task_type": "freezone_whiteboard_animation",
            "payload": _wb_payload(project_dir, script="一句很短的文案"),
        },
        _project_context(tmp_path),
    )

    # 规划失败不致命：整段文案走单幕，画面交给线稿模型自由发挥。
    assert result["scene_count"] == 1
    assert len(calls["gen"]) == 1
    assert calls["overlay"] == []
    assert [item["duration_ms"] for item in calls["reveal"]] == [6000]


@pytest.mark.asyncio
async def test_whiteboard_runner_rejects_invalid_narration_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    _patch_wb_runner_leaves(
        monkeypatch, tmp_path, plan_text="[]", narration_ms=0
    )

    with pytest.raises(RuntimeError, match="narration duration is invalid"):
        await freezone_runner._run_freezone_whiteboard_animation_async(
            {
                "task_type": "freezone_whiteboard_animation",
                "payload": _wb_payload(project_dir),
            },
            _project_context(tmp_path),
        )


@pytest.mark.asyncio
async def test_whiteboard_runner_requires_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )

    with pytest.raises(ValueError, match="whiteboard narration script is required"):
        await freezone_runner._run_freezone_whiteboard_animation_async(
            {
                "task_type": "freezone_whiteboard_animation",
                "payload": _wb_payload(project_dir, script="   "),
            },
            _project_context(tmp_path),
        )
