from pathlib import Path

import pytest


pytestmark = pytest.mark.m09


def _generation_source() -> str:
    """generation 路由包的全部源码（拆包自 generation.py，文本断言口径不变）。"""

    root = Path("src/novelvideo/api/routes/generation")
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py")))


def test_generation_routes_do_not_expose_dead_render_analyze_endpoint():
    source = _generation_source()

    assert "/render/analyze" not in source
    assert "analyze_render_auto_repair" not in source
    assert "RenderAutoRepairAnalyzer" not in source


def test_render_auto_repair_analyzer_code_is_removed():
    assert not Path("src/novelvideo/verification/render_auto_repair.py").exists()

    models_source = Path("src/novelvideo/verification/models.py").read_text(encoding="utf-8")
    prompts_source = Path("src/novelvideo/verification/prompts.py").read_text(encoding="utf-8")

    assert "RenderAutoRepairResult" not in models_source
    assert "RenderBeatReviewResult" not in models_source
    assert "RENDER_AUTO_REPAIR_PROMPT" not in prompts_source
    assert "RENDER_SINGLE_BEAT_REVIEW_PROMPT" not in prompts_source
