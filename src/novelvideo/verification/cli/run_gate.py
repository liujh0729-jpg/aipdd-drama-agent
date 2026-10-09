"""CLI: run the visual gate against the latest sketch_edit_execute_summary.

Usage:
    uv run python -m novelvideo.verification.cli.run_gate \
        <project_dir> --episode-num 1 [--summary-name sketch_edit_execute_summary.json]

Emits stdout JSON summary, writes an audit file under
`verify_reports/ep{NNN}/sketch_edit_execute_audit/gate_<ts>.json`, and
records one row in `convergence_rounds` in the project's data.db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiosqlite

try:
    from dotenv import load_dotenv as _load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parents[4]
    _load_dotenv(_REPO_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

from novelvideo.verification import convergence_log, failure_registry
from novelvideo.verification.global_registry_db import open_defs_db
from novelvideo.verification.sketch_visual_gate import gate_candidate_cells


def _resolve_paths(project_dir: Path) -> tuple[Path, Path]:
    from novelvideo.utils.project_paths import ProjectPaths

    parts = project_dir.resolve().parts
    user, project = parts[-2], parts[-1]
    pp = ProjectPaths(user, project)
    return pp.data_db, pp.global_shared_verification_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run visual gate on sketch edit output.")
    parser.add_argument("project_dir", help="SuperTale project directory")
    parser.add_argument("--episode-num", type=int, required=True)
    parser.add_argument(
        "--summary-name",
        default="sketch_edit_execute_summary.json",
        help="Summary JSON inside verify_reports/ep{NNN}/ (default: %(default)s)",
    )
    parser.add_argument(
        "--phase",
        default="correction",
        choices=["correction", "director"],
        help="Which pipeline phase this round belongs to (affects convergence_rounds.phase)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="VLM model id (default picks from env: openrouter=gemini-3.5-flash, google=gemini-3.5-flash)",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    project_dir = Path(args.project_dir).expanduser().resolve()
    episode_dir = project_dir / "verify_reports" / f"ep{args.episode_num:03d}"
    summary_path = episode_dir / args.summary_name
    if not summary_path.exists():
        print(json.dumps({"ok": False, "error": f"missing summary: {summary_path}"}))
        return 2

    project_db_path, defs_db_path = _resolve_paths(project_dir)
    if not project_db_path.exists():
        print(json.dumps({"ok": False, "error": f"missing data.db: {project_db_path}"}))
        return 2

    defs_db = await open_defs_db(defs_db_path)
    try:
        await failure_registry.ensure_seeded(defs_db)
        async with aiosqlite.connect(str(project_db_path)) as project_db:
            project_db.row_factory = aiosqlite.Row
            try:
                result = await gate_candidate_cells(
                    project_dir=project_dir,
                    summary_path=summary_path,
                    defs_db=defs_db,
                    project_hits_db=project_db,
                    model=args.model,
                )
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": str(exc)}))
                return 1

            total = len(result.cells)
            passed = len(result.passed_beats)
            failed = len(result.failed_beats)
            round_num = await convergence_log.write_round(
                project_db,
                episode_num=args.episode_num,
                phase=args.phase,
                residual_count=failed,
                fixed_count=max(total - failed, 0),
                new_failures=sorted({code for codes in result.cell_hits_map().values() for code in codes}),
            )
    finally:
        await defs_db.close()

    # Director-OS phase 2: record per-beat gate verdict onto the
    # live_edit_traces rows created during execute. Best-effort — if the
    # summary lacks source_run_id (older runs) or the training DB is
    # missing, skip silently so the CLI's primary job (gating) still
    # completes normally.
    try:
        import json as _json
        summary_payload = _json.loads(Path(summary_path).read_text(encoding="utf-8"))
        source_run_id = summary_payload.get("source_run_id")
        if source_run_id:
            from novelvideo.verification import replay_capture as _trace_mod

            resolved = _trace_mod._resolve_project_paths(project_dir)
            if resolved is not None:
                training_db_path, artifacts_root, user, project = resolved
                project_label = f"{user}/{project}"
                beat_to_trace = await _trace_mod.find_traces_for_run(
                    project_dir=project_dir, source_run_id=source_run_id,
                )
                for cell in result.cells:
                    tid = beat_to_trace.get(int(cell.beat_number))
                    if not tid:
                        continue
                    handle = _trace_mod.TraceHandle(
                        trace_id=tid,
                        source_run_id=source_run_id,
                        project=project_label,
                        episode_number=args.episode_num,
                        beat_number=cell.beat_number,
                        training_db_path=training_db_path,
                        artifacts_root=artifacts_root,
                    )
                    await _trace_mod.record_gate(
                        handle,
                        gate_verdict_raw=cell.raw_response or "{}",
                        gate_result="passed" if cell.passed else "failed",
                        failure_codes_observed=list(cell.hits),
                        candidate_sketch_path=Path(cell.cell_path) if cell.cell_path else None,
                    )
    except Exception:  # noqa: BLE001
        pass

    output = {
        "ok": True,
        "episode_num": args.episode_num,
        "phase": args.phase,
        "round_num": round_num,
        "cells_total": total,
        "passed_beats": result.passed_beats,
        "failed_beats": result.failed_beats,
        "cell_hits": result.cell_hits_map(),
        "audit_path": str(result.audit_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not result.failed_beats else 3


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
