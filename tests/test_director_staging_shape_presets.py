import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quadruped_shape_hint_registry_matches_viewer_scale():
    shape_hint = json.loads(
        (ROOT / "src/novelvideo/director_world/shape_hints/quadruped_mount.json").read_text(
            encoding="utf-8"
        )
    )

    assert shape_hint["default_scale"] == [1.4, 1.25, 2.2]
    assert shape_hint["default_attachment_points"][0]["offset"] == [0, 1.15, 0]
    assert shape_hint["default_attachment_points"][0]["facing_delta"] == 0


def test_sports_car_shape_hint_registry_is_available():
    registry = json.loads(
        (ROOT / "src/novelvideo/director_world/shape_hints/registry.json").read_text(
            encoding="utf-8"
        )
    )
    sports_car = json.loads(
        (ROOT / "src/novelvideo/director_world/shape_hints/sports_car.json").read_text(
            encoding="utf-8"
        )
    )

    assert "sports_car.json" in registry["files"]
    assert sports_car["id"] == "sports_car"
    assert sports_car["default_scale"] == [1.65, 0.65, 3.2]

