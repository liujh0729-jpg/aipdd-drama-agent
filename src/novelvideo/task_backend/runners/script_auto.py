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

from novelvideo.i18n_message import MessageLike, log_entry_payload, lmsg
from novelvideo.novel_source import require_imported_novel
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.runners.graph_build import (
    Reporter,
    _load_store,
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


async def _cast_is_empty(ctx: ProjectContext) -> bool:
    """Whether the project really has no cast, as opposed to gaining none this round.

    A build that returns zero characters is either a model that answered with nothing
    or a re-run over a cast that is already complete, and only the first is worth a
    warning line. The store is opened on the zero path alone, so a normal run pays
    nothing for the check; anything that goes wrong while confirming counts as
    "cannot prove a gap", which stays quiet rather than crying wolf.
    """
    try:
        store = await _load_store(ctx)
    except Exception:  # noqa: BLE001 - unconfirmed is not a gap
        return False
    try:
        return not await store.list_characters()
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            await store.close()
        except Exception:  # noqa: BLE001
            pass


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
    # Zero new characters only means the cast page is empty if nothing was there before.
    cast_gap = int(added.get("characters") or 0) == 0 and await _cast_is_empty(ctx)
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

    # 派生步骤失败时整链仍然是 completed（前面发布的资产不该被推翻），但终态不能
    # 谎报成一句「自动化完成」：现场见过整轮跑完、进度条 100%、图谱却是空的，用户
    # 只会认为按钮没生效。缺哪一段就写在最后一行上，任务中心不用展开日志也能看懂。
    # 分档按「越靠前越根本」取一条，不把已本地化的标签拼进另一条本地化消息里。
    scenes_gap = isinstance(results.get("scenes"), dict) and bool(
        results["scenes"].get("error")
    )
    graph = results.get("graph")
    graph_gap = isinstance(graph, dict) and bool(
        graph.get("error") or graph.get("published") is False
    )
    if cast_gap:
        # The cast is the step every other page hangs off; when it comes back empty the
        # gap is the model answer, not the scene or graph rows underneath it.
        terminal = lmsg(
            "tasks.progress.auto.doneGapCast",
            "自动化完成，但未提取到角色，请检查「设置 → 模型与渠道」的文本模型后在剧本页重试",
        )
    elif scenes_gap and graph_gap:
        terminal = lmsg(
            "tasks.progress.auto.doneGapsBoth",
            "自动化完成，但场景与知识图谱未生成，可在对应页面单独重试",
        )
    elif graph_gap:
        terminal = lmsg(
            "tasks.progress.auto.doneGapGraph",
            "自动化完成，但知识图谱未生成，可在剧本页点击「生成图谱」单独重试",
        )
    elif scenes_gap:
        terminal = lmsg(
            "tasks.progress.auto.doneGapScenes",
            "自动化完成，但场景未生成，可在场景页单独重试",
        )
    else:
        terminal = lmsg("tasks.progress.auto.done", "自动化完成")
    report(1.0, terminal)
    # The completion write would otherwise overwrite the row's headline with 「完成」
    # and this verdict would only survive inside the log tab. Hand it to the runner
    # core instead, which keeps it as the closing line on the completed row.
    results["completion_message"] = log_entry_payload(terminal)
    return results


register_project_task_runner(TASK_TYPE, run_script_auto)
