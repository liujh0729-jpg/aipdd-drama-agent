"""State-backed JSON sidecar helpers for generated media indexes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from novelvideo.config import OUTPUT_DIR, STATE_DIR

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _under(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _sibling_state_path(path: Path, filename: str) -> Path | None:
    parts = path.resolve().parts
    try:
        output_index = max(index for index, part in enumerate(parts) if part == "output")
    except ValueError:
        return None
    if output_index >= len(parts) - 1:
        return None
    return Path(*parts[:output_index], "state", *parts[output_index + 1 :]) / filename


def resolve_state_index_path(episode_dir: str | Path, filename: str) -> Path:
    """Map an output episode directory to its state-backed index path.

    Temp/test directories that are not under a known ``output`` root keep the
    legacy local sidecar path so existing fixture-style tests remain simple.
    """
    directory = Path(episode_dir)
    state_root = Path(STATE_DIR)

    state_rel = _under(directory, state_root)
    if state_rel is not None:
        return state_root / state_rel / filename

    output_rel = _under(directory, Path(OUTPUT_DIR))
    if output_rel is not None:
        return state_root / output_rel / filename

    sibling = _sibling_state_path(directory, filename)
    if sibling is not None:
        return sibling

    return directory / filename


def legacy_index_path(episode_dir: str | Path, filename: str) -> Path:
    return Path(episode_dir) / filename


def ensure_state_index_from_legacy(episode_dir: str | Path, filename: str) -> Path:
    """Move a legacy output-side index into state if this is the first access."""
    state_path = resolve_state_index_path(episode_dir, filename)
    legacy_path = legacy_index_path(episode_dir, filename)
    if _same_path(state_path, legacy_path):
        return state_path
    if state_path.exists() or not legacy_path.exists():
        return state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy_path), str(state_path))
    return state_path


@contextmanager
def index_file_lock(index_path: Path) -> Iterator[None]:
    lock_path = index_path.with_suffix(f"{index_path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_json_atomic(index_path: Path, payload: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f"{index_path.name}.",
        suffix=".tmp",
        dir=index_path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, index_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
