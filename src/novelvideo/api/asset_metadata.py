"""Helpers for asset metadata exposed by the REST API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_iso(value: object) -> str:
    """Return an ISO 8601 UTC timestamp string for SQLite/Python date values."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return str(value or "")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def path_updated_at(path: str | Path | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.exists():
        return ""
    try:
        return utc_iso(datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc))
    except OSError:
        return ""


def newest_updated_at(*values: object) -> str:
    best: datetime | None = None
    fallback = ""
    for value in values:
        text = utc_iso(value)
        if not text:
            continue
        fallback = text
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    return utc_iso(best) if best else fallback


def newest_path_updated_at(paths: Iterable[str | Path | None]) -> str:
    return newest_updated_at(*(path_updated_at(path) for path in paths if path))


def tree_updated_at(root: str | Path | None) -> str:
    if not root:
        return ""
    root_path = Path(root)
    if not root_path.exists():
        return ""
    candidates = [root_path]
    if root_path.is_dir():
        try:
            candidates.extend(path for path in root_path.rglob("*") if path.exists())
        except OSError:
            pass
    return newest_path_updated_at(candidates)
