from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import chat as chat_routes
from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.chat.store import ChatScope
from novelvideo.egress_context import TrustedEgressContext
from novelvideo.ports.authz import BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference
from novelvideo.shared.billing_errors import (
    BILLING_RULE_NOT_CONFIGURED_CODE,
    BILLING_RULE_NOT_CONFIGURED_MESSAGE,
    BillingError,
    BillingRuleNotConfiguredError,
    billing_error_payload,
    billing_rule_not_configured_payload,
)
# [aipdd fork delta] WP-D: 积分类异常与余额判定随 CE 额度体系一起拆除，
# 唯一保留的中止判定改名为上游账户额度分类。
from novelvideo.shared.upstream_quota_errors import (
    is_fatal_upstream_quota_error,
    is_upstream_quota_error,
)
from novelvideo.task_backend import run_core

# [aipdd fork delta] WP-D: 上游账户额度不足时回的真实文案片段。
_QUOTA_ERROR_TEXT = "insufficient credits for user usr_1: required 40, available 8"


class _ForeignBillingError(BillingError):
    error_code = "FAKE_BILLING_ERROR"
    http_status = 402
    user_message = "模拟的 typed 计费错误"

    def details(self) -> dict:
        return {"probe": "task-c"}


def _organization_context() -> TrustedEgressContext:
    credential = CredentialReference(
        source="organization",
        credential_id="credential-task-c",
        key_version=1,
        org_id="tenant-task-c",
    )
    return TrustedEgressContext(
        envelope_id="envelope-task-c",
        project_id="project-task-c",
        task_type="single_video",
        requester_user_id="user-task-c",
        root_task_id="root-task-c",
        admission_id="admission-task-c",
        admitted_at="2026-08-05T00:00:00Z",
        membership_id="membership-task-c",
        authz_version=1,
        billing_principal=BillingPrincipal(
            kind="organization",
            id="tenant-task-c",
        ),
        credential=credential,
    )


class _OperationPort:
    async def claim(self, *, spec):
        operation = type(
            "_Operation",
            (),
            {
                "operation_id": "operation-task-c",
                "operation_key": spec.operation_key,
                "version": 1,
            },
        )()
        return type(
            "_Claim",
            (),
            {
                "won": True,
                "operation": operation,
                "transition_token": "transition-task-c",
            },
        )()

    async def mark_rejected_before_submit(self, **_kwargs):
        return None

    async def mark_unknown(self, **_kwargs):
        return None


class _CredentialPort:
    def __init__(self, context: TrustedEgressContext) -> None:
        self.context = context

    async def resolve(self, _admission):
        return type(
            "_Credential",
            (),
            {
                "reference": self.context.credential,
                "api_key": "redacted-task-c",
                "base_url": "https://gateway.invalid/v1",
            },
        )()


def _patch_video_egress_seams(
    monkeypatch: pytest.MonkeyPatch,
    context: TrustedEgressContext | None = None,
) -> None:
    # [aipdd fork delta] WP-D: 预约 / 退款的接缝已随额度体系拆除，
    # 这里只剩组织出网所需的两个端口。
    import novelvideo.ports as ports

    if context is not None:
        monkeypatch.setattr(
            ports,
            "get_egress_operation_port",
            lambda: _OperationPort(),
        )
        monkeypatch.setattr(
            ports,
            "get_model_credentials",
            lambda: _CredentialPort(context),
        )


def test_http_handler_surfaces_foreign_billing_error_verbatim() -> None:
    from novelvideo.api.app import create_app

    app = create_app()

    @app.get("/__foreign-billing-error")
    async def foreign_billing_error() -> None:
        raise _ForeignBillingError()

    response = TestClient(app).get("/__foreign-billing-error")

    assert response.status_code == 402
    assert response.json() == {
        "ok": False,
        "error": _ForeignBillingError.user_message,
        "data": {
            "error_code": _ForeignBillingError.error_code,
            "message": _ForeignBillingError.user_message,
            "probe": "task-c",
        },
    }


def test_task_failure_mapper_surfaces_foreign_billing_error_verbatim() -> None:
    error, metadata, handled = run_core._project_task_failure_for_exception(
        _ForeignBillingError()
    )

    assert handled is True
    assert error == _ForeignBillingError.user_message
    assert metadata == {
        "error_code": _ForeignBillingError.error_code,
        "message": _ForeignBillingError.user_message,
        "probe": "task-c",
    }


def test_task_failure_mapper_handles_billing_rule_not_configured(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=run_core.logger.name):
        error, metadata, handled = run_core._project_task_failure_for_exception(
            BillingRuleNotConfiguredError(kind="model", key="doubao/seedance")
        )

    assert handled is True
    assert error == BILLING_RULE_NOT_CONFIGURED_MESSAGE
    # K56: intentional contract change — this whole-packet assertion was 2 fields
    # (Task C) and is tightened to 4 here. Never relax `==` to `in`/`.get()`.
    assert metadata == {
        "error_code": BILLING_RULE_NOT_CONFIGURED_CODE,
        "message": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
        "billing_kind": "model",
        "billing_key": "doubao/seedance",
    }
    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ] == [
        "typed billing failure in project task: "
        "billing rule is not configured for model:doubao/seedance"
    ]


def test_billing_rule_not_configured_payload_is_identical_across_constructors() -> None:
    """K56: the generic and the dedicated constructor must produce the same packet.

    Whole-packet equality is the definition of "same shape" here; asserting
    field-by-field would let a future divergence drift in unnoticed.
    """
    exc = BillingRuleNotConfiguredError(kind="model", key="doubao/seedance")

    assert billing_error_payload(exc) == billing_rule_not_configured_payload(exc)
    assert billing_error_payload(exc) == {
        "error_code": BILLING_RULE_NOT_CONFIGURED_CODE,
        "message": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
        "billing_kind": "model",
        "billing_key": "doubao/seedance",
    }


def test_billing_rule_not_configured_r2_packet_grew_from_two_to_four_fields() -> None:
    """K56: R2's triple no longer carries a thinner packet than R1/R3/R4."""
    exc = BillingRuleNotConfiguredError(kind="model", key="doubao/seedance")

    _error, metadata, _handled = run_core._project_task_failure_for_exception(exc)

    assert len(metadata) == 4
    assert set(metadata) == {
        "error_code",
        "message",
        "billing_kind",
        "billing_key",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejected",
    [
        RuntimeError(_QUOTA_ERROR_TEXT),
        BillingRuleNotConfiguredError(kind="model", key="doubao/seedance"),
    ],
    ids=["upstream_quota_text", "typed_billing"],
)
async def test_newapi_image_call_reraises_fatal_billing_errors(
    monkeypatch: pytest.MonkeyPatch,
    rejected: BaseException,
) -> None:
    # [aipdd fork delta] WP-D: 旧的「预约阶段抛错」路径已不存在，改守真实防线：
    # 致命上游额度文案与 typed 计费信封（保留的 EE 兼容层）都必须从 HTTP 层
    # 原样冒泡，不被重试循环吞成返回值。
    import httpx

    from novelvideo.generators import grid_generation

    class FatalRejectedClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers=None, json=None):
            raise rejected

    monkeypatch.setattr(httpx, "AsyncClient", FatalRejectedClient)

    with pytest.raises(type(rejected)):
        await grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="doubao/seedance",
            prompt="portrait prompt",
            base_url="http://newapi.test/v1",
        )

    assert (
        is_upstream_quota_error(
            BillingRuleNotConfiguredError(kind="model", key="x")
        )
        is False
    )


def test_chat_websocket_surfaces_foreign_billing_error_verbatim(monkeypatch) -> None:
    scope = ChatScope(kind="home")

    async def authenticate(_websocket):
        return {"username": "local"}

    async def send_scope_changed(_websocket, _user, _username, _scope):
        return scope

    async def reauthenticate(_websocket, *, original_user, **_kwargs):
        return original_user

    async def fail_turn(**_kwargs):
        raise _ForeignBillingError()

    monkeypatch.setattr(chat_routes, "_authenticate_ws", authenticate)
    monkeypatch.setattr(chat_routes, "_scope_for_authenticated_user", lambda _user: scope)
    monkeypatch.setattr(chat_routes, "_send_scope_changed", send_scope_changed)
    monkeypatch.setattr(chat_routes, "_reauthenticate_ws_event", reauthenticate)
    monkeypatch.setattr(chat_routes, "_stream_home_turn", fail_turn)

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    with TestClient(app).websocket_connect("/api/v1/chat/ws") as websocket:
        websocket.send_json(
            {
                "type": "chat.message",
                "scope": {"kind": "home"},
                "text": "probe",
                "turn_id": "turn-task-c",
            }
        )
        frame = websocket.receive_json()

    assert frame == {
        "type": "error",
        "turn_id": "turn-task-c",
        "message": _ForeignBillingError.user_message,
        "data": {
            "error_code": _ForeignBillingError.error_code,
            "message": _ForeignBillingError.user_message,
            "probe": "task-c",
        },
    }


def test_freezone_task_start_reraises_foreign_billing_error() -> None:
    billing_error = _ForeignBillingError()
    wrapper = RuntimeError("failed to start task")
    wrapper.__cause__ = billing_error

    with pytest.raises(_ForeignBillingError) as exc_info:
        freezone_routes._handle_task_start_runtime_error("start failed", wrapper)

    assert exc_info.value is billing_error


def test_fatal_predicate_does_not_widen_upstream_quota_predicate() -> None:
    # [aipdd fork delta] WP-D: 致命判定与旧同（typed 信封 ∨ 额度文案），
    # 但额度文案判定本身不把 typed 信封误认为额度耗尽。
    billing_error = _ForeignBillingError()

    assert is_fatal_upstream_quota_error(billing_error) is True
    assert is_upstream_quota_error(billing_error) is False


@pytest.mark.asyncio
async def test_video_organization_provider_error_stays_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from novelvideo.generators.video_generator import (
        NewApiVideoError,
        NewApiVideoGenerator,
        VideoGenStatus,
    )

    context = _organization_context()
    _patch_video_egress_seams(monkeypatch, context)
    generator = NewApiVideoGenerator(
        model="seedance-1.0-pro-fast",
        egress_context=context,
    )

    async def fail_submit(*_args, **_kwargs):
        raise NewApiVideoError("provider internal state")

    monkeypatch.setattr(generator, "_post_json", fail_submit)

    result = await generator.generate(
        image_path="",
        prompt="probe",
        output_path=str(tmp_path / "out.mp4"),
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error == "EGRESS_OPERATION_UNKNOWN"


@pytest.mark.asyncio
async def test_video_organization_typed_billing_error_is_reraised(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator

    context = _organization_context()
    _patch_video_egress_seams(monkeypatch, context)
    generator = NewApiVideoGenerator(
        model="seedance-1.0-pro-fast",
        egress_context=context,
    )

    async def fail_submit(*_args, **_kwargs):
        raise _ForeignBillingError()

    monkeypatch.setattr(generator, "_post_json", fail_submit)

    with pytest.raises(_ForeignBillingError):
        await generator.generate(
            image_path="",
            prompt="probe",
            output_path=str(tmp_path / "out.mp4"),
        )


@pytest.mark.asyncio
async def test_video_personal_upstream_quota_error_is_reraised(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    # [aipdd fork delta] WP-D: 原名「个人积分不足行为不变」，积分类已消失，
    # 改守上游账户额度文案必须冒泡、不被吞成失败结果。
    from novelvideo.generators.video_generator import (
        NewApiVideoError,
        NewApiVideoGenerator,
    )

    _patch_video_egress_seams(monkeypatch)
    generator = NewApiVideoGenerator(
        api_key="personal-task-c",
        endpoint="https://gateway.invalid/v1",
        model="seedance-1.0-pro-fast",
    )

    async def fail_submit(*_args, **_kwargs):
        raise NewApiVideoError("insufficient credits")

    monkeypatch.setattr(generator, "_post_json", fail_submit)

    with pytest.raises(NewApiVideoError, match="insufficient credits"):
        await generator.generate(
            image_path="",
            prompt="probe",
            output_path=str(tmp_path / "out.mp4"),
        )
