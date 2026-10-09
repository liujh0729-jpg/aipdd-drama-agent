"""Upstream provider quota detection.

This project does no local credit accounting: the CE usage meter never reserved,
charged or refunded anything, so nothing here can gate a task on a balance.
What *is* real is the provider side — Ark / AIPDD / susciyuan accounts run out of
quota, and the gateway surfaces that as an error message. Those failures must
stop the retry loops instead of burning the whole attempt budget, which is the
only job of this module.

Names carry the corrected semantics: ``billing`` described a platform-side charge
that never happened; ``upstream quota`` describes what actually aborts a task.
"""

from __future__ import annotations

from typing import Any

from novelvideo.shared.billing_errors import find_billing_error, iter_exception_chain

UPSTREAM_QUOTA_EXCEEDED_CODE = "UPSTREAM_QUOTA_EXCEEDED"
UPSTREAM_QUOTA_EXCEEDED_MESSAGE = "上游模型额度不足，请检查 Ark/AIPDD 账户余额"

# Text fragments upstream providers use when the *account* is out of quota.
# Kept identical to the historical classifier so no task changes outcome: this is
# a rename, not a widening of what counts as fatal.
_UPSTREAM_QUOTA_TEXT_MARKERS = (
    "insufficient credits",
    "insufficient_credits",
    UPSTREAM_QUOTA_EXCEEDED_CODE.lower(),
)


def is_upstream_quota_error(
    exc: BaseException | None = None, message: str = ""
) -> bool:
    """True when the failure is an upstream account-quota exhaustion."""
    combined = " ".join(str(item) for item in iter_exception_chain(exc))
    if message:
        combined = f"{combined} {message}"
    normalized = combined.lower()
    return any(marker in normalized for marker in _UPSTREAM_QUOTA_TEXT_MARKERS)


def is_fatal_upstream_quota_error(
    exc: BaseException | None = None, message: str = ""
) -> bool:
    """Retry-loop guard: quota exhaustion will not heal on the next attempt.

    Composition kept byte-for-byte identical to the historical
    ``is_fatal_billing_error``: a typed billing envelope raised by our own
    boundary is still fatal, and so is upstream quota text. Only the credit-side
    exception classes that no longer exist were dropped from the classifier.
    """
    return find_billing_error(exc) is not None or is_upstream_quota_error(
        exc, message
    )


def upstream_quota_payload(exc: BaseException | None = None) -> dict[str, Any]:
    """User-facing failure payload for a quota-exhausted task."""
    return {
        "error_code": UPSTREAM_QUOTA_EXCEEDED_CODE,
        "message": UPSTREAM_QUOTA_EXCEEDED_MESSAGE,
    }


__all__ = [
    "UPSTREAM_QUOTA_EXCEEDED_CODE",
    "UPSTREAM_QUOTA_EXCEEDED_MESSAGE",
    "is_fatal_upstream_quota_error",
    "is_upstream_quota_error",
    "upstream_quota_payload",
]
