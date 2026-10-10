"""Celery runners for graph-to-SQLite build steps."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from novelvideo.i18n_message import LocalizableMessage, MessageLike, log_entry_payload
from novelvideo.model_gateway_runtime import model_gateway_scope_for_runner
from novelvideo.novel_source import require_imported_novel
from novelvideo.knowledge_pipeline import (
    KnowledgePipelineUnsupported,
    is_structured_pipeline,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _run_async(coro, envelope: dict[str, Any], task_type: str):
    with model_gateway_scope_for_runner(envelope):
        return asyncio.run(
            await_envelope_with_cancel_watch(coro, envelope, task_type=task_type)
        )


def _progress(
    ctx: ProjectContext, task_type: str, progress: float | None, task: MessageLike
) -> None:
    """进度/日志上报。

    `progress=None` 表示「只更新步骤文案和日志，保留原有进度」。普通日志行必须
    走这条路径：图谱构建的 on_log 回调本身不带进度，早期用 0.0 占位会让前端进
    度条在 10% → 0% → 80% → 0% 之间反复倒退。
    """
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        0,
        progress=progress,
        current_task=task,
        logs=[task],
    )


Reporter = Callable[[Optional[float], MessageLike], None]


def _reporter(ctx: ProjectContext, task_type: str) -> Reporter:
    """Bind progress reporting to one task type.

    Callers that own several steps take this and wrap it, so a chained runner can
    scale each step into a slice of one bar instead of driving it 0 → 100 per step.
    """

    def report(progress: Optional[float], task: MessageLike) -> None:
        _progress(ctx, task_type, progress, task)

    return report


async def _load_store(ctx: ProjectContext):
    """Open the project store its knowledge pipeline calls for.

    structured_v1 builds never touch the graph, so they use SQLiteStore directly
    rather than reaching through the Cognee facade.
    """
    if is_structured_pipeline(ctx.state_dir):
        from novelvideo.sqlite_store import SQLiteStore

        store = SQLiteStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
    else:
        from novelvideo.cognee import CogneeStore

        store = CogneeStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
    await store.initialize()
    await store.load_graph_state()
    return store


def run_build_characters(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_build_characters(ctx), envelope, "build_characters")


async def _run_build_characters(
    ctx: ProjectContext, *, report: Optional[Reporter] = None
) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    report = report or _reporter(ctx, "build_characters")
    store = await _load_store(ctx)
    try:
        def on_log(message: MessageLike) -> None:
            report(None, message)
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_characters_structured

            added = await build_characters_structured(
                store, on_progress=report, on_log=on_log
            )
            return {"characters": len(added), "added_characters": len(added)}

        characters = await store.build_characters_from_graph(
            on_progress=report,
            on_log=on_log,
        )
        return {"characters": len(characters), "added_characters": len(characters)}
    finally:
        await store.close()


def run_build_scenes(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_build_scenes(ctx), envelope, "build_scenes")


async def _run_build_scenes(
    ctx: ProjectContext, *, report: Optional[Reporter] = None
) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    # The API rejects at enqueue; this is the final defence against state races
    # and producers that never went through HTTP. Only a *running* planner
    # blocks, so a build arriving against a queued planner is not turned away
    # for nothing. Two tasks that reach their gates together can still both
    # refuse — narrow, writes nothing, and retrying clears it.
    from novelvideo.scene_prerequisites import (
        ScenePlanningRunningError,
        running_scene_planner,
    )
    from novelvideo.task_state import get_task_manager

    if running_scene_planner(get_task_manager().list_tasks_for_project(ctx)):
        raise ScenePlanningRunningError()
    report = report or _reporter(ctx, "build_scenes")
    store = await _load_store(ctx)
    try:
        def on_log(message: MessageLike) -> None:
            report(None, message)
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_scenes_structured

            return await build_scenes_structured(
                store, on_progress=report, on_log=on_log
            )

        scenes = await store.build_scenes_from_graph(
            on_progress=report,
            on_log=on_log,
        )
        return {"scenes": len(scenes), "added_scenes": len(scenes)}
    finally:
        await store.close()


def run_build_props(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_build_props(ctx), envelope, "build_props")


async def _run_build_props(
    ctx: ProjectContext, *, report: Optional[Reporter] = None
) -> dict[str, Any]:
    report = report or _reporter(ctx, "build_props")
    store = await _load_store(ctx)
    try:
        def on_log(message: MessageLike) -> None:
            report(None, message)
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_props_structured

            return await build_props_structured(
                store, on_progress=report, on_log=on_log
            )

        props = await store.build_props_from_graph(
            on_progress=report,
            on_log=on_log,
        )
        return {"props": len(props)}
    finally:
        await store.close()


def run_build_episodes(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_build_episodes(envelope, ctx), envelope, "build_episodes")


async def _run_build_episodes(
    envelope: dict[str, Any],
    ctx: ProjectContext,
    *,
    report: Optional[Reporter] = None,
) -> dict[str, Any]:
    from novelvideo.agents.episode_planner import EpisodePlannerAgent

    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    target = int(config.get("target_episodes", 10))
    use_agent = bool(config.get("use_agent_planner", True))
    planning_mode = str(config.get("planning_mode", "ai"))
    generate_metadata = bool(config.get("generate_metadata", False))
    require_imported_novel(ctx.output_dir)
    # Defence in depth: the route rejects AI modes for structured projects
    # before enqueue, but a task queued before the project's track was known
    # must not reach a planner that needs the graph.
    if is_structured_pipeline(ctx.state_dir) and planning_mode != "chapters":
        raise KnowledgePipelineUnsupported(
            "structured_v1 only supports deterministic chapter/episode mapping"
        )
    store = await _load_store(ctx)
    try:
        report = report or _reporter(ctx, "build_episodes")

        def update(progress: float | None, task: MessageLike) -> None:
            report(progress, task)

        if planning_mode == "chapters":
            episodes = await store.build_episodes_from_chapters(
                generate_metadata=generate_metadata,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        elif planning_mode == "ai_events":
            episodes = await store.build_episodes_from_events(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        elif use_agent:
            try:
                planner = EpisodePlannerAgent(store)
                episodes = await planner.plan_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(None, message),
                )
            except Exception:
                episodes = await store.build_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(None, message),
                )
            else:
                await store.replace_episodes(episodes)
        else:
            episodes = await store.build_episodes(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        return {"episodes": len(episodes)}
    finally:
        await store.close()


def run_graph_preview(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return _run_async(_run_graph_preview_standalone(ctx), envelope, "graph_preview")


async def _run_graph_preview_standalone(ctx: ProjectContext) -> dict[str, Any]:
    """「生成图谱」按钮单独跑时，收尾那行字要留在终态上。

    这个按钮正是一键自动化说「图谱未生成」之后用户会点的那个。抽取无结果时
    builder 已经把进度打到 100% 并写了「图谱抽取无结果」，但 core 收尾统一盖成
    「完成」，用户看到的仍是一个成功的空结果。把最后一行 100% 的文案交给 core，
    终态就说了真话；legacy 轨没有 100% 的上报，照旧回落「完成」。
    """
    verdict: dict[str, MessageLike] = {}
    inner = _reporter(ctx, "graph_preview")

    def report(progress: Optional[float], task: MessageLike) -> None:
        if progress is not None and float(progress) >= 1.0:
            verdict["line"] = task
        inner(progress, task)

    result = await _run_graph_preview(ctx, report=report)
    line = verdict.get("line")
    if isinstance(line, LocalizableMessage):
        from novelvideo.task_backend.run_core import COMPLETION_MESSAGE_KEY

        result[COMPLETION_MESSAGE_KEY] = log_entry_payload(line)
    return result


async def _run_graph_preview(
    ctx: ProjectContext, *, report: Optional[Reporter] = None
) -> dict[str, Any]:
    """Materialize the graph preview sidecar the ingest page renders.

    The two tracks get the preview from opposite directions: legacy reads it out
    of the embedded graph it already built, structured has no graph to read and
    must extract one from the source text.
    """
    require_imported_novel(ctx.output_dir)
    report = report or _reporter(ctx, "graph_preview")
    store = await _load_store(ctx)
    try:
        def on_log(message: MessageLike) -> None:
            report(None, message)

        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_graph import build_graph_structured

            snapshot = await build_graph_structured(
                store, on_progress=report, on_log=on_log
            )
            return {
                "nodes": len(snapshot.get("nodes") or []),
                "edges": len(snapshot.get("edges") or []),
                "published": bool(snapshot.get("published")),
            }

        snapshot = await store.materialize_graph_preview()
        return {
            "nodes": len(snapshot.get("nodes") or []),
            "edges": len(snapshot.get("edges") or []),
            "published": True,
        }
    finally:
        await store.close()


register_project_task_runner("build_characters", run_build_characters)
register_project_task_runner("build_scenes", run_build_scenes)
register_project_task_runner("build_props", run_build_props)
register_project_task_runner("build_episodes", run_build_episodes)
register_project_task_runner("graph_preview", run_graph_preview)
