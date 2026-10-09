from __future__ import annotations

from novelvideo.task_backend.runners.sketch import _scene_refs_override_from_config


def test_canvas_scene_ref_preserves_reference_mode_without_label_variant() -> None:
    refs = _scene_refs_override_from_config(
        {
            "canvas_scene_refs": [
                {
                    "beat_number": 8,
                    "image_path": "/tmp/background.png",
                    "base_id": "时光书屋",
                    "label": "背景",
                    "source_level": "selected_background_image",
                    "reference_mode": "material_only",
                }
            ]
        },
        [8],
    )

    ref = refs[1][0]

    assert ref.asset_type == "scene"
    assert ref.base_id == "时光书屋"
    assert ref.variant_id is None
    assert ref.reference_mode == "material_only"
    assert ref.source_level == "selected_background_image"
    assert ref.image_paths == ["/tmp/background.png"]


def test_canvas_scene_ref_keeps_explicit_variant_id() -> None:
    refs = _scene_refs_override_from_config(
        {
            "canvas_scene_refs": [
                {
                    "panel_index": 0,
                    "image_path": "/tmp/director.png",
                    "base_id": "时光书屋",
                    "label": "背景",
                    "variant_id": "director_env_only_b08",
                    "source_level": "selected_background_image",
                }
            ]
        },
        [8],
    )

    ref = refs[1][0]

    assert ref.variant_id == "director_env_only_b08"
    assert ref.reference_mode == "prompt_only"
