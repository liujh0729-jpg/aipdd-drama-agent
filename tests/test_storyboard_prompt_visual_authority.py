import json
import re

import pytest

from novelvideo.generators.prompt_builder import (
    GridConfig,
    PromptComponents,
    PromptContext,
    PromptMode,
    SketchModeStrategy,
    StyleConfig,
)
from novelvideo.director_world.control_frame_to_sketch import _director_augmented_beat
from novelvideo.utils.asset_resolver import ResolvedAssetRef


def _build_sketch_prompt(beats: list[dict], rows: int = 1, cols: int = 1) -> str:
    ctx = PromptContext(
        grid=GridConfig(rows=rows, cols=cols, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=beats,
        mode=PromptMode.SKETCH,
    )
    return SketchModeStrategy().build(ctx, PromptComponents())


def test_sketch_prompt_treats_visual_description_as_authoritative():
    prompt = _build_sketch_prompt(
        [
            {
                "beat_number": 1,
                "visual_description": "【俯拍】空镜，走廊尽头一盏灯闪烁",
            }
        ]
    )

    assert "VISUAL DESCRIPTION AUTHORITY" in prompt
    assert "obey that written direction exactly" in prompt
    assert "choose the best shot yourself" in prompt
    assert "【俯拍】空镜，走廊尽头一盏灯闪烁" in prompt


def test_sketch_prompt_keeps_action_blocking_lightweight_and_visual_description_first():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    prompt = _build_sketch_prompt(
        [
            {
                "beat_number": 1,
                "visual_description": f"男人抱起[[纸箱]]，{prop_tag}在胸前，转身从拉面馆门口跨步走出",
            }
        ]
    )

    assert "ACTION BLOCKING RULE" in prompt
    assert "Treat the panel visual_description as the action source of truth" in prompt
    assert "走出/离开/进入/转身/站起/坐下/抱起/搬起/推/拉/打开/跨出/跑/摔倒" in prompt
    assert "obey that framing first" in prompt
    assert "show the prop contact clearly inside the written framing" in prompt
    assert "Do not use arrows, text labels, speed lines" in prompt
    assert "MOVEMENT DIRECTION READABILITY" not in prompt
    assert "place one foot/body part already in the destination area" not in prompt


def test_sketch_blank_placeholder_is_truly_blank():
    prompt = _build_sketch_prompt(
        [{"beat_number": 1, "visual_description": "普通画面"}],
        rows=1,
        cols=2,
    )

    assert "[BLANK PLACEHOLDER]: A completely blank unused panel" in prompt
    assert 'large white "X"' not in prompt
    assert "solid gray background" not in prompt


def test_director_control_prompt_translates_staging_semantics(tmp_path):
    control_dir = tmp_path / "director_control_frames" / "ep001" / "beat_01"
    control_dir.mkdir(parents=True)
    control_frame = control_dir / "combined.png"
    control_frame.write_bytes(b"fake")
    (control_dir / "frame_meta.json").write_text(
        json.dumps(
            {
                "props": [
                    {
                        "id": "staging_horse_1",
                        "type": "prop_staging",
                        "name": "马",
                        "semantic_label": "马",
                        "shape_hint": "quadruped_mount",
                        "marker_color": "#8B4513",
                        "scale": [1.8, 1.4, 0.6],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": "院子里有人牵着马等待",
                "director_staging_items": [
                    {
                        "label": "马",
                        "shape_hint": "quadruped_mount",
                        "marker_color": "#8B4513",
                        "scale": "1.8, 1.4, 0.6",
                    }
                ],
            }
        ],
        mode=PromptMode.SKETCH,
        scene_refs={
            1: [
                ResolvedAssetRef(
                    asset_type="scene",
                    base_id="院子",
                    variant_id=None,
                    image_paths=[str(control_frame)],
                    text_description="",
                    source_level="director_image",
                )
            ]
        },
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "DIRECTOR STAGING OBJECTS" in prompt
    assert "马" in prompt
    assert "shape_hint=quadruped_mount" not in prompt
    assert "marker=#8B4513 -> draw user object: 马" in prompt
    assert "scale=1.8, 1.4, 0.6" not in prompt
    assert "draw the user's listed object" in prompt
    assert "marker color is locator only, never output color" in prompt
    assert "STAGING COLOR BAN" in prompt
    assert "MUST be black/gray line art only" in prompt
    assert "It must NOT have colored fill, colored outline, colored tint" in prompt
    assert "Only named actors and listed-panel global props can be colored" in prompt
    assert "if the label says horse, draw a horse-like rough storyboard silhouette" in prompt
    assert "not an anonymous box" in prompt


def test_director_control_prompt_keeps_movement_rule_simple(tmp_path):
    control_dir = tmp_path / "director_control_frames" / "ep001" / "beat_15"
    control_dir.mkdir(parents=True)
    control_frame = control_dir / "combined.png"
    control_frame.write_bytes(b"fake")

    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="16:9"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 15,
                "visual_description": "男人抱起纸箱，从拉面馆门口跨步走出",
            }
        ],
        mode=PromptMode.SKETCH,
        scene_refs={
            1: [
                ResolvedAssetRef(
                    asset_type="scene",
                    base_id="兰州拉面馆",
                    variant_id=None,
                    image_paths=[str(control_frame)],
                    text_description="",
                    source_level="director_image",
                )
            ]
        },
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "final pose/facing/action comes from visual_description" in prompt
    assert "smallest local position/pose adjustment required to make that action readable" in prompt
    assert "Movement endpoints" not in prompt
    assert "adjacent doorway/threshold zone" not in prompt
    assert "entering, exiting, leaving, crossing a threshold" not in prompt
    assert "Do NOT move the actor to the doorway/final destination" not in prompt
    assert "Do not move characters to story endpoints" not in prompt


def test_sketch_prompt_uses_directional_mannequins_for_facing_readability():
    prompt = _build_sketch_prompt(
        [
            {
                "beat_number": 1,
                "visual_description": "男人抱起纸箱，转身从拉面馆门口跨步走出",
            }
        ]
    )

    assert "COLOR-CODED DIRECTIONAL STORYBOARD MANNEQUIN" in prompt
    assert "FACING DIRECTION" in prompt
    assert "tiny 5-15px nose/facing tick" in prompt
    assert "short shoulder line and hip line" in prompt
    assert "one spine center line ONLY for back-to-camera" in prompt
    assert "to show front/back direction" in prompt
    assert "ALL human figures are simple DIRECTIONAL STORYBOARD MANNEQUINS" in prompt
    assert "All humans must be drawn as STICK FIGURES only" not in prompt


def test_named_prop_marker_color_still_drives_sketch_prompt():
    prop_tag = PromptComponents.compute_prop_tag("马")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": f"院子里有人牵着[[马]]等待，{prop_tag}位于人物右侧",
            }
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={"马": "#8B4513 BROWN"},
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "COLOR-CODED GLOBAL PROPS" in prompt
    assert prop_tag in prompt
    assert "#8B4513 BROWN" in prompt
    assert "EXACT COLOR LOCK" in prompt
    assert "COLOR-CODED GLOBAL PROPS (only props from the global prop table can be colored)" not in prompt
    assert f"{prop_tag} (#8B4513 BROWN ONLY; no other colors)" in prompt
    assert "ZERO internal detail" in prompt


def test_named_prop_color_lock_overrides_material_color():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": f"男人抱起[[纸箱]]，{prop_tag}在胸前",
            }
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={"纸箱": "#0D47A1 ROYAL BLUE"},
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "COLOR-CODED GLOBAL PROPS" in prompt
    assert f'{prop_tag} — **ROYAL BLUE (#0D47A1)** global prop marker for "纸箱"' in prompt
    assert "every visible part of this global prop marker MUST use #0D47A1 ROYAL BLUE only" in prompt
    assert "Do not use any real material color" in prompt
    assert "Use the listed marker color only; do not render the object's normal material color." in prompt
    assert f"{prop_tag} (#0D47A1 ROYAL BLUE ONLY; no other colors)" in prompt
    assert f"纸箱 {prop_tag} (#0D47A1 ROYAL BLUE ONLY; no other colors)" in prompt
    assert "non-assigned hue or material-color override" in prompt
    assert "cardboard-brown" not in prompt
    assert "red/orange" not in prompt
    assert not re.search(r"\b(red|orange|brown|pink|yellow)\b", prompt, flags=re.IGNORECASE)


def test_global_prop_marker_color_is_limited_to_referenced_panels():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=2, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": "桌上有普通纸巾盒，角色正在吃饭",
            },
            {
                "beat_number": 2,
                "visual_description": f"男人抱起[[纸箱]]，{prop_tag}在胸前",
            },
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={"纸箱": "#0D47A1 ROYAL BLUE"},
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert f"PANEL SCOPE: color this prop ONLY in Panel(s): 2" in prompt
    assert (
        "Do NOT propagate a global prop color to similar-looking untagged objects "
        "in the same panel or any other panel"
        in prompt
    )
    assert "visually similar objects such as tissue boxes" in prompt
    assert "Similar boxes/tissue boxes/packages in the same panel or any other panel are NOT color-coded" in prompt
    assert (
        f"{prop_tag} #0D47A1 ROYAL BLUE must appear colored ONLY in panels: 2"
        in prompt
    )
    assert (
        "same/similar untagged objects in the same panel or every other panel must remain black/gray line art"
        in prompt
    )


def test_global_prop_marker_color_does_not_apply_to_same_panel_similar_props():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": f"桌上有普通纸巾盒，男人抱起[[纸箱]]，{prop_tag}在胸前",
            },
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={"纸箱": "#0D47A1 ROYAL BLUE"},
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "Only the exact tagged prop instance gets this color" in prompt
    assert "same panel or any other panel" in prompt
    assert "Similar boxes/tissue boxes/packages in the same panel" in prompt
    assert "explicitly tagged as this global prop" in prompt
    assert (
        f"{prop_tag} #0D47A1 ROYAL BLUE must appear colored ONLY in panels: 1"
        in prompt
    )
    assert (
        "same/similar untagged objects in the same panel or every other panel must remain black/gray line art"
        in prompt
    )


def test_local_episode_prop_is_not_color_coded():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": f"男人抱起[[纸箱]]，{prop_tag}在胸前",
            }
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={},
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "COLOR-CODED GLOBAL PROPS:" not in prompt
    assert "EXACT COLOR LOCK" not in prompt
    assert "LOCAL / EPISODE PROPS" in prompt
    assert f'LOCAL / EPISODE PROP "纸箱"' in prompt
    assert "No color fill" in prompt
    assert f"{prop_tag} (" not in prompt


def test_batch_sketch_does_not_attach_prop_identity_refs():
    prop_tag = PromptComponents.compute_prop_tag("纸箱")
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": f"男人抱起[[纸箱]]，{prop_tag}在胸前",
            }
        ],
        mode=PromptMode.SKETCH,
        prop_marker_colors={"纸箱": "#C28A3D CARDBOARD"},
        prop_asset_refs={
            1: [
                ResolvedAssetRef(
                    asset_type="prop",
                    base_id="纸箱",
                    variant_id=None,
                    image_paths=["/tmp/prop_cardboard.png"],
                    text_description="",
                    source_level="base_image",
                )
            ]
        },
    )

    plan = PromptComponents.build_reference_image_plan(ctx, [])
    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert not any(entry.get("kind") == "prop" for entry in plan)
    assert 'Prop "纸箱" prop identity reference' not in prompt
    assert "COLOR-CODED GLOBAL PROPS" in prompt
    assert "#C28A3D CARDBOARD" in prompt
    assert "ZERO internal detail" in prompt


def test_batch_sketch_scene_refs_force_line_art_background():
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {
                "beat_number": 1,
                "visual_description": "角色站在兰州拉面馆门口",
            }
        ],
        mode=PromptMode.SKETCH,
        scene_refs={
            1: [
                ResolvedAssetRef(
                    asset_type="scene",
                    base_id="兰州拉面馆",
                    variant_id=None,
                    image_paths=["/tmp/master.png"],
                    text_description="",
                    source_level="base_image",
                ),
                ResolvedAssetRef(
                    asset_type="scene",
                    base_id="兰州拉面馆",
                    variant_id="reverse_master",
                    image_paths=["/tmp/reverse_master.png"],
                    text_description="",
                    source_level="scene_reverse_master",
                ),
            ]
        },
    )

    prompt = SketchModeStrategy().build(ctx, PromptComponents())

    assert "master scene reference for sketch mode" in prompt
    assert "reverse/back-facing scene reference for sketch mode" in prompt
    assert "SCENE PAIR RULE" in prompt
    assert "complementary views of the SAME physical location" in prompt
    assert "not two different locations" in prompt
    assert "not two style options" in prompt
    assert "not two candidate backgrounds" in prompt
    assert "one coherent mental map of the room" in prompt
    assert "visual_description controls shot angle, framing, and blocking" in prompt
    assert "draw a simple black/gray background line-art version of that scene" in prompt
    assert "do NOT leave the background blank" in prompt
    assert "Do NOT copy realistic lighting, colors, texture" in prompt
    assert "Color palette / tonal mood" not in prompt


def test_batch_scene_refs_are_deduped_across_panels():
    master = ResolvedAssetRef(
        asset_type="scene",
        base_id="兰州拉面馆",
        variant_id=None,
        image_paths=["/tmp/master.png"],
        text_description="",
        source_level="base_image",
    )
    reverse = ResolvedAssetRef(
        asset_type="scene",
        base_id="兰州拉面馆",
        variant_id="reverse_master",
        image_paths=["/tmp/reverse_master.png"],
        text_description="",
        source_level="scene_reverse_master",
    )
    ctx = PromptContext(
        grid=GridConfig(rows=1, cols=2, aspect_ratio="4:3"),
        characters={},
        style=StyleConfig(style_keywords="test", avoid_keywords=""),
        beats=[
            {"beat_number": 1, "visual_description": "角色站在门口"},
            {"beat_number": 2, "visual_description": "角色走进店内"},
        ],
        mode=PromptMode.SKETCH,
        scene_refs={1: [master, reverse], 2: [master, reverse]},
    )

    plan = PromptComponents.build_reference_image_plan(ctx, [])
    scene_entries = [entry for entry in plan if entry.get("kind") == "scene"]

    assert len(scene_entries) == 2
    assert scene_entries[0]["panels"] == [1, 2]
    assert scene_entries[1]["panels"] == [1, 2]


@pytest.mark.asyncio
async def test_prepare_batch_request_sketch_attaches_scene_refs(tmp_path):
    pytest.importorskip("google.genai")
    from PIL import Image

    from novelvideo.generators.grid_generation import GridGenerator

    project_dir = tmp_path / "output" / "admin" / "demo"
    scene_dir = project_dir / "assets" / "scenes" / "兰州拉面馆"
    scene_dir.mkdir(parents=True)
    for name, color in {"master.png": "white", "reverse_master.png": "gray"}.items():
        Image.new("RGB", (16, 16), color=color).save(scene_dir / name)

    generator = GridGenerator(
        config={
            "provider": "openrouter",
            "api_key": "test",
            "model": "test-model",
            "rows": 1,
            "cols": 1,
            "total_panels": 1,
        }
    )

    req = await generator.prepare_batch_request(
        beats=[
            {
                "beat_number": 1,
                "scene_ref": {"scene_id": "兰州拉面馆"},
                "visual_description": "角色站在拉面馆门口",
            }
        ],
        scene_menu=[],
        prop_menu=[],
        sketch=True,
        output_path=str(project_dir / "grids" / "ep001" / "1x1" / "grid_01.png"),
        rows=1,
        cols=1,
    )

    # Prompt + master scene only. Batch/default sketch mode does not attach reverse.
    assert len(req["contents"]) == 2
    assert all(hasattr(part, "inline_data") for part in req["contents"][1:])


def test_director_augmented_beat_extracts_staging_items(tmp_path):
    beat = _director_augmented_beat(
        beat_payload={"beat_number": 1, "visual_description": "原始描述"},
        project_dir=tmp_path,
        episode=1,
        beat=1,
        frame_meta={
            "props": [
                {
                    "id": "staging_horse_1",
                    "type": "prop_staging",
                    "semantic_label": "马",
                    "shape_hint": "quadruped_mount",
                    "marker_color": "#8B4513",
                    "scale": [1.8, 1.4, 0.6],
                },
                {
                    "id": "ordinary_cup",
                    "type": "prop_hero",
                    "semantic_label": "杯子",
                    "shape_hint": "box",
                },
            ]
        },
    )

    assert beat["director_staging_items"] == [
        {
            "label": "马",
            "shape_hint": "quadruped_mount",
            "marker_color": "#8B4513",
            "attached_to": "",
            "scale": "1.8, 1.4, 0.6",
        }
    ]
