"""[aipdd fork delta] 「视频反推剧本」工具的后端契约回归。

覆盖五条不变量：

1. ``_render_video_script_reverse_text``：结构化分镜行 → 纯文本剧本；空字段
   的标签行不呈现，无标题回退「# 分镜剧本」。
2. runner：抽帧参数透传、**用户选定模型**必须到达视觉 leaf（留空回 None 走
   默认视觉链）、产物只写 ``outputs/freezone_video_script_reverse/{job}.txt``，
   绝不产生 ``freezone_story_script`` 的结构化 JSON。
3. 提交路由：静态 URL 反解 → payload 完整入队；非 /static 外链 400、文件
   缺失 404、无 ProjectContext 503。
4. ``GET /model-gateway/vision-models``：类型判定过滤（vision 入选、其它
   声明排除、DC- 内部别名一律排除），拍平调用名 ``{provider}::{model}``，
   60s 缓存命中零重扫。
5. 视觉 Agent 按模型分键缓存（不再全局单例互踩），组织上下文每次新建。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import model_gateway as model_gateway_routes
from novelvideo.api.routes.model_gateway.connection import (
    _is_selectable_vision_model,
    list_vision_model_options,
)
from novelvideo.api.schemas import (
    FreezoneStoryScriptGenerateData,
    FreezoneStoryScriptRow,
    FreezoneVideoScriptReverseRequest,
)
from novelvideo.task_backend.runners import freezone as runner_mod


def _story_data() -> FreezoneStoryScriptGenerateData:
    return FreezoneStoryScriptGenerateData(
        title="猫猫散步",
        rows=[
            FreezoneStoryScriptRow(
                shot_no=1,
                duration=5,
                visual_description="橘猫踩着落叶走向镜头",
                character_1="橘猫",
                character_description_1="短毛、圆脸",
                shot="中景",
                character_action="缓慢行走",
                emotion="慵懒",
                sound="落叶沙沙",
                dialogue="",
                shot_prompt="中景跟随橘猫 walking on leaves",
                video_motion_prompt="",
            ),
            FreezoneStoryScriptRow(
                shot_no=2,
                duration=3,
                visual_description="特写：猫爪抬起",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 渲染函数
# ---------------------------------------------------------------------------


def test_render_video_script_reverse_text_labels_and_omission() -> None:
    text = runner_mod._render_video_script_reverse_text(_story_data())

    assert text.startswith("# 猫猫散步")
    assert "【镜头 1】时长 5 秒｜景别：中景" in text
    assert "画面：橘猫踩着落叶走向镜头" in text
    assert "角色1：橘猫 短毛、圆脸" in text
    assert "音效：落叶沙沙" in text
    assert "分镜提示词：中景跟随橘猫 walking on leaves" in text
    # 空字段（对白 / 空提示词）的标签行必须整行省略，第二镜只剩画面行。
    assert "对白：" not in text
    assert "视频运动提示词：" not in text
    assert "【镜头 2】时长 3 秒" in text
    assert "特写：猫爪抬起" in text


def test_render_video_script_reverse_text_falls_back_to_default_title() -> None:
    data = FreezoneStoryScriptGenerateData(
        title="",
        rows=[FreezoneStoryScriptRow(shot_no=1, duration=2, visual_description="空镜")],
    )
    text = runner_mod._render_video_script_reverse_text(data)
    assert text.startswith("# 分镜剧本")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _runner_payload(project_dir: Path, **overrides) -> dict:
    payload = {
        "job_id": "job1",
        "project_dir": str(project_dir),
        "video_path": str(project_dir / "clip.mp4"),
        "prompt": "反推这个视频的分镜剧本",
        "model": "volc::doubao-seed-1.6-vision",
        "duration_sec": 15.0,
        "max_frames": 12,
        "scene_threshold": 0.4,
    }
    payload.update(overrides)
    return payload


def _patch_runner_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    frames: list[Path],
    data: FreezoneStoryScriptGenerateData,
) -> dict[str, dict]:
    """按 leaf_name 分发 fake leaf 调用，记录 kwargs 供断言。"""
    captured: dict[str, dict] = {}

    async def fake_call(envelope, leaf, leaf_name, /, **kwargs):
        captured[leaf_name] = kwargs
        if leaf_name == "run_freezone_extract_frames":
            return frames
        if leaf_name == "generate_freezone_story_script_with_vision":
            return data
        raise AssertionError(f"unexpected leaf {leaf_name!r}")

    monkeypatch.setattr(runner_mod, "_call_freezone_leaf", fake_call)
    monkeypatch.setattr(runner_mod, "_update", lambda *a, **k: None)
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda ctx, rel: f"/static/{rel}",
    )
    return captured


@pytest.mark.asyncio
async def test_video_script_reverse_runner_forwards_model_and_writes_txt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [tmp_path / f"frame_{i}.jpg" for i in range(3)]
    captured = _patch_runner_seams(
        monkeypatch, frames=frames, data=_story_data()
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path))
    envelope = {"payload": _runner_payload(tmp_path)}

    result = await runner_mod._run_freezone_video_script_reverse_async(envelope, ctx)

    extract_kwargs = captured["run_freezone_extract_frames"]
    assert extract_kwargs["max_frames"] == 12
    assert extract_kwargs["scene_threshold"] == pytest.approx(0.4)
    assert extract_kwargs["video_path"] == Path(str(tmp_path / "clip.mp4"))

    vision_kwargs = captured["generate_freezone_story_script_with_vision"]
    # 用户显式选定的视觉模型必须原样到达视觉 leaf。
    assert vision_kwargs["model"] == "volc::doubao-seed-1.6-vision"
    assert vision_kwargs["frame_paths"] == [str(path) for path in frames]
    assert vision_kwargs["duration_sec"] == 15.0
    assert vision_kwargs["prompt"] == "反推这个视频的分镜剧本"

    # 产物是独立任务类型下的 txt，绝不落进 freezone_story_script 的 JSON 表。
    out = tmp_path / "freezone" / "_outputs" / "freezone_video_script_reverse" / "job1.txt"
    assert out.exists()
    assert "【镜头 1】时长 5 秒｜景别：中景" in out.read_text(encoding="utf-8")
    assert not (tmp_path / "freezone" / "_outputs" / "freezone_story_script").exists()

    assert result["output_format"] == "txt"
    assert result["row_count"] == 2
    assert result["title"] == "猫猫散步"
    assert result["output_url"].endswith("freezone_video_script_reverse/job1.txt")
    assert result["text"] == out.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_video_script_reverse_runner_blank_model_falls_back_to_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_runner_seams(
        monkeypatch, frames=[tmp_path / "f.jpg"], data=_story_data()
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path))
    envelope = {"payload": _runner_payload(tmp_path, model="", duration_sec=None)}

    await runner_mod._run_freezone_video_script_reverse_async(envelope, ctx)

    # 留空 = 走后端默认视觉链（FREEZONE_VISION_MODEL → VISION_MODEL_FALLBACK）。
    assert captured["generate_freezone_story_script_with_vision"]["model"] is None
    assert captured["generate_freezone_story_script_with_vision"]["duration_sec"] is None


@pytest.mark.asyncio
async def test_video_script_reverse_runner_requires_video_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runner_seams(monkeypatch, frames=[], data=_story_data())
    ctx = SimpleNamespace(output_dir=str(tmp_path))
    envelope = {"payload": _runner_payload(tmp_path, video_path="")}

    with pytest.raises(ValueError, match="requires video_path"):
        await runner_mod._run_freezone_video_script_reverse_async(envelope, ctx)


@pytest.mark.asyncio
async def test_video_script_reverse_runner_rejects_empty_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runner_seams(monkeypatch, frames=[], data=_story_data())
    ctx = SimpleNamespace(output_dir=str(tmp_path))
    envelope = {"payload": _runner_payload(tmp_path)}

    with pytest.raises(ValueError, match="关键帧"):
        await runner_mod._run_freezone_video_script_reverse_async(envelope, ctx)


# ---------------------------------------------------------------------------
# 提交路由
# ---------------------------------------------------------------------------


def _patch_route_resolution(
    monkeypatch: pytest.MonkeyPatch, project_dir: Path, ctx: object | None = object()
) -> None:
    async def fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)


@pytest.mark.asyncio
async def test_video_script_reverse_route_enqueues_full_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    video = project_dir / "freezone" / "_uploads" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake-mp4")

    _patch_route_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "data": {"task_type": kwargs["task_type"]}}

    monkeypatch.setattr(freezone_routes, "_enqueue_freezone_background_job", fake_enqueue)

    result = await freezone_routes.freezone_video_script_reverse(
        project="58",
        body=FreezoneVideoScriptReverseRequest(
            video_url="/static/admin/58/freezone/_uploads/clip.mp4",
            prompt="反推剧本",
            model="volc::doubao-seed-1.6-vision",
            duration_sec=15.0,
            max_frames=12,
            scene_threshold=0.4,
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert captured["task_type"] == "freezone_video_script_reverse"
    payload = captured["payload"]
    assert payload["video_path"] == video.as_posix()
    assert payload["prompt"] == "反推剧本"
    assert payload["model"] == "volc::doubao-seed-1.6-vision"
    assert payload["duration_sec"] == 15.0
    assert payload["max_frames"] == 12
    assert payload["scene_threshold"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_video_script_reverse_route_defaults_when_fields_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    video = project_dir / "freezone" / "_uploads" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")

    _patch_route_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "data": {"task_type": kwargs["task_type"]}}

    monkeypatch.setattr(freezone_routes, "_enqueue_freezone_background_job", fake_enqueue)

    await freezone_routes.freezone_video_script_reverse(
        project="58",
        body=FreezoneVideoScriptReverseRequest(
            video_url="/static/admin/58/freezone/_uploads/clip.mp4",
        ),
        user={"username": "admin"},
    )

    payload = captured["payload"]
    assert payload["model"] == ""
    assert payload["duration_sec"] is None
    assert payload["max_frames"] == 20
    assert payload["scene_threshold"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_video_script_reverse_route_rejects_external_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    _patch_route_resolution(monkeypatch, tmp_path / "project")

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_script_reverse(
            project="58",
            body=FreezoneVideoScriptReverseRequest(
                video_url="https://evil.example.com/clip.mp4",
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_video_script_reverse_route_missing_video_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    _patch_route_resolution(monkeypatch, tmp_path / "project")

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_script_reverse(
            project="58",
            body=FreezoneVideoScriptReverseRequest(
                video_url="/static/admin/58/freezone/_uploads/ghost.mp4",
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_video_script_reverse_route_without_context_returns_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    project_dir = tmp_path / "project"
    video = project_dir / "freezone" / "_uploads" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")

    _patch_route_resolution(monkeypatch, project_dir, ctx=None)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_script_reverse(
            project="58",
            body=FreezoneVideoScriptReverseRequest(
                video_url="/static/admin/58/freezone/_uploads/clip.mp4",
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 503


# ---------------------------------------------------------------------------
# vision-models 候选端点
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_vision_cache():
    previous = model_gateway_routes._vision_models_cache
    model_gateway_routes._vision_models_cache = None
    yield
    model_gateway_routes._vision_models_cache = previous


def test_is_selectable_vision_model_classification() -> None:
    # 视觉能力位命中才入选；DC- 内部功能位一律排除。
    assert _is_selectable_vision_model("doubao-seed-1.6-vision")
    assert _is_selectable_vision_model("qwen-vl-max")
    assert _is_selectable_vision_model("anything", "vision")
    assert not _is_selectable_vision_model("anything", "video")
    assert not _is_selectable_vision_model("ap-glm-5.2")
    assert not _is_selectable_vision_model("DC-freezone-vision-VLM")
    assert not _is_selectable_vision_model("")
    # [aipdd fork delta] 多模态 embedding 不是视觉理解模型：方舟清单里的
    # doubao-embedding-vision-* 名字带 vision 曾被当候选，选上就是 404/类型错。
    assert not _is_selectable_vision_model("doubao-embedding-vision-250615")
    assert not _is_selectable_vision_model("doubao-embedding-vision-251215")
    assert not _is_selectable_vision_model("text-embedding-v3")
    # 显式声明也改不了这个事实：声明 vision 的 embedding 仍然不当候选。
    assert not _is_selectable_vision_model("doubao-embedding-vision-250615", "vision")
    # 资格面与设置页「视觉理解」TAB 同口径：seed 系列是多模态输入模型。
    assert _is_selectable_vision_model("doubao-seed-2-1-pro-260628")


@pytest.mark.asyncio
async def test_vision_models_endpoint_flattens_and_filters(
    monkeypatch: pytest.MonkeyPatch, clear_vision_cache
) -> None:
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}, {"provider": "volc"}, {"provider": "comfyui"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: {
            "aipdd": ["ap-glm-5.2", "DC-freezone-vision-VLM"],
            "volc": ["doubao-seed-1.6-vision", "seedance-2.0"],
        }[provider],
    )

    result = await list_vision_model_options()

    # 只剩视觉理解类；comfyui 渠道整体跳过；调用名 = 「渠道::模型」。
    ids = [entry["id"] for entry in result["data"]]
    assert ids == ["volc::doubao-seed-1.6-vision"]
    entry = result["data"][0]
    assert entry["channel"] == "volc"
    assert entry["providerId"] == "newapi"
    assert "isDefault" not in entry


@pytest.mark.asyncio
async def test_vision_models_endpoint_cache_hit_skips_rescan(
    monkeypatch: pytest.MonkeyPatch, clear_vision_cache
) -> None:
    calls = {"channels": 0}

    def fake_channels():
        calls["channels"] += 1
        return [{"provider": "volc"}]

    monkeypatch.setattr(model_gateway_routes, "get_newapi_provider_channels", fake_channels)
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: ["doubao-seed-1.6-vision"],
    )

    first = await list_vision_model_options()
    second = await list_vision_model_options()

    assert second == first
    assert calls["channels"] == 1


# ---------------------------------------------------------------------------
# 视觉 Agent 分键缓存 + 模型透传
# ---------------------------------------------------------------------------


@pytest.fixture
def text_node_env(monkeypatch: pytest.MonkeyPatch):
    from novelvideo.freezone import text_node

    created: list[str | None] = []

    def fake_create(model: str | None = None):
        created.append(model)
        return SimpleNamespace(tag=f"agent[{model or ''}]")

    monkeypatch.setattr(text_node, "create_freezone_video_story_script_agent", fake_create)
    monkeypatch.setattr(text_node, "current_model_gateway_context", lambda: None)
    monkeypatch.setattr(text_node, "_refresh_prompt_agents", lambda: None)
    text_node._video_story_script_agents.clear()
    yield text_node, created
    text_node._video_story_script_agents.clear()


def test_video_story_script_agent_cached_per_model(text_node_env) -> None:
    text_node, created = text_node_env

    default_a = text_node.get_freezone_video_story_script_agent()
    default_b = text_node.get_freezone_video_story_script_agent("")
    m1_a = text_node.get_freezone_video_story_script_agent("volc::doubao-vision")
    m1_b = text_node.get_freezone_video_story_script_agent("volc::doubao-vision")
    m2_a = text_node.get_freezone_video_story_script_agent("aipdd::qwen-vl-max")

    # 留空 = 同一默认键；不同模型各缓存各的，不再互踩全局单例。
    assert default_a is default_b
    assert m1_a is m1_b
    assert m1_a is not default_a
    assert m2_a is not m1_a
    assert created == [None, "volc::doubao-vision", "aipdd::qwen-vl-max"]


def test_video_story_script_agent_organization_context_not_cached(text_node_env) -> None:
    text_node, created = text_node_env
    monkeypatched_org = SimpleNamespace(is_organization=True)
    text_node.current_model_gateway_context = lambda: monkeypatched_org

    a = text_node.get_freezone_video_story_script_agent("volc::doubao-vision")
    b = text_node.get_freezone_video_story_script_agent("volc::doubao-vision")

    assert a is not b
    assert created == ["volc::doubao-vision", "volc::doubao-vision"]


@pytest.mark.asyncio
async def test_generate_story_script_forwards_selected_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone.text_node import generate_freezone_story_script_with_vision

    frames = []
    for index in range(2):
        frame = tmp_path / f"frame_{index}.png"
        Image.new("RGB", (640, 360), (index * 60, 48, 96)).save(frame)
        frames.append(frame)
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, model: str | None = None) -> None:
            captured["model"] = model

        async def run(self, messages):
            captured["count"] = len(messages)

            class Response:
                output = FreezoneStoryScriptGenerateData(title="", rows=[])

            return Response()

    monkeypatch.setattr(
        "novelvideo.freezone.text_node.get_freezone_video_story_script_agent",
        FakeAgent,
    )

    await generate_freezone_story_script_with_vision(
        frame_paths=frames,
        prompt="反推剧本",
        model="volc::doubao-seed-1.6-vision",
    )

    assert captured["model"] == "volc::doubao-seed-1.6-vision"
    assert captured["count"] == 1 + len(frames)
