"""[aipdd fork delta] WP-D: 本项目不做本地记账。

这里守两件事：

1. ``NoOpUsageMeter`` 仍提供排障要读的观测面（模型调用计数、LLM token、资源尝试
   日志、归因上下文），且全部「非拦截」——调用即返回，永远不产生预约 id。
2. 预约 / 结算 / 退还 / 余额这一整套 credit 协议已从实现与 Protocol 双双消失，
   防止任何调用点重新长出「看起来像计费判定、实际永远放行」的样板。
"""

import pytest

from novelvideo.ports.local.usage import NoOpUsageMeter
from novelvideo.ports.usage import UsageMeter

_REMOVED_CREDIT_METHODS = (
    "reserve_current_model_call_credit",
    "reserve_feature_start_credits",
    "confirm_feature_credit_reservation",
    "refund_feature_credit_reservation",
    "refund_model_call_credit_reservation",
    "require_feature_credit_balance",
    "get_user_credit_balance",
    "settle_model_call_credit",
)


@pytest.mark.asyncio
async def test_noop_usage_meter_supports_observability_surface() -> None:
    meter = NoOpUsageMeter()

    await meter.update_current_model_call_log(error_message="boom")
    await meter.bump_model_call(user_id="u1", model="gpt-test")
    meter.set_llm_usage_context("u1", project_id="p1", resource_kind="script")
    meter.clear_llm_usage_context()
    await meter.set_project_llm_usage_context(username="alice", project_name="demo")
    await meter.bump_content_counter(
        user_id="u1",
        metric="beats_written",
        value=3,
        model="gpt-test",
        project_id="p1",
        resource_kind="script",
    )
    await meter.log_resource_attempts(
        user_id="u1",
        project_id="p1",
        kind="sketch",
        refs=["beat-1"],
        outcome="success",
        model="gpt-test",
    )
    await meter.record_llm_tokens(
        user_id="u1",
        input_tokens=11,
        output_tokens=7,
        model="gpt-test",
        project_id="p1",
        resource_kind="script",
    )


@pytest.mark.parametrize("method", _REMOVED_CREDIT_METHODS)
def test_credit_protocol_methods_are_gone(method: str) -> None:
    assert not hasattr(NoOpUsageMeter, method)
    assert not hasattr(UsageMeter, method)
