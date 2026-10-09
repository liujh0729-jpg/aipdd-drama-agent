"""Convert SQLite files under state/ to WAL mode for backup replication."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger("novelvideo.backup.wal_migrator")

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal", ".snapshot", ".snapshot.tmp")


def _is_sidecar_or_litestream(path: Path) -> bool:
    if any(path.name.endswith(suffix) for suffix in _SIDECAR_SUFFIXES):
        return True
    return any(part.endswith("-litestream") for part in path.parts)


def iter_sqlite_files(state_dir: Path) -> Iterator[Path]:
    for path in sorted(Path(state_dir).rglob("*")):
        if not path.is_file() or _is_sidecar_or_litestream(path):
            continue
        try:
            with path.open("rb") as file:
                if file.read(len(_SQLITE_MAGIC)) != _SQLITE_MAGIC:
                    continue
        except OSError:
            continue
        yield path


def ensure_wal(db_path: Path) -> bool:
    """Return True when the database is already WAL or was converted."""

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        current = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        if current == "wal":
            return True
        result = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
        if result == "wal":
            conn.execute("PRAGMA synchronous=NORMAL")
            logger.info("converted to WAL: %s", db_path)
            return True
        logger.warning("WAL switch returned %s: %s", result, db_path)
        return False
    except sqlite3.Error as exc:
        logger.warning("WAL switch failed (%s): %s", exc, db_path)
        return False
    finally:
        conn.close()


def migrate_state_tree(state_dir: Path) -> tuple[int, int]:
    """Return (converted, failed). Databases already in WAL are not counted."""

    converted = 0
    failed = 0
    for db_path in iter_sqlite_files(state_dir):
        if db_path.name == "cognee_db":
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            try:
                already = conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("journal mode pre-check failed (%s): %s", exc, db_path)
            failed += 1
            continue
        if already:
            continue
        if ensure_wal(db_path):
            converted += 1
        else:
            failed += 1
    logger.info("WAL migration done: converted=%d failed=%d", converted, failed)
    return converted, failed
