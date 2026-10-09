# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""O7 回归：image_generator 把上游异常塞进 ImageGenResult.error 前必须抹 URL + 截断。"""

from __future__ import annotations

from novelvideo.generators.image_generator import _safe_error_message


def test_safe_error_message_redacts_urls_and_signature() -> None:
    exc = RuntimeError(
        "Client error 400 for request to "
        "http://10.0.0.7:8780/v1/images?X-Signature=SECRET&key=abc failed"
    )
    msg = _safe_error_message(exc)
    assert "<url>" in msg
    # 内网主机与签名/密钥片段一律不回显。
    assert "10.0.0.7" not in msg
    assert "SECRET" not in msg
    assert "abc" not in msg
    # 保留可读的异常类型，便于排障。
    assert msg.startswith("RuntimeError:")


def test_safe_error_message_bounds_length() -> None:
    msg = _safe_error_message(ValueError("x" * 5000))
    assert len(msg) <= 300
