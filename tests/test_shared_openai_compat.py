"""shared/openai_compat 公共层契约：规范化 / 头构造 / SSE / 错误映射 / 下载。"""

from __future__ import annotations

import httpx

from novelvideo.shared import openai_compat


def test_normalize_base_url_appends_single_v1() -> None:
    assert openai_compat.normalize_base_url("http://host:3000") == "http://host:3000/v1"
    assert openai_compat.normalize_base_url("http://host:3000/") == "http://host:3000/v1"


def test_normalize_base_url_keeps_existing_v1() -> None:
    assert openai_compat.normalize_base_url("http://host:3000/v1") == "http://host:3000/v1"
    assert openai_compat.normalize_base_url("http://host:3000/v1/") == "http://host:3000/v1"
    assert (
        openai_compat.normalize_base_url("  http://host:3000/v1  ")
        == "http://host:3000/v1"
    )


def test_normalize_base_url_empty() -> None:
    assert openai_compat.normalize_base_url("") == ""
    assert openai_compat.normalize_base_url(None) == ""


def test_auth_headers_and_openrouter_extra() -> None:
    assert openai_compat.auth_headers("sk-1") == {
        "Authorization": "Bearer sk-1",
        "Content-Type": "application/json",
    }
    headers = openai_compat.openrouter_headers(
        "sk-2", referer="https://r.test", title="T"
    )
    assert headers == {
        "Authorization": "Bearer sk-2",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://r.test",
        "X-Title": "T",
    }


def test_openrouter_headers_defaults_match_legacy_identity() -> None:
    headers = openai_compat.openrouter_headers("sk")
    assert headers["HTTP-Referer"] == "https://novelvideo.ai"
    assert headers["X-Title"] == "NovelVideo Studio"


def test_chat_completions_url_trims_trailing_slash() -> None:
    assert (
        openai_compat.chat_completions_url("http://host:3000/v1/")
        == "http://host:3000/v1/chat/completions"
    )


def test_resolve_credentials_strips_and_forwards_overrides(monkeypatch) -> None:
    import novelvideo.config as config

    calls: dict[str, object] = {}

    def fake_credentials(**kwargs):
        calls.update(kwargs)
        return (" sk-1 ", " http://host:3000/v1 ")

    monkeypatch.setattr(config, "get_newapi_runtime_credentials", fake_credentials)

    assert openai_compat.resolve_credentials(
        api_key_override="override-key", env_api_key="MODEL_API_KEY"
    ) == ("sk-1", "http://host:3000/v1")
    assert calls["api_key_override"] == "override-key"
    assert calls["env_api_key"] == "MODEL_API_KEY"
    assert calls["env_base_url"] == "NEWAPI_BASE_URL"


def test_error_detail_prefers_nested_error_message() -> None:
    body = '{"error": {"message": "boom", "code": "x"}}'
    assert openai_compat.error_detail(body) == "boom"


def test_error_detail_fallbacks() -> None:
    assert openai_compat.error_detail('{"message": "m"}') == "m"
    assert openai_compat.error_detail('{"detail": "d"}') == "d"
    assert openai_compat.error_detail('{"error": "plain"}') == "plain"
    assert openai_compat.error_detail("raw text") == "raw text"


def test_error_detail_truncates_raw_body() -> None:
    assert len(openai_compat.error_detail("x" * 500)) == 300
    assert len(openai_compat.error_detail("x" * 500, limit=10)) == 10


def test_error_message_format() -> None:
    assert (
        openai_compat.error_message(401, '{"error": {"message": "nope"}}')
        == "公共网关返回 401：nope"
    )
    assert openai_compat.error_message(500, "") == "公共网关返回 500：无详情"
    assert (
        openai_compat.error_message(400, "bad", prefix="OpenRouter")
        == "OpenRouter返回 400：bad"
    )


def test_first_message_text_str_and_parts() -> None:
    assert (
        openai_compat.first_message_text(
            {"choices": [{"message": {"content": "  hi  "}}]}
        )
        == "hi"
    )
    assert (
        openai_compat.first_message_text(
            {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
        )
        == "ab"
    )


def test_first_message_text_empty_shapes() -> None:
    assert openai_compat.first_message_text({}) == ""
    assert openai_compat.first_message_text({"choices": []}) == ""
    assert (
        openai_compat.first_message_text(
            {"choices": [{"message": {"content": None}}]}
        )
        == ""
    )
    assert openai_compat.first_message_text({"choices": ["bad"]}) == ""
    assert openai_compat.first_message_text({"choices": [{"message": None}]}) == ""


class _LinesResponse:
    """只实现 iter_sse_json 需要的最小协议（aiter_lines）。"""

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


async def test_iter_sse_json_parses_and_stops_at_done() -> None:
    response = _LinesResponse(
        [
            "",
            ": comment",
            'data: {"a": 1}',
            "data: not-json",
            'data: [DONE]',
            'data: {"a": 2}',
        ]
    )
    chunks = [chunk async for chunk in openai_compat.iter_sse_json(response)]
    assert chunks == [{"a": 1}]


async def test_download_bytes_returns_content_and_stripped_type(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        headers = {"content-type": "image/png; charset=utf-8"}
        content = b"png-bytes"

        def raise_for_status(self):
            captured["raised"] = True

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            captured["follow_redirects"] = kwargs.get("follow_redirects")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    payload, content_type = await openai_compat.download_bytes(
        "https://cdn.test/a.png", timeout=42.0
    )
    assert payload == b"png-bytes"
    assert content_type == "image/png"
    assert captured == {
        "timeout": 42.0,
        "follow_redirects": True,
        "url": "https://cdn.test/a.png",
        "raised": True,
    }