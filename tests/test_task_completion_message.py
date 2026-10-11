"""任务终态那行字：runner 的判断要活过 complete 写入。

现场形状：一键自动化跑完、进度条 100%、状态 completed，角色却是空的。runner 已经把
「未提取到角色」报成最后一条进度，但 `run_core` 收尾时统一写 `current_task="完成"`，
把这行判断盖掉了——用户只剩一个「完成」，只能认为按钮没生效。

这里是 opt-in 通道：runner 在 result 里放 `completion_message`，core 取出来当终态文案；
没放的 runner 一个字节都不变（「完成」这个词还被僵尸行判定和几个既有断言依赖着）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.i18n_message import LocalizableMessage, lmsg
from novelvideo.ports.authz import AdmissionContext, BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference
from novelvideo.task_backend import run_core
from novelvideo.task_backend.consumer import VerifiedTaskDelivery
from novelvideo.task_backend.registry import register_project_task_runner

pytestmark = pytest.mark.m07


class _FakeTaskManager:
    def __init__(self) -> None:
        self.completed: list[dict] = []

    def update_progress_for_project(self, *_args, **_kwargs) -> None:
        return None

    def begin_task_execution_for_project(self, *_args, **_kwargs) -> bool:
        return True

    def complete_task_for_project(self, *_args, **kwargs) -> None:
        self.completed.append(kwargs)

    def fail_task_for_project(self, *_args, **_kwargs) -> None:
        return None


def _delivery(task_type: str) -> VerifiedTaskDelivery:
    admission = AdmissionContext(
        requester_user_id="usr_1",
        billing_principal=BillingPrincipal(kind="local", id="usr_1"),
        credential=CredentialReference("local", "local-newapi", 1),
        admission_id="admission-1",
        root_task_id="task_1",
        admitted_at="2026-10-11T04:05:00Z",
        authz_version=1,
    )
    return VerifiedTaskDelivery(
        envelope_id="envelope-1",
        admission=admission,
        task_type=task_type,
        project_id="proj_completion",
        requester_user_id="usr_1",
        episode=0,
        beat_num=None,
        scope=None,
        queue_kind="default",
        payload={},
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        project_id="proj_completion",
        requester_user_id="usr_1",
        owner_id="usr_1",
        is_home_node=True,
    )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
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
    return run_core


def _run(task_type: str, runner) -> dict:
    register_project_task_runner(task_type, runner)
    manager = _FakeTaskManager()
    run_core.run_project_task_core_sync(
        _delivery(task_type), _ctx(), manager, run_task_id="task_1"
    )
    assert manager.completed, "任务必须以 completed 收尾"
    return manager.completed[-1]


def test_a_runner_verdict_survives_the_completion_write(harness) -> None:
    verdict = lmsg(
        "tasks.progress.auto.doneGapCast",
        "自动化完成，但未提取到角色，请检查「设置 → 模型与渠道」的文本模型后在剧本页重试",
    )

    def runner(_envelope, _ctx):
        return {
            "characters": {"characters": 0},
            "completion_message": {
                "code": verdict.code,
                "text": verdict.text,
            },
        }

    completed = _run("completion_verdict", runner)

    current_task = completed["current_task"]
    assert isinstance(current_task, LocalizableMessage)
    assert current_task.code == "tasks.progress.auto.doneGapCast"
    assert current_task.text == verdict.text
    assert completed["logs"] == [current_task]


def test_the_verdict_key_never_reaches_the_stored_result(harness) -> None:
    def runner(_envelope, _ctx):
        return {"characters": 0, "completion_message": {"code": "a.b", "text": "文案"}}

    completed = _run("completion_result_shape", runner)

    assert "completion_message" not in completed["result"]
    assert completed["result"] == {"characters": 0}


def test_a_runner_without_a_verdict_still_completes_as_done(harness) -> None:
    """没走的调用点必须一个字节都不变：「完成」还被僵尸行判定和既有断言依赖着。"""

    def runner(_envelope, _ctx):
        return {"asset_id": "asset_1"}

    completed = _run("completion_plain", runner)

    assert completed["current_task"] == "完成"
    assert completed["logs"] == ["完成"]


def test_an_unusable_verdict_falls_back_instead_of_blanking_the_row(harness) -> None:
    bad_values = ("不是结构化消息", {"text": "没有 code"}, {"code": "  "}, None, 7)

    for index, bad in enumerate(bad_values):

        def runner(_envelope, _ctx, _bad=bad):
            return {"ok": True, "completion_message": _bad}

        completed = _run(f"completion_bad_{index}", runner)
        assert completed["current_task"] == "完成"
        assert completed["result"] == {"ok": True}
