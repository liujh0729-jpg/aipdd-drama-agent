"""Contract tests for the task payload projection protocol.

T1 (coverage): every registered project task type either declares no
projection requirement at all, or ``build_projection`` produces every field
that ``PROJECTION_REQUIREMENTS`` declares for it.  Adding a requirement and
forgetting to send it turns this test red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class _FakeCharacter:
    def __init__(self, name: str, *, is_main: bool = False) -> None:
        self.name = name
        self.is_main = is_main
        self.identities: list[Any] = []

    def model_dump(self) -> dict[str, Any]:
        return {"name": self.name, "is_main": self.is_main, "identities_json": "[]"}


class _FakeScene:
    def __init__(self, name: str) -> None:
        self.name = name

    def model_dump(self) -> dict[str, Any]:
        return {"name": self.name, "aliases": []}


class _FakeStore:
    """Minimal stand-in exposing only the reads the projection performs."""

    def __init__(self) -> None:
        self.state_dir = "/state/u/p"
        self.characters = [_FakeCharacter("阿茶", is_main=True)]
        self.scenes = [_FakeScene("皇宫·大殿")]

    async def get_script_as_dict(self, episode_number: int) -> dict[str, Any]:
        return {
            "beats": [{"beat_number": 1, "scene_id": "皇宫·大殿"}],
            "sketch_colors": {"阿茶": "#ff0000"},
            "scene_menu": ["皇宫·大殿"],
            "prop_menu": ["折扇"],
        }

    def get_sketch_colors(self, episode_number: int) -> dict[str, str]:
        return {"阿茶": "#ff0000"}

    def get_all_characters(self) -> list[_FakeCharacter]:
        return list(self.characters)

    async def list_characters(self) -> list[_FakeCharacter]:
        return list(self.characters)

    async def list_scenes(self) -> list[_FakeScene]:
        return list(self.scenes)


def _build_context() -> dict[str, Any]:
    return {
        "episode": 1,
        "username": "u",
        "project_name": "p",
        "voice_ref": {"scope": "character_default", "character_name": "阿茶"},
    }


@pytest.fixture
def stub_project_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the STATE-file reads out of the contract tests.

    The projection reads project_config.json through the existing helpers; what
    those return is their own tests' business, not this contract's.
    """
    from novelvideo import project_config

    monkeypatch.setattr(
        project_config,
        "load_project_config_file",
        lambda username, project: {
            "visual_style": "chinese_period_drama",
            "sketch_image_selection": "default",
        },
    )
    monkeypatch.setattr(
        project_config,
        "load_effective_narration_style_for_voice_from_state_dir",
        lambda state_dir: "first_person",
    )
    monkeypatch.setattr(
        project_config,
        "load_narrator_reference_audio_from_state_dir",
        lambda state_dir: {
            "path": "assets/narrator/voice.mp3",
            "sha256": "",
            "updated_at": "",
        },
    )


def _registered_task_types() -> tuple[str, ...]:
    """Task types registered by the freezone runner module, read from its source.

    The runtime registry is process-global and also holds the mainline runners,
    so it cannot answer "which task types does this module register".  Reading
    the module source keeps the iteration set pinned to the bridge runners and
    makes a newly registered one show up here without any test edit.
    """
    import ast

    import novelvideo.task_backend.runners.freezone as freezone_runners

    tree = ast.parse(Path(freezone_runners.__file__).read_text(encoding="utf-8"))
    task_types: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "register_project_task_runner" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            task_types.append(first.value)
    return tuple(task_types)


def test_registered_task_types_are_the_full_bridge_set() -> None:
    """Guards the iteration source itself: T1 is only meaningful if it sees them all."""
    task_types = _registered_task_types()
    # 19 → 20：视频复刻（freezone_video_clone）注册进 bridge 集。
    # 20 → 21：混剪短视频（freezone_video_mix）注册进 bridge 集。
    # 21 → 22：一句话生成短视频（freezone_video_idea）注册进 bridge 集。
    # 22 → 23：抖音文案提取（freezone_douyin_copywrite）注册进 bridge 集。
    # 23 → 24：音视频提取文案（freezone_media_copywrite，req21）注册进 bridge 集。
    # 24 → 25：视频反推剧本（freezone_video_script_reverse，[aipdd fork delta]）注册进 bridge 集。
    # 25 → 26：替换人声（freezone_voice_replace，[aipdd fork delta]）注册进 bridge 集。
    # 26 → 27：白板动画视频（freezone_whiteboard_animation，[aipdd fork delta]）注册进
    # bridge 集（配音叶与混剪同源，项目态逐字复用 _build_audio_speech_fields）。
    # 27 → 28：科普讲解视频（freezone_explainer_video，[aipdd fork delta]）注册进
    # bridge 集（改写解说词 + 分镜两次文本 leaf 与配音 leaf 全部走既有分发，
    # 需要的投影与白板动画逐字相同）。
    # 28 → 29：音乐创作（freezone_music_generate，[aipdd fork delta req46]）注册进 bridge
    # 集（MiniMax 直连叶只需要 payload 里的渠道/模型/参考素材，不额外读项目态，
    # 因此 PROJECTION_REQUIREMENTS 里不留条目 —— 投影为 None 即逐字透传）。
    # 29 → 30：视频配音（freezone_video_dubbing，[aipdd fork delta]）注册进 bridge 集
    # （与 voice_replace 同族：多一步本地音轨回填，无额外项目态读取）。
    assert len(task_types) == 30, task_types
    assert {
        "mainline_sketch_from_context",
        "mainline_frame_from_context",
        "mainline_director_control_sketch",
        "freezone_audio_speech",
    } <= set(task_types)


@pytest.mark.asyncio
async def test_build_projection_covers_requirements_for_every_task_type(
    stub_project_config: None,
) -> None:
    from novelvideo.task_backend.projection import (
        PROJECTION_REQUIREMENTS,
        build_projection,
    )

    store = _FakeStore()
    context = _build_context()

    for task_type in _registered_task_types():
        projection = await build_projection(store, context, task_type=task_type)
        required = PROJECTION_REQUIREMENTS.get(task_type)
        if required is None:
            assert projection is None, task_type
            continue
        assert projection is not None, task_type
        fields = projection["fields"]
        assert set(fields) >= set(required), (task_type, sorted(set(required) - set(fields)))


def test_every_requirement_key_belongs_to_a_registered_task_type() -> None:
    import novelvideo.task_backend.runners.freezone  # noqa: F401  (registers runners)
    from novelvideo.task_backend.projection import PROJECTION_REQUIREMENTS
    from novelvideo.task_backend.registry import registered_project_task_types

    assert set(PROJECTION_REQUIREMENTS) <= set(registered_project_task_types())


@pytest.mark.asyncio
async def test_read_projection_round_trips_what_build_projection_wrote(
    stub_project_config: None,
) -> None:
    from novelvideo.task_backend.projection import build_projection, read_projection

    projection = await build_projection(
        _FakeStore(), _build_context(), task_type="mainline_sketch_from_context"
    )
    assert projection is not None
    payload = {"episode": 1, "projection": projection}

    proj = read_projection(payload)
    assert proj is not None
    assert proj.task_type == "mainline_sketch_from_context"
    assert proj.require("scenes")


def test_read_projection_returns_none_when_not_injected() -> None:
    from novelvideo.task_backend.projection import read_projection

    assert read_projection({"episode": 1}) is None


def test_read_projection_raises_when_a_required_field_is_missing() -> None:
    from novelvideo.task_backend.projection import read_projection

    payload = {
        "projection": {
            "projection_version": 1,
            "task_type": "mainline_sketch_from_context",
            "fields": {},
        }
    }
    with pytest.raises(ValueError):
        read_projection(payload)
