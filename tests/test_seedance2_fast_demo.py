import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.seedance2_fast_demo import (
    ExperimentName,
    build_experiment_request,
    build_seedance2_prompt,
    build_seedance2_prompt_atoms,
    render_seedance2_prompt_from_atoms,
    sanitize_request,
    validate_asset_paths,
)


pytestmark = pytest.mark.m09


def _beat() -> dict:
    return {
        "beat_number": 1,
        "video_prompt": "全景慢推，人物低头翻动旧书，定格在发现异常的瞬间",
        "visual_description": "{{陆辰_书店老板时期}}在潮湿地下室旧书堆中翻找。",
        "scene_description": "昏暗潮湿的地下室，旧书架，砖墙，小窗外暴雨。",
        "props_description": "羊皮笔记本：厚重封面，红色衡字图腾。",
        "narration_segment": "暴雨砸在窗外，我在地下室翻旧书。",
    }


def test_atom_prompt_renders_identity_scene_and_storyboard_reference_roles():
    atoms = build_seedance2_prompt_atoms(
        beat=_beat(),
        include_character_reference=True,
        include_scene_reference=True,
        include_storyboard_reference=True,
        include_first_frame=False,
    )

    assert [atom.atom_type for atom in atoms[:3]] == ["identity", "scene", "storyboard"]
    assert [atom.source_label for atom in atoms[:3]] == ["图片1", "图片2", "图片3"]

    prompt = render_seedance2_prompt_from_atoms(
        atoms=atoms,
        beat=_beat(),
        duration=5,
        ratio="9:16",
    )

    assert "图片1：身份原子，只用于锚定陆辰“书店老板时期”的身份造型" in prompt
    assert "图片2：场景原子，只用于地下室空间、旧书架、潮湿砖墙和冷暗光线" in prompt
    assert "图片3：分镜原子，只用于构图、人物站位、姿势、镜头角度和画面重心" in prompt
    assert "不要继承黑白线稿风格、草图线条或简化五官" in prompt


def test_seedance2_prompt_binds_reference_roles():
    prompt = build_seedance2_prompt(
        beat=_beat(),
        duration=5,
        ratio="9:16",
        include_character_reference=True,
        include_scene_reference=True,
    )

    assert "生成 5 秒 9:16 写实悬疑短剧镜头" in prompt
    assert "首帧：作为起始构图和人物当前位置" in prompt
    assert "图片1：身份原子，只用于锚定陆辰“书店老板时期”的身份造型" in prompt
    assert "图片2：场景原子，只用于地下室空间、旧书架、潮湿砖墙和冷暗光线" in prompt
    assert "不要生成分屏、拼贴、证件照或参考图展示墙" in prompt
    assert "[0-1s]" in prompt
    assert "地下室环境声、雨声、纸页摩擦声" in prompt


def test_build_atom_reference_experiment_maps_identity_scene_storyboard_images(tmp_path):
    first = tmp_path / "beat_01.png"
    character = tmp_path / "character.png"
    scene = tmp_path / "scene.png"
    storyboard = tmp_path / "storyboard.png"
    for path in (first, character, scene, storyboard):
        path.write_bytes(b"\x89PNG\r\n\x1a\n")

    request = build_experiment_request(
        experiment=ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
        beat=_beat(),
        first_frame_path=first,
        last_frame_path=None,
        character_reference_path=character,
        scene_reference_path=scene,
        storyboard_reference_path=storyboard,
        duration=5,
        resolution="720p",
        ratio="9:16",
        generate_audio=True,
    )

    params = request["params"]
    assert "image_url" not in params
    assert len(params["reference_images"]) == 3
    assert all(value.startswith("data:image/png;base64,") for value in params["reference_images"])
    assert "图片1：身份原子" in params["prompt"]
    assert "图片2：场景原子" in params["prompt"]
    assert "图片3：分镜原子" in params["prompt"]


def test_build_reference_experiment_maps_reference_images(tmp_path):
    first = tmp_path / "beat_01.png"
    character = tmp_path / "character.png"
    scene = tmp_path / "scene.png"
    for path in (first, character, scene):
        path.write_bytes(b"\x89PNG\r\n\x1a\n")

    request = build_experiment_request(
        experiment=ExperimentName.REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT,
        beat=_beat(),
        first_frame_path=first,
        last_frame_path=None,
        character_reference_path=character,
        scene_reference_path=scene,
        duration=5,
        resolution="720p",
        ratio="9:16",
        generate_audio=True,
    )

    assert request["model"] == "seedance-2.0-fast"
    params = request["params"]
    assert "image_url" not in params
    assert len(params["reference_images"]) == 2
    assert all(value.startswith("data:image/png;base64,") for value in params["reference_images"])
    assert "图片1" in params["prompt"]
    assert "图片2" in params["prompt"]
    assert "首帧：作为起始构图" not in params["prompt"]


def test_experiment_requests_do_not_mix_strict_frames_with_reference_images(tmp_path):
    first = tmp_path / "beat_01.png"
    last = tmp_path / "beat_02.png"
    character = tmp_path / "character.png"
    scene = tmp_path / "scene.png"
    second_scene = tmp_path / "second_scene.png"
    storyboard = tmp_path / "storyboard.png"
    for path in (first, last, character, scene, second_scene, storyboard):
        path.write_bytes(b"\x89PNG\r\n\x1a\n")

    for experiment in ExperimentName:
        request = build_experiment_request(
            experiment=experiment,
            beat=_beat(),
            first_frame_path=first,
            last_frame_path=last,
            character_reference_path=character,
            scene_reference_path=scene,
            second_scene_reference_path=second_scene,
            storyboard_reference_path=storyboard,
            duration=5,
            resolution="720p",
            ratio="9:16",
            generate_audio=True,
        )

        params = request["params"]
        has_strict_frame = any(
            key in params for key in ("image_url", "first_frame_image", "last_frame_image")
        )
        assert not ("reference_images" in params and has_strict_frame), experiment.value


def test_build_scene_reference_experiment_uses_reference_images_without_first_frame(tmp_path):
    first = tmp_path / "beat_01.png"
    scene = tmp_path / "scene.png"
    second_scene = tmp_path / "second_scene.png"
    for path in (first, scene, second_scene):
        path.write_bytes(b"\x89PNG\r\n\x1a\n")

    request = build_experiment_request(
        experiment=ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT,
        beat=_beat(),
        first_frame_path=first,
        last_frame_path=None,
        character_reference_path=None,
        scene_reference_path=scene,
        second_scene_reference_path=second_scene,
        duration=5,
        resolution="720p",
        ratio="9:16",
        generate_audio=True,
    )

    params = request["params"]
    assert "image_url" not in params
    assert len(params["reference_images"]) == 2
    assert params["reference_images"][0].startswith("data:image/png;base64,")
    assert params["reference_images"][1].startswith("data:image/png;base64,")
    assert "图片1：场景原子，只用于地下室空间" in params["prompt"]
    assert "图片2：场景补充原子，只用于旧木结构、石板路和潮湿陈旧质感" in params["prompt"]


def test_build_human_review_request_sets_flag(tmp_path):
    first = tmp_path / "beat_01.png"
    first.write_bytes(b"\x89PNG\r\n\x1a\n")

    request = build_experiment_request(
        experiment=ExperimentName.I2V_SEEDANCE2_PROMPT,
        beat=_beat(),
        first_frame_path=first,
        last_frame_path=None,
        character_reference_path=None,
        scene_reference_path=None,
        second_scene_reference_path=None,
        duration=5,
        resolution="720p",
        ratio="9:16",
        generate_audio=True,
        human_review=True,
    )

    assert request["params"]["human_review"] is True


def test_build_flf_experiment_uses_first_and_last_frame(tmp_path):
    first = tmp_path / "beat_01.png"
    last = tmp_path / "beat_02.png"
    first.write_bytes(b"\x89PNG\r\n\x1a\n")
    last.write_bytes(b"\x89PNG\r\n\x1a\n")

    request = build_experiment_request(
        experiment=ExperimentName.FLF_SEEDANCE2_PROMPT,
        beat=_beat(),
        first_frame_path=first,
        last_frame_path=last,
        character_reference_path=None,
        scene_reference_path=None,
        second_scene_reference_path=None,
        duration=5,
        resolution="720p",
        ratio="9:16",
        generate_audio=False,
    )

    params = request["params"]
    assert "image_url" not in params
    assert params["first_frame_image"].startswith("data:image/png;base64,")
    assert params["last_frame_image"].startswith("data:image/png;base64,")
    assert params["generate_audio"] is False


def test_sanitize_request_replaces_data_urls():
    request = {
        "model": "seedance-2.0-fast",
        "params": {
            "image_url": "data:image/png;base64,abcdef",
            "reference_images": ["data:image/png;base64,123456"],
            "prompt": "short prompt",
        },
    }

    sanitized = sanitize_request(
        request,
        source_paths={
            "image_url": Path("frame.png"),
            "reference_images[0]": Path("character.png"),
        },
    )

    assert sanitized["params"]["image_url"] == "<data-url image_url: frame.png>"
    assert sanitized["params"]["reference_images"] == [
        "<data-url reference_images[0]: character.png>"
    ]
    assert sanitized["params"]["prompt"] == "short prompt"


def test_validate_asset_paths_reports_missing_files(tmp_path):
    existing = tmp_path / "frame.png"
    missing = tmp_path / "missing.png"
    existing.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(FileNotFoundError) as exc:
        validate_asset_paths({"first_frame": existing, "last_frame": missing})

    assert "last_frame" in str(exc.value)
    assert str(missing) in str(exc.value)
