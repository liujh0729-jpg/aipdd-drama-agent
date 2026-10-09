"""CLI: record a human override (accept / veto) against a live_edit_traces row.

Two lookup modes:

1. Direct by `--trace-id` — precise.
2. By `--source-run-id --beat N` — convenient when reviewing a run
   directly from a summary.json. Useful in pipelines where a beat
   passed the gate but a human still wants to veto (or vice versa).

Writes one row to `human_override_events` (audit log) and mirrors the
summarized `human_override_status` / `human_override_reason` onto the
trace's main row (single source of truth). This is the capture point
that turns tacit human judgments into first-class training data —
without this, nobody can tell the difference between "gate passed and
human accepted" vs. "gate passed and human silently overrode".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a human override on a trace row.")
    parser.add_argument("project_dir", help="SuperTale project directory")
    parser.add_argument("--verdict", required=True, choices=["accept", "veto"])
    parser.add_argument("--reason", default="", help="Short free-text rationale")
    parser.add_argument("--actor", default="", help="Who is making the call (email / username)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trace-id", help="Direct trace_id to target")
    group.add_argument(
        "--source-run-id",
        help="Run scope; combined with --beat to resolve trace_id",
    )
    parser.add_argument("--beat", type=int, help="Beat number (with --source-run-id)")
    return parser.parse_args()


async def _resolve_trace_id(
    project_dir: Path,
    args: argparse.Namespace,
) -> str | None:
    if args.trace_id:
        return args.trace_id
    if not args.beat:
        return None
    from novelvideo.verification import replay_capture

    mapping = await replay_capture.find_traces_for_run(
        project_dir=project_dir, source_run_id=args.source_run_id,
    )
    return mapping.get(int(args.beat))


async def main_async() -> int:
    args = parse_args()
    project_dir = Path(args.project_dir).expanduser().resolve()
    trace_id = await _resolve_trace_id(project_dir, args)
    if not trace_id:
        print(json.dumps({"ok": False, "error": "could not resolve trace_id"}))
        return 2

    from novelvideo.verification import replay_capture

    try:
        await replay_capture.record_override(
            project_dir=project_dir,
            trace_id=trace_id,
            verdict=args.verdict,
            reason=args.reason or None,
            actor=args.actor or None,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "trace_id": trace_id,
                "verdict": args.verdict,
                "reason": args.reason,
                "actor": args.actor,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
