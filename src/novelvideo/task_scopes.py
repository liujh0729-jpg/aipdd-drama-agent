"""Stable task scope helpers shared by API routes and tests."""

from __future__ import annotations

from novelvideo.task_identity import task_config_scope


def prop_reference_asset_scope(prop_name: str) -> str:
    return task_config_scope("prop_ref", {"prop": prop_name})


def scene_reference_asset_scope(scene_name: str, kind: str) -> str:
    return task_config_scope("scene_ref", {"scene": scene_name, "kind": kind})


def stage_asset_scope(scene_name: str, step: str) -> str:
    return task_config_scope("stage_asset", {"scene": scene_name, "step": step})
