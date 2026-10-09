"""CLI: print the convergence_rounds table as markdown.

Usage:
    uv run python -m novelvideo.verification.cli.show_trend \
        <project_dir> [--episode-num 1]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiosqlite

from novelvideo.verification import convergence_log


def _resolve_db_path(project_dir: Path) -> Path:
    from novelvideo.utils.project_paths import ProjectPaths

    parts = project_dir.resolve().parts
    user, project = parts[-2], parts[-1]
    return ProjectPaths(user, project).data_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print sketch-edit convergence trend.")
    parser.add_argument("project_dir", help="SuperTale project directory")
    parser.add_argument(
        "--episode-num",
        type=int,
        default=None,
        help="Filter to one episode; omit to show all",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    project_dir = Path(args.project_dir).expanduser().resolve()
    db_path = _resolve_db_path(project_dir)
    if not db_path.exists():
        print(f"missing data.db: {db_path}")
        return 2
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        # convergence_rounds is project-local; failure-mode defs are no
        # longer seeded against this DB (they live in user-shared
        # verification.db as of phase 2).
        trend = await convergence_log.query_trend(db, episode_num=args.episode_num)
    print(convergence_log.format_trend_markdown(trend))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
