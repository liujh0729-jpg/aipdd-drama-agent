"""[aipdd fork delta] 通用 ComfyUI 执行器 class_type 自动绑定测试。

覆盖 ``bind_comfyui_workflow`` 纯函数：正/负提示词、多 LoadImage 顺序、seed 注入、
尺寸推导、帧数换算、输出类型判定，以及"识别不到的槽位保留工作流自带值"的兜底。
"""

from __future__ import annotations

from novelvideo.comfyui_executor import (
    bind_comfyui_workflow,
    detect_media_type_from_workflow,
)


def _single_positive_prompt_node() -> dict:
    return {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
        "9": {"class_type": "KSampler", "inputs": {"noise_seed": 1}},
        "10": {"class_type": "SaveImage", "inputs": {}},
    }


def test_single_clip_text_encode_treated_as_positive_prompt() -> None:
    bound, report = bind_comfyui_workflow(
        _single_positive_prompt_node(), prompt="a red panda", seed=42
    )
    assert bound["6"]["inputs"]["text"] == "a red panda"
    assert report.prompt_nodes == ["6"]
    assert report.negative_nodes == []


def test_seed_injected_into_any_int_seed_slot() -> None:
    wf = {
        "9": {"class_type": "KSampler", "inputs": {"noise_seed": 1}},
        "11": {"class_type": "RandomNoise", "inputs": {"seed": 2}},
    }
    bound, report = bind_comfyui_workflow(wf, seed=777)
    assert bound["9"]["inputs"]["noise_seed"] == 777
    assert bound["11"]["inputs"]["seed"] == 777
    assert set(report.seed_nodes) == {"9", "11"}
    assert report.seed == 777


def test_negative_title_marks_second_text_node() -> None:
    wf = {
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ""},
            "_meta": {"title": "正面提示词"},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ""},
            "_meta": {"title": "负面提示词"},
        },
    }
    bound, report = bind_comfyui_workflow(
        wf, prompt="sunny", negative_prompt="blurry, worst"
    )
    assert bound["6"]["inputs"]["text"] == "sunny"
    assert bound["7"]["inputs"]["text"] == "blurry, worst"
    assert report.prompt_nodes == ["6"]
    assert report.negative_nodes == ["7"]


def test_two_unmarked_text_nodes_second_becomes_negative() -> None:
    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    bound, report = bind_comfyui_workflow(wf, prompt="pos", negative_prompt="neg")
    assert bound["1"]["inputs"]["text"] == "pos"
    assert bound["2"]["inputs"]["text"] == "neg"
    assert report.prompt_nodes == ["1"]
    assert report.negative_nodes == ["2"]


def test_multiple_load_image_injected_in_order() -> None:
    wf = {
        "20": {"class_type": "LoadImage", "inputs": {"image": "default.png"}},
        "21": {"class_type": "LoadImage", "inputs": {"image": "default.png"}},
        "22": {"class_type": "LoadImageMask", "inputs": {"image": "keep.png"}},
    }
    bound, report = bind_comfyui_workflow(
        wf, uploaded_image_names=["first.png", "second.png"]
    )
    assert bound["20"]["inputs"]["image"] == "first.png"
    assert bound["21"]["inputs"]["image"] == "second.png"
    # LoadImageMask 被跳过，保留自带值。
    assert bound["22"]["inputs"]["image"] == "keep.png"
    assert report.image_nodes == ["20", "21"]


def test_latent_size_injected_only_on_empty_latent_nodes() -> None:
    wf = {
        "30": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "31": {"class_type": "UpscaleImageNode", "inputs": {"width": 100, "height": 100}},
    }
    bound, report = bind_comfyui_workflow(wf, width=1024, height=768)
    assert bound["30"]["inputs"]["width"] == 1024
    assert bound["30"]["inputs"]["height"] == 768
    # 非空潜空间节点含 width/height 也不动。
    assert bound["31"]["inputs"]["width"] == 100
    assert bound["31"]["inputs"]["height"] == 100
    assert report.size_nodes == ["30"]


def test_duration_converted_to_frames_with_fps_present() -> None:
    wf = {
        "40": {"class_type": "SomeFpsControl", "inputs": {"fps": 24}},
        "41": {"class_type": "WanImageToVideo", "inputs": {"length": 81}},
    }
    bound, report = bind_comfyui_workflow(wf, duration=5.0)
    # 5s * 24fps = 120，命中 4n 边界补 1 → 121。
    assert bound["41"]["inputs"]["length"] == 121
    assert report.frame_nodes == ["41"]


def test_duration_without_fps_keeps_workflow_frame_value() -> None:
    wf = {"41": {"class_type": "WanImageToVideo", "inputs": {"length": 81}}}
    bound, report = bind_comfyui_workflow(wf, duration=5.0)
    assert bound["41"]["inputs"]["length"] == 81
    assert report.frame_nodes == []
    assert any("fps" in warning for warning in report.warnings)


def test_unknown_nodes_are_preserved() -> None:
    wf = {
        "99": {"class_type": "MysteryCustomNode", "inputs": {"weird": "keep-me"}},
        "10": {"class_type": "SaveImage", "inputs": {}},
    }
    bound, report = bind_comfyui_workflow(wf, prompt="p", seed=1)
    assert bound["99"]["inputs"]["weird"] == "keep-me"
    assert report.detected_output() == "image"


def test_detect_media_type_from_output_nodes() -> None:
    image_wf = {"1": {"class_type": "SaveImage", "inputs": {}}}
    video_wf = {"1": {"class_type": "VHS_VideoCombine", "inputs": {}}}
    unknown_wf = {"1": {"class_type": "SomeThing", "inputs": {}}}
    assert detect_media_type_from_workflow(image_wf) == "image"
    assert detect_media_type_from_workflow(video_wf) == "video"
    assert detect_media_type_from_workflow(unknown_wf) is None
    assert detect_media_type_from_workflow({"1": {"class_type": "SaveVideo"}}) == "video"
