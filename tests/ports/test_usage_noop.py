"""[aipdd fork delta] WP-D: usage port 只剩「记账之外」的语义。

`NoOpUsageMeter` 的 LLM 归因上下文必须真的写进 `llm_instrumentation` 的
contextvar（排障要靠它把 token 归到人 / 项目上），同时整套调用不允许出现
任何余额判定。
"""

import pytest

from novelvideo import llm_instrumentation
from novelvideo.ports.local.usage import NoOpProviderInstrumentation, NoOpUsageMeter


@pytest.mark.asyncio
async def test_noop_usage_meter_carries_llm_attribution_context() -> None:
    meter = NoOpUsageMeter()

    meter.set_llm_usage_context(
        "u1",
        project_id="proj-1",
        resource_kind="script",
        billing_metadata={"feature_key": "mainline.script_writer"},
    )
    try:
        assert llm_instrumentation.get_llm_user_context() == "u1"
        assert llm_instrumentation.get_billing_metadata_context() == {
            "feature_key": "mainline.script_writer"
        }
        await meter.bump_model_call(user_id="u1", model="gpt-test")
        await meter.record_llm_tokens(
            user_id="u1", input_tokens=3, output_tokens=4, model="gpt-test"
        )
    finally:
        meter.clear_llm_usage_context()

    assert llm_instrumentation.get_llm_user_context() is None
    assert llm_instrumentation.get_billing_metadata_context() == {}


def test_noop_provider_instrumentation_is_idempotent() -> None:
    instrumentation = NoOpProviderInstrumentation()
    instrumentation.install()
    instrumentation.install()
