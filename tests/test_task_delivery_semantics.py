"""WP-D 红线等价性测试：任务 delivered / undelivered 与成功失败语义。

这些用例刻意不引用任何额度 / 结算 API（`[aipdd fork delta]` WP-D 拆除额度体系时
先立此基线）：拆除前后都必须原样通过，用来证明「结算钩子消失」不等于「任务结果
语义变化」。唯一与额度体系相关的分支（上游配额失败）单独放在
``test_upstream_quota_failure_keeps_the_task_failed_with_quota_semantics`` 里，因为它携带的
error_code / 文案是本次改名的一部分。
"""

from types import SimpleNamespace

import pytest

from novelvideo.ports.authz import AdmissionContext, BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference
from novelvideo.task_backend import run_core
from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut
from novelvideo.task_backend.consumer import VerifiedTaskDelivery
from novelvideo.task_backend.registry import register_project_task_runner


class _FakeTaskManager:
    def __init__(self) -> None:
        self.completed: list[dict] = []
        self.failed: list[dict] = []
        self.updates: list[dict] = []
        self.started: list[dict] = []

    def update_progress_for_project(self, *_args, **kwargs) -> None:
        self.updates.append(kwargs)

    def begin_task_execution_for_project(self, *_args, **kwargs) -> bool:
        self.started.append(kwargs)
        return True

    def complete_task_for_project(self, *_args, **kwargs) -> None:
        self.completed.append(kwargs)

    def fail_task_for_project(self, *_args, **kwargs) -> None:
        self.failed.append(kwargs)


def _delivery(*, task_type: str, project_id: str = "proj_delivery") -> VerifiedTaskDelivery:
    admission = AdmissionContext(
        requester_user_id="usr_1",
        billing_principal=BillingPrincipal(kind="local", id="usr_1"),
        credential=CredentialReference("local", "local-newapi", 1),
        admission_id="admission-1",
        root_task_id="task_1",
        admitted_at="2026-08-03T04:05:00Z",
        authz_version=1,
    )
    return VerifiedTaskDelivery(
        envelope_id="envelope-1",
        admission=admission,
        task_type=task_type,
        project_id=project_id,
        requester_user_id="usr_1",
        episode=1,
        beat_num=2,
        scope=None,
        queue_kind="default",
        payload={},
    )


@pytest.fixture
def harness(monkeypatch):
    """Patch away everything orthogonal to delivery semantics."""

    async def _no_cancel_requested(**_kwargs):
        return False

    async def _no_metrics(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_core, "_ensure_builtin_runners_registered", lambda: None)
    monkeypatch.setattr(run_core, "is_cancel_requested", _no_cancel_requested)
    monkeypatch.setattr(run_core, "_emit_project_task_metrics", _no_metrics)
    monkeypatch.setattr(
        run_core,
        "_set_project_task_metrics_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    return SimpleNamespace(
        cancel_requested=_no_cancel_requested,
        monkeypatch=monkeypatch,
    )


def _ctx(project_id: str = "proj_delivery") -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        requester_user_id="usr_1",
        owner_id="usr_1",
        is_home_node=True,
    )


def test_successful_runner_result_is_the_delivery(harness):
    calls: list[str] = []

    def runner(_envelope, _ctx):
        calls.append("ran")
        return {"asset_id": "asset_1"}

    register_project_task_runner("delivery_success", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_success"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert calls == ["ran"]
    assert result == {"asset_id": "asset_1"}
    assert manager.failed == []
    assert manager.completed[-1]["result"] == {"asset_id": "asset_1"}


def test_empty_runner_result_still_counts_as_delivered(harness):
    register_project_task_runner("delivery_empty_result", lambda _e, _c: None)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_empty_result"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result == {"ok": True}
    assert manager.completed[-1]["result"] == {"ok": True}


def test_generic_runner_failure_propagates_and_marks_task_failed(harness):
    def runner(_envelope, _ctx):
        raise RuntimeError("boom")

    register_project_task_runner("delivery_generic_failure", runner)
    manager = _FakeTaskManager()

    with pytest.raises(RuntimeError, match="boom"):
        run_core.run_project_task_core_sync(
            _delivery(task_type="delivery_generic_failure"),
            _ctx(),
            manager,
            run_task_id="task_1",
        )

    assert manager.completed == []
    assert manager.failed[-1]["error"] == "boom"


def test_handled_failure_returns_failed_payload_without_raising(harness):
    from novelvideo.scene_prerequisites import ScenePlanningPrerequisiteError

    def runner(_envelope, _ctx):
        raise ScenePlanningPrerequisiteError("请先完成场景规划")

    register_project_task_runner("delivery_handled_failure", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_handled_failure"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result["failed"] is True
    assert result["error_code"] == ScenePlanningPrerequisiteError.error_code
    assert manager.completed == []
    assert manager.failed[-1]["error"] == "请先完成场景规划"


def test_timeout_is_a_failure_not_a_cancel(harness):
    def runner(_envelope, _ctx):
        raise TaskTimedOut(timeout_seconds=60)

    register_project_task_runner("delivery_timeout", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_timeout"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result["failed"] is True
    assert result["error_code"] == "TASK_TIMEOUT"
    assert result["timeout_seconds"] == 60
    assert manager.failed[-1]["error"] == "任务超过 1 分钟未完成，已自动放弃"
    assert manager.updates == []


def test_cancel_during_run_returns_cancelled_and_writes_cancelled_state(harness):
    def runner(_envelope, _ctx):
        raise TaskCancelled("user cancelled")

    register_project_task_runner("delivery_cancelled_midway", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_cancelled_midway"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result == {"cancelled": True}
    assert manager.completed == []
    assert manager.failed == []
    assert manager.updates[-1]["status"] == "cancelled"
    assert manager.updates[-1]["current_task"] == "任务已取消"


def test_cancel_requested_before_start_never_runs_the_runner(harness):
    calls: list[str] = []

    def runner(_envelope, _ctx):
        calls.append("ran")
        return {"ok": True}

    async def _cancel_requested(**_kwargs):
        return True

    harness.monkeypatch.setattr(run_core, "is_cancel_requested", _cancel_requested)
    register_project_task_runner("delivery_cancel_before_start", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_cancel_before_start"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result == {"cancelled": True}
    assert calls == []
    assert manager.started == []
    assert manager.updates[-1]["status"] == "cancelled"


def test_replaced_queued_state_skips_execution(harness):
    calls: list[str] = []

    def runner(_envelope, _ctx):
        calls.append("ran")
        return {"ok": True}

    register_project_task_runner("delivery_state_replaced", runner)

    class _ReplacedManager(_FakeTaskManager):
        def begin_task_execution_for_project(self, *_args, **kwargs) -> bool:
            self.started.append(kwargs)
            return False

    manager = _ReplacedManager()
    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_state_replaced"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result == {"cancelled": True, "cancelled_before_execution": True}
    assert calls == []


def test_missing_runner_is_a_failure(harness):
    manager = _FakeTaskManager()

    with pytest.raises(RuntimeError, match="No project task runner registered"):
        run_core.run_project_task_core_sync(
            _delivery(task_type="delivery_no_runner_registered"),
            _ctx(),
            manager,
            run_task_id="task_1",
        )

    assert manager.completed == []
    assert "No project task runner registered" in manager.failed[-1]["error"]


def test_read_model_write_failure_does_not_undo_a_delivered_result(harness):
    def runner(_envelope, _ctx):
        return {"asset_id": "asset_1"}

    register_project_task_runner("delivery_state_write_fails", runner)

    class _FailingManager(_FakeTaskManager):
        def complete_task_for_project(self, *_args, **kwargs) -> None:
            raise OSError("task state write failed")

    manager = _FailingManager()

    with pytest.raises(OSError, match="task state write failed"):
        run_core.run_project_task_core_sync(
            _delivery(task_type="delivery_state_write_fails"),
            _ctx(),
            manager,
            run_task_id="task_1",
        )

    # The runner produced the asset; only the read-model row failed.
    assert manager.failed == []


def test_metrics_context_is_cleared_even_when_the_runner_raises(harness):
    cleared: list[str] = []
    harness.monkeypatch.setattr(
        run_core,
        "_clear_project_task_metrics_context",
        lambda: cleared.append("cleared"),
    )

    def runner(_envelope, _ctx):
        raise RuntimeError("boom")

    register_project_task_runner("delivery_context_cleanup", runner)
    manager = _FakeTaskManager()

    with pytest.raises(RuntimeError):
        run_core.run_project_task_core_sync(
            _delivery(task_type="delivery_context_cleanup"),
            _ctx(),
            manager,
            run_task_id="task_1",
        )

    assert "cleared" in cleared


def test_provider_task_id_from_result_is_carried_into_completion_metadata(harness):
    register_project_task_runner(
        "delivery_provider_task_id", lambda _e, _c: {"provider_task_id": "prov-9"}
    )
    manager = _FakeTaskManager()

    run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_provider_task_id"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert manager.completed[-1]["metadata"]["provider_task_id"] == "prov-9"


def test_upstream_quota_failure_keeps_the_task_failed_with_quota_semantics(harness):
    """上游配额失败：仍然判失败，但携带配额语义的 error_code / 文案。"""
    from novelvideo.shared.upstream_quota_errors import (
        UPSTREAM_QUOTA_EXCEEDED_CODE,
        UPSTREAM_QUOTA_EXCEEDED_MESSAGE,
    )

    def runner(_envelope, _ctx):
        raise RuntimeError("Insufficient credits for this application")

    register_project_task_runner("delivery_upstream_quota", runner)
    manager = _FakeTaskManager()

    result = run_core.run_project_task_core_sync(
        _delivery(task_type="delivery_upstream_quota"),
        _ctx(),
        manager,
        run_task_id="task_1",
    )

    assert result["failed"] is True
    assert result["error_code"] == UPSTREAM_QUOTA_EXCEEDED_CODE
    assert manager.completed == []
    assert manager.failed[-1]["error"] == UPSTREAM_QUOTA_EXCEEDED_MESSAGE
