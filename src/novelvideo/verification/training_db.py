"""User-level shared `director_training.db` — the data-flywheel sink.

Lives at `state/<user>/_shared/director_training.db`. Holds five tables:

- `accepted_sketch_samples` — backfilled terminal positive samples from
  historical projects. No process fields; `observed_by_current_gate`
  is analysis-only and explicitly not ground truth.
- `live_edit_traces` — per-beat-attempt replay-grade records for every
  new director/correction run. All large payloads (prompts, model
  responses, gate verdicts) are stored as content-addressable artifacts
  with their path / sha256 / size inlined.
- `reject_buffer` — metadata pointing at gate-failed candidate cells
  kept for negative-sample mining.
- `human_override_events` — append-only audit log; the `live_edit_traces`
  main row owns the summarized override status so both sides have a
  clear master/slave relationship.
- `sketch_format_versions` — versioned snapshots of the canonical
  stick-figure spec; every row in `live_edit_traces` / `accepted_sketch_samples`
  must cite a `sketch_format_version` that exists in this table.

Trace rows land here only after the attached artifacts are written to
the shared artifact store; `record_*` helpers enforce ordering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from novelvideo.sqlite_pragmas import configure_sqlite_connection_async


TRAINING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sketch_format_versions (
    version                  TEXT PRIMARY KEY,
    description              TEXT DEFAULT '',
    style_lock_artifact_path TEXT NOT NULL,
    created_at               TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accepted_sketch_samples (
    sample_id                       TEXT PRIMARY KEY,
    project                         TEXT NOT NULL,
    episode_number                  INTEGER NOT NULL,
    beat_number                     INTEGER NOT NULL,
    scene_id                        TEXT,
    audio_type                      TEXT,
    narration_segment               TEXT,
    visual_description              TEXT,
    sketch_colors_json              TEXT,
    identity_markers_json           TEXT,
    sketch_format_version           TEXT NOT NULL,
    sketch_artifact_path            TEXT NOT NULL,
    sketch_sha256                   TEXT NOT NULL,
    observed_by_current_gate        TEXT,
    observed_gate_registry_version  TEXT,
    backfilled_at                   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_samples_sketch_sha
    ON accepted_sketch_samples(sketch_sha256);
CREATE INDEX IF NOT EXISTS idx_samples_project_ep
    ON accepted_sketch_samples(project, episode_number, beat_number);

CREATE TABLE IF NOT EXISTS live_edit_traces (
    trace_id                   TEXT PRIMARY KEY,
    source_run_id              TEXT NOT NULL,
    parent_trace_id            TEXT,
    project                    TEXT NOT NULL,
    episode_number             INTEGER NOT NULL,
    beat_number                INTEGER NOT NULL,
    scene_id                   TEXT,
    audio_type                 TEXT,
    model_name                 TEXT NOT NULL,
    prompt_version             TEXT NOT NULL,
    registry_version           TEXT NOT NULL,
    sketch_format_version      TEXT NOT NULL,
    prompt_artifact_path       TEXT,
    prompt_sha256              TEXT,
    prompt_size_bytes          INTEGER,
    response_artifact_path     TEXT,
    response_sha256            TEXT,
    response_size_bytes        INTEGER,
    gate_verdict_artifact_path TEXT,
    gate_verdict_sha256        TEXT,
    edit_instruction           TEXT,
    failure_codes_observed     TEXT,
    gate_result                TEXT,
    trace_kind                 TEXT NOT NULL,
    final_status               TEXT NOT NULL DEFAULT 'pending',
    human_override_status      TEXT,
    human_override_reason      TEXT,
    input_sketch_path          TEXT,
    input_sketch_sha256        TEXT,
    output_sketch_path         TEXT,
    output_sketch_sha256       TEXT,
    input_grid_path            TEXT,
    input_grid_sha256          TEXT,
    output_grid_path           TEXT,
    output_grid_sha256         TEXT,
    created_at                 TEXT DEFAULT (datetime('now')),
    completed_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_project_ep
    ON live_edit_traces(project, episode_number, beat_number);
CREATE INDEX IF NOT EXISTS idx_traces_run
    ON live_edit_traces(source_run_id);
CREATE INDEX IF NOT EXISTS idx_traces_final
    ON live_edit_traces(final_status);

CREATE TABLE IF NOT EXISTS reject_buffer (
    reject_id              TEXT PRIMARY KEY,
    source_trace_id        TEXT,
    project                TEXT,
    episode_number         INTEGER,
    beat_number            INTEGER,
    failure_codes          TEXT,
    gate_verdict_sha256    TEXT,
    sketch_artifact_path   TEXT NOT NULL,
    sketch_sha256          TEXT NOT NULL,
    rejected_at            TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rejects_sketch_sha ON reject_buffer(sketch_sha256);

CREATE TABLE IF NOT EXISTS human_override_events (
    event_id   TEXT PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    verdict    TEXT NOT NULL,
    reason     TEXT,
    actor      TEXT,
    at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON human_override_events(trace_id);
"""


async def open_training_db(db_path: Path) -> aiosqlite.Connection:
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await configure_sqlite_connection_async(db)
    await db.executescript(TRAINING_SCHEMA_SQL)
    await db.commit()
    return db


async def ensure_sketch_format_seeded(
    db: aiosqlite.Connection,
    *,
    version: str,
    description: str,
    style_lock_artifact_path: str,
) -> None:
    """Idempotent insert of a canonical sketch-format snapshot row.

    Called once per `SKETCH_FORMAT_VERSION` — the style-lock artifact
    itself is written by the caller via `artifact_store`, we only
    record its path here.
    """
    await db.execute(
        """
        INSERT INTO sketch_format_versions (version, description, style_lock_artifact_path, created_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(version) DO UPDATE SET
            description = excluded.description,
            style_lock_artifact_path = excluded.style_lock_artifact_path
        """,
        (version, description, style_lock_artifact_path),
    )
    await db.commit()


# --------------------------------------------------------------------- #
# Writers: accepted samples                                              #
# --------------------------------------------------------------------- #

async def insert_accepted_sample(
    db: aiosqlite.Connection,
    row: dict[str, Any],
) -> None:
    columns = [
        "sample_id", "project", "episode_number", "beat_number",
        "scene_id", "audio_type",
        "narration_segment", "visual_description",
        "sketch_colors_json", "identity_markers_json",
        "sketch_format_version",
        "sketch_artifact_path", "sketch_sha256",
        "observed_by_current_gate", "observed_gate_registry_version",
    ]
    placeholders = ",".join("?" for _ in columns)
    values = [row.get(col) for col in columns]
    await db.execute(
        f"INSERT OR REPLACE INTO accepted_sketch_samples ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    await db.commit()


# --------------------------------------------------------------------- #
# Writers: live_edit_traces                                              #
# --------------------------------------------------------------------- #

async def begin_trace(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    """Insert the minimum viable trace row at beat-attempt start.

    Remaining fields (prompt / response / gate / final_status /
    output artifacts) land through `update_trace_fields` as the beat
    progresses through the pipeline.
    """
    required = [
        "trace_id", "source_run_id", "project", "episode_number", "beat_number",
        "model_name", "prompt_version", "registry_version", "sketch_format_version",
        "trace_kind",
    ]
    for field in required:
        if not row.get(field) and row.get(field) != 0:
            raise ValueError(f"begin_trace missing required field: {field}")

    columns = required + [
        "parent_trace_id",
        "scene_id", "audio_type",
        "input_sketch_path", "input_sketch_sha256",
        "input_grid_path", "input_grid_sha256",
        "edit_instruction",
        "final_status",
    ]
    placeholders = ",".join("?" for _ in columns)
    values = [row.get(col) for col in columns]
    # Default final_status to 'pending' if caller didn't supply one.
    if row.get("final_status") is None:
        values[columns.index("final_status")] = "pending"
    await db.execute(
        f"INSERT INTO live_edit_traces ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    await db.commit()


async def update_trace_fields(
    db: aiosqlite.Connection,
    trace_id: str,
    updates: dict[str, Any],
) -> None:
    if not updates:
        return
    cols = list(updates.keys())
    set_clause = ", ".join(f"{col} = ?" for col in cols)
    params = [updates[col] for col in cols]
    await db.execute(
        f"UPDATE live_edit_traces SET {set_clause} WHERE trace_id = ?",
        (*params, trace_id),
    )
    await db.commit()


async def finalize_trace(
    db: aiosqlite.Connection,
    trace_id: str,
    *,
    final_status: str,
    output_updates: dict[str, Any] | None = None,
) -> None:
    """Terminal transition for a trace. Bumps completed_at + final_status."""
    allowed = {"accepted", "rejected_by_gate", "vetoed_by_human", "skipped"}
    if final_status not in allowed:
        raise ValueError(f"Illegal final_status: {final_status} (allowed={sorted(allowed)})")
    payload: dict[str, Any] = {
        "final_status": final_status,
    }
    if output_updates:
        payload.update(output_updates)
    cols = list(payload.keys())
    set_clause = ", ".join(f"{col} = ?" for col in cols)
    params = [payload[col] for col in cols]
    await db.execute(
        f"UPDATE live_edit_traces SET {set_clause}, completed_at = datetime('now') WHERE trace_id = ?",
        (*params, trace_id),
    )
    await db.commit()


# --------------------------------------------------------------------- #
# Writers: reject_buffer + human_override_events                         #
# --------------------------------------------------------------------- #

async def record_reject(db: aiosqlite.Connection, row: dict[str, Any]) -> None:
    columns = [
        "reject_id", "source_trace_id", "project", "episode_number", "beat_number",
        "failure_codes", "gate_verdict_sha256",
        "sketch_artifact_path", "sketch_sha256",
    ]
    placeholders = ",".join("?" for _ in columns)
    values = [row.get(col) for col in columns]
    await db.execute(
        f"INSERT INTO reject_buffer ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    await db.commit()


async def record_override_event(
    db: aiosqlite.Connection,
    *,
    event_id: str,
    trace_id: str,
    verdict: str,
    reason: str | None = None,
    actor: str | None = None,
) -> None:
    allowed = {"accept", "veto"}
    if verdict not in allowed:
        raise ValueError(f"verdict must be one of {allowed}, got {verdict}")
    await db.execute(
        """
        INSERT INTO human_override_events (event_id, trace_id, verdict, reason, actor)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, trace_id, verdict, reason, actor),
    )
    # Mirror summarized status onto the main trace row (single source of truth).
    await db.execute(
        """
        UPDATE live_edit_traces
        SET human_override_status = ?, human_override_reason = COALESCE(?, human_override_reason)
        WHERE trace_id = ?
        """,
        (verdict, reason, trace_id),
    )
    await db.commit()
