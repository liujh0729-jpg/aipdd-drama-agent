from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import beat_blocking_path, world_path


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_world(project_dir: Path, scene_id: str) -> dict[str, Any] | None:
    return _read_json(world_path(project_dir, scene_id))


def load_beat_blocking(project_dir: Path, episode: int, beat_num: int) -> dict[str, Any] | None:
    return _read_json(beat_blocking_path(project_dir, episode, beat_num))


def save_beat_blocking_file(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return path


def save_beat_blocking(
    project_dir: Path,
    episode: int,
    beat_num: int,
    payload: dict[str, Any],
) -> Path:
    return save_beat_blocking_file(beat_blocking_path(project_dir, episode, beat_num), payload)
