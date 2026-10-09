from __future__ import annotations

import pytest

from novelvideo.generators.prompt_builder import (
    GridConfig,
    PromptComponents,
    PromptContext,
    PromptMode,
    RenderModeStrategy,
    StyleConfig,
)
from novelvideo.utils.asset_resolver import ResolvedAssetRef


pytestmark = pytest.mark.m09


def _scene_ref(
    source_level: str,
    *,
    base_id: str = "兰州拉面馆",
    variant_id: str | None = None,
    path: str = "/tmp/scene.png",
    reference_mode: str = "prompt_only",
    time_baked: bool = False,
) -> ResolvedAssetRef:
    return ResolvedAssetRef(
        asset_type="scene",
        base_id=base_id,
        variant_id=variant_id,
        image_paths=[path],
        text_description="",
        source_level=source_level,
        reference_mode=reference_mode,
        time_baked=time_baked,
    )


def _prop_ref(prop_id: str = "纸箱") -> ResolvedAssetRef:
    return ResolvedAssetRef(
        asset_type="prop",
        base_id=prop_id,
        variant_id=None,
        image_paths=[f"/tmp/{prop_id}.png"],
        text_description="",
        source_level="base_image",
    )


def _render_context(
    *,
    scene_refs: list[ResolvedAssetRef] | None = None,
    prop_refs: list[ResolvedAssetRef] | None = None,
    prop_marker_colors: dict[str, str] | None = None,
) -> PromptContext:
    beats = [
        {
            "beat_number": 1,
            "visual_description": "面馆内三人吃面交谈。",
            "detected_identities": [],
            "detected_props": ["纸箱"] if prop_refs else [],
            "scene_ref": {"scene_id": "兰州拉面馆"},
        }
    ]
    return PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="16:9"),
        characters={},
        style=StyleConfig(style_keywords="cinematic realism", avoid_keywords=""),
        beats=beats,
        mode=PromptMode.RENDER,
        scene_refs={1: scene_refs or []},
        prop_asset_refs={1: prop_refs or []},
        prop_marker_colors=prop_marker_colors or {},
    )


def test_render_treats_pano_or_reverse_as_plain_scene_anchor() -> None:
    ctx = _render_context(
        scene_refs=[
            _scene_ref(
                "pano_cubemap_face",
                variant_id="back",
                path="/tmp/reverse_or_360_screenshot.png",
            )
        ]
    )

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "Image 1 = SKETCH TO COLORIZE" in prompt
    assert 'Image 2 = Scene "兰州拉面馆": environment reference asset' in prompt
    assert "Use it only to match that location's architecture" in prompt
    assert "SCENE ANCHOR RULE" in prompt
    assert "Scene anchors do NOT define time-of-day lighting" in prompt
    assert "Do NOT use a scene anchor as the base image" in prompt
    assert "BASE SKETCH LOCK" in prompt
    assert "Image 1 / SKETCH is the ONLY spatial source of truth" in prompt
    assert "SCENE COLOR ANCHOR" not in prompt
    assert "LINE-ART MASK LOCK" not in prompt
    assert "TRACE TEST" not in prompt
    assert "360-derived" not in prompt
    assert "pano" not in prompt.lower()
    assert "director" not in prompt.lower()
    assert "space map" not in prompt.lower()
    assert "SKETCH TO COLORIZE" in prompt


def test_render_repairs_same_angle_director_env_only_without_changing_geometry() -> None:
    ctx = _render_context(
        scene_refs=[
            _scene_ref(
                "selected_background_image",
                variant_id="director_env_only_b03",
                path="/tmp/director_control_frames/ep001/beat_03/env_only.png",
            )
        ]
    )

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "ROUGH 3DGS ENV REPAIR" in prompt
    assert "Remove Gaussian-splat noise" in prompt
    assert "floating speckles/floaters" in prompt
    assert "sketch's corrected perspective" in prompt
    assert "do not copy the plate's distortion" in prompt
    assert "does NOT override the sketch's framing" in prompt
    assert "Do NOT copy this scene image's camera" in prompt


def test_render_material_only_scene_ref_does_not_trigger_rough_3dgs_repair() -> None:
    ctx = _render_context(
        scene_refs=[
            _scene_ref(
                "selected_background_image",
                path="/tmp/ordinary_distorted_background.png",
                reference_mode="material_only",
            )
        ]
    )

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "BACKGROUND APPEARANCE REFERENCE RULE" in prompt
    assert "visual appearance reference" in prompt
    assert "material, texture, color palette" in prompt
    assert "Image 1 / SKETCH remains the only geometry source" in prompt
    assert "major props" not in prompt
    assert "stable set dressing" not in prompt
    assert "architecture style" not in prompt
    assert "perspective distortion" not in prompt
    assert "wide-angle" not in prompt
    assert "warped counters" not in prompt
    assert "ROUGH 3DGS ENV REPAIR" not in prompt
    assert "Gaussian Splatting" not in prompt
    assert "floating speckles" not in prompt


def test_render_scene_anchor_image_suppresses_environment_text_but_keeps_time() -> None:
    ref = _scene_ref("base_image", path="/tmp/master.png")
    ref.text_description = "完整环境提示词不应该在有图锚点时进入 render panel roster"
    ctx = _render_context(scene_refs=[ref])
    ctx.beats[0]["time_of_day"] = "夜"

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "完整环境提示词不应该" not in prompt
    assert "Scene: 兰州拉面馆" in prompt
    assert "SCENE ANCHOR RULE" in prompt
    assert "TIME-OF-DAY RULE" in prompt
    assert "Time of day: 夜" in prompt
    assert "Apply the panel's time-of-day lighting state" in prompt


def test_render_time_baked_scene_anchor_keeps_anchor_lighting() -> None:
    ref = _scene_ref("base_image", base_id="兰州拉面馆_夜", path="/tmp/night.png", time_baked=True)
    ctx = _render_context(scene_refs=[ref])
    ctx.beats[0]["time_of_day"] = "夜"

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "TIME-OF-DAY RULE" in prompt
    assert "time-of-day lighting is already baked into its scene anchor image" in prompt
    assert "preserve that anchor lighting and do not relight" in prompt
    assert "Apply the panel's time-of-day lighting state" not in prompt


def test_render_scene_anchor_without_time_of_day_locks_anchor_lighting() -> None:
    ref = _scene_ref("base_image", path="/tmp/master.png")
    ctx = _render_context(scene_refs=[ref])
    ctx.beats[0]["time_of_day"] = ""

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "TIME-OF-DAY RULE" in prompt
    assert "No panel time_of_day is specified" in prompt
    assert "preserve the scene anchor image's existing lighting" in prompt
    assert "do not relight" in prompt


def test_render_keeps_different_beat_background_anchors_for_same_scene() -> None:
    beat_1_master = _scene_ref("base_image", path="/tmp/master.png")
    beat_2_reverse = _scene_ref(
        "variant_image",
        variant_id="reverse",
        path="/tmp/reverse.png",
    )
    ctx = _render_context()
    ctx.grid = GridConfig(rows=1, cols=2, aspect_ratio="16:9")
    ctx.beats = [
        {
            "beat_number": 1,
            "visual_description": "面馆正面。",
            "detected_identities": [],
            "detected_props": [],
            "scene_ref": {"scene_id": "兰州拉面馆"},
        },
        {
            "beat_number": 2,
            "visual_description": "面馆背面。",
            "detected_identities": [],
            "detected_props": [],
            "scene_ref": {"scene_id": "兰州拉面馆_reverse"},
        },
    ]
    ctx.scene_refs = {1: [beat_1_master], 2: [beat_2_reverse]}

    plan = PromptComponents.build_reference_image_plan(ctx, [])
    scene_entries = [entry for entry in plan if entry.get("kind") == "scene"]

    assert len(scene_entries) == 2
    assert scene_entries[0]["ref"] is beat_1_master
    assert scene_entries[0]["panels"] == [1]
    assert scene_entries[1]["ref"] is beat_2_reverse
    assert scene_entries[1]["panels"] == [2]

    prompt = RenderModeStrategy().build(ctx, PromptComponents())
    assert "Used for Panel(s): 1." in prompt
    assert "Used for Panel(s): 2." in prompt


def test_render_keeps_prop_colorization_as_incremental_reference() -> None:
    ctx = _render_context(
        scene_refs=[_scene_ref("base_image", path="/tmp/master.png")],
        prop_refs=[_prop_ref("纸箱")],
        prop_marker_colors={"纸箱": "#00a2ff ELECTRIC AZURE"},
    )

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert 'Prop "纸箱" prop identity reference' in prompt
    assert "PROP COLORIZATION" in prompt
    assert "PROP COLOR IDENTIFICATION" in prompt
    assert "ELECTRIC AZURE (#00a2ff) tint = named prop" in prompt
    assert "#00a2ff ELECTRIC AZURE marker =" in prompt


def test_render_reference_map_keeps_sketch_as_first_image() -> None:
    ctx = _render_context(
        scene_refs=[_scene_ref("base_image", path="/tmp/master.png")],
        prop_refs=[_prop_ref("纸箱")],
        prop_marker_colors={"纸箱": "#0D47A1 ROYAL BLUE"},
    )

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "Colorize this 1×1 storyboard SKETCH (first attached image / Image 1)" in prompt
    assert "Image 1 = SKETCH TO COLORIZE" in prompt
    assert 'Image 2 = Scene "兰州拉面馆": environment reference asset' in prompt
    assert 'Image 3 = Prop "纸箱" prop identity reference' in prompt
    assert "LAST attached image" not in prompt


def test_single_beat_render_includes_visual_description_as_reference_only() -> None:
    ctx = _render_context(
        scene_refs=[_scene_ref("base_image", path="/tmp/master.png")],
        prop_refs=[_prop_ref("纸箱")],
        prop_marker_colors={"纸箱": "#0D47A1 ROYAL BLUE"},
    )
    ctx.beats[0]["visual_description"] = "杜晨抱起[[纸箱]]，转身从兰州拉面馆跨步走出。"
    ctx.beats[0]["time_of_day"] = "夜"

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "SINGLE-BEAT VISUAL DESCRIPTION REFERENCE" in prompt
    assert "Visual description: 杜晨抱起纸箱" in prompt
    assert "转身从兰州拉面馆跨步走出" in prompt
    assert "- Scene: 兰州拉面馆" in prompt
    assert "- Time of day: 夜" in prompt
    assert "Do NOT use this text to redraw, reframe, move, add, remove, or restage" in prompt
    assert "Image 1 / SKETCH" in prompt


def test_multi_panel_render_does_not_reintroduce_full_visual_descriptions() -> None:
    ctx = _render_context(scene_refs=[_scene_ref("base_image", path="/tmp/master.png")])
    ctx.grid = GridConfig(rows=1, cols=2, aspect_ratio="16:9")
    ctx.beats = [
        {
            "beat_number": 1,
            "visual_description": "第一格详细动作不应该进入多格 render。",
            "detected_identities": [],
            "detected_props": [],
            "scene_ref": {"scene_id": "兰州拉面馆"},
        },
        {
            "beat_number": 2,
            "visual_description": "第二格详细动作也不应该进入多格 render。",
            "detected_identities": [],
            "detected_props": [],
            "scene_ref": {"scene_id": "兰州拉面馆"},
        },
    ]

    prompt = RenderModeStrategy().build(ctx, PromptComponents())

    assert "SINGLE-BEAT VISUAL DESCRIPTION REFERENCE" not in prompt
    assert "第一格详细动作" not in prompt
    assert "第二格详细动作" not in prompt
