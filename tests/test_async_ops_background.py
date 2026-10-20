# SPDX-License-Identifier: Elastic-2.0
"""fire-and-forget 任务必须被强引用，并在完成后释放；异常不能静默丢失。"""

from __future__ import annotations

import asyncio
import logging

from novelvideo.utils import async_ops


async def test_spawn_background_holds_reference_until_done() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> None:
        started.set()
        await release.wait()

    task = async_ops.spawn_background(work())
    await started.wait()
    assert task in async_ops._background_tasks

    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in async_ops._background_tasks


def test_spawn_background_logs_failure(caplog) -> None:
    async def main() -> None:
        async def boom() -> None:
            raise RuntimeError("worker exploded")

        task = async_ops.spawn_background(boom())
        await asyncio.sleep(0.01)
        assert task.done()

    with caplog.at_level(logging.ERROR, logger=async_ops.__name__):
        asyncio.run(main())

    assert "background task failed" in caplog.text
