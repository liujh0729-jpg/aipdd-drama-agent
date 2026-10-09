"""Helpers for promoting episode prop-menu items into global prop assets."""

from __future__ import annotations

from typing import Any

from novelvideo.models import NovelProp, build_prop_menu


def _normalize_lookup(value: str) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").strip().lower().split())


def _prop_store(store: Any) -> Any:
    return getattr(store, "sqlite_store", None) or store


async def promote_episode_props_to_global(store: Any, prop_menu: list[Any]) -> list[str]:
    """Create global props for episode prop-menu items that do not exist globally.

    Planning keeps the episode prop_menu as the durable per-episode record. This
    helper mirrors those planned props into the global props table so sketch
    color assignment and downstream render consistency can treat them as
    tracked assets by default.
    """
    target_store = _prop_store(store)
    list_props = getattr(target_store, "list_props", None)
    add_prop = getattr(target_store, "add_prop", None)
    if not callable(list_props) or not callable(add_prop):
        return []

    existing_props = await list_props()
    existing_keys: set[str] = set()
    for prop in existing_props or []:
        name = str(getattr(prop, "name", "") or "").strip()
        if name:
            existing_keys.add(_normalize_lookup(name))
        for alias in getattr(prop, "aliases", []) or []:
            alias_text = str(alias or "").strip()
            if alias_text:
                existing_keys.add(_normalize_lookup(alias_text))

    promoted: list[str] = []
    for item in build_prop_menu(prop_menu=prop_menu or []):
        prop_id = str(item.prop_id or "").strip()
        if not prop_id:
            continue
        lookup = _normalize_lookup(prop_id)
        if lookup in existing_keys:
            continue

        prop = NovelProp(
            name=prop_id,
            prop_type=str(item.prop_type or "").strip() or "object",
            visual_prompt=(
                str(item.visual_prompt or "").strip()
                or str(item.description or "").strip()
                or prop_id
            ),
            description=str(item.description or "").strip(),
            owner=str(item.owner_identity_id or "").strip(),
            notes="auto_from_episode_planning",
        )
        await add_prop(prop)
        promoted.append(prop.name)
        existing_keys.add(lookup)

        cache = getattr(store, "_props", None)
        if isinstance(cache, dict):
            cache[prop.name] = prop

    return promoted
