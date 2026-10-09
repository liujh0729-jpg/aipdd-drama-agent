"""Helpers for showing provider errors without leaking secrets."""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;]+"),
    re.compile(r"(?i)(token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;]+"),
    re.compile(r"(?i)(secret[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;]+"),
]


def redact_secrets(value: Any) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[redacted]", text)
    return text


def safe_exception_message(exc: BaseException) -> str:
    message = redact_secrets(exc)
    return message or exc.__class__.__name__


__all__ = ["redact_secrets", "safe_exception_message"]
