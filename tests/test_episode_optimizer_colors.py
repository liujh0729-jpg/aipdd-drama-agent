from __future__ import annotations

from novelvideo.api.routes.generation import _color_assignment_requires_full_sketch_clean
from novelvideo.generators.episode_optimizer import (
    BRIDGMAN_CHARACTER_PALETTE,
    EpisodeOptimizer,
    PROP_MARKER_PALETTE,
)
from novelvideo.generators.grid_generation import _global_prop_marker_colors


def _rgb_distance(a: str, b: str) -> float:
    ar, ag, ab = (int(a[index:index + 2], 16) for index in (1, 3, 5))
    br, bg, bb = (int(b[index:index + 2], 16) for index in (1, 3, 5))
    return ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2) ** 0.5


def test_prop_marker_palette_stays_visually_separated_from_character_palette():
    prop_hexes = [hex_code for hex_code, _name in PROP_MARKER_PALETTE]
    character_hexes = [hex_code for hex_code, _name in BRIDGMAN_CHARACTER_PALETTE]

    assert "#E65100" not in prop_hexes
    assert "#4A148C" not in prop_hexes
    assert "#6D4C41" in prop_hexes
    assert "#7B1FA2" in prop_hexes
    assert min(
        _rgb_distance(prop_hex, character_hex)
        for prop_hex in prop_hexes
        for character_hex in character_hexes
    ) >= 80
    assert min(
        _rgb_distance(left, right)
        for index, left in enumerate(prop_hexes)
        for right in prop_hexes[index + 1:]
    ) >= 55


def test_assign_sketch_colors_preserves_existing_when_identity_set_changes():
    existing = {
        "Hero_A": "#FF00FF FLUORESCENT MAGENTA",
        "Hero_Removed": "#00FFFF FLUORESCENT CYAN",
    }
    beats = [
        {"visual_description": "{{Hero_B}} enters"},
        {"visual_description": "{{Hero_A}} stays"},
    ]

    colors = EpisodeOptimizer.assign_sketch_colors(
        characters=[],
        episode_beats=beats,
        existing_colors=existing,
    )

    assert colors["Hero_A"] == "#FF00FF FLUORESCENT MAGENTA"
    assert colors["Hero_Removed"] == "#00FFFF FLUORESCENT CYAN"
    assert "Hero_B" in colors
    assert colors["Hero_B"] not in set(existing.values())


def test_prop_marker_colors_preserve_existing_when_prop_set_changes():
    beats = [
        {"visual_description": "男人抱起[[新道具]]，旁边没有旧道具。"},
    ]
    prop_menu = [
        {
            "prop_id": "旧道具",
            "is_global_asset": True,
            "marker_color": "#0D47A1 ROYAL BLUE",
        },
        {"prop_id": "新道具", "is_global_asset": True},
    ]

    colors = _global_prop_marker_colors(
        beats,
        prop_menu=prop_menu,
        assign_missing=True,
    )

    assert colors["新道具"] != "#0D47A1 ROYAL BLUE"

    beats_with_old = [{"visual_description": "[[旧道具]] 和 [[新道具]] 同时出现。"}]
    colors_with_old = _global_prop_marker_colors(
        beats_with_old,
        prop_menu=prop_menu,
        assign_missing=True,
    )
    assert colors_with_old["旧道具"] == "#0D47A1 ROYAL BLUE"


def test_incremental_color_assignment_does_not_force_full_sketch_clean():
    assert (
        _color_assignment_requires_full_sketch_clean(
            {"Hero_A": "#FF00FF FLUORESCENT MAGENTA"},
            {
                "Hero_A": "#FF00FF FLUORESCENT MAGENTA",
                "Hero_B": "#00FFFF FLUORESCENT CYAN",
            },
        )
        is False
    )


def test_first_prop_after_existing_identity_does_not_force_full_sketch_clean():
    previous = {"identity:Hero_A": "#FF00FF FLUORESCENT MAGENTA"}
    current = {
        "identity:Hero_A": "#FF00FF FLUORESCENT MAGENTA",
        "prop:账单": "#0D47A1 ROYAL BLUE",
    }

    assert _color_assignment_requires_full_sketch_clean(previous, current) is False


def test_initial_or_changed_color_assignment_forces_full_sketch_clean():
    assert (
        _color_assignment_requires_full_sketch_clean(
            {},
            {"Hero_A": "#FF00FF FLUORESCENT MAGENTA"},
        )
        is True
    )
    assert (
        _color_assignment_requires_full_sketch_clean(
            {"Hero_A": "#FF00FF FLUORESCENT MAGENTA"},
            {"Hero_A": "#00FFFF FLUORESCENT CYAN"},
        )
        is True
    )
