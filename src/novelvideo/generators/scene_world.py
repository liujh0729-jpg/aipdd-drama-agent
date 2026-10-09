"""Lightweight scene-world manifest for explicit scene-reference assets.

This is not the DramaClaw DirectorWorld used for editable blocking. It is a
small optional manifest kept for render prompt compatibility.
The editable DirectorWorld lives under output/<project>/director_worlds/<scene_id>/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _default_scene_world_payload(scene_dir: Path) -> dict[str, Any]:
    scene_id = scene_dir.name
    return {
        "schema_version": "scene_world_manifest_v0",
        "scene_id": scene_id,
        "source": "scene_reference_chain",
        "role": "style_reference_manifest",
        "director_world_note": (
            "This file supports scene reference prompts only. DramaClaw DirectorWorld "
            "is the editable geometry/camera source of truth."
        ),
        "view_index": [
            {
                "name": "front",
                "spatial_summary": "front-facing scene reference view",
                "visible_fixtures": [],
            },
            {
                "name": "right",
                "spatial_summary": "right-side scene reference view",
                "visible_fixtures": [],
            },
            {
                "name": "back",
                "spatial_summary": "back-facing scene reference view",
                "visible_fixtures": [],
            },
            {
                "name": "left",
                "spatial_summary": "left-side scene reference view",
                "visible_fixtures": [],
            },
        ],
        "zones": [],
    }


def validate_scene_world_file(path: Path | str) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if not str(payload.get("schema_version", "") or "").strip():
        return False
    view_index = payload.get("view_index")
    return isinstance(view_index, list)


def ensure_scene_world_file(scene_dir: Path | str) -> Path:
    scene_dir = Path(scene_dir)
    scene_dir.mkdir(parents=True, exist_ok=True)
    path = scene_dir / "scene_world.json"
    if validate_scene_world_file(path):
        return path
    path.write_text(
        json.dumps(_default_scene_world_payload(scene_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
