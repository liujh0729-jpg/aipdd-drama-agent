"""[aipdd fork delta] 一句话生成短视频（``freezone_video_idea``）后端测试。

覆盖三层：
1. `novelvideo.freezone.idea` 纯逻辑：角色/分镜/镜头分解/机位树/参考图选择
   解析、机位拓扑序、prompt 组装与时长归一化；
2. 路由层：入队走 video 队列、图片/视频目录模型定型、音频能力降级、载荷
   透传与 4xx；
3. runner 全链：故事扩写 → 角色提取 → 三视图（含 side 失败兜底）→ 分场景
   脚本 → 分镜 → 逐镜分解（含重试）→ 机位树 → 逐帧（过渡视频/新机位/拷贝
   分支/选择器降级）→ 逐镜视频 → 场景拼接 → 全片拼接 → 归档。

runner 用例直接调用 `_run_freezone_video_idea_async`（与混剪 runner 测试同
口径），leaf 全部换成显式签名假对象，守的是「链路编排与产物形状」而不是真实
出网或 ffmpeg。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.idea import (
    IdeaCamera,
    IdeaShot,
    IdeaShotBrief,
    apply_idea_camera_tree,
    build_idea_new_camera_text,
    build_idea_scene_scripts_prompt,
    build_idea_storyboard_prompt,
    build_idea_transition_video_prompt,
    camera_topological_order,
    clamp_idea_shot_seconds,
    clamp_idea_total_seconds,
    compose_idea_frame_prompt,
    derive_idea_scene_cap,
    group_shots_into_cameras,
    parse_idea_characters,
    parse_idea_decompose,
    parse_idea_reference_selection,
    parse_idea_scene_scripts,
    parse_idea_storyboard,
    portrait_description,
)
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


# ---------------------------------------------------------------
# 1) idea.py 纯逻辑
# ---------------------------------------------------------------


def test_idea_clamp_shot_seconds_bounds() -> None:
    assert clamp_idea_shot_seconds(None) == 5.0
    assert clamp_idea_shot_seconds("7.5") == 7.5
    assert clamp_idea_shot_seconds(2) == 3.0
    assert clamp_idea_shot_seconds(30) == 10.0
    assert clamp_idea_shot_seconds("bogus") == 5.0


def test_idea_total_duration_helpers() -> None:
    """[需求6] 总时长归一（缺省/非法 → None 回退旧口径）与场景数上限推导。"""
    assert clamp_idea_total_seconds(None) is None
    assert clamp_idea_total_seconds("") is None
    assert clamp_idea_total_seconds("bogus") is None
    assert clamp_idea_total_seconds(0) is None
    assert clamp_idea_total_seconds(-3) is None
    assert clamp_idea_total_seconds(2) == 5.0
    assert clamp_idea_total_seconds(45) == 45.0
    assert clamp_idea_total_seconds(9999) == 300.0

    # 场景数上限 = ceil(总时长 / 10s)，夹取 [1, 6]。
    assert derive_idea_scene_cap(5) == 1
    assert derive_idea_scene_cap(10) == 1
    assert derive_idea_scene_cap(11) == 2
    assert derive_idea_scene_cap(30) == 3
    assert derive_idea_scene_cap(60) == 6
    assert derive_idea_scene_cap(300) == 6


def test_idea_total_duration_prompts_and_parse() -> None:
    """[需求6] 总时长口径注入分场/分镜 prompt；每镜时长解析归一（3-10s）。"""
    scene_prompt = build_idea_scene_scripts_prompt(
        story="一只流浪猫", max_scenes=3, total_duration_seconds=60
    )
    assert "about 60 seconds" in scene_prompt
    assert "at most 3 scenes" in scene_prompt
    legacy_scene_prompt = build_idea_scene_scripts_prompt(story="一只流浪猫")
    assert "Total Screen Time" not in legacy_scene_prompt

    storyboard_prompt = build_idea_storyboard_prompt(
        script="SCENE_1 ...", characters=[], target_duration_seconds=15.0
    )
    assert "about 15.0 seconds" in storyboard_prompt
    assert '"duration_seconds"' in storyboard_prompt
    legacy_storyboard_prompt = build_idea_storyboard_prompt(
        script="SCENE_1 ...", characters=[]
    )
    assert "Target Duration" not in legacy_storyboard_prompt
    assert "duration_seconds" not in legacy_storyboard_prompt

    briefs = parse_idea_storyboard(
        json.dumps(
            {
                "storyboard": [
                    {
                        "idx": 0,
                        "cam_idx": 0,
                        "visual_desc": "A",
                        "duration_seconds": 4,
                    },
                    {
                        "idx": 1,
                        "cam_idx": 0,
                        "visual_desc": "B",
                        "duration_seconds": 99,
                    },
                    {"idx": 2, "cam_idx": 1, "visual_desc": "C"},
                ]
            }
        )
    )
    assert [brief.duration_seconds for brief in briefs] == [4.0, 10.0, None]


def test_idea_parse_characters_renumbers_and_limits() -> None:
    characters = parse_idea_characters(
        json.dumps(
            {
                "characters": [
                    {
                        "idx": 0,
                        "identifier_in_scene": "Alice",
                        "is_visible": True,
                        "static_features": "young woman, black hair",
                        "dynamic_features": "white dress",
                    },
                    {
                        "idx": 1,
                        "identifier": "Bob",
                        "is_visible": False,
                        "static_features": "",
                        "dynamic_features": "",
                    },
                    {"idx": 2, "identifier_in_scene": "Crowd"},
                ]
            }
        ),
        limit=2,
    )

    assert [(c.idx, c.identifier, c.is_visible) for c in characters] == [
        (0, "Alice", True),
        (1, "Bob", False),
    ]
    # 上限截断 + 缺 identifier 的条目被丢弃。
    assert parse_idea_characters("no json", limit=2) == []
    assert parse_idea_characters('{"characters": [{"idx": 0}]}') == []


def test_idea_parse_storyboard_reenumbers_and_marks_last() -> None:
    briefs = parse_idea_storyboard(
        json.dumps(
            {
                "storyboard": [
                    {"idx": 9, "cam_idx": 0, "visual_desc": "Wide shot.", "audio_desc": "a"},
                    {"idx": 10, "cam_idx": 1, "visual_desc": "Close-up."},
                    {"idx": 11, "cam_idx": 1, "visual_desc": ""},
                    {"idx": 12, "cam_idx": 2, "visual_desc": "Cut away."},
                ]
            }
        ),
        max_shots=2,
    )

    assert [(b.idx, b.cam_idx, b.visual_desc, b.audio_desc) for b in briefs] == [
        (0, 0, "Wide shot.", "a"),
        (1, 1, "Close-up.", ""),
    ]
    # 空 visual_desc 被跳过；尾镜强制 is_last。
    assert briefs[-1].is_last is True
    assert parse_idea_storyboard("garbage", max_shots=3) == []


def test_idea_parse_decompose_requires_core_fields_and_filters_idxs() -> None:
    brief = IdeaShotBrief(
        idx=0, cam_idx=0, visual_desc="Wide.", audio_desc="speech", is_last=True
    )

    assert parse_idea_decompose("garbage", brief, character_count=3) is None
    assert (
        parse_idea_decompose(
            json.dumps({"ff_desc": "FF", "motion_desc": ""}),
            brief,
            character_count=3,
        )
        is None
    )

    shot = parse_idea_decompose(
        json.dumps(
            {
                "ff_desc": "FF text",
                "lf_desc": "",
                "motion_desc": "Static camera.",
                "variation_type": "bogus",
                "ff_vis_char_idxs": [0, 0, 7, -1, 2],
                "lf_vis_char_idxs": "bad",
            }
        ),
        brief,
        character_count=3,
    )

    assert shot is not None
    assert (shot.idx, shot.cam_idx, shot.audio_desc, shot.is_last) == (
        0,
        0,
        "speech",
        True,
    )
    # 空 lf_desc 回退 ff_desc；非法 variation 归一为 small；越界/重复索引过滤。
    assert shot.lf_desc == "FF text"
    assert shot.variation_type == "small"
    assert shot.ff_vis_char_idxs == [0, 2]
    assert shot.lf_vis_char_idxs == []
    assert shot.needs_last_frame is False


def test_idea_shot_needs_last_frame_only_medium_large() -> None:
    for variation, expected in (("small", False), ("medium", True), ("large", True)):
        shot = IdeaShot(idx=0, cam_idx=0, visual_desc="v", variation_type=variation)
        assert shot.needs_last_frame is expected


def test_idea_group_shots_and_topological_order() -> None:
    shots = [
        IdeaShot(idx=0, cam_idx=0, visual_desc="a"),
        IdeaShot(idx=1, cam_idx=1, visual_desc="b"),
        IdeaShot(idx=2, cam_idx=0, visual_desc="c"),
    ]

    cameras = group_shots_into_cameras(shots)
    assert [(c.idx, c.active_shot_idxs) for c in cameras] == [
        (0, [0, 2]),
        (1, [1]),
    ]

    # 手工挂父子关系：cam2 → cam1 → cam0，输入顺序故意打乱。
    cameras = [
        IdeaCamera(idx=2, active_shot_idxs=[4], parent_cam_idx=1, parent_shot_idx=1),
        IdeaCamera(idx=0, active_shot_idxs=[0]),
        IdeaCamera(idx=1, active_shot_idxs=[1], parent_cam_idx=0, parent_shot_idx=0),
    ]
    assert [camera.idx for camera in camera_topological_order(cameras)] == [0, 1, 2]


def test_idea_apply_camera_tree_valid_and_invalid() -> None:
    shots = [
        IdeaShot(idx=0, cam_idx=0, visual_desc="wide"),
        IdeaShot(idx=1, cam_idx=1, visual_desc="close"),
    ]
    cameras = group_shots_into_cameras(shots)

    assert apply_idea_camera_tree(
        json.dumps(
            {
                "camera_parent_items": [
                    None,
                    {
                        "parent_cam_idx": 0,
                        "parent_shot_idx": 0,
                        "is_parent_fully_covers_child": False,
                        "missing_info": "The frontal view of Alice.",
                    },
                ]
            }
        ),
        cameras,
        shots,
    )
    assert cameras[0].parent_cam_idx is None
    assert cameras[1].parent_cam_idx == 0
    assert cameras[1].parent_shot_idx == 0
    assert cameras[1].missing_info == "The frontal view of Alice."

    def _fresh_cameras() -> list[IdeaCamera]:
        return group_shots_into_cameras(shots)

    # 长度不匹配。
    assert not apply_idea_camera_tree(
        json.dumps({"camera_parent_items": [None]}), _fresh_cameras(), shots
    )
    # 自环。
    assert not apply_idea_camera_tree(
        json.dumps({"camera_parent_items": [None, {"parent_cam_idx": 1}]}),
        _fresh_cameras(),
        shots,
    )
    # 父机位不存在。
    assert not apply_idea_camera_tree(
        json.dumps({"camera_parent_items": [None, {"parent_cam_idx": 5}]}),
        _fresh_cameras(),
        shots,
    )
    # 互指成环。
    cyclic = group_shots_into_cameras(shots)
    assert not apply_idea_camera_tree(
        json.dumps(
            {
                "camera_parent_items": [
                    {"parent_cam_idx": 1, "parent_shot_idx": 1},
                    {"parent_cam_idx": 0, "parent_shot_idx": 0},
                ]
            }
        ),
        cyclic,
        shots,
    )
    # 校验失败时不落任何赋值（保持 root）。
    assert cyclic[0].parent_cam_idx is None
    assert cyclic[1].parent_cam_idx is None


def test_idea_parse_reference_selection_bounds_and_dedup() -> None:
    idxs, prompt = parse_idea_reference_selection(
        json.dumps(
            {"ref_image_indices": [2, 2, 9, -1, 0], "text_prompt": "Use Image 0."}
        ),
        total=3,
    )
    assert idxs == [2, 0]
    assert prompt == "Use Image 0."

    # 上限 8 张：多出的第 9 张被截断。
    big, _ = parse_idea_reference_selection(
        json.dumps({"ref_image_indices": list(range(10))}), total=10
    )
    assert big == list(range(8))

    assert parse_idea_reference_selection("garbage", total=3) == ([], "")


def test_idea_compose_frame_prompt_prefixes_images() -> None:
    assert compose_idea_frame_prompt(
        reference_texts=["A", "B"], text_prompt="Use Image 0."
    ) == ("Image 0: A\nImage 1: B\n\nUse Image 0.")
    assert compose_idea_frame_prompt(reference_texts=[], text_prompt="  ") == ""


def test_idea_transition_and_new_camera_text() -> None:
    prompt = build_idea_transition_video_prompt(
        parent_visual_desc="Wide shot of the alley.",
        child_visual_desc="Close-up of Alice.",
    )
    assert "The transition between the shots is a cut to." in prompt
    assert "The first shot description: Wide shot of the alley." in prompt
    assert "The second shot description: Close-up of Alice." in prompt

    text = build_idea_new_camera_text(missing_info="The frontal view of Alice.")
    assert "Wrong elements: The frontal view of Alice." in text
    assert "select this image as the main reference" in text
    assert "Wrong elements: None." in build_idea_new_camera_text(missing_info=None)


def test_idea_portrait_description_and_scene_scripts() -> None:
    assert portrait_description(identifier="Alice", view="front") == (
        "A front view portrait of Alice."
    )
    assert portrait_description(identifier="Bob", view="back") == (
        "A back view portrait of Bob."
    )

    scenes = parse_idea_scene_scripts(
        json.dumps({"script": ["Scene one.", "", "Scene two."]}), max_scenes=1
    )
    assert scenes == ["Scene one."]
    assert parse_idea_scene_scripts("garbage") == []

    # [req-08] 模型输出波动容错：前后夹带说明文字 / code fence / 别名键都能解析。
    noisy = (
        "好的，以下是分场景脚本：\n```json\n"
        '{"script": ["Scene one.", "Scene two."]}\n'
        "```\n希望有帮助！"
    )
    assert parse_idea_scene_scripts(noisy, max_scenes=5) == [
        "Scene one.",
        "Scene two.",
    ]
    assert parse_idea_scene_scripts(
        '{"scripts": ["A", "B"]}', max_scenes=5
    ) == ["A", "B"]
    assert parse_idea_scene_scripts('{"scenes": ["A"]}', max_scenes=5) == ["A"]
    # 裸数组根（无 script 包裹）同样直接可用。
    assert parse_idea_scene_scripts('["A", "B"]', max_scenes=5) == ["A", "B"]
    # 截断输出（无闭合符号）仍安全返回空，不抛异常。
    assert parse_idea_scene_scripts('{"script": ["cut off', max_scenes=5) == []


# ---------------------------------------------------------------
# 2) 路由 / 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_idea_enqueue_uses_video_queue_and_verbatim_payload(
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
    result = await freezone_routes._enqueue_or_start_freezone_video_idea(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="idea_job",
        payload={"idea": "一只流浪猫"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_idea"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_video_idea"
    assert captured["queue_kind"] == "video"
    assert captured["episode"] == 0
    assert captured["scope"] == "idea_job"
    # 载荷原样透传：入队侧只补 job_id / project_dir，不改字段。
    assert captured["payload"] == {
        "job_id": "idea_job",
        "project_dir": str(tmp_path),
        "idea": "一只流浪猫",
    }


def _patch_idea_route_resolve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ctx: SimpleNamespace,
) -> None:
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "idea_job")


@pytest.mark.asyncio
async def test_video_idea_route_freezes_catalogs_and_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_idea_route_resolve(monkeypatch, tmp_path, ctx)

    catalog_calls: list[tuple] = []
    captured: dict[str, object] = {}

    async def fake_catalog(media_type, model, model_params, **kwargs):
        catalog_calls.append((media_type, model, model_params, kwargs))
        if media_type == "image":
            return (
                {"type": "image-schema"},
                {"steps": 20},
                {
                    "catalogId": "img-catalog",
                    "providerId": "volcengine",
                    "apiModel": "doubao-seedream-4.0",
                },
            )
        return (
            {"type": "video-schema"},
            {"duration": 5},
            {"catalogId": "vid-entry", "supportsGenerateAudio": False},
        )

    async def fake_video_backend(model, *, requester_user_id):
        del model, requester_user_id
        return "newapi_seedance-2.0-fast"

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_idea",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_video_idea:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(freezone_routes, "_resolve_catalog_request", fake_catalog)
    monkeypatch.setattr(
        freezone_routes, "_resolve_catalog_video_backend", fake_video_backend
    )
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_idea", fake_enqueue
    )

    result = await freezone_routes.freezone_video_idea(
        project="59",
        body=freezone_routes.FreezoneVideoIdeaRequest(
            idea="  一只流浪猫在雨夜救下迷路的小女孩  ",
            style="Cinematic.",
            user_requirement="适合儿童",
            model_text="DC-freezone-text-writer-LLM",
            max_scenes=2,
            max_shots_per_scene=4,
            shot_duration=6.0,
            total_duration=45.0,
            portrait_mode="front",
            model_image="img-catalog",
            model_video="video-catalog",
            generate_audio=True,
            archive_to_library=True,
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_idea"

    # 图片目录定型为内部执行身份；请求参数随目录选型提交。
    assert captured["idea"] == "一只流浪猫在雨夜救下迷路的小女孩"
    assert captured["style"] == "Cinematic."
    assert captured["user_requirement"] == "适合儿童"
    # 文本步骤的用户显式选型原样透传（路由不解释模型，交由 runner/leaf 执行）。
    assert captured["model_text"] == "DC-freezone-text-writer-LLM"
    assert captured["max_scenes"] == 2
    assert captured["max_shots_per_scene"] == 4
    assert captured["shot_duration"] == 6.0
    assert captured["total_duration"] == 45.0
    assert captured["portrait_mode"] == "front"
    assert captured["image_provider"] == "volcengine"
    assert captured["image_model"] == "doubao-seedream-4.0"
    assert captured["image_model_params"] == {"steps": 20}
    assert captured["image_request_schema"] == {"type": "image-schema"}
    assert captured["backend"] == "newapi_seedance-2.0-fast"
    assert captured["model_id"] == "video-catalog"
    assert captured["catalog_id"] == "vid-entry"
    assert captured["model_params"] == {"duration": 5}
    assert captured["request_schema"] == {"type": "video-schema"}
    assert captured["aspect_ratio"] == "16:9"
    assert captured["resolution"] == "720p"
    # 目录声明不支持音频 → 静默降级。
    assert captured["generate_audio"] is False
    assert captured["archive_to_library"] is True

    assert [call[0] for call in catalog_calls] == ["image", "video"]
    assert catalog_calls[1][3].get("mode") == "firstLastFrame"


@pytest.mark.asyncio
async def test_video_idea_route_rejects_invalid_idea(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_idea_route_resolve(monkeypatch, tmp_path, ctx)

    async def boom_catalog(*_args, **_kwargs):
        raise AssertionError("invalid idea must not read catalogs")

    monkeypatch.setattr(freezone_routes, "_resolve_catalog_request", boom_catalog)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_idea(
            project="59",
            body=freezone_routes.FreezoneVideoIdeaRequest(idea="   "),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert "idea is required" in excinfo.value.detail

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_idea(
            project="59",
            body=freezone_routes.FreezoneVideoIdeaRequest(idea="x" * 2001),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert "2000" in excinfo.value.detail


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


def _idea_payload(project_dir: Path, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "job_id": "idea_job",
        "project_dir": str(project_dir),
        "idea": "一只流浪猫在雨夜救下迷路的小女孩",
        "style": "Cinematic, realistic, film-like.",
        "user_requirement": "",
        "model_text": "",
        "max_scenes": 2,
        "max_shots_per_scene": 5,
        "shot_duration": 5.0,
        "portrait_mode": "three",
        "image_provider": "volcengine",
        "image_model": "doubao-seedream-4.0",
        "image_model_params": {"steps": 20},
        "image_request_schema": {"type": "image-schema"},
        "backend": "newapi_seedance-2.0-fast",
        "model_id": "seedance-2.0-fast",
        "catalog_id": "vid-entry",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "generate_audio": True,
        "human_review": False,
        "scene_optimize": None,
        "model_params": {"duration": 5},
        "request_schema": {"type": "video-schema"},
        "archive_to_library": True,
    }
    payload.update(overrides)
    return payload


_IDEA_CHARACTERS_JSON = {
    "characters": [
        {
            "idx": 0,
            "identifier_in_scene": "Alice",
            "is_visible": True,
            "static_features": "young woman, black hair, large eyes",
            "dynamic_features": "white dress",
        },
        {
            "idx": 1,
            "identifier_in_scene": "Bob",
            "is_visible": True,
            "static_features": "young man, short hair",
            "dynamic_features": "blue coat",
        },
        {
            "idx": 2,
            "identifier_in_scene": "Narrator voice",
            "is_visible": False,
            "static_features": "",
            "dynamic_features": "",
        },
    ]
}

_IDEA_SCENE_SCRIPTS_JSON = {
    "script": [
        "SCENE_1 rainy alley at night: Alice waits; Bob arrives.",
        "SCENE_2 sunset park: they sit on a bench.",
    ]
}

_IDEA_STORYBOARD_BY_SCENE = {
    "SCENE_1": {
        "storyboard": [
            {
                "idx": 0,
                "cam_idx": 0,
                "duration_seconds": 9,
                "visual_desc": (
                    "WIDE_RAINY_ALLEY Alice stands in the rain-soaked alley, "
                    "facing the camera."
                ),
                "audio_desc": "[Rain] heavy rain",
            },
            {
                "idx": 1,
                "cam_idx": 0,
                "duration_seconds": 12,
                "visual_desc": (
                    "MEDIUM_RAINY_ALLEY Bob runs along the alley toward Alice "
                    "in the middle of the frame."
                ),
                "audio_desc": "[Bob] (shouting) Wait!",
            },
            {
                "idx": 2,
                "cam_idx": 1,
                "duration_seconds": 4,
                "visual_desc": (
                    "CLOSEUP_RAINY_ALLEY Alice turns her head toward Bob, "
                    "raindrops on her face."
                ),
                "audio_desc": "[Alice] (softly) You came.",
            },
        ]
    },
    "SCENE_2": {
        "storyboard": [
            {
                "idx": 0,
                "cam_idx": 0,
                "duration_seconds": 6,
                "visual_desc": (
                    "WIDE_SUNSET_PARK Alice and Bob sit on a park bench at sunset."
                ),
                "audio_desc": "[Birds] chirping",
            },
            {
                "idx": 1,
                "cam_idx": 1,
                "duration_seconds": 8,
                "visual_desc": (
                    "CLOSEUP_SUNSET_PARK Bob smiles at Alice in the golden light."
                ),
                "audio_desc": "[Bob] (smiling) Thank you.",
            },
        ]
    },
}

_IDEA_DECOMPOSE_BY_TOKEN = {
    "WIDE_RAINY_ALLEY": {
        "ff_desc": "FF_WIDE_RAINY Alice stands in the rain-soaked alley, facing the camera.",
        "ff_vis_char_idxs": [0],
        "lf_desc": "LF_WIDE_RAINY Alice covers her head as the rain thickens.",
        "lf_vis_char_idxs": [0, 1],
        "motion_desc": "MOTION_WIDE_RAINY Static camera. The rain intensifies.",
        "variation_type": "medium",
    },
    "MEDIUM_RAINY_ALLEY": {
        "ff_desc": "FF_MEDIUM_RAINY Bob stops in the middle of the frame, facing Alice.",
        "ff_vis_char_idxs": [1],
        "lf_desc": "LF_MEDIUM_RAINY Bob reaches out a hand toward Alice.",
        "lf_vis_char_idxs": [1],
        "motion_desc": "MOTION_MEDIUM_RAINY Tracking shot following Bob.",
        "variation_type": "small",
    },
    "CLOSEUP_RAINY_ALLEY": {
        "ff_desc": "FF_CLOSEUP_RAINY Alice turns her head toward Bob.",
        "ff_vis_char_idxs": [0],
        "lf_desc": "LF_CLOSEUP_RAINY Alice faces Bob fully.",
        "lf_vis_char_idxs": [0],
        "motion_desc": "MOTION_CLOSEUP_RAINY Static camera on Alice.",
        "variation_type": "small",
    },
    "WIDE_SUNSET_PARK": {
        "ff_desc": "FF_WIDE_SUNSET Alice and Bob sit apart on the bench.",
        "ff_vis_char_idxs": [0, 1],
        "lf_desc": "LF_WIDE_SUNSET Alice and Bob lean toward each other.",
        "lf_vis_char_idxs": [0],
        "motion_desc": "MOTION_WIDE_SUNSET Slow push-in on the bench.",
        "variation_type": "large",
    },
    "CLOSEUP_SUNSET_PARK": {
        "ff_desc": "FF_CLOSEUP_SUNSET Bob smiles at Alice in the golden light.",
        "ff_vis_char_idxs": [1],
        "lf_desc": "LF_CLOSEUP_SUNSET Bob keeps smiling.",
        "lf_vis_char_idxs": [1],
        "motion_desc": "MOTION_CLOSEUP_SUNSET Static close-up.",
        "variation_type": "small",
    },
}

_IDEA_CAMERA_TREE_BY_SCENE = {
    "RAINY": {
        "camera_parent_items": [
            None,
            {
                "parent_cam_idx": 0,
                "parent_shot_idx": 1,
                "is_parent_fully_covers_child": False,
                "missing_info": "The frontal view of Alice.",
            },
        ]
    },
    "SUNSET": {
        "camera_parent_items": [
            None,
            {
                "parent_cam_idx": 0,
                "parent_shot_idx": 0,
                "is_parent_fully_covers_child": True,
                "missing_info": None,
            },
        ]
    },
}

_IDEA_STORY_TEXT = "STORY_TEXT 一只流浪猫在雨夜救下迷路的小女孩，她们成为朋友。"


@pytest.mark.asyncio
async def test_video_idea_runner_full_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner
    from novelvideo.usage.video_duration import normalize_video_duration_for_backend

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    out_dir = outputs_dir(project_dir, "freezone_video_idea")
    work_dir = out_dir / "idea_job_work"

    text_calls: list[str] = []
    text_models: list[str | None] = []
    selector_prompts: list[str] = []
    decompose_attempts: dict[str, int] = {}
    scene_script_attempts = 0
    image_calls: list[dict] = []
    video_calls: list[dict] = []
    extract_calls: list[dict] = []
    concat_calls: list[dict] = []
    archived: list[dict] = []

    async def fake_generate_text(*, prompt, model=None):
        nonlocal scene_script_attempts
        text_calls.append(prompt)
        text_models.append(model)
        if "<IDEA>" in prompt:
            return "DC-story", _IDEA_STORY_TEXT
        if "identifier_in_scene" in prompt:
            return "DC-story", json.dumps(_IDEA_CHARACTERS_JSON)
        if "divided by scenes" in prompt:
            scene_script_attempts += 1
            if scene_script_attempts == 1:
                # 第一次返回截断 JSON（模拟模型输出波动）→ runner 重试一次。
                return "DC-story", '{"script": ["SCENE_1 rainy alley at night'
            return "DC-story", json.dumps(_IDEA_SCENE_SCRIPTS_JSON)
        if "<CAMERA_SEQ>" in prompt:
            for token, payload in _IDEA_CAMERA_TREE_BY_SCENE.items():
                if token in prompt:
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected camera tree prompt")
        if "<SCRIPT>" in prompt:
            for token, payload in _IDEA_STORYBOARD_BY_SCENE.items():
                if token in prompt:
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected storyboard prompt")
        if "<VISUAL_DESC>" in prompt:
            for token, payload in _IDEA_DECOMPOSE_BY_TOKEN.items():
                if token in prompt:
                    attempts = decompose_attempts.get(token, 0) + 1
                    decompose_attempts[token] = attempts
                    if token == "MEDIUM_RAINY_ALLEY" and attempts == 1:
                        # 第一次返回坏 JSON，runner 按 ViMax retry 口径重试一次。
                        return "DC-story", "not a json"
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected decompose prompt")
        if "<SEQ_DESC>" in prompt:
            selector_prompts.append(prompt)
            if len(selector_prompts) == 1:
                # 第一个选择器返回坏 JSON → 降级为无参考图直生成。
                return "DC-story", "not a json"
            return "DC-story", json.dumps(
                {
                    "ref_image_indices": [0],
                    "text_prompt": (
                        "Create an image following the given description. "
                        "The person should reference Image 0."
                    ),
                }
            )
        raise AssertionError(f"unexpected text prompt: {prompt[:120]}")

    async def fake_image_gen(**kwargs):
        image_calls.append(kwargs)
        if kwargs["job_id"].endswith("_c02_side"):
            # 侧面视图失败 → runner 用正面图复制兜底。
            raise RuntimeError("side view failed")
        out = (
            outputs_dir(project_dir, kwargs["output_task_type"])
            / f"{kwargs['job_id']}.png"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png:" + kwargs["job_id"].encode())
        return out

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = outputs_dir(project_dir, "freezone_video_gen") / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4:" + kwargs["job_id"].encode())
        return out

    async def fake_extract(*, transition_video_path, output_path, scene_threshold=0.3):
        extract_calls.append(
            {
                "transition": str(transition_video_path),
                "output": str(output_path),
                "scene_threshold": scene_threshold,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cam-frame")
        return output_path

    async def fake_concat(*, segment_paths, output_path):
        concat_calls.append(
            {
                "segments": [str(path) for path in segment_paths],
                "output": str(output_path),
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"concat:" + output_path.name.encode())

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_gen", fake_image_gen)
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.extract_idea_new_camera_frame", fake_extract
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.concat_freezone_video_segments", fake_concat
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: archived.append(kwargs),
    )

    envelope = {
        "task_type": "freezone_video_idea",
        "payload": _idea_payload(
            project_dir, model_text="DC-freezone-text-writer-LLM"
        ),
    }

    result = await freezone_runner._run_freezone_video_idea_async(
        envelope, _project_context(tmp_path)
    )

    # ---- 结果形状 ----
    assert result["job_id"] == "idea_job"
    assert result["idea"] == "一只流浪猫在雨夜救下迷路的小女孩"
    assert result["model"] == "DC-story"
    assert result["character_count"] == 3
    assert result["visible_character_count"] == 2
    assert result["portrait_mode"] == "three"
    assert result["scene_count"] == 2
    assert result["shot_count"] == 5
    assert result["aspect_ratio"] == "16:9"
    assert result["resolution"] == "720p"
    assert result["generate_audio"] is True
    assert result["shot_duration"] == 5.0
    # 旧口径（未给总时长）：目标缺失；规划总长 = 逐镜基线时长之和。
    assert result["total_duration"] is None
    assert result["planned_total_duration"] == round(
        5 * normalize_video_duration_for_backend("newapi_seedance-2.0-fast", 5.0), 3
    )
    assert result["characters"] == [
        {"idx": 0, "identifier": "Alice"},
        {"idx": 1, "identifier": "Bob"},
        {"idx": 2, "identifier": "Narrator voice"},
    ]

    final_path = out_dir / "idea_job.mp4"
    assert result["output_path"] == str(final_path)
    assert (
        result["output_url"]
        == "/static/freezone/_outputs/freezone_video_idea/idea_job.mp4"
    )
    assert result["scenes"] == [
        {
            "scene": 1,
            "shot_count": 3,
            "camera_count": 2,
            "video_path": str(out_dir / "idea_job_scene01.mp4"),
            "video_url": (
                "/static/freezone/_outputs/freezone_video_idea/idea_job_scene01.mp4"
            ),
        },
        {
            "scene": 2,
            "shot_count": 2,
            "camera_count": 2,
            "video_path": str(out_dir / "idea_job_scene02.mp4"),
            "video_url": (
                "/static/freezone/_outputs/freezone_video_idea/idea_job_scene02.mp4"
            ),
        },
    ]

    # ---- 文本快照落盘 ----
    assert (work_dir / "story.txt").read_text(encoding="utf-8") == _IDEA_STORY_TEXT
    assert (work_dir / "characters.json").exists()
    assert (work_dir / "script.json").exists()
    scene1_snapshot = json.loads(
        (work_dir / "scene_01.json").read_text(encoding="utf-8")
    )
    assert scene1_snapshot["cameras"][1]["parent_cam_idx"] == 0
    assert scene1_snapshot["cameras"][1]["parent_shot_idx"] == 1
    assert len(scene1_snapshot["shots"]) == 3
    assert (work_dir / "scene_02.json").exists()

    # ---- 三视图：正/侧/背，side 失败复制正面兜底 ----
    assert (out_dir / "idea_job_c01_front.png").exists()
    assert (out_dir / "idea_job_c01_side.png").exists()
    assert (out_dir / "idea_job_c01_back.png").exists()
    assert (out_dir / "idea_job_c02_front.png").exists()
    assert not (out_dir / "idea_job_c02_side.png").exists()
    assert (work_dir / "portrait_c02_side.png").read_bytes() == (
        out_dir / "idea_job_c02_front.png"
    ).read_bytes()

    by_image_job = {call["job_id"]: call for call in image_calls}
    assert by_image_job["idea_job_c01_front"]["reference_paths"] is None
    assert by_image_job["idea_job_c01_side"]["reference_paths"] == [
        str(out_dir / "idea_job_c01_front.png")
    ]
    portrait_call = by_image_job["idea_job_c01_front"]
    assert portrait_call["aspect_ratio"] == "16:9"
    assert portrait_call["image_size"] == "2K"
    assert portrait_call["provider"] == "volcengine"
    assert portrait_call["model"] == "doubao-seedream-4.0"
    assert portrait_call["model_params"] == {"steps": 20}
    assert portrait_call["request_schema"] == {"type": "image-schema"}
    assert portrait_call["output_task_type"] == "freezone_video_idea"

    # ---- 帧：选择器降级 / 有效选择 / 新机位 / 拷贝分支 ----
    assert len(image_calls) == 12  # 6 次肖像尝试 + 6 个帧
    assert len(selector_prompts) == 6
    # 第一个选择器坏 JSON → 降级为无参考图直生成（prompt 退回帧描述）。
    degraded = by_image_job["idea_job_s01_f00ff"]
    assert degraded["reference_paths"] is None
    assert degraded["prompt"] == (
        "FF_WIDE_RAINY Alice stands in the rain-soaked alley, facing the camera."
    )
    # 有效选择：参考文本前缀 + 改写提示词；参考图为 Bob 正面视图。
    picked = by_image_job["idea_job_s01_f01ff"]
    assert picked["reference_paths"] == [str(out_dir / "idea_job_c02_front.png")]
    assert picked["prompt"].startswith("Image 0: A front view portrait of Bob.")
    assert "The person should reference Image 0." in picked["prompt"]
    # 父机位完整覆盖 → 直接拷贝「新机位」参考帧，不调图片 leaf。
    assert "idea_job_s02_f01ff" not in by_image_job
    assert (out_dir / "idea_job_s02_f01ff.png").read_bytes() == b"cam-frame"

    assert len(extract_calls) == 2
    assert [Path(call["output"]).name for call in extract_calls] == [
        "idea_job_s01_f02_cam.png",
        "idea_job_s02_f01_cam.png",
    ]
    assert Path(extract_calls[0]["transition"]).name == "idea_job_s01_t02.mp4"
    assert extract_calls[0]["scene_threshold"] == 0.3

    # ---- 视频：过渡视频 + 逐镜视频（首帧 / 首尾帧） ----
    assert [call["job_id"] for call in video_calls] == [
        "idea_job_s01_t02",
        "idea_job_s01_v00",
        "idea_job_s01_v01",
        "idea_job_s01_v02",
        "idea_job_s02_t01",
        "idea_job_s02_v00",
        "idea_job_s02_v01",
    ]
    by_video_job = {call["job_id"]: call for call in video_calls}

    transition = by_video_job["idea_job_s01_t02"]
    assert transition["gen_mode"] == "first_frame"
    assert transition["last_frame_path"] is None
    # 过渡视频首帧取父镜头（shot1）的首帧。
    assert transition["reference_items"] == [
        {
            "type": "image",
            "path": str(out_dir / "idea_job_s01_f01ff.png"),
            "role": "首帧",
        }
    ]
    assert "The first shot description: MEDIUM_RAINY_ALLEY" in transition["prompt"]
    assert "The second shot description: CLOSEUP_RAINY_ALLEY" in transition["prompt"]

    shot_video = by_video_job["idea_job_s01_v00"]
    assert shot_video["gen_mode"] == "first_last_frame"
    assert shot_video["last_frame_path"] == str(out_dir / "idea_job_s01_f00lf.png")
    assert shot_video["prompt"] == (
        "MOTION_WIDE_RAINY Static camera. The rain intensifies.\n[Rain] heavy rain"
    )
    expected_duration = normalize_video_duration_for_backend(
        "newapi_seedance-2.0-fast", 5.0
    )
    assert shot_video["duration_seconds"] == expected_duration
    assert shot_video["task_type"] == "freezone_video_idea"
    assert shot_video["scope"] == "idea_job"
    assert shot_video["egress_context"] is None
    assert shot_video["aspect_ratio"] == "16:9"
    assert shot_video["resolution"] == "720p"
    assert shot_video["generate_audio"] is True
    assert shot_video["model_params"] == {"duration": 5}
    assert shot_video["request_schema"] == {"type": "video-schema"}
    # small 变体不产尾帧。
    assert by_video_job["idea_job_s01_v01"]["gen_mode"] == "first_frame"
    assert by_video_job["idea_job_s01_v01"]["last_frame_path"] is None
    # large 变体走首尾帧。
    assert by_video_job["idea_job_s02_v00"]["gen_mode"] == "first_last_frame"
    assert by_video_job["idea_job_s02_v00"]["last_frame_path"] == str(
        out_dir / "idea_job_s02_f00lf.png"
    )

    # ---- 拼接：场景内按镜头序，最终按场景序 ----
    assert [Path(call["output"]).name for call in concat_calls] == [
        "idea_job_scene01.mp4",
        "idea_job_scene02.mp4",
        "idea_job.mp4",
    ]
    assert [Path(path).name for path in concat_calls[0]["segments"]] == [
        "idea_job_s01_v00.mp4",
        "idea_job_s01_v01.mp4",
        "idea_job_s01_v02.mp4",
    ]
    assert [Path(path).name for path in concat_calls[2]["segments"]] == [
        "idea_job_scene01.mp4",
        "idea_job_scene02.mp4",
    ]
    assert final_path.read_bytes() == b"concat:idea_job.mp4"

    # ---- 归档 ----
    assert len(archived) == 1
    assert archived[0]["media"] == "video"
    assert archived[0]["task_type"] == "freezone_video_idea"
    assert archived[0]["job_id"] == "idea_job"
    assert archived[0]["url"] == result["output_url"]
    assert archived[0]["stem"] == "一只流浪猫在雨夜救下迷路的小女孩"

    # ---- 进度与重试 ----
    progresses = [update["progress"] for update in task_manager.updates]
    assert 0.03 in progresses
    assert 0.97 in progresses
    assert any("场景 1/2" in update["current_task"] for update in task_manager.updates)
    assert any("场景 2/2" in update["current_task"] for update in task_manager.updates)
    # 镜头分解重试一次（只有 MEDIUM_RAINY_ALLEY 首次返回坏 JSON）。
    assert decompose_attempts["MEDIUM_RAINY_ALLEY"] == 2
    assert all(
        attempts == 1
        for token, attempts in decompose_attempts.items()
        if token != "MEDIUM_RAINY_ALLEY"
    )
    # 场景脚本重试一次（第一次返回截断 JSON）。
    assert scene_script_attempts == 2
    # 文本 leaf 次数：故事 1 + 角色 1 + 场景脚本 2（含重试）+ 分镜 2
    # + 分解 6（含重试）+ 机位树 2 + 选择器 6。
    assert len(text_calls) == 20
    # 用户显式选型的文本模型贯穿所有文本步骤（载荷 model_text → leaf model）。
    assert text_models == ["DC-freezone-text-writer-LLM"] * len(text_calls)


@pytest.mark.asyncio
async def test_video_idea_runner_total_duration_governs_ai_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[需求6] 总时长口径：旧三参被忽略，分场/分镜上限与逐镜时长交给 AI。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner
    from novelvideo.usage.video_duration import normalize_video_duration_for_backend

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)

    text_calls: list[str] = []
    video_calls: list[dict] = []

    async def fake_generate_text(*, prompt, model=None):
        del model
        text_calls.append(prompt)
        if "<IDEA>" in prompt:
            return "DC-story", "STORY_TEXT"
        if "identifier_in_scene" in prompt:
            return "DC-story", json.dumps(_IDEA_CHARACTERS_JSON)
        if "divided by scenes" in prompt:
            return "DC-story", json.dumps(_IDEA_SCENE_SCRIPTS_JSON)
        if "<CAMERA_SEQ>" in prompt:
            for token, payload in _IDEA_CAMERA_TREE_BY_SCENE.items():
                if token in prompt:
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected camera tree prompt")
        if "<SCRIPT>" in prompt:
            for token, payload in _IDEA_STORYBOARD_BY_SCENE.items():
                if token in prompt:
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected storyboard prompt")
        if "<VISUAL_DESC>" in prompt:
            for token, payload in _IDEA_DECOMPOSE_BY_TOKEN.items():
                if token in prompt:
                    return "DC-story", json.dumps(payload)
            raise AssertionError("unexpected decompose prompt")
        if "<SEQ_DESC>" in prompt:
            return "DC-story", json.dumps(
                {
                    "ref_image_indices": [0],
                    "text_prompt": (
                        "Create an image following the given description. "
                        "The person should reference Image 0."
                    ),
                }
            )
        raise AssertionError(f"unexpected text prompt: {prompt[:120]}")

    async def fake_image_gen(**kwargs):
        out = (
            outputs_dir(project_dir, kwargs["output_task_type"])
            / f"{kwargs['job_id']}.png"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png:" + kwargs["job_id"].encode())
        return out

    async def fake_video_gen(**kwargs):
        video_calls.append(kwargs)
        out = outputs_dir(project_dir, "freezone_video_gen") / f"{kwargs['job_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4:" + kwargs["job_id"].encode())
        return out

    async def fake_extract(*, transition_video_path, output_path, scene_threshold=0.3):
        del transition_video_path, scene_threshold
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cam-frame")
        return output_path

    async def fake_concat(*, segment_paths, output_path):
        del segment_paths
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"concat")

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_gen", fake_image_gen)
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.run_trusted_freezone_video_gen", fake_video_gen
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.extract_idea_new_camera_frame", fake_extract
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.concat_freezone_video_segments", fake_concat
    )
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )

    envelope = {
        "task_type": "freezone_video_idea",
        "payload": _idea_payload(
            project_dir,
            total_duration=24.0,
            # 旧三参与总时长同时给出：总时长口径必须忽略旧值。
            max_scenes=1,
            max_shots_per_scene=2,
        ),
    }

    result = await freezone_runner._run_freezone_video_idea_async(
        envelope, _project_context(tmp_path)
    )

    # ---- 分场：场景上限由总时长推导（ceil(24/10)=3；旧参 1 被忽略） ----
    scene_prompt = next(
        prompt for prompt in text_calls if "divided by scenes" in prompt
    )
    assert "about 24 seconds" in scene_prompt
    assert "at most 3 scenes" in scene_prompt

    # ---- 分镜：每场景镜头上限放满（旧参 2 被忽略）+ 场景预算均分 ----
    storyboard_prompts = [prompt for prompt in text_calls if "<SCRIPT>" in prompt]
    assert len(storyboard_prompts) == 2
    for prompt in storyboard_prompts:
        assert "at most 10 shots" in prompt
        assert "about 12.0 seconds" in prompt

    # ---- 逐镜时长：AI 规划值（3-10 夹取）按后端归一；过渡保持基线 ----
    backend = "newapi_seedance-2.0-fast"
    by_video_job = {call["job_id"]: call for call in video_calls}
    expected_durations = {
        "idea_job_s01_v00": normalize_video_duration_for_backend(backend, 9.0),
        "idea_job_s01_v01": normalize_video_duration_for_backend(backend, 10.0),
        "idea_job_s01_v02": normalize_video_duration_for_backend(backend, 4.0),
        "idea_job_s02_v00": normalize_video_duration_for_backend(backend, 6.0),
        "idea_job_s02_v01": normalize_video_duration_for_backend(backend, 8.0),
    }
    for job_id, expected in expected_durations.items():
        assert by_video_job[job_id]["duration_seconds"] == expected
    baseline = normalize_video_duration_for_backend(backend, 5.0)
    assert by_video_job["idea_job_s01_t02"]["duration_seconds"] == baseline
    assert by_video_job["idea_job_s02_t01"]["duration_seconds"] == baseline

    # ---- 结果：目标总时长 + 规划总长（逐镜之和，不含过渡） ----
    assert result["total_duration"] == 24.0
    assert result["planned_total_duration"] == round(
        sum(expected_durations.values()), 3
    )


@pytest.mark.asyncio
async def test_video_idea_scene_scripts_double_failure_persists_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req-08] 场景脚本两次都解析失败：报错带模型名/长度，原始输出落盘。"""

    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    work_dir = outputs_dir(project_dir, "freezone_video_idea") / "idea_job_work"

    scene_script_attempts = 0

    async def fake_generate_text(*, prompt, model=None):
        nonlocal scene_script_attempts
        if "<IDEA>" in prompt:
            return "DC-story", _IDEA_STORY_TEXT
        if "identifier_in_scene" in prompt:
            return "DC-story", json.dumps(_IDEA_CHARACTERS_JSON)
        if "divided by scenes" in prompt:
            scene_script_attempts += 1
            # 两次都返回截断 JSON（无闭合符号，无法提取）。
            return "DC-story", '{"script": ["SCENE_1 rainy alley at night'
        raise AssertionError(f"unexpected text prompt: {prompt[:120]}")

    async def fake_image_gen(**kwargs):
        out = (
            outputs_dir(project_dir, kwargs["output_task_type"])
            / f"{kwargs['job_id']}.png"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png:" + kwargs["job_id"].encode())
        return out

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_gen", fake_image_gen)

    envelope = {
        "task_type": "freezone_video_idea",
        "payload": _idea_payload(project_dir, portrait_mode="front"),
    }

    with pytest.raises(RuntimeError) as excinfo:
        await freezone_runner._run_freezone_video_idea_async(
            envelope, _project_context(tmp_path)
        )

    # 重试一次后才报错。
    assert scene_script_attempts == 2
    message = str(excinfo.value)
    assert "idea scene script generation returned no scenes" in message
    assert "model=DC-story" in message
    assert "output_chars=" in message
    # 原始输出落盘（含被截断的 JSON 全文），供事后诊断。
    raw_path = work_dir / "scene_scripts_raw.txt"
    assert raw_path.read_text(encoding="utf-8") == (
        '{"script": ["SCENE_1 rainy alley at night'
    )