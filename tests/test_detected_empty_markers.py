from novelvideo.generators.prompt_builder import PromptComponents
from novelvideo.models import (
    NO_CHARACTER_MARKER,
    NO_PROP_MARKER,
    complete_detected_refs_from_visual_description,
    real_detected_identities,
    real_detected_props,
)


def test_real_detected_identities_filters_no_character_marker():
    assert real_detected_identities([NO_CHARACTER_MARKER]) == []
    assert real_detected_identities([NO_CHARACTER_MARKER, "陆辰_青年时期"]) == ["陆辰_青年时期"]


def test_real_detected_props_filters_no_prop_marker():
    assert real_detected_props([NO_PROP_MARKER]) == []
    assert real_detected_props([NO_PROP_MARKER, "羊皮笔记本"]) == ["羊皮笔记本"]


def test_prompt_character_collection_ignores_no_character_marker():
    result = PromptComponents._collect_char_identity_ids(
        [{"detected_identities": [NO_CHARACTER_MARKER]}],
        use_detected_identities=True,
    )

    assert result == {}


def test_complete_detected_refs_adds_markers_from_visual_description():
    identities, props = complete_detected_refs_from_visual_description(
        visual_description="{{陆辰_青年时期}}握着[[羊皮笔记本]]站在雨中。",
        detected_identities=[],
        detected_props=[],
        allowed_identity_ids={"陆辰_青年时期"},
        allowed_prop_ids={"羊皮笔记本"},
    )

    assert identities == ["陆辰_青年时期"]
    assert props == ["羊皮笔记本"]


def test_complete_detected_refs_writes_empty_markers_when_no_valid_refs():
    identities, props = complete_detected_refs_from_visual_description(
        visual_description="没有合法 marker。",
        detected_identities=[],
        detected_props=[],
        allowed_identity_ids={"陆辰_青年时期"},
        allowed_prop_ids={"羊皮笔记本"},
    )

    assert identities == [NO_CHARACTER_MARKER]
    assert props == [NO_PROP_MARKER]
