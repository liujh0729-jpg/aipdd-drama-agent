from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.mark.asyncio
async def test_director_control_frame_to_sketch_accepts_newapi_provider(monkeypatch, tmp_path):
    from novelvideo.director_world import control_frame_to_sketch as module

    project_dir = tmp_path / "output" / "admin" / "demo"
    state_dir = tmp_path / "state" / "admin" / "demo"
    control_dir = project_dir / "director_control_frames" / "ep001" / "beat_03"
    control_dir.mkdir(parents=True)
    Image.new("RGB", (1600, 900), "white").save(control_dir / "combined.png")

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def get_script_as_dict(self, _episode):
            return {
                "beats": [
                    {
                        "beat_number": 3,
                        "visual_description": "测试画面",
                    }
                ],
                "sketch_colors": {"测试": "#ff0000"},
                "scene_menu": [],
                "prop_menu": [],
            }

        def get_sketch_colors(self, _episode):
            return {"测试": "#ff0000"}

        def get_all_characters(self):
            return []

        async def close(self):
            pass

    class FakeGenerator:
        def __init__(self, config):
            self.provider = config["provider"]

        async def generate_grid(self, **_kwargs):
            return SimpleNamespace(success=True, error="", generation_time=0.1)

    monkeypatch.setattr(module, "SQLiteStore", FakeStore)
    monkeypatch.setattr(module, "GridGenerator", FakeGenerator)
    monkeypatch.setattr(module, "build_character_map_for_grid", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        module,
        "load_project_config_file",
        lambda *_args, **_kwargs: {
            "visual_style": "realistic",
            "sketch_image_selection": "newapi_gpt_image2",
        },
    )
    monkeypatch.setattr(
        module,
        "get_sketch_generation_config",
        lambda **_kwargs: {
            "provider": "newapi",
            "api_key": "newapi-test",
            "model": "gpt-image-2",
            "base_url": "http://newapi.test/v1",
        },
    )
    monkeypatch.setattr(
        module,
        "save_grid_and_split",
        lambda **_kwargs: {
            "grid_path": "grid.jpg",
            "added": 1,
            "skipped": 0,
            "cell_paths": [],
        },
    )

    result = await module.convert_control_frame_to_sketch(
        user="admin",
        project="demo",
        episode=1,
        beat=3,
        output_dir=project_dir,
        state_dir=state_dir,
    )

    assert result["ok"] is True
    assert result["beat"] == 3
