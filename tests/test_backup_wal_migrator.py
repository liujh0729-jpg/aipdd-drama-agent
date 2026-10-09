"""WAL migrator tests for converting SQLite files under state/ to WAL."""

import sqlite3

from novelvideo.backup.wal_migrator import iter_sqlite_files, migrate_state_tree


def _make_db(path, journal_mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if journal_mode:
        conn.execute(f"PRAGMA journal_mode={journal_mode}")
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()


def test_iter_finds_sqlite_by_magic_and_skips_junk(tmp_path):
    _make_db(tmp_path / "u1" / "p1" / "data.db")
    _make_db(tmp_path / "u1" / "p1" / "cognee_system" / "databases" / "cognee_db")
    (tmp_path / "u1" / "p1" / "project_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "u1" / "p1" / "data.db-wal").write_bytes(b"junk")
    sidecar_dir = tmp_path / "u1" / "p1" / "data.db-litestream"
    sidecar_dir.mkdir()
    (sidecar_dir / "x").write_bytes(b"SQLite format 3\x00junk")

    found = {p.relative_to(tmp_path).as_posix() for p in iter_sqlite_files(tmp_path)}
    assert found == {"u1/p1/data.db", "u1/p1/cognee_system/databases/cognee_db"}


def test_migrate_converts_delete_mode_only(tmp_path):
    _make_db(tmp_path / "u1" / "p1" / "data.db", journal_mode="WAL")
    _make_db(tmp_path / "u1" / "p1" / "chat.db")
    _make_db(tmp_path / "_shared" / "verification.db")

    converted, failed = migrate_state_tree(tmp_path)

    assert converted == 2 and failed == 0
    for rel in ("u1/p1/chat.db", "_shared/verification.db"):
        conn = sqlite3.connect(tmp_path / rel)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.close()


def test_one_bad_db_does_not_abort_sweep(tmp_path):
    bad = tmp_path / "u1" / "a_bad" / "data.db"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"SQLite format 3\x00" + b"garbage" * 10)
    _make_db(tmp_path / "u1" / "z_good" / "chat.db")

    converted, failed = migrate_state_tree(tmp_path)

    assert converted == 1 and failed == 1
    conn = sqlite3.connect(tmp_path / "u1" / "z_good" / "chat.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_migrate_skips_cognee_db_but_iter_still_yields_it(tmp_path):
    _make_db(tmp_path / "u1" / "p1" / "cognee_system" / "databases" / "cognee_db")

    converted, failed = migrate_state_tree(tmp_path)

    assert converted == 0 and failed == 0
    conn = sqlite3.connect(tmp_path / "u1" / "p1" / "cognee_system" / "databases" / "cognee_db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    conn.close()
    found = {p.name for p in iter_sqlite_files(tmp_path)}
    assert "cognee_db" in found
