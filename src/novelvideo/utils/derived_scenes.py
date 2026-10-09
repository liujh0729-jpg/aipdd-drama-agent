"""Helpers for derived scene naming."""

from __future__ import annotations


def compose_derived_scene_name(base: str, suffix: str) -> str:
    """Return the canonical name for a derived scene."""

    return f"{str(base or '').strip()}_{str(suffix or '').strip()}"


def derived_scene_ids(base: str, names: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    """Return scene names that follow the `base_suffix` convention."""

    base_name = str(base or "").strip()
    if not base_name:
        return set()
    prefix = f"{base_name}_"
    return {
        str(name or "").strip()
        for name in names or []
        if str(name or "").strip() != base_name and str(name or "").strip().startswith(prefix)
    }


def resolve_base_of(name: str, names: list[str] | set[str] | tuple[str, ...]) -> str:
    """Resolve a scene name to the longest known base prefix.

    If no known prefix matches, the scene is treated as its own base.
    """

    scene_name = str(name or "").strip()
    if not scene_name:
        return ""
    known_names = {str(candidate or "").strip() for candidate in names or []}
    candidates = [
        candidate
        for candidate in known_names
        if candidate
        and candidate != scene_name
        and scene_name.startswith(f"{candidate}_")
    ]
    return max(candidates, key=len) if candidates else scene_name
