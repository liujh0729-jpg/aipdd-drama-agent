"""C5 回归：资源请求日志必须对查询串里的凭证/签名参数打码。

SSE 端点用 `get_api_user_or_query`（EventSource 不能带 header，令牌走 query），
静态资源预签名 URL 又把签名放在 query——两者都会被 `_log_resource_requests`
原文 INFO 落盘的话就是凭证进访问/代理日志。
"""

from __future__ import annotations

from novelvideo.api.app import _redact_query


def test_redact_query_masks_credential_and_signature_params() -> None:
    raw = (
        "token=sk-live-abcdef"
        "&access_token=aabbcc"
        "&api_key=zzzzzzzzzzzz"
        "&X-Amz-Signature=deadbeefcafe"
        "&X-Amz-Credential=AKIA123"
        "&sig=abcdef"
        "&secret=hunter2"
        "&episode=3"
        "&revision=7"
    )
    redacted = _redact_query(raw)

    # 敏感值一律不得原文出现。
    for leak in (
        "sk-live-abcdef",
        "aabbcc",
        "zzzzzzzzzzzz",
        "deadbeefcafe",
        "AKIA123",
        "hunter2",
    ):
        assert leak not in redacted

    # 非敏感参数保持可读。
    assert "episode=3" in redacted
    assert "revision=7" in redacted

    # 敏感参数保留键名 + 长度提示，便于排障又不泄露内容。
    assert "token=[redacted:14]" in redacted


def test_redact_query_keeps_benign_query_intact() -> None:
    assert _redact_query("scope=all&summary=true") == "scope=all&summary=true"


def test_redact_query_handles_empty() -> None:
    assert _redact_query("") == ""
