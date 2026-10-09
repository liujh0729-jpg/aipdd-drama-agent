"""CLI: copy candidate cells back to formal sketches, but only gate-passed beats.

Usage:
    uv run python -m novelvideo.verification.cli.copy_back_passed \
        <project_dir> --episode-num 1

Reads the most recent `gate_*.json` under `verify_reports/ep{NNN}/
sketch_edit_execute_audit/`, maps passed beats → candidate cell paths
from `sketch_edit_execute_summary.json`, and copies only those cells
over `sketches/ep{NNN}/beat_{NN}.png`. Failed / errored beats are
left untouched so prior good renders are never clobbered by a bad
gate-fail output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy gate-passed candidate cells back.")
    parser.add_argument("project_dir", help="SuperTale project directory")
    parser.add_argument("--episode-num", type=int, required=True)
    parser.add_argument(
        "--summary-name",
        default="sketch_edit_execute_summary.json",
    )
    return parser.parse_args()


def _latest_gate_audit(episode_dir: Path, summary_name: str) -> Path | None:
    from novelvideo.verification.sketch_edit_execute import derive_audit_dir_name

    audit_dir = episode_dir / derive_audit_dir_name(summary_name)
    if not audit_dir.exists():
        return None
    candidates = sorted(audit_dir.glob("gate_*.json"))
    return candidates[-1] if candidates else None


def _build_beat_to_cell_map(summary: dict) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for grid in summary.get("grid_results") or []:
        beats = grid.get("beat_nums") or []
        cells = grid.get("candidate_cell_paths") or []
        for bn, cell_rel in zip(beats, cells):
            mapping[int(bn)] = str(cell_rel)
    return mapping


def main() -> int:
    args = parse_args()
    project_dir = Path(args.project_dir).expanduser().resolve()
    episode_dir = project_dir / "verify_reports" / f"ep{args.episode_num:03d}"
    summary_path = episode_dir / args.summary_name
    if not summary_path.exists():
        print(json.dumps({"ok": False, "error": f"missing summary: {summary_path}"}))
        return 2
    audit_path = _latest_gate_audit(episode_dir, args.summary_name)
    if audit_path is None:
        print(json.dumps({"ok": False, "error": "no gate audit found; run run_gate first"}))
        return 2

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    beat_to_cell = _build_beat_to_cell_map(summary)
    passed_beats = audit.get("passed_beats") or []
    failed_beats = audit.get("failed_beats") or []

    sketch_dir = project_dir / "sketches" / f"ep{args.episode_num:03d}"
    sketch_dir.mkdir(parents=True, exist_ok=True)

    copied: list[int] = []
    missing: list[int] = []
    for bn in passed_beats:
        cell_rel = beat_to_cell.get(int(bn))
        if not cell_rel:
            missing.append(int(bn))
            continue
        src = project_dir / cell_rel
        dst = sketch_dir / f"beat_{int(bn):02d}.png"
        if not src.exists():
            missing.append(int(bn))
            continue
        shutil.copyfile(src, dst)
        copied.append(int(bn))

    # Director-OS phase 2: finalize traces. Best-effort; missing
    # source_run_id on older summaries silently skips the hook.
    try:
        import asyncio as _asyncio
        from novelvideo.verification import replay_capture as _trace_mod

        source_run_id = summary.get("source_run_id")
        if source_run_id:
            beat_to_trace = _asyncio.run(
                _trace_mod.find_traces_for_run(
                    project_dir=project_dir, source_run_id=source_run_id,
                )
            )
            for bn in copied:
                tid = beat_to_trace.get(int(bn))
                if not tid:
                    continue
                handle = _trace_mod.TraceHandle(
                    trace_id=tid,
                    source_run_id=source_run_id,
                    project="",
                    episode_number=args.episode_num,
                    beat_number=bn,
                    training_db_path=_trace_mod._resolve_project_paths(project_dir)[0],  # type: ignore[index]
                    artifacts_root=_trace_mod._resolve_project_paths(project_dir)[1],  # type: ignore[index]
                )
                _trace_mod.finalize_sync(handle, final_status="accepted")
            for bn in failed_beats:
                tid = beat_to_trace.get(int(bn))
                if not tid:
                    continue
                handle = _trace_mod.TraceHandle(
                    trace_id=tid,
                    source_run_id=source_run_id,
                    project="",
                    episode_number=args.episode_num,
                    beat_number=bn,
                    training_db_path=_trace_mod._resolve_project_paths(project_dir)[0],  # type: ignore[index]
                    artifacts_root=_trace_mod._resolve_project_paths(project_dir)[1],  # type: ignore[index]
                )
                _trace_mod.finalize_sync(handle, final_status="rejected_by_gate")
            for bn in missing:
                tid = beat_to_trace.get(int(bn))
                if not tid:
                    continue
                handle = _trace_mod.TraceHandle(
                    trace_id=tid,
                    source_run_id=source_run_id,
                    project="",
                    episode_number=args.episode_num,
                    beat_number=bn,
                    training_db_path=_trace_mod._resolve_project_paths(project_dir)[0],  # type: ignore[index]
                    artifacts_root=_trace_mod._resolve_project_paths(project_dir)[1],  # type: ignore[index]
                )
                _trace_mod.finalize_sync(handle, final_status="skipped")
    except Exception:  # noqa: BLE001
        pass

    print(
        json.dumps(
            {
                "ok": True,
                "audit_path": str(audit_path),
                "copied_beats": copied,
                "skipped_failed_beats": failed_beats,
                "missing_source_beats": missing,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
