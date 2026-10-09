"""Read / write `convergence_rounds` — per-episode / per-phase pass history.

Feeds the `show_trend` CLI and the upcoming convergence loop controller.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite


async def next_round_num(
    db: aiosqlite.Connection,
    episode_num: int,
    phase: str,
) -> int:
    """Return the next round number for this episode+phase (1-based)."""
    async with db.execute(
        "SELECT COALESCE(MAX(round_num), 0) FROM convergence_rounds "
        "WHERE episode_number = ? AND phase = ?",
        (int(episode_num), phase),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0) + 1


async def write_round(
    db: aiosqlite.Connection,
    *,
    episode_num: int,
    phase: str,
    residual_count: int,
    fixed_count: int,
    new_failures: list[str] | None = None,
    round_num: int | None = None,
) -> int:
    """Insert a round record. Returns the round_num actually written."""
    if round_num is None:
        round_num = await next_round_num(db, episode_num, phase)
    cursor = await db.execute(
        """
        INSERT INTO convergence_rounds (
            episode_number, phase, round_num,
            residual_count, fixed_count, new_failures_json,
            started_at, ended_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(episode_num),
            phase,
            int(round_num),
            int(residual_count),
            int(fixed_count),
            json.dumps(new_failures or [], ensure_ascii=False),
        ),
    )
    await db.commit()
    await cursor.close()
    return round_num


async def query_trend(
    db: aiosqlite.Connection,
    episode_num: int | None = None,
) -> list[dict[str, Any]]:
    if episode_num is not None:
        query = (
            "SELECT * FROM convergence_rounds WHERE episode_number = ? "
            "ORDER BY episode_number, phase, round_num"
        )
        params: tuple[Any, ...] = (int(episode_num),)
    else:
        query = (
            "SELECT * FROM convergence_rounds "
            "ORDER BY episode_number, phase, round_num"
        )
        params = ()
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        raw = data.get("new_failures_json") or "[]"
        try:
            data["new_failures"] = json.loads(raw)
        except (TypeError, ValueError):
            data["new_failures"] = []
        result.append(data)
    return result


def format_trend_markdown(trend: list[dict[str, Any]]) -> str:
    """Render the trend list as a scannable markdown table."""
    if not trend:
        return "_no convergence rounds recorded yet_"
    header = "| ep | phase | round | residual | fixed | new_failures | started_at |"
    divider = "|----|-------|-------|----------|-------|--------------|------------|"
    lines = [header, divider]
    for row in trend:
        failures = ", ".join(row.get("new_failures") or []) or "-"
        lines.append(
            "| {ep} | {phase} | {round} | {residual} | {fixed} | {failures} | {started} |".format(
                ep=row["episode_number"],
                phase=row["phase"],
                round=row["round_num"],
                residual=row["residual_count"],
                fixed=row["fixed_count"],
                failures=failures,
                started=row.get("started_at") or "-",
            )
        )
    return "\n".join(lines)
