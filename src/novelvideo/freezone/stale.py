"""Read/write helpers for Freezone stale sidecar marks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def stale_marks_path(project_dir: Path) -> Path:
    return project_dir / "freezone" / "stale_marks.json"


def load_stale_marks(project_dir: Path) -> list[dict[str, Any]]:
    path = stale_marks_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    marks = data.get("marks") if isinstance(data, dict) else None
    return [m for m in marks if isinstance(m, dict)] if isinstance(marks, list) else []


def stale_marks_for_beat(project_dir: Path, episode: int, beat: int) -> list[dict[str, Any]]:
    return [
        mark
        for mark in load_stale_marks(project_dir)
        if int(mark.get("episode") or -1) == int(episode)
        and int(mark.get("beat") or -1) == int(beat)
    ]
