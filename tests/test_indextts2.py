from pathlib import Path

import pytest

# [aipdd fork delta] WP-D: CE 额度体系（预约 / 确认 / 退款）已拆除，
# 唯一保留的中止判定是上游账户额度文案分类，故不再导入积分类异常。

pytestmark = pytest.mark.m07

# [aipdd fork delta] WP-D: 上游账户额度不足时回的真实文案片段。
_QUOTA_ERROR_TEXT = "insufficient credits for user usr_1: required 40, available 8"


class _FakeResponse:
    def __init__(
        self, payload=None, content=b"audio-bytes", headers=None, status_code=200
    ):
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {"content-type": "application/json"}
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    calls = []
    post_response = _FakeResponse(
        {"audio": {"url": "https://example.com/generated.mp3"}}
    )
    get_response = _FakeResponse(content=b"generated-mp3")

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, headers=None, json=None):
        self.calls.append(("post", url, headers, json))
        return self.post_response

    async def get(self, url):
        self.calls.append(("get", url, None, None))
        return self.get_response


class _FakeDirectClient:
    """[aipdd fork delta] AIPDD 原生协议假客户端：回放 提交 → 轮询 → 下载。"""

    calls = []
    submit_payload = {"id": "task-tts-1", "status": "QUEUED"}
    poll_payloads = []
    download_content = b"wav-bytes"
    download_headers = {"content-type": "audio/wav"}

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, headers=None, json=None):
        self.calls.append(("post", url, headers, json))
        return _FakeResponse(
            payload=self.submit_payload,
            headers={"content-type": "application/json"},
        )

    async def get(self, url, *, headers=None, timeout=None):
        self.calls.append(("get", url, headers, timeout))
        if url.endswith(f"/audio/speech/{self.submit_payload.get('id')}"):
            return _FakeResponse(
                payload=self.poll_payloads.pop(0),
                headers={"content-type": "application/json"},
            )
        return _FakeResponse(
            content=self.download_content, headers=self.download_headers
        )


async def _async_noop(*_args, **_kwargs):
    return None


def _patch_platform_gateway(monkeypatch) -> None:
    """直连模式公共桩：未显式传凭据时必须能回落到生效网关凭据。"""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "novelvideo.config.get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(
            api_key="gateway-key", base_url="http://newapi.test/v1"
        ),
    )


def _patch_direct_pipeline(monkeypatch, indextts2) -> list:
    """直连路径公共桩：零轮询间隔 + 下载后转码 / 时长 / 计量打桩。"""

    async def _duration(_path):
        return 1.25

    transcodes: list = []

    async def _transcode(source_path, output_path):
        transcodes.append((source_path, output_path))
        output_path.write_bytes(b"transcoded-mp3")

    monkeypatch.setattr(indextts2, "AIPDD_AUDIO_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(indextts2, "_transcode_audio_to_mp3", _transcode)
    monkeypatch.setattr(indextts2, "_audio_duration_seconds", _duration)
    monkeypatch.setattr(indextts2, "record_tts_model_call", _async_noop)
    return transcodes


@pytest.mark.asyncio
async def test_indextts2_newapi_posts_audio_speech_schema(monkeypatch, tmp_path):
    import httpx

    from novelvideo.generators.indextts2 import IndexTTS2Client

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.post_response = _FakeResponse(
        content=b"generated-wav",
        headers={"content-type": "audio/wav", "x-oneapi-request-id": "req_tts_1"},
    )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    output_path = tmp_path / "beat_03.mp3"
    client = IndexTTS2Client(
        api_key="newapi-test-key",
        endpoint="http://newapi.test/v1",
        model="index-tts-2",
        timeout_seconds=12,
    )
    result = await client.generate(
        prompt="你终于来了。",
        audio_url="data:audio/wav;base64,abc",
        output_path=output_path,
        emotion_prompt="压低声音，克制但急切",
    )

    assert result.success is True
    assert output_path.read_bytes() == b"generated-wav"
    assert _FakeAsyncClient.calls == [
        (
            "post",
            "http://newapi.test/v1/audio/speech",
            {
                "Authorization": "Bearer newapi-test-key",
                "Content-Type": "application/json",
            },
            {
                "model": "index-tts-2",
                "input": "你终于来了。",
                "metadata": {
                    "audio_url": "data:audio/wav;base64,abc",
                    "should_use_prompt_for_emotion": True,
                    "emotion_prompt": "压低声音，克制但急切",
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_indextts2_reraises_upstream_quota_error(monkeypatch, tmp_path):
    # [aipdd fork delta] WP-D: 旧的「积分类异常必须冒泡」改守上游账户额度文案：
    # 真实上游只回一段文本，分类器必须把它识别成致命错误并原样抛出，
    # 绝不被吞成 success=False 的返回值。
    import httpx

    from novelvideo.generators.indextts2 import IndexTTS2Client

    _FakeAsyncClient.calls = []

    class QuotaRejectedClient(_FakeAsyncClient):
        async def post(self, url, *, headers=None, json=None):
            raise RuntimeError(_QUOTA_ERROR_TEXT)

    monkeypatch.setattr(httpx, "AsyncClient", QuotaRejectedClient)

    client = IndexTTS2Client(
        api_key="newapi-test-key",
        endpoint="http://newapi.test/v1",
        model="index-tts-2",
        timeout_seconds=12,
    )

    with pytest.raises(RuntimeError, match="insufficient credits"):
        await client.generate(
            prompt="测试",
            audio_url="data:audio/wav;base64,abc",
            output_path=tmp_path / "beat_03.mp3",
        )


@pytest.mark.asyncio
async def test_indextts2_returns_failure_without_api_key(tmp_path):
    from novelvideo.generators.indextts2 import IndexTTS2Client

    result = await IndexTTS2Client(api_key="", model="index-tts-2").generate(
        prompt="测试",
        audio_url="https://example.com/reference.wav",
        output_path=Path(tmp_path / "out.mp3"),
    )

    assert result.success is False
    assert result.error == "DramaClawAPI API key not set"


@pytest.mark.asyncio
async def test_indextts2_aipdd_direct_uses_native_task_protocol(monkeypatch, tmp_path):
    """[aipdd fork delta] 绑定 aipdd 渠道的语音模型直连原生异步任务协议。

    LocalNewAPI 代理会丢掉参考音频与情绪字段（上游 400「缺少必填参数：
    audio」），直连必须：顶层 audio=data URL、提交取 id、轮询取 content_url、
    下载非 mp3 交付时转码到输出路径。
    """
    import httpx

    from novelvideo import model_gateway_settings
    from novelvideo.generators import indextts2
    from novelvideo.generators.indextts2 import IndexTTS2Client

    _FakeDirectClient.calls = []
    _FakeDirectClient.poll_payloads = [
        {"status": "QUEUED"},
        {"status": "SUCCEEDED", "content_url": "https://oss.example/0.wav"},
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _FakeDirectClient)
    _patch_platform_gateway(monkeypatch)
    monkeypatch.setattr(
        model_gateway_settings,
        "resolve_media_provider_override",
        lambda model, media_type=None: (
            ("https://api.aipdd.work/v1", "channel-key", "aipdd")
            if model == "ap-indextts-2" and media_type == "audio"
            else None
        ),
    )
    transcodes = _patch_direct_pipeline(monkeypatch, indextts2)

    output_path = tmp_path / "beat_03.mp3"
    client = IndexTTS2Client(model="ap-indextts-2", timeout_seconds=12)
    result = await client.generate(
        prompt="你终于来了。",
        audio_url="data:audio/wav;base64,abc",
        output_path=output_path,
        emotion_prompt="压低声音，克制但急切",
    )

    assert result.success is True
    assert result.duration_seconds == 1.25
    assert output_path.read_bytes() == b"transcoded-mp3"
    assert _FakeDirectClient.calls[0] == (
        "post",
        "https://api.aipdd.work/v1/audio/speech",
        {"Authorization": "Bearer channel-key", "Content-Type": "application/json"},
        {
            "model": "ap-indextts-2",
            "input": "你终于来了。",
            "response_format": "mp3",
            "audio": "data:audio/wav;base64,abc",
            "metadata": {
                "should_use_prompt_for_emotion": True,
                "emotion_prompt": "压低声音，克制但急切",
            },
        },
    )
    assert [call[1] for call in _FakeDirectClient.calls if call[0] == "get"] == [
        "https://api.aipdd.work/v1/audio/speech/task-tts-1",
        "https://api.aipdd.work/v1/audio/speech/task-tts-1",
        "https://oss.example/0.wav",
    ]
    assert transcodes == [(output_path.with_name("beat_03.aipdd-src.wav"), output_path)]
    assert not list(tmp_path.glob("*.aipdd-src*"))


@pytest.mark.asyncio
async def test_indextts2_unselected_model_uses_panel_audio_default(
    monkeypatch, tmp_path
):
    """[aipdd fork delta] 未选模型（内置默认名）时按面板 audio 默认条目路由。"""
    import httpx

    from novelvideo import model_gateway_settings
    from novelvideo.generators import indextts2
    from novelvideo.generators.indextts2 import IndexTTS2Client

    _FakeDirectClient.calls = []
    _FakeDirectClient.poll_payloads = [
        {"status": "SUCCEEDED", "content_url": "https://oss.example/0.wav"}
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _FakeDirectClient)
    _patch_platform_gateway(monkeypatch)
    monkeypatch.setattr(
        model_gateway_settings,
        "resolve_media_provider_override",
        lambda model, media_type=None: (
            ("https://api.aipdd.work/v1", "channel-key", "aipdd")
            if model == "ap-indextts-2"
            else None
        ),
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "resolve_default_media_model",
        lambda media_type: "ap-indextts-2" if media_type == "audio" else "",
    )
    _patch_direct_pipeline(monkeypatch, indextts2)

    result = await IndexTTS2Client(timeout_seconds=12).generate(
        prompt="未选模型的默认路由。",
        audio_url="data:audio/wav;base64,abc",
        output_path=tmp_path / "beat_03.mp3",
    )

    assert result.success is True
    assert _FakeDirectClient.calls[0][3]["model"] == "ap-indextts-2"


@pytest.mark.asyncio
async def test_indextts2_bound_non_aipdd_channel_replaces_endpoint(
    monkeypatch, tmp_path
):
    """[aipdd fork delta] 绑定非 aipdd 渠道时替换 base+key，保持 OpenAI 协议。"""
    import httpx

    from novelvideo import model_gateway_settings
    from novelvideo.generators import indextts2
    from novelvideo.generators.indextts2 import IndexTTS2Client

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.post_response = _FakeResponse(
        payload={"audio": {"url": "https://media.example/generated.mp3"}},
    )
    _FakeAsyncClient.get_response = _FakeResponse(content=b"generated-mp3")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _patch_platform_gateway(monkeypatch)
    monkeypatch.setattr(
        model_gateway_settings,
        "resolve_media_provider_override",
        lambda model, media_type=None: (
            "https://relay.example/v1",
            "relay-key",
            "custom",
        ),
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "resolve_default_media_model",
        lambda media_type: "",
    )

    async def _duration(_path):
        return 2.0

    monkeypatch.setattr(indextts2, "_audio_duration_seconds", _duration)
    monkeypatch.setattr(indextts2, "record_tts_model_call", _async_noop)

    output_path = tmp_path / "beat_03.mp3"
    client = IndexTTS2Client(model="ap-indextts-2", timeout_seconds=12)
    result = await client.generate(
        prompt="换成绑定渠道。",
        audio_url="data:audio/wav;base64,abc",
        output_path=output_path,
    )

    assert result.success is True
    assert output_path.read_bytes() == b"generated-mp3"
    assert _FakeAsyncClient.calls[0] == (
        "post",
        "https://relay.example/v1/audio/speech",
        {"Authorization": "Bearer relay-key", "Content-Type": "application/json"},
        {
            "model": "ap-indextts-2",
            "input": "换成绑定渠道。",
            "metadata": {
                "audio_url": "data:audio/wav;base64,abc",
                "should_use_prompt_for_emotion": True,
            },
        },
    )
    assert _FakeAsyncClient.calls[1] == (
        "get",
        "https://media.example/generated.mp3",
        None,
        None,
    )
