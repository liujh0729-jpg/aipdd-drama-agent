"""[aipdd fork delta] WP-D: 运行中任务的鉴权不确定性仍然是「失败」，但不再牵扯结算。

原来这份文件里六条用例有一半在守「结算决议（settlement resolution）先于 runner 失败」
以及「决议被拒时的错误码 / 落盘归属」。额度体系整体拆除后那些钩子已不存在，留下的硬
契约是：runner 抛 ``RunningTaskAuthorityIndeterminate`` 时任务判失败、错误码原样回、
指标记 failed、且投递方伪造的结算句柄不得渗进 runner 信封。delivered / undelivered 的
语义等价性由 ``tests/test_task_delivery_semantics.py`` 守。
"""
from __future__ import annotations

from types import SimpleNamespace

from novelvideo.ports.authz import AdmissionContext, BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference
from novelvideo.task_backend.consumer import VerifiedTaskDelivery
from novelvideo.task_backend.envelope import RunningTaskAuthorityIndeterminate


class Manager:
    def __init__(self) -> None:
        self.failures: list[dict] = []

    def begin_task_execution_for_project(self, *_args, **_kwargs) -> bool:
        return True

    def fail_task_for_project(self, *_args, **kwargs) -> None:
        self.failures.append(kwargs)


def _delivery() -> VerifiedTaskDelivery:
    admission = AdmissionContext(
        requester_user_id="user-1",
        billing_principal=BillingPrincipal(kind="organization", id="org-1"),
        credential=CredentialReference(
            "organization", "credential-1", 2, org_id="org-1"
        ),
        admission_id="admission-1",
        root_task_id="task-1",
        admitted_at="2026-08-18T00:00:00Z",
        membership_id="membership-1",
        authz_version=2,
    )
    return VerifiedTaskDelivery(
        envelope_id="envelope-1",
        admission=admission,
        task_type="running_authz_probe",
        project_id="project-1",
        requester_user_id="user-1",
        episode=1,
        beat_num=None,
        scope=None,
        queue_kind="default",
        payload={},
        billing_metadata={
            "feature_credit_reservation_id": "foreign-reservation",
            "feature_key": "forged.feature",
        },
    )


def _run(monkeypatch, runner, manager, *, metric_outcomes):
    from novelvideo.task_backend import run_core
    from novelvideo.task_backend.registry import register_project_task_runner

    class Usage:
        async def set_llm_usage_context(self, *_args, **_kwargs) -> None:
            return None

        def clear_llm_usage_context(self) -> None:
            return None

    async def not_cancelled(**_kwargs):
        return False

    async def capture_metrics(*_args, **kwargs):
        metric_outcomes.append(str(kwargs.get("outcome") or ""))

    register_project_task_runner("running_authz_probe", runner)
    monkeypatch.setattr(run_core, "_ensure_builtin_runners_registered", lambda: None)
    monkeypatch.setattr(run_core, "is_cancel_requested", not_cancelled)
    monkeypatch.setattr(run_core, "get_usage_meter", lambda: Usage())
    monkeypatch.setattr(run_core, "_emit_project_task_metrics", capture_metrics)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(
        run_core, "_set_project_task_metrics_context", lambda *_a, **_k: None
    )

    return run_core.run_project_task_core_sync(
        _delivery(),
        SimpleNamespace(
            project_id="project-1", requester_user_id="user-1", is_home_node=True
        ),
        manager,
        run_task_id="task-1",
    )


def test_post_start_authz_indeterminate_fails_the_task(monkeypatch) -> None:
    metric_outcomes: list[str] = []

    def runner(envelope, _ctx):
        # 投递方带来的结算句柄不得渗进 runner 信封。
        assert "billing_metadata" not in envelope
        raise RunningTaskAuthorityIndeterminate(failure_kind="unavailable")

    manager = Manager()
    result = _run(monkeypatch, runner, manager, metric_outcomes=metric_outcomes)

    assert result == {
        "failed": True,
        "error_code": "TASK_AUTHZ_REVALIDATION_INDETERMINATE",
        "failure_kind": "unavailable",
    }
    assert metric_outcomes == ["failed"]
    assert manager.failures[0]["metadata"]["error_code"] == (
        "TASK_AUTHZ_REVALIDATION_INDETERMINATE"
    )
    assert "foreign-reservation" not in repr(manager.failures)


def test_indeterminate_path_has_no_settlement_handles(monkeypatch) -> None:
    """防回潮：run_core 里不该再有「结算决议 / 退还」这一层拦截。"""
    import inspect

    from novelvideo.task_backend import run_core

    source = inspect.getsource(run_core)
    for removed in (
        "resolve_feature_credit_reservation",
        "mark_feature_credit_settlement_for_review",
        "FEATURE_SETTLEMENT_RESOLUTION",
    ):
        assert removed not in source, removed
