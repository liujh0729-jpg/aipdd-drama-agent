"""取消与进度/完成/失败写入的丢失更新守卫。

进度写入过去是「先读、再整行 UPSERT」：取消与它交错时，后写的 stale 状态会把
cancelled 冲回 running（任务被 kill 后界面还显示运行中）。现在读写收进同一个
BEGIN IMMEDIATE 事务，这组用例锁定「终态一经写入，后续写入不得复活」的契约。
"""

import asyncio
import threading
from pathlib import Path

import pytest

from novelvideo.ports.local.tasks import InlineTaskBackend
from novelvideo.task_state import TaskStateManager, get_task_manager


class _SignedEnvelope:
    def to_dict(self) -> dict:
        return {"envelope_id": "test-envelope"}


class _FakeProducer:
    async def sign_top_level(self, **_kwargs):
        return _SignedEnvelope()


def _ctx(tmp_path: Path):
    from novelvideo.project_context import ProjectContext

    return ProjectContext(
        project_id="proj_lost_update",
        project_name="demo",
        owner_type="user",
        owner_id="owner_1",
        owner_username="alice",
        requester_user_id="editor_1",
        requester_username="bob",
        requester_principals=(("user", "editor_1"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def test_cancelled_task_is_not_resurrected_by_late_writers(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    queued = manager.create_task_for_project(ctx, "single_video", 1, status="queued")

    cancelled, state = manager.cancel_queued_task_for_project(
        ctx, "single_video", 1, expected_task_id=queued.task_id
    )
    assert cancelled is True
    assert state is not None and state.status == "cancelled"

    manager.update_progress_for_project(
        ctx,
        "single_video",
        1,
        progress=0.5,
        status="running",
        expected_task_id=queued.task_id,
    )
    assert manager.get_task_for_project(ctx, "single_video", 1).status == "cancelled"

    assert (
        manager.complete_task_for_project(
            ctx, "single_video", 1, expected_task_id=queued.task_id
        )
        is False
    )
    manager.fail_task_for_project(
        ctx, "single_video", 1, error="late failure", expected_task_id=queued.task_id
    )
    final = manager.get_task_for_project(ctx, "single_video", 1)
    assert final is not None
    assert final.status == "cancelled"
    assert final.error is None


def test_completed_task_is_not_regressed_by_late_progress(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "single_video", 2, status="running")

    assert (
        manager.complete_task_for_project(
            ctx, "single_video", 2, result={"ok": True}, expected_task_id=created.task_id
        )
        is True
    )
    manager.update_progress_for_project(
        ctx, "single_video", 2, progress=0.2, status="running", expected_task_id=created.task_id
    )
    final = manager.get_task_for_project(ctx, "single_video", 2)
    assert final is not None
    assert final.status == "completed"
    assert final.progress == 1.0


def test_complete_without_existing_row_creates_completed_row(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    assert manager.complete_task_for_project(ctx, "single_video", 3, result={"ok": True}) is True

    final = manager.get_task_for_project(ctx, "single_video", 3)
    assert final is not None
    assert final.status == "completed"
    assert final.error is None


def test_fail_without_existing_row_creates_failed_row(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    manager.fail_task_for_project(ctx, "single_video", 4, error="boom")

    final = manager.get_task_for_project(ctx, "single_video", 4)
    assert final is not None
    assert final.status == "failed"
    assert final.error == "boom"


@pytest.mark.asyncio
async def test_reserve_row_carries_backend_before_queued_update(
    monkeypatch, tmp_path: Path
) -> None:
    """reserve 与 queued 更新之间崩溃时，行必须已带 backend 标记。

    否则僵尸清扫（按 ``$.task_metadata.backend`` 过滤）看不见这行 submitting，
    task_key 被永久占住，同键任务再也发不出去。
    """
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend(producer=_FakeProducer())
    manager = get_task_manager()
    monkeypatch.setattr(backend, "_submit_lane_job", lambda _job: None)

    def _crash(*_args, **_kwargs):
        raise RuntimeError("simulated crash between reserve and queued update")

    monkeypatch.setattr(manager, "update_progress_for_project", _crash)
    with pytest.raises(RuntimeError):
        await backend.enqueue_project_task(
            ctx, task_type="single_video", product_surface="mainline", episode=5
        )
    monkeypatch.undo()

    state = manager.get_task_for_project(ctx, "single_video", 5)
    assert state is not None
    assert state.status == "submitting"
    assert state.metadata.get("backend") == "inline"


def test_late_writer_from_thread_waits_for_commit(tmp_path: Path) -> None:
    """事务期间的第二个写者必须等提交，不能插进读-写窗口。

    在第一次读取之后、写入之前发起一个后台「取消」写入：旧实现会当场读到
    running 行并写回 cancelled，随后被本进度写覆盖（或反之）；现在后台写者
    被写锁挡住，只能看到提交后的状态，最终两笔更新按顺序生效、不会撕裂。
    """
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "single_video", 6, status="running")

    spawned: list[threading.Thread] = []
    started = threading.Event()

    def _cancel_in_thread() -> None:
        started.set()
        manager.update_progress_for_project(
            ctx,
            "single_video",
            6,
            progress=0.9,
            status="cancelled",
            expected_task_id=created.task_id,
        )

    original_row_to_state = TaskStateManager._row_to_state

    def _hook(row):
        state = original_row_to_state(row)
        if (
            not spawned
            and state.task_id == created.task_id
            and threading.current_thread() is threading.main_thread()
        ):
            thread = threading.Thread(target=_cancel_in_thread, daemon=True)
            spawned.append(thread)
            thread.start()
            started.wait(timeout=2)
        return state

    TaskStateManager._row_to_state = staticmethod(_hook)
    try:
        manager.update_progress_for_project(
            ctx,
            "single_video",
            6,
            progress=0.1,
            status="running",
            expected_task_id=created.task_id,
        )
    finally:
        TaskStateManager._row_to_state = staticmethod(original_row_to_state)

    assert started.is_set()
    for thread in spawned:
        thread.join(timeout=10)
        assert not thread.is_alive()

    final = manager.get_task_for_project(ctx, "single_video", 6)
    assert final is not None
    assert final.status == "cancelled"
    assert final.progress == 0.9


def test_queue_kind_metadata_survives_reserve(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    state, reserved = manager.reserve_task_for_project(
        ctx,
        "single_video",
        7,
        metadata={"backend": "inline", "queue_kind": "default", "project_id": ctx.project_id},
        queue_kind="default",
    )
    assert reserved is True
    row = manager.get_task_for_project(ctx, "single_video", 7)
    assert row is not None
    assert row.metadata.get("backend") == "inline"
    assert row.queue_kind == "default"
    assert state.status == "submitting"


def test_concurrent_progress_writers_do_not_lose_updates(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "single_video", 8, status="running")

    async def _writer(progress: float) -> None:
        await asyncio.to_thread(
            manager.update_progress_for_project,
            ctx,
            "single_video",
            8,
            None,
            None,
            progress,
            None,
            None,
            None,
            "running",
            created.task_id,
        )

    async def _run() -> None:
        await asyncio.gather(*[_writer(i / 10) for i in range(1, 10)])

    asyncio.run(_run())

    final = manager.get_task_for_project(ctx, "single_video", 8)
    assert final is not None
    assert final.status == "running"
    assert 0.1 <= final.progress <= 0.9