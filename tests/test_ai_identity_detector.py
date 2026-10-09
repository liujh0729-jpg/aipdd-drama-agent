from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.mark.asyncio
async def test_identity_detector_task_matches_structured_list_output(monkeypatch, tmp_path):
    from novelvideo.agents import global_video_optimizer
    from novelvideo.agents.global_video_optimizer import BeatIdentity

    image_path = tmp_path / "grid.png"
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(image_path)
    captured: dict[str, str] = {}

    class FakeAgent:
        async def run(self, items):
            captured["task"] = items[0]
            return SimpleNamespace(
                output=[BeatIdentity(beat_number=1, identities=["Hero_Main"])]
            )

    monkeypatch.setattr(
        global_video_optimizer,
        "_create_identity_detector_agent",
        lambda: FakeAgent(),
    )

    result = await global_video_optimizer.detect_identities_by_ai(
        sketch_image_paths=[str(image_path)],
        color_identity_map={"#ff0000 RED": "Hero_Main"},
        total_beats=1,
    )

    assert result == {1: ["Hero_Main"]}
    assert "JSON array" in captured["task"]
    assert "JSON object" not in captured["task"]
