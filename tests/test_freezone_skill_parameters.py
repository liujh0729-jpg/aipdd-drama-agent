from __future__ import annotations

from novelvideo.api.routes.freezone import _skill_background_reference_mode


def test_skill_background_reference_mode_accepts_supported_modes() -> None:
    assert (
        _skill_background_reference_mode({"background_reference_mode": "scene_anchor"})
        == "scene_anchor"
    )
    assert (
        _skill_background_reference_mode({"background_reference_mode": "material_only"})
        == "material_only"
    )


def test_skill_background_reference_mode_defaults_to_material_only() -> None:
    assert _skill_background_reference_mode({}) == "material_only"
    assert (
        _skill_background_reference_mode({"background_reference_mode": "unsupported"})
        == "material_only"
    )
