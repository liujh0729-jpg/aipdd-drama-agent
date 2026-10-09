import json

import pytest

from novelvideo.generators.grid_generation import GridGenerator
from novelvideo.generators.prompt_builder import (
    CharacterConfig,
    GridConfig,
    PromptComponents,
    PromptContext,
    PromptMode,
    StyleConfig,
)


pytestmark = pytest.mark.m09


def _render_context_with_composite_reference() -> PromptContext:
    return _render_context_with_composite_references(1)


def _render_context_with_composite_references(count: int, tmp_path=None) -> PromptContext:
    marker_colors = [
        "#ff00ff MAGENTA",
        "#00ffff CYAN",
        "#ffff00 YELLOW",
        "#00ff00 GREEN",
    ]
    characters = {}
    detected = []
    for idx in range(count):
        name = f"ProbeActor{idx + 1}"
        ref_path = (
            str(tmp_path / f"{name}.png")
            if tmp_path is not None
            else f"/tmp/probe_actor_{idx + 1}_5_view.png"
        )
        characters[name] = CharacterConfig(
            name=name,
            face_prompt="distinct face",
            appearance_details="black outfit, slim build",
            reference_path=ref_path,
            reference_mode="composite",
            sketch_color=marker_colors[idx],
        )
        detected.append(f"{name}_uploaded")

    return PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="2:3"),
        characters=characters,
        style=StyleConfig(style_name="realistic"),
        beats=[
            {
                "beat_number": 1,
                "detected_identities": detected,
                "visual_description": " ".join(f"[{name}] stands in frame." for name in characters),
            }
        ],
        mode=PromptMode.RENDER,
    )


def test_composite_reference_prompt_does_not_claim_fixed_four_panel_order() -> None:
    ctx = _render_context_with_composite_reference()
    prompt = PromptComponents.build_reference_map(
        ctx,
        list(ctx.characters),
        include_face_desc=False,
    )

    assert "multi-view character reference sheet" in prompt
    assert "Do not assume a fixed panel count or order" in prompt
    assert "do not require a face-closeup panel" in prompt
    assert "4-panel reference sheet" not in prompt
    assert "left to right" not in prompt
    assert "three-quarter view full body (45" not in prompt

    references_json = PromptComponents.build_references_json(
        ctx,
        list(ctx.characters),
        list(ctx.characters),
        include_sketch=False,
    )
    json_map = json.loads(references_json)["references"]
    ref_obj = json_map["ProbeActor1"]
    assert ref_obj["layout"] == "uploaded multi-view sheet with variable panel count/order"
    assert "4-panel" not in ref_obj["layout"]
    assert "face closeup" not in ref_obj["layout"]


def test_combined_composite_prompt_describes_full_multiview_board() -> None:
    ctx = _render_context_with_composite_references(4)

    prompt = PromptComponents.build_reference_map(
        ctx,
        list(ctx.characters),
        include_face_desc=False,
    )

    assert "Combined multi-character full-sheet reference board" in prompt
    assert "full uploaded multi-view sheet" in prompt
    assert "Do not assume a fixed panel count or order" in prompt
    assert "center panel" not in prompt
    assert "front body" not in prompt

    references_json = PromptComponents.build_references_json(
        ctx,
        list(ctx.characters),
        list(ctx.characters),
        include_sketch=False,
    )
    json_map = json.loads(references_json)["references"]
    combined_ref = json_map["COMBINED_CHARACTERS"]
    assert combined_ref["layout"] == "full-sheet board: one uploaded multi-view sheet per character"
    assert "front-body" not in combined_ref["layout"]
    assert "left to right" not in combined_ref["layout"]
    assert all(
        entry["panel"] == "full uploaded multi-view sheet" for entry in combined_ref["characters"]
    )


def test_color_identification_binds_marker_to_reference_image_slot() -> None:
    ctx = _render_context_with_composite_references(3)

    color_map = PromptComponents.build_color_identification_map(
        ctx,
        list(ctx.characters),
    )

    assert "MAGENTA sketch figure must use Image 2" in color_map
    assert "CYAN sketch figure must use Image 3" in color_map
    assert "YELLOW sketch figure must use Image 4" in color_map
    assert "Marker color is only an identity key, not clothing color" in color_map


def test_color_identification_binds_combined_board_marker_to_shared_image_slot() -> None:
    ctx = _render_context_with_composite_references(4)

    color_map = PromptComponents.build_color_identification_map(
        ctx,
        list(ctx.characters),
    )

    assert "MAGENTA sketch figure must use Image 2" in color_map
    assert "CYAN sketch figure must use Image 2" in color_map
    assert "YELLOW sketch figure must use Image 2" in color_map
    assert "GREEN sketch figure must use Image 2" in color_map
    assert "full uploaded multi-view sheet in that combined reference board" in color_map


def test_combined_composite_attachment_uses_full_sheets_not_center_crops(tmp_path) -> None:
    from PIL import Image

    sizes = [(300, 100), (420, 120), (510, 140), (630, 160)]
    ctx = _render_context_with_composite_references(4, tmp_path=tmp_path)
    for idx, char_cfg in enumerate(ctx.characters.values()):
        Image.new("RGB", sizes[idx], (idx * 40, 0, 0)).save(char_cfg.reference_path)

    generator = GridGenerator.__new__(GridGenerator)
    captured_sizes = []

    def fail_crop_center_panel(_path):
        raise AssertionError("_crop_center_panel should not be called for combined_composite")

    def fake_merge_character_panels(images, compress_quality=60):
        captured_sizes.extend(image.size for image in images)
        return "merged-full-sheet-part"

    generator._crop_center_panel = fail_crop_center_panel
    generator._merge_character_panels = fake_merge_character_panels

    contents = []
    generator._append_reference_parts_from_plan(
        contents,
        ctx,
        list(ctx.characters),
        {},
        allowed_kinds={"combined_composite"},
    )

    assert contents == ["merged-full-sheet-part"]
    assert captured_sizes == sizes
