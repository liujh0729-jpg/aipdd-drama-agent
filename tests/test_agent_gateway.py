"""agent/gateway 公共网关门面契约：凭据规范化 / SSE 流 / 错误映射 / 下载。"""

from __future__ import annotations

import httpx
import pytest

from novelvideo.agent import gateway


def test_gateway_credentials_normalizes_base_url(monkeypatch) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(
        config,
        "get_newapi_runtime_credentials",
        lambda **kwargs: ("sk-1", "http://host:3000"),
    )
    assert gateway.gateway_credentials() == ("sk-1", "http://host:3000/v1")


def test_gateway_credentials_keeps_existing_v1(monkeypatch) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(
        config,
        "get_newapi_runtime_credentials",
        lambda **kwargs: ("sk-1", "http://host:3000/v1/"),
    )
    assert gateway.gateway_credentials() == ("sk-1", "http://host:3000/v1")


def test_gateway_credentials_missing_raises(monkeypatch) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(
        config, "get_newapi_runtime_credentials", lambda **kwargs: ("", "")
    )
    with pytest.raises(gateway.GatewayNotConfigured):
        gateway.gateway_credentials()


async def test_chat_stream_parses_sse_and_builds_url(monkeypatch) -> None:
    last: dict[str, object] = {}

    class StreamResponse:
        status_code = 200

        async def aiter_lines(self):
            for line in (
                "",
                ": ping",
                'data: {"choices": [{"delta": {"content": "hi"}}]}',
                "data: broken",
                "data: [DONE]",
                'data: {"choices": [{"delta": {"content": "late"}}]}',
            ):
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            last["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, *, headers, json):
            last["method"] = method
            last["url"] = url
            last["headers"] = headers
            return StreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    chunks = [
        chunk
        async for chunk in gateway.chat_stream(
            api_key="sk-1", base_url="http://host:3000/v1/", payload={"model": "m"}
        )
    ]
    assert chunks == [{"choices": [{"delta": {"content": "hi"}}]}]
    assert last["method"] == "POST"
    assert last["url"] == "http://host:3000/v1/chat/completions"
    assert last["headers"] == {
        "Authorization": "Bearer sk-1",
        "Content-Type": "application/json",
    }
    timeout = last["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == gateway.CONNECT_TIMEOUT_SECONDS
    assert timeout.read == gateway.CHAT_READ_TIMEOUT_SECONDS


async def test_chat_stream_maps_http_error(monkeypatch) -> None:
    class StreamResponse:
        status_code = 401

        async def aread(self):
            return b'{"error": {"message": "bad key"}}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, *, headers, json):
            return StreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await anext(
            gateway.chat_stream(
                api_key="sk-1", base_url="http://host:3000/v1", payload={}
            )
        )
    assert str(exc_info.value) == "公共网关返回 401：bad key"


async def test_chat_stream_maps_timeout(monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, *, headers, json):
            raise httpx.ReadTimeout("slow upstream")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await anext(
            gateway.chat_stream(
                api_key="sk-1", base_url="http://host:3000/v1", payload={}
            )
        )
    assert str(exc_info.value) == "公共网关响应超时，请稍后重试"


async def test_generate_image_sync_response(monkeypatch) -> None:
    last: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": "QUJD"}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            last["url"] = url
            last["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await gateway.generate_image(
        api_key="sk-1",
        base_url="http://host:3000/v1",
        model="img-model",
        prompt="draw",
        size="1024x1024",
    )
    assert result == {"b64": "QUJD", "url": ""}
    assert last["url"] == "http://host:3000/v1/images/generations"
    assert last["json"] == {
        "model": "img-model",
        "prompt": "draw",
        "n": 1,
        "size": "1024x1024",
    }


async def test_generate_image_maps_http_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 500
        text = '{"message": "upstream exploded"}'

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await gateway.generate_image(
            api_key="sk-1", base_url="http://host:3000/v1", model="m", prompt="p"
        )
    assert str(exc_info.value) == "公共网关返回 500：upstream exploded"


async def test_download_media_returns_bytes_and_strips_charset(monkeypatch) -> None:
    class FakeResponse:
        headers = {"content-type": "image/jpeg; charset=binary"}
        content = b"jpeg-bytes"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    payload, content_type = await gateway.download_media("https://cdn.test/a.jpg")
    assert payload == b"jpeg-bytes"
    assert content_type == "image/jpeg"


async def test_download_media_maps_transport_error(monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await gateway.download_media("https://cdn.test/a.jpg")
    assert str(exc_info.value).startswith("图片下载失败：")


# ---------------------------------------------------------------------------
# generate_video：AIPDD /videos 协议与标准 /video/generations 提交
# ---------------------------------------------------------------------------


async def test_generate_video_aipdd_submits_and_polls(monkeypatch) -> None:
    """[aipdd fork delta] AIPDD host 走 /videos：提交后轮询到 result_url（data 解包）。"""
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)
    monkeypatch.setattr(gateway, "VIDEO_POLL_INTERVAL_SECONDS", 0)
    calls: list[tuple[str, str]] = []
    poll_count = {"n": 0}

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            calls.append(("POST", url))
            assert json["duration"] == 5
            assert json["resolution"] == "720p"
            return FakeResponse(
                {"code": 0, "data": {"task_id": "t1", "status": "queued"}}
            )

        async def get(self, url, *, headers):
            calls.append(("GET", url))
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return FakeResponse({"data": {"task_id": "t1", "status": "processing"}})
            return FakeResponse(
                {
                    "data": {
                        "task_id": "t1",
                        "status": "succeeded",
                        "result_url": "https://cdn.test/v.mp4",
                    }
                }
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await gateway.generate_video(
        api_key="sk-1",
        base_url="http://aipdd.example.com/v1",
        model="vid-1",
        prompt="fly",
        resolution="720p",
        duration=5,
        ratio="16:9",
    )
    assert result == {"url": "https://cdn.test/v.mp4"}
    assert calls[0] == ("POST", "http://aipdd.example.com/v1/videos")
    assert calls[1] == ("GET", "http://aipdd.example.com/v1/videos/t1")
    assert calls[2][0] == "GET"


async def test_generate_video_standard_path_returns_inline_url(monkeypatch) -> None:
    """非 AIPDD host 走 /video/generations；提交响应自带视频地址时不轮询。"""
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)
    last: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"url": "https://cdn.test/direct.mp4"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            last["url"] = url
            last["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await gateway.generate_video(
        api_key="sk-1", base_url="http://host:3000/v1", model="m", prompt="p"
    )
    assert result == {"url": "https://cdn.test/direct.mp4"}
    assert last["url"] == "http://host:3000/v1/video/generations"
    assert last["json"] == {"model": "m", "prompt": "p"}


async def test_generate_video_maps_failure_status(monkeypatch) -> None:
    """轮询到失败状态：直接报错并带上游错误消息。"""
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)
    monkeypatch.setattr(gateway, "VIDEO_POLL_INTERVAL_SECONDS", 0)

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse({"task_id": "t9"})

        async def get(self, url, *, headers):
            return FakeResponse(
                {"task_id": "t9", "status": "failed", "error": {"message": "bad prompt"}}
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await gateway.generate_video(
            api_key="sk-1", base_url="http://host:3000/v1", model="m", prompt="p"
        )
    assert str(exc_info.value) == "视频任务失败：bad prompt"


async def test_generate_video_requires_task_id(monkeypatch) -> None:
    """提交响应既无地址也无任务编号：直接报格式错误。"""
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"status": "queued"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(gateway.GatewayError) as exc_info:
        await gateway.generate_video(
            api_key="sk-1", base_url="http://host:3000/v1", model="m", prompt="p"
        )
    assert str(exc_info.value) == "视频生成响应缺少任务编号"