"""Helpers for promoting screenplay scene-header characters into global assets."""

from __future__ import annotations

from typing import Any

from novelvideo.models import NovelCharacter

_NON_SPECIFIC_CHARACTER_LABELS = {
    "无",
    "暂无",
    "无人物",
    "无出场人物",
    "众人",
    "群众",
    "人群",
    "路人",
    "旁人",
    "所有人",
}


def _normalize_character_name(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").strip().split())


def _is_specific_character_name(name: str) -> bool:
    if not name or name in _NON_SPECIFIC_CHARACTER_LABELS:
        return False
    return True


async def promote_scene_characters_to_global(
    store: Any,
    character_names: list[Any],
) -> list[str]:
    """Create global characters for explicit scene-header cast names.

    The caller must pass only structured screenplay cast labels, such as
    ``出场人物`` parsed from scene headers. This helper intentionally does not
    infer names from prose.
    """
    add_character = getattr(store, "add_character", None)
    get_character = getattr(store, "get_character", None)
    resolve_name = getattr(store, "resolve_name", None)
    if not callable(add_character) or not callable(get_character):
        return []

    promoted: list[str] = []
    seen: set[str] = set()
    for raw_name in character_names or []:
        name = _normalize_character_name(raw_name)
        if not _is_specific_character_name(name):
            continue

        resolved = resolve_name(name) if callable(resolve_name) else name
        resolved_name = _normalize_character_name(resolved or name)
        if not _is_specific_character_name(resolved_name) or resolved_name in seen:
            continue
        seen.add(resolved_name)

        if get_character(resolved_name) is not None:
            continue

        character = NovelCharacter(
            name=resolved_name,
            role="",
            is_main=False,
            description="",
        )
        await add_character(character)
        promoted.append(character.name)

    return promoted
