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


def test_legacy_batch_render_route_is_removed_from_generation_source() -> None:
    source = _generation_source()

    assert "/grids/batch-render" not in source
    assert "batch_generate_render" not in source
    assert "start_batch_render_task" not in source


def test_legacy_batch_render_ray_surface_is_removed() -> None:
    from novelvideo.task_identity import TASK_IDENTITY_SPECS

    assert "batch_render" not in TASK_IDENTITY_SPECS
    assert importlib.util.find_spec("novelvideo.ray_tasks") is None
