"""One-click script automation: imported text to a usable asset set.

Import has always stopped at splitting the text. Everything after it — cast,
episodes, scenes, graph — was four separate buttons on four separate pages, and a
project that never got pressed through them showed an empty knowledge graph and no
assets, which reads as breakage rather than as work not yet done.

This runner chains the existing builds under a single task type. It does not
reimplement any of them: it calls the same structured/legacy helpers the individual
buttons call and hands each one a reporter that maps its 0..1 progress onto a slice
of this task's bar. Progress stays under ``script_auto`` so the task center does not
gain a phantom ``build_characters`` row every time someone clicks one button.

Steps are split by how much the rest of the flow depends on them. The cast and the
episode split are foundational — a failure there aborts, because later pages would
show assets no episode can reference. Scenes and the graph are derived views over
text that already landed, so they degrade to a logged warning instead of discarding
work the earlier steps published.
"""

from __future__ import annotations

from typing import Any, Optional

from novelvideo.i18n_message import MessageLike, lmsg
from novelvideo.novel_source import require_imported_novel
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.runners.graph_build import (
    Reporter,
    _reporter,
    _run_async,
    _run_build_characters,
    _run_build_episodes,
    _run_build_props,
    _run_build_scenes,
    _run_graph_preview,
)

TASK_TYPE = "script_auto"

# Share of the bar each step owns. Characters dominate — they sweep the whole text.
# Scenes only read headings, props do no work at all up front, and the graph replays
# the chunk results the character pass already paid for whenever the cache holds.
_STEP_WINDOWS: dict[str, tuple[float, float]] = {
    "characters": (0.00, 0.45),
    "episodes": (0.45, 0.60),
    "scenes": (0.60, 0.82),
    "props": (0.82, 0.84),
    "graph": (0.84, 1.00),
}

_STEP_TITLES: dict[str, MessageLike] = {
    "characters": lmsg("tasks.progress.auto.characters", "第 1 步 · 提取角色..."),
    "episodes": lmsg("tasks.progress.auto.episodes", "第 2 步 · 规划分集..."),
    "scenes": lmsg("tasks.progress.auto.scenes", "第 3 步 · 提取场景..."),
    "props": lmsg("tasks.progress.auto.props", "第 4 步 · 道具..."),
    "graph": lmsg("tasks.progress.auto.graph", "第 5 步 · 生成知识图谱..."),
}


def _step_reporter(report: Reporter, step: str) -> Reporter:
    """Scale one step's progress into its slice of the overall bar.

    ``progress=None`` still means 'log line only, leave the bar alone' — the same
    contract the per-type runners use, so a log row cannot yank the bar backwards.
    """
    low, high = _STEP_WINDOWS[step]

    def stepped(progress: Optional[float], task: MessageLike) -> None:
        if progress is None:
            report(None, task)
            return
        bounded = max(0.0, min(float(progress), 1.0))
        report(low + (high - low) * bounded, task)

    return stepped


def run_script_auto(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_script_auto(envelope, ctx), envelope, TASK_TYPE)


async def _run_script_auto(
    envelope: dict[str, Any],
    ctx: ProjectContext,
    *,
    task_type: str = TASK_TYPE,
) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)

    report = _reporter(ctx, task_type)
    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    # Deterministic chapter/episode mapping is the only mode both tracks offer without
    # a Cognee graph, and it is what the episodes route accepts for these projects.
    planning_mode = str(config.get("planning_mode") or "chapters")

    results: dict[str, Any] = {}

    # ── 1. Cast ────────────────────────────────────────────────────────────────
    report(_STEP_WINDOWS["characters"][0], _STEP_TITLES["characters"])
    added = await _run_build_characters(ctx, report=_step_reporter(report, "characters"))
    results["characters"] = added
    report(
        None,
        lmsg(
            "tasks.log.auto.charactersDone",
            f"角色完成：新增 {added.get('added_characters', 0)} 个",
            addedCount=added.get("added_characters", 0),
        ),
    )

    # ── 2. Episodes ────────────────────────────────────────────────────────────
    report(_STEP_WINDOWS["episodes"][0], _STEP_TITLES["episodes"])
    results["episodes"] = await _run_build_episodes(
        {
            "payload": {
                "config": {
                    **config,
                    "planning_mode": planning_mode,
                    "generate_metadata": bool(config.get("generate_metadata", False)),
                }
            }
        },
        ctx,
        report=_step_reporter(report, "episodes"),
    )

    # ── 3. Scenes ──────────────────────────────────────────────────────────────
    report(_STEP_WINDOWS["scenes"][0], _STEP_TITLES["scenes"])
    scenes_report = _step_reporter(report, "scenes")
    try:
        results["scenes"] = await _run_build_scenes(ctx, report=scenes_report)
    except Exception as exc:  # noqa: BLE001 - scenes are derived, never fatal
        results["scenes"] = {"error": str(exc)[:500]}
        report(
            None,
            lmsg(
                "tasks.log.auto.scenesFailed",
                f"⚠️ 场景提取未完成，可稍后单独重试：{exc}",
                error=str(exc),
            ),
        )

    # ── 4. Props ───────────────────────────────────────────────────────────────
    report(_STEP_WINDOWS["props"][0], _STEP_TITLES["props"])
    results["props"] = await _run_build_props(ctx, report=_step_reporter(report, "props"))

    # ── 5. Knowledge graph ─────────────────────────────────────────────────────
    report(_STEP_WINDOWS["graph"][0], _STEP_TITLES["graph"])
    try:
        results["graph"] = await _run_graph_preview(
            ctx, report=_step_reporter(report, "graph")
        )
    except Exception as exc:  # noqa: BLE001 - the graph is a view, not an asset
        results["graph"] = {"published": False, "error": str(exc)[:500]}
        report(
            None,
            lmsg(
                "tasks.log.auto.graphFailed",
                f"⚠️ 知识图谱未生成，可点击「生成图谱」单独重试：{exc}",
                error=str(exc),
            ),
        )

    report(1.0, lmsg("tasks.progress.auto.done", "自动化完成"))
    return results


register_project_task_runner(TASK_TYPE, run_script_auto)
