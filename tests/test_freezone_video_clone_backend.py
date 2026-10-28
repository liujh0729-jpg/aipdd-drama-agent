"""[aipdd fork delta] 视频复刻（freezone_video_clone）后端契约测试。

覆盖三层：
1. `novelvideo.freezone.clone` 纯逻辑：镜头规整 / 时长钳制 / 关键帧映射 /
   规划 prompt 构建 / 规划输出解析；
2. 路由层：入队走 video 队列、载荷原样透传、frames 模式模型门禁；
3. runner 全链：抽帧 → 解读 → 规划 → 逐镜生成 → 拼接 → 归档。

runner 用例直接调用 `_run_freezone_video_clone_async`（与
`test_p0g4i_freezone_leaf_classification.py` 同口径），leaf 全部换成显式
签名的假对象，守的是「链路编排与产物形状」而不是真实出网。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.clone import (
    MAX_CLONE_SHOTS,
    build_video_clone_plan_prompt,
    normalize_clone_shots,
    parse_video_clone_plan,
    shot_reference_paths,
    shot_target_duration,
)
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


# ---------------------------------------------------------------
# 1) clone.py 纯逻辑
# ---------------------------------------------------------------


def test_normalize_clone_shots_filters_non_dicts_and_caps_at_limit() -> None:
    assert normalize_clone_shots(None) == []
    assert normalize_clone_shots(["not", "a", "dict"]) == []
    assert normalize_clone_shots({"shots": "nope"}) == []

    raw: list[object] = [{"shot": 1}, "junk"] + [{"shot": i} for i in range(2, 20)]
    shots = normalize_clone_shots({"shots": raw})

    assert len(shots) == MAX_CLONE_SHOTS
    assert all(isinstance(shot, dict) for shot in shots)


def test_shot_target_duration_prefers_duration_then_span_then_default() -> None:
    assert shot_target_duration({"duration": 3.5}) == 3.5
    assert shot_target_duration({"start_time": 2.0, "end_time": 6.5}) == 4.5
    assert shot_target_duration({}) == 5.0
    # 钳制在 1~60 秒：过短顶到 1s，过长收到 60s。
    assert shot_target_duration({"duration": 0.1}) == 1.0
    assert shot_target_duration({"duration": 999}) == 60.0


def test_shot_reference_paths_maps_one_based_keyframes_in_order(tmp_path: Path) -> None:
    frames = [tmp_path / f"scene_{index:02d}.png" for index in range(1, 5)]

    # keyframes 是 1-based 帧序号：2→第二帧；9 越界丢弃；2 重复只保留一次；
    # 结果保序（按给入顺序）且最多 MAX_SHOT_REFERENCE_FRAMES=2 张。
    paths = shot_reference_paths({"keyframes": [2, 1, 9, 2]}, frames)

    assert paths == [str(frames[1]), str(frames[0])]


def test_shot_reference_paths_falls_back_to_start_time_ratio(tmp_path: Path) -> None:
    frames = [tmp_path / f"even_{index:03d}.png" for index in range(1, 5)]

    # 无 keyframes：按 start_time / duration_sec 比例定位（30/60 * 4 = 2）。
    paths = shot_reference_paths({"start_time": 30.0}, frames, duration_sec=60.0)

    assert paths == [str(frames[2])]


def test_shot_reference_paths_returns_empty_without_frames() -> None:
    assert shot_reference_paths({"keyframes": [1]}, []) == []


def test_build_plan_prompt_frames_mode_keeps_static_looks_to_reference() -> None:
    shots = [
        {"duration": 3.0, "image_prompt": "a", "motion_prompt": "m"},
        {"duration": 4.0, "narrative": "n"},
    ]

    prompt = build_video_clone_plan_prompt(
        shots=shots,
        frame_count=8,
        user_prompt="基调更冷",
        reference_mode="frames",
    )

    assert "共抽取 8 张关键帧" in prompt
    assert "归纳为 2 个镜头" in prompt
    assert "用户附加要求（必须遵守）：基调更冷" in prompt
    assert "不要在提示词里重复描述参考图已经承载的静态外观" in prompt
    assert '"shot": 1' in prompt
    assert "纯文本重制" not in prompt


def test_build_plan_prompt_text_mode_requires_full_visual_description() -> None:
    prompt = build_video_clone_plan_prompt(
        shots=[{}],
        frame_count=3,
        reference_mode="text",
    )

    assert "提示词必须完整描述画面视觉细节" in prompt
    assert "用户附加要求" not in prompt


def test_build_plan_prompt_mentions_content_references_when_present() -> None:
    prompt = build_video_clone_plan_prompt(
        shots=[{}],
        frame_count=2,
        reference_mode="text",
        content_reference_count=3,
    )

    assert "用户另外提供了 3 张「参考内容」图片" in prompt
    assert "参考图会随每镜一并提交给视频模型" in prompt
    # text 重制 + 参考内容：两条提示并存，互不吞并。
    assert "提示词必须完整描述画面视觉细节" in prompt

    without = build_video_clone_plan_prompt(
        shots=[{}], frame_count=2, reference_mode="text"
    )
    assert "参考内容" not in without


def test_build_plan_prompt_generate_mode_carries_sound_design_and_dialogue() -> None:
    """模型生成音时，解读阶段的声音字段与 ASR 台词都要进规划 prompt。

    实测口径：音画同步类视频模型只按提示词发声，提示词里没有声音设计时成片
    根本没有任何人声（ASR 转写为空），逐字写入台词则能复现原句。
    """
    prompt = build_video_clone_plan_prompt(
        shots=[{"duration": 3.0, "voice_sound": "小女孩的问句", "image_prompt": "a"}],
        frame_count=4,
        audio_mode="generate",
        dialogue_text="Mom, when will you be back?",
    )

    assert "每个 video_prompt 末尾必须再补一行 `Audio:`" in prompt
    assert "参考视频台词转写（按时间顺序，逐字保留原语言" in prompt
    assert "Mom, when will you be back?" in prompt
    assert '"voice_sound": "小女孩的问句"' in prompt


def test_build_plan_prompt_non_generate_mode_forbids_audio_design() -> None:
    prompt = build_video_clone_plan_prompt(
        shots=[{"duration": 3.0}],
        frame_count=4,
        audio_mode="mute",
        dialogue_text="Mom, when will you be back?",
    )

    assert "不要写 `Audio:`" in prompt
    assert "参考视频台词转写" not in prompt
    assert "Mom, when will you be back?" not in prompt
    assert '"duration": 3.0' in prompt


def test_build_plan_prompt_subject_priority_drops_original_looks() -> None:
    """主体替换优先：不提交关键帧，image_prompt 里写死的原主体也不能再出现。"""
    prompt = build_video_clone_plan_prompt(
        shots=[
            {
                "duration": 3.0,
                "image_prompt": "a cute little blonde girl in a pink dress",
                "motion_prompt": "slow push in",
            }
        ],
        frame_count=4,
        content_reference_count=1,
        subject_reference_only=True,
    )

    assert "主体替换优先" in prompt
    assert "a cute little blonde girl" not in prompt
    assert '"motion_prompt": "slow push in"' in prompt
    assert "本次复刻只提交用户参考内容作为参考图" in prompt
    assert "保持主体外观、服装" not in prompt


def test_parse_video_clone_plan_include_audio_fallback_keeps_sound_design() -> None:
    shots = [{"voice_sound": "小女孩的问句", "background_music": "轻钢琴"}]

    plan = parse_video_clone_plan("not json", shots, include_audio=True)
    assert "小女孩的问句" in plan[0]["video_prompt"]
    assert "轻钢琴" in plan[0]["video_prompt"]

    # 不用模型生成音时不必把声音设计塞进提示词。
    plan = parse_video_clone_plan("not json", shots)
    assert plan[0]["video_prompt"] == "cinematic shot"


def test_parse_video_clone_plan_strips_markdown_and_matches_numbers() -> None:
    shots = [
        {"motion_prompt": "m1"},
        {"image_prompt": "i2"},
        {"visual_description": "v3"},
    ]
    raw = (
        "```json\n"
        '{"shots": [{"shot": 1, "video_prompt": "P1"}, '
        '{"shot": 3, "video_prompt": "P3"}]}\n'
        "```"
    )

    plan = parse_video_clone_plan(raw, shots)

    # 规划漏掉 2 号镜时回退到解读自带的 image_prompt，保证每镜都有提示词。
    assert [item["video_prompt"] for item in plan] == ["P1", "i2", "P3"]
    assert [item["shot"] for item in plan] == [1, 2, 3]
    assert all(item["target_duration"] == 5.0 for item in plan)


def test_parse_video_clone_plan_accepts_bare_list_and_falls_back() -> None:
    plan = parse_video_clone_plan('[{"shot": 1, "video_prompt": "P"}]', [{}])
    assert plan[0]["video_prompt"] == "P"

    plan = parse_video_clone_plan("not json", [{}])
    assert plan[0]["video_prompt"] == "cinematic shot"


# ---------------------------------------------------------------
# 2) 路由 / 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_clone_enqueue_uses_video_queue_and_verbatim_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    scoped: list[tuple[str, str, str]] = []

    async def fake_enqueue_project_task(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task_1"),
            backend="celery",
            queue="node.video",
        )

    async def fake_require_scoped_media_model(
        media_type: str,
        requested: str | None,
        *,
        requester_user_id: str,
    ):
        scoped.append((media_type, str(requested), requester_user_id))
        return None

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )
    monkeypatch.setattr(
        freezone_routes, "_require_scoped_media_model", fake_require_scoped_media_model
    )

    ctx = SimpleNamespace(project_id="project_59", requester_user_id="user-1")
    result = await freezone_routes._enqueue_or_start_freezone_video_clone(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="clone_job",
        payload={
            "video_path": "/tmp/x.mp4",
            "catalog_id": "cat-fast",
            "reference_mode": "frames",
        },
    )

    assert result["data"]["task_type"] == "freezone_video_clone"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_video_clone"
    assert captured["queue_kind"] == "video"
    assert captured["episode"] == 0
    assert captured["scope"] == "clone_job"
    # 载荷原样透传：入队侧只补 job_id / project_dir，不改字段。
    assert captured["payload"] == {
        "job_id": "clone_job",
        "project_dir": str(tmp_path),
        "video_path": "/tmp/x.mp4",
        "catalog_id": "cat-fast",
        "reference_mode": "frames",
    }
    # 入队前按目录 id 复查模型可见性（video 类型）。
    assert scoped == [("video", "cat-fast", "user-1")]


def _patch_route_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ctx: SimpleNamespace,
) -> None:
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)


@pytest.mark.asyncio
async def test_video_clone_route_resolves_model_and_passes_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    captured: dict[str, object] = {}
    catalog_calls: list[tuple] = []

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_route_project(monkeypatch, tmp_path, ctx)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "clone_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )

    async def fake_resolve_video_backend(model, *, requester_user_id=None):
        catalog_calls.append(("backend", model, requester_user_id))
        return "newapi_seedance-2.0-fast"

    async def fake_resolve_catalog_request(
        media_type, model, model_params, *, mode=None, requester_user_id=None
    ):
        catalog_calls.append(("request", media_type, model, mode, requester_user_id))
        capabilities = {"supportedModes": ["all_reference"], "maxImageInputs": 9}
        return {"schema": "x"}, {"seed": 7}, capabilities

    async def fake_probe_duration(_path):
        return 12.5

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        # 入队辅助本身没有 task_type 形参（路由级 monkeypatch 掉了整个辅助），
        # 返回值按真实辅助的形状给出，只用于断言路由把载荷交给了入队。
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_clone",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_video_clone:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_video_backend", fake_resolve_video_backend
    )
    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_request", fake_resolve_catalog_request
    )
    monkeypatch.setattr(freezone_routes, "probe_video_duration_seconds", fake_probe_duration)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_clone", fake_enqueue
    )

    result = await freezone_routes.freezone_video_clone(
        project="59",
        body=freezone_routes.FreezoneVideoCloneRequest(
            video_url="/static/admin/59/freezone/_uploads/ref.mp4",
            prompt="  更冷冽的色调  ",
            model="cat-fast",
            reference_mode="frames",
            reference_priority="composition",
            audio_mode="source",
            aspect_ratio="9:16",
            resolution="720p",
            max_frames=16,
            scene_threshold=0.25,
            human_review=True,
            model_params={"seed": 7},
            archive_to_library=True,
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_clone"
    # frames 模式按 allReference 解析目录能力。
    assert catalog_calls[0] == ("backend", "cat-fast", "user-1")
    assert catalog_calls[1] == ("request", "video", "cat-fast", "allReference", "user-1")
    assert captured["video_path"] == video_path.as_posix()
    assert captured["prompt"] == "更冷冽的色调"
    assert captured["backend"] == "newapi_seedance-2.0-fast"
    assert captured["model_id"] == "cat-fast"
    assert captured["reference_mode"] == "frames"
    assert captured["reference_priority"] == "composition"
    assert captured["audio_mode"] == "source"
    # source 模式不请求模型生成音。
    assert captured["generate_audio"] is False
    assert captured["duration_sec"] == 12.5
    assert captured["max_frames"] == 16
    assert captured["scene_threshold"] == 0.25
    assert captured["human_review"] is True
    assert captured["aspect_ratio"] == "9:16"
    assert captured["resolution"] == "720p"
    assert captured["model_params"] == {"seed": 7}
    assert captured["request_schema"] == {"schema": "x"}
    assert captured["archive_to_library"] is True


@pytest.mark.asyncio
async def test_video_clone_route_rejects_frames_mode_on_incapable_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_route_project(monkeypatch, tmp_path, ctx)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "clone_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )

    async def fake_resolve_video_backend(model, *, requester_user_id=None):
        return "newapi_doubao-1.0"

    async def fake_resolve_catalog_request(
        media_type, model, model_params, *, mode=None, requester_user_id=None
    ):
        # 明确声明不支持 all_reference：门禁必须直接 400，而不是放行进 runner。
        return {}, {}, {"supportedModes": ["text_to_video"]}

    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_video_backend", fake_resolve_video_backend
    )
    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_request", fake_resolve_catalog_request
    )

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_clone(
            project="59",
            body=freezone_routes.FreezoneVideoCloneRequest(
                video_url="/static/admin/59/freezone/_uploads/ref.mp4",
                model="cat-plain",
                reference_mode="frames",
            ),
            user={"username": "admin"},
        )

    assert excinfo.value.status_code == 400
    assert "reference_mode" in excinfo.value.detail


def test_video_clone_schema_caps_reference_urls_at_four() -> None:
    request = freezone_routes.FreezoneVideoCloneRequest(
        video_url="/static/x/a.mp4",
        reference_urls=["/static/x/a.png"] * 4,
    )
    assert len(request.reference_urls) == 4

    with pytest.raises(ValidationError):
        freezone_routes.FreezoneVideoCloneRequest(
            video_url="/static/x/a.mp4",
            reference_urls=["/static/x/a.png"] * 5,
        )


def test_video_clone_schema_defaults_reference_priority_to_subject() -> None:
    """默认主体替换优先：带参考内容时期望的是「换成参考内容」而不是保留原主体。"""
    request = freezone_routes.FreezoneVideoCloneRequest(video_url="/static/x/a.mp4")
    assert request.reference_priority == "subject"
    assert request.aspect_ratio == "16:9"

    with pytest.raises(ValidationError):
        freezone_routes.FreezoneVideoCloneRequest(
            video_url="/static/x/a.mp4", reference_priority="whatever"
        )


@pytest.mark.asyncio
async def test_video_clone_route_passes_content_reference_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    ref_a = tmp_path / "ref-a.png"
    ref_a.write_bytes(b"png")
    ref_b = tmp_path / "ref-b.png"
    ref_b.write_bytes(b"png")
    captured: dict[str, object] = {}
    catalog_calls: list[tuple] = []

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_route_project(monkeypatch, tmp_path, ctx)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "clone_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )
    monkeypatch.setattr(
        freezone_routes,
        "_resolve_url_list",
        lambda _project_dir, _urls: [ref_a.as_posix(), ref_b.as_posix()],
    )

    async def fake_resolve_video_backend(model, *, requester_user_id=None):
        return "newapi_seedance-2.0-fast"

    async def fake_resolve_catalog_request(
        media_type, model, model_params, *, mode=None, requester_user_id=None
    ):
        catalog_calls.append((media_type, mode, requester_user_id))
        return {}, {}, {"supportedModes": ["all_reference"], "maxImageInputs": 9}

    async def fake_probe_duration(_path):
        return 9.0

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_clone",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_video_clone:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_video_backend", fake_resolve_video_backend
    )
    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_request", fake_resolve_catalog_request
    )
    monkeypatch.setattr(freezone_routes, "probe_video_duration_seconds", fake_probe_duration)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_clone", fake_enqueue
    )

    result = await freezone_routes.freezone_video_clone(
        project="59",
        body=freezone_routes.FreezoneVideoCloneRequest(
            video_url="/static/admin/59/freezone/_uploads/ref.mp4",
            reference_urls=[
                "/static/admin/59/library/ref-a.png",
                "/static/admin/59/library/ref-b.png",
            ],
            reference_mode="text",
            model="cat-fast",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    # text 模式 + 参考内容：能力解析仍按 allReference 请求（参考图逐镜进模型）。
    assert catalog_calls[0] == ("video", "allReference", "user-1")
    assert captured["reference_paths"] == [ref_a.as_posix(), ref_b.as_posix()]
    assert captured["reference_mode"] == "text"


@pytest.mark.asyncio
async def test_video_clone_route_rejects_reference_content_on_incapable_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_route_project(monkeypatch, tmp_path, ctx)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "clone_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )
    monkeypatch.setattr(
        freezone_routes,
        "_resolve_url_list",
        lambda _project_dir, _urls: [video_path.as_posix()],
    )

    async def fake_resolve_video_backend(model, *, requester_user_id=None):
        return "newapi_doubao-1.0"

    async def fake_resolve_catalog_request(
        media_type, model, model_params, *, mode=None, requester_user_id=None
    ):
        # 明确声明不支持 all_reference：参考内容门禁必须直接 400。
        return {}, {}, {"supportedModes": ["text_to_video"]}

    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_video_backend", fake_resolve_video_backend
    )
    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_request", fake_resolve_catalog_request
    )

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_clone(
            project="59",
            body=freezone_routes.FreezoneVideoCloneRequest(
                video_url="/static/admin/59/freezone/_uploads/ref.mp4",
                model="cat-plain",
                reference_mode="text",
                reference_urls=["/static/admin/59/library/ref-a.png"],
            ),
            user={"username": "admin"},
        )

    assert excinfo.value.status_code == 400
    assert "reference content" in excinfo.value.detail


@pytest.mark.asyncio
async def test_video_clone_route_rejects_missing_content_reference_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_route_project(monkeypatch, tmp_path, ctx)
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )
    monkeypatch.setattr(
        freezone_routes,
        "_resolve_url_list",
        lambda _project_dir, _urls: [(tmp_path / "missing.png").as_posix()],
    )

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_clone(
            project="59",
            body=freezone_routes.FreezoneVideoCloneRequest(
                video_url="/static/admin/59/freezone/_uploads/ref.mp4",
                model="cat-fast",
                reference_urls=["/static/admin/59/library/ref-a.png"],
            ),
            user={"username": "admin"},
        )

    assert excinfo.value.status_code == 404
    assert "reference content file not found" in excinfo.value.detail


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


def _video_clone_payload(
    project_dir: Path,
    video_path: Path,
    **overrides: object,
) -> dict:
    payload: dict[str, object] = {
        "job_id": "clone_job",
        "project_dir": str(project_dir),
        "video_path": str(video_path),
        "duration_sec": 12.0,
        "prompt": "更冷冽",
        "backend": "newapi_seedance-2.0-fast",
        "reference_mode": "frames",
        "audio_mode": "generate",
        "generate_audio": True,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "max_frames": 8,
        "scene_threshold": 0.25,
        "archive_to_library": True,
    }
    payload.update(overrides)
    return payload


def _stub_clone_dialogue(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "",
) -> list[dict]:
    """把复刻台词叶换成假对象并记录调用。

    runner 用例守的是链路编排，不碰 ffmpeg 抽音轨与 ASR 出网；返回值就是
    runner 拿到的台词文本（空串 = 拿不到台词的 best-effort 降级）。
    """
    calls: list[dict] = []

    async def fake_transcribe(
        *, project_dir, job_id, video_path, asr_model="", egress_context=None
    ):
        calls.append(
            {
                "project_dir": project_dir,
                "job_id": job_id,
                "video_path": video_path,
                "egress_context": egress_context,
            }
        )
        return text

    monkeypatch.setattr(
        "novelvideo.freezone.jobs.transcribe_freezone_clone_dialogue",
        fake_transcribe,
    )
    return calls


@pytest.mark.asyncio
async def test_video_clone_runner_full_chain_with_frame_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    frame_paths = [project_dir / f"scene_{index:02d}.png" for index in range(1, 5)]
    for frame in frame_paths:
        frame.write_bytes(b"png")

    video_calls: list[dict] = []
    stitch_calls: list[dict] = []
    archived: list[dict] = []
    text_models: list[str | None] = []

    async def fake_extract_frames(
        *, project_dir, job_id, video_path, max_frames, scene_threshold
    ):
        assert job_id == "clone_job"
        assert max_frames == 8
        assert scene_threshold == 0.25
        return list(frame_paths)

    async def fake_analyze_shots(
        *,
        project_dir,
        job_id,
        frame_paths,
        analysis_mode,
        duration_sec,
        egress_context=None,
    ):
        assert analysis_mode == "video_story"
        assert duration_sec == 12.0
        return {
            "model": "DC-vision",
            "video_story": {
                "shots": [
                    {"keyframes": [1], "duration": 3.0, "image_prompt": "one"},
                    {"keyframes": [3], "duration": 4.0, "motion_prompt": "two"},
                    {
                        "start_time": 8.0,
                        "end_time": 11.0,
                        "visual_description": "three",
                    },
                ]
            },
        }

    async def fake_generate_text(*, prompt, model=None):
        assert "视频复刻" in prompt
        text_models.append(model)
        return (
            "DC-text",
            '{"shots": [{"shot": 1, "video_prompt": "P1"}, '
            '{"shot": 2, "video_prompt": "P2"}, '
            '{"shot": 3, "video_prompt": "P3"}]}',
        )

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = project_dir / "freezone_video_gen" / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out

    async def fake_stitch(
        *, segment_paths, shot_durations, output_path, audio_mode, source_video_path
    ):
        stitch_calls.append(
            {
                "segment_paths": list(segment_paths),
                "shot_durations": list(shot_durations),
                "output_path": output_path,
                "audio_mode": audio_mode,
                "source_video_path": source_video_path,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")

    task_manager = _TaskManager()
    dialogue_calls = _stub_clone_dialogue(monkeypatch)
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_extract_frames", fake_extract_frames
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_analyze_shots", fake_analyze_shots
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.stitch_freezone_video_clone_segments", fake_stitch
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    # 逐镜时长先经 normalize_video_duration_for_backend 规整，钳制范围随
    # LOCALAPI_VIDEO_DURATION_BOUNDS 环境配置变化；这里清空配置钉住链路本身
    # （seedance 系列的最短时长钳制由 video_duration 自身的用例覆盖）。
    monkeypatch.setattr("novelvideo.config.LOCALAPI_VIDEO_DURATION_BOUNDS", "")
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: archived.append(kwargs),
    )

    envelope = {
        "task_type": "freezone_video_clone",
        "payload": _video_clone_payload(project_dir, video_path, model_text="DC-llm-custom"),
    }

    result = await freezone_runner._run_freezone_video_clone_async(
        envelope, _project_context(tmp_path)
    )

    assert result["job_id"] == "clone_job"
    assert result["shot_count"] == 3
    assert result["reference_mode"] == "frames"
    assert result["audio_mode"] == "generate"
    assert result["model"] == "DC-vision"
    assert [item["video_prompt"] for item in result["plan"]] == ["P1", "P2", "P3"]
    assert [item["duration"] for item in result["plan"]] == [3.0, 4.0, 3.0]

    # 逐镜生成：segment 按 {job_id}_sNN 命名；frames 模式带关键帧参考图。
    assert [call["job_id"] for call in video_calls] == [
        "clone_job_s01",
        "clone_job_s02",
        "clone_job_s03",
    ]
    assert all(call["gen_mode"] == "all_reference" for call in video_calls)
    assert all(call["task_type"] == "freezone_video_clone" for call in video_calls)
    assert all(call["scope"] == "clone_job" for call in video_calls)
    assert all(call["generate_audio"] is True for call in video_calls)
    assert [call["duration_seconds"] for call in video_calls] == [3, 4, 3]
    assert video_calls[0]["reference_items"] == [
        {"type": "image", "path": str(frame_paths[0]), "role": "reference"}
    ]
    assert video_calls[1]["reference_items"] == [
        {"type": "image", "path": str(frame_paths[2]), "role": "reference"}
    ]
    # 第三镜无 keyframes：start_time 8.0 / duration 12.0 比例定位到第 3 帧。
    assert video_calls[2]["reference_items"] == [
        {"type": "image", "path": str(frame_paths[2]), "role": "reference"}
    ]

    # 拼接：按镜头原时长裁齐，成片落 freezone_video_clone 输出目录。
    expected_output = outputs_dir(project_dir, "freezone_video_clone") / "clone_job.mp4"
    assert len(stitch_calls) == 1
    assert stitch_calls[0]["shot_durations"] == [3.0, 4.0, 3.0]
    assert stitch_calls[0]["audio_mode"] == "generate"
    assert stitch_calls[0]["source_video_path"] == video_path
    assert stitch_calls[0]["output_path"] == expected_output
    assert result["output_path"] == str(expected_output)
    assert result["output_url"] == "/static/freezone/_outputs/freezone_video_clone/clone_job.mp4"
    assert result["segment_urls"] == [
        f"/static/freezone_video_gen/clone_job_s{index:02d}.mp4" for index in (1, 2, 3)
    ]

    # 归档：来源素材名作条目名（stem）。
    assert len(archived) == 1
    assert archived[0]["media"] == "video"
    assert archived[0]["task_type"] == "freezone_video_clone"
    assert archived[0]["url"] == result["output_url"]
    assert archived[0]["stem"] == "ref"

    # [aipdd fork delta] 逐镜规划模型的显式选型原样透传到文本 leaf。
    assert text_models == ["DC-llm-custom"]

    # [aipdd fork delta] 模型生成音 → 先转写参考视频人声为台词（单一调用，在规划之前）。
    assert len(dialogue_calls) == 1
    assert dialogue_calls[0]["job_id"] == "clone_job"
    assert Path(dialogue_calls[0]["video_path"]) == video_path
    assert result["dialogue_text"] == ""

    # 进度覆盖五个阶段：抽帧 / 解读 / 规划 / 逐镜 / 拼接。
    assert len(task_manager.updates) >= 7


@pytest.mark.asyncio
async def test_video_clone_runner_text_mode_probes_duration_and_skips_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    frame_paths = [project_dir / "scene_01.png"]
    frame_paths[0].write_bytes(b"png")

    seen: list[dict] = []
    video_calls: list[dict] = []

    async def fake_extract_frames(
        *, project_dir, job_id, video_path, max_frames, scene_threshold
    ):
        return list(frame_paths)

    async def fake_analyze_shots(
        *,
        project_dir,
        job_id,
        frame_paths,
        analysis_mode,
        duration_sec,
        egress_context=None,
    ):
        seen.append({"duration_sec": duration_sec})
        return {
            "model": "DC-vision",
            "video_story": {"shots": [{"duration": 2.0}]},
        }

    async def fake_generate_text(*, prompt, model=None):
        return "DC-text", '{"shots": [{"shot": 1, "video_prompt": "P1"}]}'

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = project_dir / "freezone_video_gen" / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out

    async def fake_stitch(
        *, segment_paths, shot_durations, output_path, audio_mode, source_video_path
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")

    async def fake_probe_duration(_path):
        return 6.0

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    dialogue_calls = _stub_clone_dialogue(monkeypatch, text="不该被用到的台词")
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_extract_frames", fake_extract_frames
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_analyze_shots", fake_analyze_shots
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.stitch_freezone_video_clone_segments", fake_stitch
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.usage.video_billing.probe_video_duration_seconds", fake_probe_duration
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **_kwargs: None
    )

    envelope = {
        "task_type": "freezone_video_clone",
        "payload": _video_clone_payload(
            project_dir,
            video_path,
            duration_sec=0.0,
            reference_mode="text",
            audio_mode="mute",
            generate_audio=False,
        ),
    }

    result = await freezone_runner._run_freezone_video_clone_async(
        envelope, _project_context(tmp_path)
    )

    # duration_sec 缺省时 runner 自行 probe；解读拿到真实时长。
    assert seen == [{"duration_sec": 6.0}]
    # text 模式不携带参考图，gen_mode 留给后端默认。
    assert video_calls[0]["reference_items"] is None
    assert video_calls[0]["gen_mode"] is None
    assert result["reference_mode"] == "text"
    assert result["audio_mode"] == "mute"
    # mute（且 generate_audio=False）不需要模型发声，台词转写整个跳过。
    assert dialogue_calls == []


@pytest.mark.asyncio
async def test_video_clone_runner_appends_content_references_to_every_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    frame_paths = [project_dir / "scene_01.png"]
    frame_paths[0].write_bytes(b"png")
    ref_a = tmp_path / "content-a.png"
    ref_a.write_bytes(b"png")
    ref_b = tmp_path / "content-b.png"
    ref_b.write_bytes(b"png")

    video_calls: list[dict] = []
    plan_prompts: list[str] = []

    async def fake_extract_frames(
        *, project_dir, job_id, video_path, max_frames, scene_threshold
    ):
        return list(frame_paths)

    async def fake_analyze_shots(
        *,
        project_dir,
        job_id,
        frame_paths,
        analysis_mode,
        duration_sec,
        egress_context=None,
    ):
        return {
            "model": "DC-vision",
            "video_story": {
                "shots": [
                    {"keyframes": [1], "duration": 3.0, "image_prompt": "one"},
                    {"keyframes": [1], "duration": 3.0, "image_prompt": "two"},
                ]
            },
        }

    async def fake_generate_text(*, prompt, model=None):
        plan_prompts.append(prompt)
        return (
            "DC-text",
            '{"shots": [{"shot": 1, "video_prompt": "P1"}, '
            '{"shot": 2, "video_prompt": "P2"}]}',
        )

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = project_dir / "freezone_video_gen" / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out

    async def fake_stitch(
        *, segment_paths, shot_durations, output_path, audio_mode, source_video_path
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    _stub_clone_dialogue(monkeypatch)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_extract_frames", fake_extract_frames
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_analyze_shots", fake_analyze_shots
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.stitch_freezone_video_clone_segments", fake_stitch
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr("novelvideo.config.LOCALAPI_VIDEO_DURATION_BOUNDS", "")
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **_kwargs: None
    )

    envelope = {
        "task_type": "freezone_video_clone",
        "payload": _video_clone_payload(
            project_dir,
            video_path,
            reference_paths=[ref_a.as_posix(), ref_b.as_posix()],
            reference_priority="composition",
        ),
    }

    result = await freezone_runner._run_freezone_video_clone_async(
        envelope, _project_context(tmp_path)
    )

    # 规划 prompt 感知参考内容张数。
    assert "用户另外提供了 2 张「参考内容」图片" in plan_prompts[0]
    # 每镜 = 原帧参考 + 用户参考内容（role 区分），frames 在前、内容在后。
    assert video_calls[0]["reference_items"] == [
        {"type": "image", "path": str(frame_paths[0]), "role": "reference"},
        {"type": "image", "path": ref_a.as_posix(), "role": "参考内容"},
        {"type": "image", "path": ref_b.as_posix(), "role": "参考内容"},
    ]
    assert video_calls[1]["reference_items"] == [
        {"type": "image", "path": str(frame_paths[0]), "role": "reference"},
        {"type": "image", "path": ref_a.as_posix(), "role": "参考内容"},
        {"type": "image", "path": ref_b.as_posix(), "role": "参考内容"},
    ]
    assert all(call["gen_mode"] == "all_reference" for call in video_calls)
    # 结果回报参考内容张数。
    assert result["reference_count"] == 2
    assert result["reference_priority"] == "composition"
    assert result["subject_reference_only"] is False


@pytest.mark.asyncio
async def test_video_clone_runner_subject_priority_submits_only_content_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主体替换优先（默认档）：只提交参考内容，台词逐字进规划 prompt。

    实测两个缺陷的回归门：关键帧与参考内容同提时 MiniMax H3 只还原关键帧里的原
    主体（排序救不了）；提示词没有声音设计时成片根本没有任何人声。
    """
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")
    frame_path = project_dir / "scene_01.png"
    frame_path.write_bytes(b"png")
    ref_a = tmp_path / "content-a.png"
    ref_a.write_bytes(b"png")

    video_calls: list[dict] = []
    plan_prompts: list[str] = []

    async def fake_extract_frames(
        *, project_dir, job_id, video_path, max_frames, scene_threshold
    ):
        return [frame_path]

    async def fake_analyze_shots(
        *, project_dir, job_id, frame_paths, analysis_mode, duration_sec, egress_context=None
    ):
        return {
            "model": "DC-vision",
            "video_story": {
                "shots": [
                    {
                        "keyframes": [1],
                        "duration": 3.0,
                        "image_prompt": "a cute little blonde girl",
                        "motion_prompt": "slow push in",
                        "voice_sound": "小女孩的问句",
                    }
                ]
            },
        }

    async def fake_generate_text(*, prompt, model=None):
        plan_prompts.append(prompt)
        return "DC-text", '{"shots": [{"shot": 1, "video_prompt": "P1"}]}'

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = project_dir / "freezone_video_gen" / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out

    async def fake_stitch(
        *, segment_paths, shot_durations, output_path, audio_mode, source_video_path
    ):
        del segment_paths, shot_durations, audio_mode, source_video_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")

    dialogue_calls = _stub_clone_dialogue(
        monkeypatch, text="Mom, when will you be back from your business trip?"
    )
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_extract_frames", fake_extract_frames
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_analyze_shots", fake_analyze_shots
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.stitch_freezone_video_clone_segments", fake_stitch
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr("novelvideo.config.LOCALAPI_VIDEO_DURATION_BOUNDS", "")
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **_kwargs: None
    )

    envelope = {
        "task_type": "freezone_video_clone",
        "payload": _video_clone_payload(
            project_dir, video_path, reference_paths=[ref_a.as_posix()]
        ),
    }

    result = await freezone_runner._run_freezone_video_clone_async(
        envelope, _project_context(tmp_path)
    )

    # 台词只转写一次，结原文交给规划；产物形状里回报优先级与台词。
    assert len(dialogue_calls) == 1
    assert result["reference_priority"] == "subject"
    assert result["subject_reference_only"] is True
    assert result["dialogue_text"].startswith("Mom, when will you be back")

    prompt = plan_prompts[0]
    assert "Mom, when will you be back from your business trip?" in prompt
    assert "主体替换优先" in prompt
    # image_prompt 写死了原主体长相，这一档不能把它喂给规划器。
    assert "a cute little blonde girl" not in prompt
    assert '"motion_prompt": "slow push in"' in prompt

    # 逐镜提交：原视频关键帧不出现，只留参考内容。
    assert video_calls[0]["reference_items"] == [
        {"type": "image", "path": ref_a.as_posix(), "role": "参考内容"}
    ]
    assert video_calls[0]["gen_mode"] == "all_reference"


@pytest.mark.asyncio
async def test_video_clone_runner_fails_when_analysis_returns_no_shots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"mp4")

    async def fake_extract_frames(
        *, project_dir, job_id, video_path, max_frames, scene_threshold
    ):
        return [project_dir / "scene_01.png"]

    async def fake_analyze_shots(
        *,
        project_dir,
        job_id,
        frame_paths,
        analysis_mode,
        duration_sec,
        egress_context=None,
    ):
        return {"model": "DC-vision", "video_story": {"shots": []}}

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_extract_frames", fake_extract_frames
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_analyze_shots", fake_analyze_shots
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )

    envelope = {
        "task_type": "freezone_video_clone",
        "payload": _video_clone_payload(project_dir, video_path),
    }

    with pytest.raises(RuntimeError, match="no shots"):
        await freezone_runner._run_freezone_video_clone_async(
            envelope, _project_context(tmp_path)
        )


# ---------------------------------------------------------------
# 4) 复刻台词转写叶（jobs.transcribe_freezone_clone_dialogue）
# ---------------------------------------------------------------


_FUNASR_OPTION = {
    "id": "funasr:aipdd:asr",
    "kind": "funasr",
    "provider": "aipdd",
    "model": "asr",
    "label": "FunASR",
    "ready": True,
}
_GATEWAY_OPTION = {
    "id": "gateway:aipdd:whisper-1",
    "kind": "gateway",
    "provider": "aipdd",
    "model": "whisper-1",
    "label": "whisper-1",
    "ready": True,
}
_DOUBAO_OPTION = {
    "id": "doubao_asr:openspeech:seedasr",
    "kind": "doubao_asr",
    "provider": "volcengine",
    "model": "seedasr",
    "label": "豆包语音大模型",
    "ready": True,
}


def _raise_runtime_error(*_args, **_kwargs):
    raise RuntimeError("ASR 候选清单不可读")


def _stub_asr_options(monkeypatch: pytest.MonkeyPatch, options: list[dict]) -> None:
    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.list_douyin_asr_models", lambda: options
    )


def _stub_transcribe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "台词",
    error: Exception | None = None,
) -> list[dict]:
    calls: list[dict] = []

    async def fake_transcribe(
        candidate, *, audio_path, audio_url, job_id, egress_context
    ):
        calls.append(
            {
                "candidate": candidate,
                "audio_path": audio_path,
                "audio_url": audio_url,
                "job_id": job_id,
                "egress_context": egress_context,
            }
        )
        if error is not None:
            raise error
        return text, f"语音识别（{candidate['display']}）"

    monkeypatch.setattr("novelvideo.freezone.media_copywrite._transcribe", fake_transcribe)
    return calls


def _stub_audio_separate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    audio_path: Path | None,
    error: Exception | None = None,
) -> list[dict]:
    calls: list[dict] = []

    async def fake_separate(*, project_dir, job_id, source_path):
        calls.append({"job_id": job_id, "source_path": source_path})
        if error is not None:
            raise error
        return {"audio_path": audio_path, "mute_video_path": None}

    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_audio_separate", fake_separate
    )
    return calls


@pytest.mark.asyncio
async def test_clone_dialogue_prefers_doubao_over_gateway_and_skips_funasr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动选择：豆包语音优先，网关兵底；FunASR 只吃公网直链，不参与。"""
    from novelvideo.freezone.jobs import transcribe_freezone_clone_dialogue

    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"mp3")
    separate_calls = _stub_audio_separate(monkeypatch, audio_path=mp3)
    _stub_asr_options(
        monkeypatch,
        [_FUNASR_OPTION, _GATEWAY_OPTION, _DOUBAO_OPTION],
    )
    transcribe_calls = _stub_transcribe(monkeypatch, text="Mom, when will you be back?")

    text = await transcribe_freezone_clone_dialogue(
        project_dir=tmp_path,
        job_id="clone_job",
        video_path=tmp_path / "ref.mp4",
    )

    assert text == "Mom, when will you be back?"
    assert separate_calls[0]["job_id"] == "clone_job_asr"
    assert transcribe_calls[0]["candidate"]["kind"] == "doubao_asr"
    assert transcribe_calls[0]["audio_path"] == mp3
    # 本地音轨走 base64 / multipart，不会伪造一个公网直链出去。
    assert transcribe_calls[0]["audio_url"] == ""


@pytest.mark.asyncio
async def test_clone_dialogue_organization_task_uses_gateway_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组织任务不用直连通道（豆包/FunASR 都是平台侧凭据），只走网关 ASR。"""
    from novelvideo.freezone.jobs import transcribe_freezone_clone_dialogue

    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"mp3")
    _stub_audio_separate(monkeypatch, audio_path=mp3)
    _stub_asr_options(monkeypatch, [_DOUBAO_OPTION, _GATEWAY_OPTION])
    transcribe_calls = _stub_transcribe(monkeypatch, text="台词")
    context = SimpleNamespace(is_organization=True)

    text = await transcribe_freezone_clone_dialogue(
        project_dir=tmp_path,
        job_id="clone_job",
        video_path=tmp_path / "ref.mp4",
        egress_context=context,
    )

    assert text == "台词"
    assert transcribe_calls[0]["candidate"]["kind"] == "gateway"
    assert transcribe_calls[0]["egress_context"] is context


@pytest.mark.asyncio
async def test_clone_dialogue_falls_back_when_specified_candidate_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指定的候选不可用（FunASR 无直链）时退回自动选择，不报错。"""
    from novelvideo.freezone.jobs import transcribe_freezone_clone_dialogue

    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"mp3")
    _stub_audio_separate(monkeypatch, audio_path=mp3)
    _stub_asr_options(monkeypatch, [_FUNASR_OPTION, _GATEWAY_OPTION])
    transcribe_calls = _stub_transcribe(monkeypatch, text="台词")

    text = await transcribe_freezone_clone_dialogue(
        project_dir=tmp_path,
        job_id="clone_job",
        video_path=tmp_path / "ref.mp4",
        asr_model=_FUNASR_OPTION["id"],
    )

    assert text == "台词"
    assert transcribe_calls[0]["candidate"]["kind"] == "gateway"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio_path", "separate_error", "options", "transcribe_error"),
    [
        # 参考视频没有音轨。
        (None, None, [_DOUBAO_OPTION], None),
        # 拆音轨失败。
        (None, RuntimeError("ffmpeg not found"), [_DOUBAO_OPTION], None),
        # 没有就绪的 ASR 通道。
        ("audio.mp3", None, [], None),
        # 候选清单读取失败。
        ("audio.mp3", None, None, None),
        # 转写报错（凭证缺失 / 上游失败）。
        ("audio.mp3", None, [_DOUBAO_OPTION], RuntimeError("missing api key")),
    ],
)
async def test_clone_dialogue_is_best_effort_and_returns_empty_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audio_path: str | None,
    separate_error: Exception | None,
    options: list[dict] | None,
    transcribe_error: Exception | None,
) -> None:
    """拿不到台词只降级为空串，不抛错、不阻断复刻。"""
    from novelvideo.freezone.jobs import transcribe_freezone_clone_dialogue

    stub_path = tmp_path / audio_path if audio_path else None
    if stub_path is not None:
        stub_path.write_bytes(b"mp3")
    _stub_audio_separate(
        monkeypatch, audio_path=stub_path, error=separate_error
    )
    if options is None:
        monkeypatch.setattr(
            "novelvideo.freezone.douyin_copywrite.list_douyin_asr_models",
            _raise_runtime_error,
        )
    else:
        _stub_asr_options(monkeypatch, options)
    transcribe_calls = _stub_transcribe(monkeypatch, error=transcribe_error)

    text = await transcribe_freezone_clone_dialogue(
        project_dir=tmp_path,
        job_id="clone_job",
        video_path=tmp_path / "ref.mp4",
    )

    assert text == ""
    if audio_path is None:
        assert transcribe_calls == []