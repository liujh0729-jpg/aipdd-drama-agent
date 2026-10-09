from pydantic import ValidationError

from novelvideo.api.schemas import FreezoneRelightRequest
from novelvideo.freezone.route_helpers import build_relight_prompt


def test_relight_prompt_keeps_color_hex_and_color_temperature_kelvin() -> None:
    body = FreezoneRelightRequest(
        source_url="/static/source.png",
        brightness=60,
        color_hex="#FFB877",
        color_temperature_kelvin=3200,
    )

    prompt = build_relight_prompt(body)

    assert "Key light color / overall color tone: #FFB877." in prompt
    assert "Color temperature: 3200K (warm tungsten / amber practical light)." in prompt


def test_relight_prompt_keeps_legacy_color_hex_fallback() -> None:
    body = FreezoneRelightRequest(
        source_url="/static/source.png",
        color_hex="#FFB877",
    )

    prompt = build_relight_prompt(body)

    assert "Key light color / overall color tone: #FFB877." in prompt
    assert "Color temperature:" not in prompt


def test_relight_color_temperature_kelvin_is_bounded() -> None:
    try:
        FreezoneRelightRequest(
            source_url="/static/source.png",
            color_temperature_kelvin=1300,
        )
    except ValidationError as exc:
        assert "color_temperature_kelvin" in str(exc)
    else:
        raise AssertionError("Expected color_temperature_kelvin below range to be rejected")
