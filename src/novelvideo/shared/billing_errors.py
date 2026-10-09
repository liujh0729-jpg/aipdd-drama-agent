"""Typed billing failures still renderable by the application-level HTTP handlers.

[aipdd fork delta] WP-D: 本项目的额度（积分）体系已整体拆除——预约 / 结算 / 退还
全部不再存在。这里只保留两样东西：

1. `iter_exception_chain`：authz / 上游配额判定都要沿异常链找一次原因，它不属于
   计费语义，只是共用的异常遍历工具。
2. `BillingError` 家族：EE 侧仍会抛「计费规则未配置 / 计费身份无效」这类类型化
   错误，`api/app.py`、`api/task_start_errors.py`、`api/routes/chat.py` 依赖它们
   把失败渲染成契约信封而不是裸 5xx。CE 下没有抛出点，属兼容保留。

「上游账户余额不足」不属于本模块：它已迁到 `shared/upstream_quota_errors.py`。
"""

from __future__ import annotations

from typing import Any

BILLING_RULE_NOT_CONFIGURED_CODE = "BILLING_RULE_NOT_CONFIGURED"
BILLING_RULE_NOT_CONFIGURED_MESSAGE = "计费规则未配置，请联系管理员设置积分规则"
FEATURE_BILLING_IDENTITY_INVALID_CODE = "FEATURE_BILLING_IDENTITY_INVALID"
FEATURE_BILLING_IDENTITY_INVALID_MESSAGE = "计费身份无效或无法确定，请联系管理员"
FEATURE_BILLING_IDENTITY_REASON_MAX_LENGTH = 512


class BillingError(RuntimeError):
    """Typed billing failure that every render point can surface verbatim."""

    error_code: str
    http_status: int
    user_message: str

    def details(self) -> dict[str, Any]:
        """Extra, already-redacted fields merged into the rendered payload."""
        return {}


class BillingRuleNotConfiguredError(BillingError):
    """Raised when a billable action has no usable admin pricing rule."""

    error_code = BILLING_RULE_NOT_CONFIGURED_CODE
    http_status = 409
    user_message = BILLING_RULE_NOT_CONFIGURED_MESSAGE

    def __init__(self, *, kind: str, key: str) -> None:
        self.kind = str(kind or "").strip()
        self.key = str(key or "").strip()
        super().__init__(
            f"billing rule is not configured for {self.kind or 'billing'}:{self.key}"
        )

    def details(self) -> dict[str, Any]:
        return {"billing_kind": self.kind, "billing_key": self.key}


class FeatureBillingIdentityError(BillingError):
    """Raised when the billing identity for a feature cannot be trusted."""

    error_code = FEATURE_BILLING_IDENTITY_INVALID_CODE
    http_status = 409
    user_message = FEATURE_BILLING_IDENTITY_INVALID_MESSAGE

    def __init__(self, *, internal_reason: str = "") -> None:
        safe_reason = internal_reason if type(internal_reason) is str else ""
        if len(safe_reason) > FEATURE_BILLING_IDENTITY_REASON_MAX_LENGTH:
            safe_reason = (
                safe_reason[: FEATURE_BILLING_IDENTITY_REASON_MAX_LENGTH - 3] + "..."
            )
        super().__init__(safe_reason or "feature billing identity is invalid")


def iter_exception_chain(exc: BaseException | None):
    """Yield an exception and its explicit/implicit causes once each."""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def find_billing_rule_not_configured_error(
    exc: BaseException | None,
) -> BillingRuleNotConfiguredError | None:
    for item in iter_exception_chain(exc):
        if isinstance(item, BillingRuleNotConfiguredError):
            return item
    return None


def find_billing_error(exc: BaseException | None) -> BillingError | None:
    for item in iter_exception_chain(exc):
        if isinstance(item, BillingError):
            return item
    return None


def billing_error_payload(exc: BaseException | None) -> dict[str, Any]:
    err = find_billing_error(exc)
    if err is None:
        return {}
    payload: dict[str, Any] = {
        "error_code": err.error_code,
        "message": err.user_message,
    }
    payload.update(err.details())
    return payload


def billing_rule_not_configured_payload(
    exc: BaseException | None = None,
) -> dict[str, Any]:
    err = find_billing_rule_not_configured_error(exc)
    payload: dict[str, Any] = {
        "error_code": BILLING_RULE_NOT_CONFIGURED_CODE,
        "message": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
    }
    if err is not None:
        payload.update({"billing_kind": err.kind, "billing_key": err.key})
    return payload


__all__ = [
    "BILLING_RULE_NOT_CONFIGURED_CODE",
    "BILLING_RULE_NOT_CONFIGURED_MESSAGE",
    "BillingError",
    "BillingRuleNotConfiguredError",
    "FEATURE_BILLING_IDENTITY_INVALID_CODE",
    "FEATURE_BILLING_IDENTITY_INVALID_MESSAGE",
    "FEATURE_BILLING_IDENTITY_REASON_MAX_LENGTH",
    "FeatureBillingIdentityError",
    "billing_error_payload",
    "billing_rule_not_configured_payload",
    "find_billing_error",
    "find_billing_rule_not_configured_error",
    "iter_exception_chain",
]
