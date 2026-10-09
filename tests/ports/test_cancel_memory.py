import asyncio

import pytest

from novelvideo.ports.local.tasks import InMemoryCancellationStore
from novelvideo.ports.tasks import cancel_key


def test_cancel_key_matches_existing_shape() -> None:
    assert (
        cancel_key(
            project_id="p1",
            task_type="render",
            episode=2,
            beat_num=3,
            scope="scene",
            task_id="t1",
        )
        == "task:cancel:p1:render:2:3:scene:t1"
    )


@pytest.mark.asyncio
async def test_memory_cancellation_store_can_write_read_and_isolate_keys() -> None:
    store = InMemoryCancellationStore()

    await store.request_cancel(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
    )

    assert await store.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
    )
    assert not await store.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="different",
        beat_num=3,
    )


def test_memory_store_sync_read_path_without_event_loop() -> None:
    """[O4] 同步读出口可直接查字典，无需事件循环（看门狗热路）。"""
    store = InMemoryCancellationStore()

    async def _write() -> None:
        await store.request_cancel(
            project_id="p1",
            task_type="render",
            episode=2,
            beat_num=3,
            scope="scene",
            task_id="t1",
        )

    asyncio.run(_write())
    # 无 running loop 环境下同步读命中
    assert store.is_cancel_requested_sync(
        project_id="p1",
        task_type="render",
        episode=2,
        beat_num=3,
        scope="scene",
        task_id="t1",
    ) is True
    # 未取消的 key 返回 False
    assert store.is_cancel_requested_sync(
        project_id="p1",
        task_type="render",
        episode=2,
        beat_num=3,
        scope="scene",
        task_id="t2",
    ) is False


@pytest.mark.asyncio
async def test_memory_store_async_read_delegates_to_sync_path() -> None:
    store = InMemoryCancellationStore()
    await store.request_cancel(
        project_id="p1",
        task_type="render",
        episode=1,
        task_id="t1",
    )
    assert (
        await store.is_cancel_requested(
            project_id="p1",
            task_type="render",
            episode=1,
            task_id="t1",
        )
        is True
    )


def test_prune_expired_keys_on_write_above_threshold() -> None:
    """[O1] 写路径超阈值时清扫过期键，但绝不淘汰未过期（活）键。"""
    import time

    store = InMemoryCancellationStore()
    past = time.time() - 10
    future = time.time() + 1000
    for i in range(4100):
        store._keys[f"stale-{i}"] = past
    # 播种一个尚未过期的键，必须存活
    store._keys["must-survive"] = future
    assert len(store._keys) == 4101

    asyncio.run(
        store.request_cancel(
            project_id="p1", task_type="render", episode=1,
            task_id="live", ttl_seconds=100,
        )
    )
    assert not any(k.startswith("stale-") for k in store._keys)
    assert "must-survive" in store._keys  # 未过期项不被误删
    assert any(k.endswith(":live") for k in store._keys)


def test_prune_noop_below_threshold() -> None:
    import time

    store = InMemoryCancellationStore()
    future = time.time() + 1000
    for i in range(10):
        store._keys[f"live-{i}"] = future
    asyncio.run(
        store.request_cancel(
            project_id="p1", task_type="r", episode=1, task_id="t", ttl_seconds=100,
        )
    )
    # 未超阈值不触发清扫，全部保留
    assert len(store._keys) == 11
