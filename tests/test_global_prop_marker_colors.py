from novelvideo.generators.grid_generation import _global_prop_marker_colors
from novelvideo.models import build_prop_menu


def test_global_prop_marker_colors_use_episode_marker_color_only():
    beats = [{"visual_description": "男人抱起[[纸箱]]离开"}]
    prop_menu = [
        {
            "prop_id": "纸箱",
            "marker_color": "#0D47A1 ROYAL BLUE",
        }
    ]

    assert _global_prop_marker_colors(beats, prop_menu) == {
        "纸箱": "#0D47A1 ROYAL BLUE"
    }


def test_global_prop_marker_colors_do_not_assign_missing_by_default():
    beats = [{"visual_description": "男人抱起[[纸箱]]离开"}]
    prop_menu = [{"prop_id": "纸箱", "is_global_asset": True}]

    assert _global_prop_marker_colors(beats, prop_menu) == {}


def test_global_prop_marker_colors_assign_missing_only_for_persist_flow():
    beats = [{"visual_description": "男人抱起[[纸箱]]离开"}]
    prop_menu = [{"prop_id": "纸箱", "is_global_asset": True}]

    assigned = _global_prop_marker_colors(beats, prop_menu, assign_missing=True)

    assert set(assigned) == {"纸箱"}
    assert assigned["纸箱"].startswith("#")


def test_build_prop_menu_preserves_episode_marker_color_only():
    menu = build_prop_menu(
        prop_menu=[
            {
                "prop_id": "纸箱",
                "marker_color": "#0D47A1 ROYAL BLUE",
            }
        ]
    )

    assert menu[0].marker_color == "#0D47A1 ROYAL BLUE"
    dumped = [item.model_dump() for item in build_prop_menu(prop_menu=menu)][0]
    assert dumped["marker_color"] == "#0D47A1 ROYAL BLUE"
    assert "asset_scope" not in dumped
    assert "is_global_asset" not in dumped
    assert "preserve_marker_color" not in dumped
