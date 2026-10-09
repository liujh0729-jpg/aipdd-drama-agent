"""Orchestration for per-beat-attempt trace capture.

Every time a beat goes through the execute → gate → copy-back pipeline,
one `live_edit_traces` row must land in the user-shared
`director_training.db`. That row is the canonical, replayable record
of the director decision — it must contain enough context to be
re-run months later when we have more data and want to compare a new
director policy against this one.

This module owns the 4-stage handshake:

1. `begin_trace_for_beat(...)` — at the top of each beat attempt.
   Computes trace_id + sample row, writes input sketch/grid artifacts,
   records `registry_version` + `sketch_format_version` + initial
   metadata. Returns a `TraceHandle` the caller keeps for later stages.

2. `record_prompt_and_response(...)` — after the model call. Writes
   prompt bytes + response bytes to the artifact store, computes
   `prompt_version = sha256(prompt_bytes)[:12]`, updates the trace row.

3. `record_gate(...)` — after gate verdicts. Writes gate verdict JSON
   to artifact store, sets `gate_result` + `failure_codes_observed`.

4. `finalize(...)` — terminal. Sets `final_status` and optional output
   sketch / grid artifacts.

All stages are no-ops if the training DB isn't reachable (best-effort
capture; pipeline progress is never blocked by trace-write failure).
This keeps phase 2 data-sinking optional from the pipeline's
perspective — if someone rips the training DB out, sketches still
generate.

Handles are plain dataclasses, not async context managers, because the
pipeline flow crosses several async boundaries (asyncio.run of a
generator call, gate runs, copy_back) and the handle needs to travel
through sync callers too.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from novelvideo.verification import artifact_store, failure_registry, trace_ids, training_db, version_hash


@dataclass
class TraceHandle:
    trace_id: str
    source_run_id: str
    project: str
    episode_number: int
    beat_number: int
    training_db_path: Path
    artifacts_root: Path
    trace_kind: str = "live"           # or "replay"
    final_status: str = "pending"
    # Scratch state: the beat's resolved prompt bytes so
    # `record_prompt_and_response` can avoid recomputing hashes.
    last_prompt_sha: str = ""
    disabled: bool = False             # if True, all writes become no-ops


def _disabled_handle(**overrides: Any) -> TraceHandle:
    defaults: dict[str, Any] = {
        "trace_id": "",
        "source_run_id": "",
        "project": "",
        "episode_number": 0,
        "beat_number": 0,
        "training_db_path": Path("/dev/null"),
        "artifacts_root": Path("/dev/null"),
        "disabled": True,
    }
    defaults.update(overrides)
    return TraceHandle(**defaults)


async def _open_training_db_for_handle(handle: TraceHandle) -> aiosqlite.Connection:
    return await training_db.open_training_db(handle.training_db_path)


def _resolve_project_paths(project_dir: Path) -> tuple[Path, Path, str, str] | None:
    from novelvideo.utils.project_paths import ProjectPaths

    parts = Path(project_dir).resolve().parts
    if len(parts) < 2:
        return None
    user, project = parts[-2], parts[-1]
    try:
        pp = ProjectPaths(user, project)
    except Exception:
        return None
    return pp.global_shared_training_db, pp.global_shared_artifacts_dir, user, project


def _project_label(user: str, project: str) -> str:
    return f"{user}/{project}"


# --------------------------------------------------------------------- #
# Stage 1: begin_trace_for_beat                                          #
# --------------------------------------------------------------------- #

async def begin_trace_for_beat(
    *,
    project_dir: Path,
    episode_number: int,
    beat_number: int,
    source_run_id: str,
    model_name: str,
    trace_kind: str = "live",
    scene_id: str | None = None,
    audio_type: str | None = None,
    edit_instruction: str | None = None,
    input_sketch_path: Path | None = None,
    input_grid_path: Path | None = None,
    parent_trace_id: str | None = None,
) -> TraceHandle:
    """Open a new trace row for this beat attempt.

    Returns a disabled handle if the training DB can't be resolved.
    """
    resolved = _resolve_project_paths(project_dir)
    if resolved is None:
        return _disabled_handle()
    training_db_path, artifacts_root, user, project = resolved
    project_label = _project_label(user, project)

    # Resolve registry_version against canonical verification.db only.
    try:
        defs_db = await failure_registry.open_defs_db_for_project(str(project_dir))
    except Exception:
        return _disabled_handle(
            project=project_label,
            episode_number=episode_number,
            beat_number=beat_number,
            source_run_id=source_run_id,
            training_db_path=training_db_path,
            artifacts_root=artifacts_root,
        )
    try:
        registry_version = await version_hash.compute_registry_version(defs_db)
    finally:
        await defs_db.close()

    trace_id = trace_ids.make_trace_id(
        project=project_label,
        episode_number=episode_number,
        beat_number=beat_number,
        source_run_id=source_run_id,
    )

    # Absorb input sketch / grid into artifact store (idempotent on content).
    input_sketch_sha = None
    input_sketch_artifact = None
    if input_sketch_path and Path(input_sketch_path).exists():
        ref = artifact_store.copy_file_in(artifacts_root, Path(input_sketch_path), ext=Path(input_sketch_path).suffix.lstrip(".") or "png")
        input_sketch_sha = ref.sha256
        input_sketch_artifact = str(ref.path)

    input_grid_sha = None
    input_grid_artifact = None
    if input_grid_path and Path(input_grid_path).exists():
        ref = artifact_store.copy_file_in(artifacts_root, Path(input_grid_path), ext=Path(input_grid_path).suffix.lstrip(".") or "png")
        input_grid_sha = ref.sha256
        input_grid_artifact = str(ref.path)

    db = await training_db.open_training_db(training_db_path)
    try:
        await training_db.begin_trace(
            db,
            {
                "trace_id": trace_id,
                "source_run_id": source_run_id,
                "parent_trace_id": parent_trace_id,
                "project": project_label,
                "episode_number": episode_number,
                "beat_number": beat_number,
                "scene_id": scene_id,
                "audio_type": audio_type,
                "model_name": model_name,
                # prompt_version gets a placeholder until record_prompt_and_response
                # writes the real one — main row NOT NULL demands something.
                "prompt_version": "pending",
                "registry_version": registry_version,
                "sketch_format_version": version_hash.SKETCH_FORMAT_VERSION,
                "trace_kind": trace_kind,
                "edit_instruction": edit_instruction,
                "input_sketch_path": input_sketch_artifact,
                "input_sketch_sha256": input_sketch_sha,
                "input_grid_path": input_grid_artifact,
                "input_grid_sha256": input_grid_sha,
                "final_status": "pending",
            },
        )
    finally:
        await db.close()

    return TraceHandle(
        trace_id=trace_id,
        source_run_id=source_run_id,
        project=project_label,
        episode_number=episode_number,
        beat_number=beat_number,
        training_db_path=training_db_path,
        artifacts_root=artifacts_root,
        trace_kind=trace_kind,
    )


# --------------------------------------------------------------------- #
# Stage 2: record_prompt_and_response                                    #
# --------------------------------------------------------------------- #

async def record_prompt_and_response(
    handle: TraceHandle,
    *,
    prompt_text: str,
    response_text: str | None = None,
) -> None:
    if handle.disabled or not handle.trace_id:
        return
    prompt_ref = artifact_store.write_text(handle.artifacts_root, prompt_text, ext="txt")
    handle.last_prompt_sha = prompt_ref.sha256
    updates: dict[str, Any] = {
        "prompt_version": version_hash.compute_prompt_version_from_artifact(prompt_text.encode("utf-8")),
        "prompt_artifact_path": str(prompt_ref.path),
        "prompt_sha256": prompt_ref.sha256,
        "prompt_size_bytes": prompt_ref.size_bytes,
    }
    if response_text is not None:
        response_ref = artifact_store.write_json_gz(handle.artifacts_root, response_text)
        updates.update({
            "response_artifact_path": str(response_ref.path),
            "response_sha256": response_ref.sha256,
            "response_size_bytes": response_ref.size_bytes,
        })
    db = await _open_training_db_for_handle(handle)
    try:
        await training_db.update_trace_fields(db, handle.trace_id, updates)
    finally:
        await db.close()


# --------------------------------------------------------------------- #
# Stage 2.5: record_execute_output                                       #
# --------------------------------------------------------------------- #
# execute produces a candidate cell per beat + a batch-level grid image.
# Those artifacts are already final content — gate + copy-back decide
# whether they reach the formal sketches dir, but the artifact bytes
# don't change. Writing them now gives every trace a replayable
# "model output" the moment execute returns, regardless of whether
# gate ultimately passes the beat.


async def record_execute_output(
    handle: TraceHandle,
    *,
    output_sketch_path: Path | None = None,
    output_grid_path: Path | None = None,
) -> None:
    if handle.disabled or not handle.trace_id:
        return
    updates: dict[str, Any] = {}
    if output_sketch_path and Path(output_sketch_path).exists():
        ref = artifact_store.copy_file_in(
            handle.artifacts_root,
            Path(output_sketch_path),
            ext=Path(output_sketch_path).suffix.lstrip(".") or "png",
        )
        updates["output_sketch_path"] = str(ref.path)
        updates["output_sketch_sha256"] = ref.sha256
    if output_grid_path and Path(output_grid_path).exists():
        ref = artifact_store.copy_file_in(
            handle.artifacts_root,
            Path(output_grid_path),
            ext=Path(output_grid_path).suffix.lstrip(".") or "png",
        )
        updates["output_grid_path"] = str(ref.path)
        updates["output_grid_sha256"] = ref.sha256
    if not updates:
        return
    db = await _open_training_db_for_handle(handle)
    try:
        await training_db.update_trace_fields(db, handle.trace_id, updates)
    finally:
        await db.close()


# --------------------------------------------------------------------- #
# Stage 3: record_gate                                                   #
# --------------------------------------------------------------------- #

async def record_gate(
    handle: TraceHandle,
    *,
    gate_verdict_raw: str,
    gate_result: str,
    failure_codes_observed: list[str],
    candidate_sketch_path: Path | None = None,
) -> None:
    if handle.disabled or not handle.trace_id:
        return
    ref = artifact_store.write_json_gz(handle.artifacts_root, gate_verdict_raw)
    db = await _open_training_db_for_handle(handle)
    try:
        await training_db.update_trace_fields(
            db,
            handle.trace_id,
            {
                "gate_verdict_artifact_path": str(ref.path),
                "gate_verdict_sha256": ref.sha256,
                "gate_result": gate_result,
                "failure_codes_observed": json.dumps(failure_codes_observed, ensure_ascii=False),
            },
        )
        # Index gate-failed sketches into reject_buffer so the
        # "super model made a mistake gate caught" corpus stays queryable.
        # Without this the bytes exist in the artifact store but no row
        # points at them as negative training samples.
        if (
            gate_result == "failed"
            and candidate_sketch_path is not None
            and Path(candidate_sketch_path).exists()
        ):
            sketch_ref = artifact_store.copy_file_in(
                handle.artifacts_root,
                Path(candidate_sketch_path),
                ext=Path(candidate_sketch_path).suffix.lstrip(".") or "png",
            )
            await training_db.record_reject(
                db,
                {
                    "reject_id": trace_ids.make_reject_id(sketch_sha256=sketch_ref.sha256),
                    "source_trace_id": handle.trace_id,
                    "project": handle.project,
                    "episode_number": handle.episode_number,
                    "beat_number": handle.beat_number,
                    "failure_codes": json.dumps(failure_codes_observed, ensure_ascii=False),
                    "gate_verdict_sha256": ref.sha256,
                    "sketch_artifact_path": str(sketch_ref.path),
                    "sketch_sha256": sketch_ref.sha256,
                },
            )
    finally:
        await db.close()


# --------------------------------------------------------------------- #
# Stage 4: finalize                                                      #
# --------------------------------------------------------------------- #

async def finalize(
    handle: TraceHandle,
    *,
    final_status: str,
    output_sketch_path: Path | None = None,
    output_grid_path: Path | None = None,
) -> None:
    if handle.disabled or not handle.trace_id:
        return
    updates: dict[str, Any] = {}
    if output_sketch_path and Path(output_sketch_path).exists():
        ref = artifact_store.copy_file_in(handle.artifacts_root, Path(output_sketch_path), ext=Path(output_sketch_path).suffix.lstrip(".") or "png")
        updates["output_sketch_path"] = str(ref.path)
        updates["output_sketch_sha256"] = ref.sha256
    if output_grid_path and Path(output_grid_path).exists():
        ref = artifact_store.copy_file_in(handle.artifacts_root, Path(output_grid_path), ext=Path(output_grid_path).suffix.lstrip(".") or "png")
        updates["output_grid_path"] = str(ref.path)
        updates["output_grid_sha256"] = ref.sha256
    db = await _open_training_db_for_handle(handle)
    try:
        await training_db.finalize_trace(
            db,
            handle.trace_id,
            final_status=final_status,
            output_updates=updates or None,
        )
    finally:
        await db.close()
    handle.final_status = final_status


# --------------------------------------------------------------------- #
# Sync bridges for pipelines that aren't already inside an event loop    #
# --------------------------------------------------------------------- #

def begin_trace_for_beat_sync(**kwargs: Any) -> TraceHandle:
    """Synchronous wrapper for `begin_trace_for_beat`.

    `sketch_edit_execute.execute_sketch_edit_batches` is sync (uses
    `asyncio.run` internally for model calls). The trace-capture hooks
    have to run from that sync context without nesting event loops.
    """
    return asyncio.run(begin_trace_for_beat(**kwargs))


def record_prompt_and_response_sync(handle: TraceHandle, **kwargs: Any) -> None:
    asyncio.run(record_prompt_and_response(handle, **kwargs))


def record_execute_output_sync(handle: TraceHandle, **kwargs: Any) -> None:
    asyncio.run(record_execute_output(handle, **kwargs))


def record_gate_sync(handle: TraceHandle, **kwargs: Any) -> None:
    asyncio.run(record_gate(handle, **kwargs))


def finalize_sync(handle: TraceHandle, **kwargs: Any) -> None:
    asyncio.run(finalize(handle, **kwargs))


# --------------------------------------------------------------------- #
# Human override                                                         #
# --------------------------------------------------------------------- #

async def record_override(
    *,
    project_dir: Path,
    trace_id: str,
    verdict: str,
    reason: str | None = None,
    actor: str | None = None,
) -> None:
    resolved = _resolve_project_paths(project_dir)
    if resolved is None:
        raise RuntimeError(f"cannot resolve training DB from project_dir={project_dir}")
    training_db_path, _artifacts, _user, _project = resolved
    event_id = trace_ids.make_event_id(trace_id=trace_id)
    db = await training_db.open_training_db(training_db_path)
    try:
        await training_db.record_override_event(
            db,
            event_id=event_id,
            trace_id=trace_id,
            verdict=verdict,
            reason=reason,
            actor=actor,
        )
    finally:
        await db.close()


# --------------------------------------------------------------------- #
# Reverse lookup: beat_number → trace_id (for gate/copy_back to find     #
# the trace established at execute time)                                 #
# --------------------------------------------------------------------- #

async def find_traces_for_run(
    *,
    project_dir: Path,
    source_run_id: str,
) -> dict[int, str]:
    """Return `{beat_number: trace_id}` for every trace under this run."""
    resolved = _resolve_project_paths(project_dir)
    if resolved is None:
        return {}
    training_db_path, _artifacts, _user, _project = resolved
    if not training_db_path.exists():
        return {}
    db = await training_db.open_training_db(training_db_path)
    try:
        async with db.execute(
            "SELECT beat_number, trace_id FROM live_edit_traces WHERE source_run_id = ?",
            (source_run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()
    return {int(row["beat_number"]): row["trace_id"] for row in rows}
