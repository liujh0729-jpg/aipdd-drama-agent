"""req33：模型 HTTP 失败必须变成可行动诊断（科普讲解视频 404 的修法）。

背景（容器实测）：工具页文本模型下拉里的 ``volcengine::deepseek-v4-flash-260425``
来自火山方舟 ``GET /models`` 的平台全量清单，账号并没有开通它 —— 直连 Ark 与经
本地网关都回 404 ``InvalidEndpointOrModel.NotFound``。PydanticAI 把它压成
``status_code: 404, model_name: ..., body: {...}`` 的英文残渣原样落任务库、原样
显示到工具页，用户既看不出是谁的错，也不知道该去开通还是改选。
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from novelvideo.config import _newapi_text_openai_model
from novelvideo.model_call_diagnostics import (
    describe_model_call_failure,
    is_http_call_failure,
)

ARK_404_BODY = {
    "message": (
        "The model or endpoint deepseek-v4-flash-260425 does not exist or you do "
        "not have access to it. Request id: "
        "021791271434254fa0eb8de5025091839561e6fd538adcb109c4c"
    ),
    "type": "Not Found",
    "param": "",
    "code": "InvalidEndpointOrModel.NotFound",
}


def test_ark_not_granted_404_becomes_actionable_chinese() -> None:
    exc = ModelHTTPError(
        404, "volcengine::deepseek-v4-flash-260425", dict(ARK_404_BODY)
    )
    detail = describe_model_call_failure(exc)

    # 状态码 / 上游错误码 / 上游原文 / 模型名一个都不能丢，否则运维无法定位。
    assert "HTTP 404" in detail
    assert "InvalidEndpointOrModel.NotFound" in detail
    assert "volcengine::deepseek-v4-flash-260425" in detail
    assert "does not exist or you do not have access" in detail
    # 但不是英文残渣：要给出「是谁的问题 + 怎么行动」与可核对的 Request id。
    assert "未开通推理服务" in detail
    assert "火山方舟" in detail
    assert "跟随系统默认" in detail
    assert "021791271434254fa0eb8de5025091839561e6fd538adcb109c4c" in detail
    assert "api_key" not in detail.lower()


def test_gateway_model_not_found_points_at_channel_binding() -> None:
    exc = ModelHTTPError(
        503,
        "aipdd::ap-funasr",
        {
            "error": {
                "message": (
                    "No available channel for model ap-funasr under group default "
                    "(distributor)"
                ),
                "type": "new_api_error",
                "code": "model_not_found",
            }
        },
    )
    detail = describe_model_call_failure(exc)
    assert "HTTP 503" in detail
    assert "model_not_found" in detail
    assert "设置 → 模型网关" in detail


def test_gateway_unbound_on_internal_slot_points_at_feature_binding() -> None:
    """[aipdd fork delta] ``DC-*`` 同一个 503 但动作完全不同：绑功能位，不是开通模型。

    回归场景：导演剧本功能位没绑模型时，用户拿到的是「No available channel for model
    DC-director-screenwriter-LLM」，而旧文案让他去「设置 → 模型网关」为渠道开通这个
    「模型」—— 渠道目录里根本没这一项，于是一条死路。它不是模型名，而是只从功能位
    绑定投影的路由键。
    """

    exc = ModelHTTPError(
        503,
        "DC-director-screenwriter-LLM",
        {
            "error": {
                "message": (
                    "No available channel for model DC-director-screenwriter-LLM "
                    "under group default (distributor)"
                ),
                "type": "new_api_error",
                "code": "model_not_found",
            }
        },
    )
    detail = describe_model_call_failure(exc)
    assert "HTTP 503" in detail
    assert "DC-director-screenwriter-LLM" in detail
    assert "内部功能位" in detail
    assert "还没绑定可用模型" in detail
    assert "设置 → 模型" in detail
    assert "自动投影" in detail
    # 不能再让用户去网关侧「为渠道开通」一个不存在的模型。
    assert "为对应渠道开通该模型" not in detail


def test_auth_and_vision_failures_have_their_own_guidance() -> None:
    unauthorized = describe_model_call_failure(
        ModelHTTPError(
            401, "volcengine::doubao-seed", {"message": "invalid api key", "code": ""}
        )
    )
    assert "鉴权失败" in unauthorized

    vision = describe_model_call_failure(
        ModelHTTPError(
            400,
            "aipdd::ap-glm-5.2",
            {"message": "当前模型不支持请求所需的图片能力", "code": "bad_response"},
        )
    )
    assert "VISION_MODEL_FALLBACK" in vision


def test_non_http_failures_are_never_rewritten() -> None:
    # 出网拒绝码 / 结构化输出校验失败等靠类型与文案判定，必须原样透传。
    assert not is_http_call_failure(RuntimeError("ORG_EGRESS_DENIED"))
    assert not is_http_call_failure(ValueError("bad payload"))


async def test_newapi_text_model_translates_http_failure(monkeypatch) -> None:
    from pydantic_ai.models.openai import OpenAIChatModel

    async def _boom(self, *args, **kwargs):  # noqa: ANN002, ANN001
        raise ModelHTTPError(404, "volcengine::deepseek-v4-flash-260425", dict(ARK_404_BODY))

    monkeypatch.setattr(OpenAIChatModel, "request", _boom)
    model = _newapi_text_openai_model(
        "volcengine::deepseek-v4-flash-260425",
        api_key="secret-key-must-not-leak",
        base_url="http://127.0.0.1:1/v1",
        timeout_seconds=5.0,
        profile=None,
    )

    with pytest.raises(RuntimeError) as raised:
        await model.request(None, [], None)

    detail = str(raised.value)
    assert "HTTP 404" in detail
    assert "未开通推理服务" in detail
    assert "secret-key-must-not-leak" not in detail
    # 因果链不能断：运维还要能看到原始的 ModelHTTPError。
    assert isinstance(raised.value.__cause__, ModelHTTPError)
