import pytest

from novelvideo.models import NovelCharacter


class FakeCharacterStore:
    def __init__(self, characters=None, aliases=None):
        self._characters = {character.name: character for character in characters or []}
        self._aliases = aliases or {}
        self.added = []

    def resolve_name(self, name):
        return self._aliases.get(name, name)

    def get_character(self, name):
        resolved = self.resolve_name(name)
        return self._characters.get(resolved)

    def get_all_characters(self):
        return list(self._characters.values())

    async def add_character(self, character):
        self.added.append(character)
        self._characters[character.name] = character


@pytest.mark.asyncio
async def test_promotes_missing_scene_header_characters_to_global():
    from novelvideo.services.character_promotion_service import (
        promote_scene_characters_to_global,
    )

    store = FakeCharacterStore()

    promoted = await promote_scene_characters_to_global(store, ["陆辰"])

    assert promoted == ["陆辰"]
    assert store.get_character("陆辰") is not None
    assert store.added[0].name == "陆辰"


@pytest.mark.asyncio
async def test_skips_existing_characters_and_resolved_aliases():
    from novelvideo.services.character_promotion_service import (
        promote_scene_characters_to_global,
    )

    store = FakeCharacterStore(
        characters=[NovelCharacter(name="陆辰")],
        aliases={"陆先生": "陆辰"},
    )

    promoted = await promote_scene_characters_to_global(store, ["陆辰", "陆先生"])

    assert promoted == []
    assert store.added == []


@pytest.mark.asyncio
async def test_filters_non_specific_scene_character_labels():
    from novelvideo.services.character_promotion_service import (
        promote_scene_characters_to_global,
    )

    store = FakeCharacterStore()

    promoted = await promote_scene_characters_to_global(
        store,
        ["", "无", "暂无", "众人", "路人", "群众", "陆辰"],
    )

    assert promoted == ["陆辰"]
    assert [character.name for character in store.added] == ["陆辰"]
