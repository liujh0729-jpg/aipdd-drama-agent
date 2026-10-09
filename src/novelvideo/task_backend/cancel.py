"""Cancellation flags and cooperative cancellation helpers for project tasks."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from novelvideo.ports import get_cancellation_store
from novelvideo.ports.tasks import cancel_key


class TaskCancelled(Exception):
    """Raised when a runner's watcher observes a Redis cancel flag mid-flight.

    Belongs here(not in any specific runner module)so the generic celery
    entry can catch it without importing concrete runners — the entry point
    in `celery_tasks.py:run_project_task` handles this exception to mark the
    task as `cancelled` instead of `failed`.
    """


class TaskTimedOut(Exception):
    """Raised when a project task exceeds its cooperative execution deadline."""

    def __init__(self, *, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or 30 * 60
        super().__init__(self.timeout_seconds)


async def request_cancel(
    *,
    project_id: str,
    task_type: str,
    episode: int,
    task_id: str,
    beat_num: int | None = None,
    scope: str | None = None,
    ttl_seconds: int = 86_400,
) -> None:
    await get_cancellation_store().request_cancel(
        project_id=project_id,
        task_type=task_type,
        episode=episode,
        task_id=task_id,
        beat_num=beat_num,
        scope=scope,
        ttl_seconds=ttl_seconds,
    )


async def is_cancel_requested(
    *,
    project_id: str,
    task_type: str,
    episode: int,
    task_id: str,
    beat_num: int | None = None,
    scope: str | None = None,
) -> bool:
    return await get_cancellation_store().is_cancel_requested(
        project_id=project_id,
        task_type=task_type,
        episode=episode,
        task_id=task_id,
        beat_num=beat_num,
        scope=scope,
    )


def is_cancel_requested_sync(
    *,
    project_id: str,
    task_type: str,
    episode: int,
    task_id: str,
    beat_num: int | None = None,
    scope: str | None = None,
) -> bool:
    """[O4] 同步取消读出口，供热轮询（子进程看门狗等）使用。

    纯同步实现（如 CE 的进程内取消存储）提供
    ``is_cancel_requested_sync``，直接查字典、不建事件循环；其它仅
    提供异步接口的实现（如 Redis）在无线程循环时回落一次短命
    ``asyncio.run``，调用方已处于运行循环内时不阻断（返回 False）。
    """
    fields = {
        "project_id": project_id,
        "task_type": task_type,
        "episode": episode,
        "task_id": task_id,
        "beat_num": beat_num,
        "scope": scope,
    }
    store = get_cancellation_store()
    sync_reader = getattr(store, "is_cancel_requested_sync", None)
    if callable(sync_reader):
        return bool(sync_reader(**fields))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return bool(asyncio.run(is_cancel_requested(**fields)))
        except Exception:
            return False
    return False


async def await_with_cancel_watch(
    coro,
    *,
    project_id: str,
    task_type: str,
    episode: int,
    task_id: str,
    beat_num: int | None = None,
    scope: str | None = None,
    deadline_monotonic: float | None = None,
    timeout_seconds: int | None = None,
    poll_seconds: float = 0.5,
):
    """Await a coroutine while polling the project task cancel flag.

    Celery revoke/terminate is process-level and may not interrupt an active
    async HTTP await quickly. Async runners should wrap their long-running
    coroutine with this helper so user cancellation raises TaskCancelled.
    """
    if not task_id and deadline_monotonic is None:
        return await coro

    main_task = asyncio.create_task(coro)
    stop_reason: str | None = None

    async def _watch() -> None:
        nonlocal stop_reason
        try:
            while not main_task.done():
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    stop_reason = "timeout"
                    main_task.cancel()
                    return
                try:
                    cancelled = (
                        await is_cancel_requested(
                            project_id=project_id,
                            task_type=task_type,
                            episode=episode,
                            task_id=task_id,
                            beat_num=beat_num,
                            scope=scope,
                        )
                        if task_id
                        else False
                    )
                except Exception:
                    cancelled = False
                if cancelled:
                    stop_reason = "cancelled"
                    main_task.cancel()
                    return
                sleep_seconds = poll_seconds
                if deadline_monotonic is not None:
                    sleep_seconds = min(
                        sleep_seconds,
                        max(deadline_monotonic - time.monotonic(), 0.0),
                    )
                await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            pass

    watcher = asyncio.create_task(_watch())
    try:
        return await main_task
    except asyncio.CancelledError:
        if stop_reason == "timeout":
            raise TaskTimedOut(timeout_seconds=timeout_seconds)
        raise TaskCancelled()
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def remaining_timeout_seconds(
    envelope: dict[str, Any],
    *,
    default_seconds: int | None = None,
) -> int | None:
    """Return seconds remaining before this task's cooperative deadline."""
    deadline_monotonic = _optional_float(envelope.get("__deadline_monotonic"))
    timeout_seconds = _optional_int(envelope.get("__timeout_seconds"))
    if deadline_monotonic is None:
        return default_seconds
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TaskTimedOut(timeout_seconds=timeout_seconds)
    deadline_seconds = max(math.ceil(remaining), 1)
    if default_seconds is None:
        return deadline_seconds
    return max(min(int(default_seconds), deadline_seconds), 1)


def _envelope_cancel_fields(
    envelope: dict[str, Any],
    *,
    task_type: str | None = None,
    episode: int | None = None,
    beat_num: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    payload = envelope.get("payload") or {}
    resolved_task_type = str(task_type or envelope.get("task_type") or "")
    resolved_episode = int(
        episode
        if episode is not None
        else envelope.get("episode") or payload.get("episode") or 0
    )
    resolved_beat_num = (
        beat_num
        if beat_num is not None
        else _optional_int(envelope.get("beat_num"))
        if envelope.get("beat_num") is not None
        else _optional_int(payload.get("beat_num"))
    )
    resolved_scope = str(
        scope
        if scope is not None
        else envelope.get("scope") or payload.get("scope") or payload.get("job_id") or ""
    )
    return {
        "project_id": str(envelope.get("project_id") or ""),
        "task_type": resolved_task_type,
        "episode": resolved_episode,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "beat_num": resolved_beat_num,
        "scope": resolved_scope or None,
    }


async def await_envelope_with_cancel_watch(
    coro,
    envelope: dict[str, Any],
    *,
    task_type: str | None = None,
    episode: int | None = None,
    beat_num: int | None = None,
    scope: str | None = None,
):
    """Await a runner coroutine using the Celery envelope cancel identity."""
    fields = _envelope_cancel_fields(
        envelope,
        task_type=task_type,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    return await await_with_cancel_watch(
        coro,
        **fields,
        deadline_monotonic=_optional_float(envelope.get("__deadline_monotonic")),
        timeout_seconds=_optional_int(envelope.get("__timeout_seconds")),
    )


def raise_if_envelope_cancel_requested(
    envelope: dict[str, Any],
    *,
    task_type: str | None = None,
    episode: int | None = None,
    beat_num: int | None = None,
    scope: str | None = None,
) -> None:
    """Synchronous cancellation checkpoint for non-async runner boundaries."""
    deadline_monotonic = _optional_float(envelope.get("__deadline_monotonic"))
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TaskTimedOut(timeout_seconds=_optional_int(envelope.get("__timeout_seconds")))

    fields = _envelope_cancel_fields(
        envelope,
        task_type=task_type,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if not fields["task_id"]:
        return
    if is_cancel_requested_sync(**fields):
        raise TaskCancelled()
