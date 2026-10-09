from novelvideo.api.schemas import FreezoneCharacterMultiViewRequest
from novelvideo.freezone.route_helpers import build_multi_view_prompt


def test_multi_view_prompt_supports_extreme_close_up() -> None:
    body = FreezoneCharacterMultiViewRequest(
        source_url="/static/source.png",
        shot_size="extreme_close_up",
    )

    prompt = build_multi_view_prompt(body)

    assert "Shot size: extreme close-up." in prompt


def test_multi_view_prompt_supports_extreme_wide() -> None:
    body = FreezoneCharacterMultiViewRequest(
        source_url="/static/source.png",
        shot_size="extreme_wide",
    )

    prompt = build_multi_view_prompt(body)

    assert "Shot size: extreme wide shot." in prompt
