"""One-shot migration: mirror legacy project `sketch_failure_modes` into
the user-shared `verification.db`.

Phase 1 stored failure-mode definitions inside each project's
`data.db`. Phase 2 moves canonical defs to the user-shared
`verification.db`. For runtime compatibility during the transition,
`failure_registry` can double-read (defs preferred in the shared DB,
legacy fallback from project tables), but `registry_version` only
hashes the shared DB. Leaving definitions split across projects
therefore means different projects compute different registry
versions for the same active policy — silent training-data poison.

Running this CLI once after shipping phase 2 mirrors every known
project's legacy defs into the shared DB so the fallback double-read
becomes a dead code path, and `registry_version` becomes consistent
across projects.

Usage::

    uv run python -m novelvideo.verification.cli.seed_mirror_once \
        --user admin
    # or auto-detect all users under STATE_DIR:
    uv run python -m novelvideo.verification.cli.seed_mirror_once --all-users
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiosqlite

from novelvideo.config import STATE_DIR
from novelvideo.utils.project_paths import ProjectPaths
from novelvideo.verification import failure_registry
from novelvideo.verification.global_registry_db import open_defs_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror legacy per-project failure defs into user-shared verification.db.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="Single user id under STATE_DIR to process")
    group.add_argument("--all-users", action="store_true", help="Scan every user under STATE_DIR")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be mirrored without writing",
    )
    return parser.parse_args()


def _discover_users() -> list[str]:
    root = Path(STATE_DIR)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))


def _discover_projects(user: str) -> list[str]:
    root = Path(STATE_DIR) / user
    if not root.exists():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and not p.name.startswith("_")
        and (p / "data.db").exists()
    )


async def _mirror_one_project(
    user: str,
    project: str,
    dry_run: bool,
) -> tuple[int, list[str]]:
    """Return (rows_mirrored, new_codes_list)."""
    paths = ProjectPaths(user, project)
    project_db_path = paths.data_db
    defs_db_path = paths.global_shared_verification_db
    if not project_db_path.exists():
        return 0, []

    async with aiosqlite.connect(str(project_db_path)) as project_db:
        project_db.row_factory = aiosqlite.Row
        legacy_rows = await failure_registry.read_legacy_defs(project_db)

    if not legacy_rows:
        return 0, []

    defs_db = await open_defs_db(defs_db_path)
    try:
        new_codes: list[str] = []
        mirrored = 0
        for row in legacy_rows:
            code = row.get("code")
            if not code:
                continue
            existing = await failure_registry.get_by_code(defs_db, code)
            if existing is None:
                new_codes.append(code)
            if dry_run:
                mirrored += 1
                continue
            await failure_registry.upsert(
                defs_db,
                code,
                layer=row.get("layer") or "correction",
                detection=row.get("detection") or "",
                prevention_rule=row.get("prevention_rule") or "",
                correction_template=row.get("correction_template") or "",
                negative_prompt_clause=row.get("negative_prompt_clause") or "",
                gate_enabled=int(row.get("gate_enabled") or 0),
                fixture_path=row.get("fixture_path") or "",
            )
            mirrored += 1
        return mirrored, new_codes
    finally:
        await defs_db.close()


async def main_async() -> int:
    args = parse_args()
    users = [args.user] if args.user else _discover_users()
    if not users:
        print(json.dumps({"ok": False, "error": "no users found"}))
        return 2

    summary: dict[str, dict] = {}
    total_rows = 0
    for user in users:
        projects = _discover_projects(user)
        user_summary: dict[str, dict] = {}
        for project in projects:
            rows, new_codes = await _mirror_one_project(user, project, args.dry_run)
            user_summary[project] = {
                "rows_mirrored": rows,
                "new_codes": new_codes,
            }
            total_rows += rows
        summary[user] = user_summary

    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": args.dry_run,
                "total_rows_mirrored": total_rows,
                "by_user": summary,
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
