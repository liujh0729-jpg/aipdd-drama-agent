from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.m09


REPO_ROOT = Path(__file__).resolve().parents[1]


def _generation_source() -> str:
    """generation 路由包的全部源码（拆包自 generation.py，文本断言口径不变）。"""

    root = REPO_ROOT / "src/novelvideo/api/routes/generation"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py")))


def test_render_plan_routes_are_restored_without_legacy_episode_video_route() -> None:
    source = _generation_source()

    assert "/videos/generate" not in source
    assert "/render/plan" in source
    assert "/render/execute" in source
    assert "RenderPlanRequest" in source
    assert "RenderPlanExecuteRequest" in source
    assert "start_render_plan_task" not in source


def test_legacy_video_generation_task_surface_is_removed() -> None:
    from novelvideo.task_identity import TASK_IDENTITY_SPECS

    assert "video_generation" not in TASK_IDENTITY_SPECS
    assert importlib.util.find_spec("novelvideo.ray_tasks") is None
