import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import anyio
from anyio.lowlevel import RunVar


logger = logging.getLogger(__name__)

# 事件循环只弱引用 Task；不持有强引用时，未完成的 fire-and-forget 任务可能
# 在运行中被 GC 掉，表现为「任务刚开始就悄无声息地消失」。
_background_tasks: set[asyncio.Task[Any]] = set()


def _finish_background_task(task: asyncio.Task[Any]) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("background task failed", exc_info=error)


def spawn_background(
    coro: Coroutine[Any, Any, Any], *, name: str = ""
) -> asyncio.Task[Any]:
    """Launch a background task and keep it strongly referenced until done."""

    task = asyncio.create_task(coro, name=name or None)
    _background_tasks.add(task)
    task.add_done_callback(_finish_background_task)
    return task


METADATA_IO_CONCURRENCY = 4
_metadata_io_limiter_var: RunVar[anyio.CapacityLimiter] = RunVar(
    "metadata_io_limiter"
)


def metadata_io_limiter() -> anyio.CapacityLimiter:
    """Return the lightweight metadata/file-operation gate for this async run."""

    limiter = _metadata_io_limiter_var.get(None)
    if limiter is None:
        limiter = anyio.CapacityLimiter(METADATA_IO_CONCURRENCY)
        _metadata_io_limiter_var.set(limiter)
    return limiter


async def wait_for_task_completion(
    task: asyncio.Task,
    cancellation: asyncio.CancelledError | None = None,
) -> tuple[Any, asyncio.CancelledError | None]:
    """Wait through repeated cancellation, preserving the first cancellation."""

    while not task.done():
        try:
            if cancellation is None:
                await asyncio.shield(task)
            else:
                with anyio.CancelScope(shield=True):
                    await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
        except BaseException:
            if cancellation is not None:
                raise cancellation
            raise
    try:
        return task.result(), cancellation
    except BaseException:
        if cancellation is not None:
            raise cancellation
        raise


async def run_sync_bounded(
    function: Callable[..., Any],
    /,
    *args: Any,
    limiter: anyio.CapacityLimiter,
    finalize: Callable[[Any], Awaitable[Any]] | None = None,
    shield: bool = True,
    **kwargs: Any,
) -> Any:
    """Run bounded blocking work with optional transactional cancellation shielding.

    When ``shield`` is true, cancellation is delayed while waiting for capacity
    and until the worker and optional finalizer complete. When false, waiting
    for capacity is cancellable; once admitted, the operation retains its
    limiter token until the worker and optional finalizer finish. If a finalizer
    is supplied, its return value is the operation result.
    """

    bound = functools.partial(function, *args, **kwargs)
    if shield:
        worker_task = asyncio.create_task(
            anyio.to_thread.run_sync(
                bound,
                abandon_on_cancel=False,
                limiter=limiter,
            )
        )
        result, cancellation = await wait_for_task_completion(worker_task)
    else:
        await limiter.acquire()
        try:
            worker_task = asyncio.create_task(asyncio.to_thread(bound))
            result, cancellation = await wait_for_task_completion(worker_task)
            if finalize is not None:
                finalize_task = asyncio.create_task(finalize(result))
                result, cancellation = await wait_for_task_completion(
                    finalize_task, cancellation
                )
        finally:
            limiter.release()
        if cancellation is not None:
            raise cancellation
        return result
    if finalize is not None:
        finalize_task = asyncio.create_task(finalize(result))
        result, cancellation = await wait_for_task_completion(
            finalize_task, cancellation
        )
    if cancellation is not None:
        raise cancellation
    return result


async def call_blocking(func: Callable[..., Any], /, *args, **kwargs) -> Any:
    """Run a blocking callable in the default thread pool."""
    bound = functools.partial(func, *args, **kwargs)
    return await asyncio.to_thread(bound)
