"""Prop reference menu helpers shared by API, Freezone, and legacy UI."""

from __future__ import annotations

from typing import Any

from novelvideo.models import build_prop_menu, collect_prop_marker_ids_from_beat


def runtime_prop_menu_with_cached_global_props(
    *,
    prop_menu: list[dict],
    beats: list[dict],
    store: Any,
) -> list[dict]:
    """Resolve [[prop]] markers against cached global props without mutating episode data."""
    marked_prop_ids: list[str] = []
    for beat in beats or []:
        for prop_id in collect_prop_marker_ids_from_beat(beat):
            if prop_id and prop_id not in marked_prop_ids:
                marked_prop_ids.append(prop_id)
    if not marked_prop_ids or not store or not hasattr(store, "get_cached_prop"):
        return list(prop_menu or [])

    existing = {
        item.prop_id: item.model_dump() for item in build_prop_menu(prop_menu=prop_menu or [])
    }
    changed = False
    for marker_prop_id in marked_prop_ids:
        global_prop = store.get_cached_prop(marker_prop_id)
        if not global_prop:
            continue
        item = dict(existing.get(marker_prop_id) or {"prop_id": marker_prop_id})
        item["is_global_asset"] = True
        item["prop_type"] = (
            item.get("prop_type") or getattr(global_prop, "prop_type", "") or "object"
        )
        item["description"] = (
            item.get("description")
            or getattr(global_prop, "description", "")
            or getattr(global_prop, "visual_prompt", "")
            or marker_prop_id
        )
        existing[marker_prop_id] = item
        changed = True
    if not changed:
        return list(prop_menu or [])

    ordered_ids: list[str] = []
    for item in build_prop_menu(prop_menu=prop_menu or []):
        if item.prop_id not in ordered_ids:
            ordered_ids.append(item.prop_id)
    for prop_id in marked_prop_ids:
        if prop_id in existing and prop_id not in ordered_ids:
            ordered_ids.append(prop_id)
    return [existing[prop_id] for prop_id in ordered_ids if prop_id in existing]


def episode_prop_menu_from_store(
    *,
    episode: int,
    current_episode: dict,
    store: Any,
) -> list[dict]:
    """Read episode prop_menu from the persisted episode row, with UI state as fallback."""
    if store:
        try:
            store_episode = store.get_episode(episode)
            if store_episode and store_episode.prop_menu:
                return [item.model_dump() for item in store_episode.prop_menu]
        except Exception:
            pass
    return list((current_episode or {}).get("prop_menu", []) or [])


__all__ = ["episode_prop_menu_from_store", "runtime_prop_menu_with_cached_global_props"]
