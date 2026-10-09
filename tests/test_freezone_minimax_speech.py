"""[aipdd fork delta req46] MiniMax Speech 2.8 直连（t2a_v2）的分流与协议回归。

零网络：上游 POST 一律打桩 ``minimax_api.post_json``。钉四件事——

1. **分流判定**：只有「模型绑定的渠道是 MiniMax」或「生效网关本身就指向 MiniMax」
   且模型名属于 speech 族时才直连，其余（网关语音模型、IndexTTS2 系）行为与
   req46 之前逐字一致 —— 分错渠道等于把请求发到不认这条协议的地址上。
2. **请求体形状**：``voice_setting`` / ``audio_setting`` 两组参数、``stream=false``
   与 ``output_format=hex`` 都是上游契约；码率只对 mp3 有意义。档位越界在提交前
   就报错，不等上游回一屏 2013。
3. **hex 落盘**：``data.audio`` 是十六进制字符串，解码后必须逐字节等于产物；
   时长优先取 ``extra_info.audio_length``，没有才本地探测。
4. **失败与边界的文案**：V1 语音接口 HTTP 恒 200、失败写在 ``base_resp``；渠道没配
   默认音色要说清去哪配（本期只支持系统音色）；组织任务在叶内 fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone import audio_node
from novelvideo.freezone import minimax_api
from novelvideo.freezone.minimax_api import (
    PATH_T2A,
    MiniMaxChannel,
    MiniMaxChannelUnavailable,
    MiniMaxError,
)

VOICE_ID = "Chinese (Mandarin)_Lyrical_Voice"


def _channel(**settings: object) -> MiniMaxChannel:
    return MiniMaxChannel(
        provider="minimax",
        api_root="https://api.minimax.io",
        api_key="test-key",
        base_url="https://api.minimax.io/v1",
        settings=dict(settings) or {"minimax": {"voiceId": VOICE_ID}},
    )


class _StubPost:
    def __init__(self, response: dict, *, http_status: int = 200) -> None:
        self.response = response
        self.http_status = http_status
        self.calls: list[tuple[str, dict]] = []

    async def __call__(
        self, channel: MiniMaxChannel, path: str, payload: dict, *, timeout: float = 0.0
    ) -> dict:
        assert channel.api_key, "直连必须带渠道 Key"
        self.calls.append((path, json.loads(json.dumps(payload, default=str))))
        # 与真实 post_json 同口径：先消化两种错误形态，再返回响应体。
        return minimax_api._ensure_business_ok(
            self.response, http_status=self.http_status, endpoint=path
        )


def _install_stub(monkeypatch: pytest.MonkeyPatch, response: dict) -> _StubPost:
    stub = _StubPost(response)
    monkeypatch.setattr(minimax_api, "post_json", stub)
    return stub


def _ok_response(audio_bytes: bytes = b"\xff\xf1\xfb\x90mini") -> dict:
    return {
        "data": {"audio": audio_bytes.hex(), "status": 2},
        "extra_info": {"audio_length": 3210, "audio_sample_rate": 32000},
        "trace_id": "mm-trace-1",
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


@pytest.fixture
def _no_call_log(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    logs: list[dict] = []

    async def fake_log(**kwargs):
        logs.append(kwargs)

    import novelvideo.ports as ports

    monkeypatch.setattr(ports, "update_current_model_call_log", fake_log)
    return logs


# ---------------------------------------------------------------------------
# 1. 分流判定
# ---------------------------------------------------------------------------


def test_speech_family_is_the_only_direct_candidate() -> None:
    assert audio_node.is_minimax_speech_model("speech-2.8-hd") is True
    assert audio_node.is_minimax_speech_model("Speech-2.8-Turbo") is True
    assert audio_node.is_minimax_speech_model("music-3.0") is False
    assert audio_node.is_minimax_speech_model("") is False


def test_channel_bound_to_minimax_routes_direct(monkeypatch) -> None:
    channel = _channel()
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_media_provider_override",
        lambda model, media_type=None: ("https://api.minimax.io/v1", "k", "minimax"),
    )
    monkeypatch.setattr(minimax_api, "resolve_minimax_channel", lambda provider="": channel)
    assert audio_node.minimax_speech_channel("speech-2.8-hd") is channel


def test_channel_bound_to_another_provider_stays_on_gateway(monkeypatch) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_media_provider_override",
        lambda model, media_type=None: ("https://gateway.example/v1", "k", "aipdd"),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("绑定到别的渠道就不该再去解析 MiniMax 渠道")

    monkeypatch.setattr(minimax_api, "resolve_minimax_channel", forbidden)
    assert audio_node.minimax_speech_channel("speech-2.8-hd") is None
    # 非 speech 族连渠道都不必问
    assert audio_node.minimax_speech_channel("music-3.0") is None


def test_gateway_pointing_at_minimax_falls_back_to_host_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_media_provider_override",
        lambda model, media_type=None: None,
    )
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_runtime_credentials",
        lambda **_kwargs: ("env-key", "https://api.minimax.cn/v1"),
    )
    monkeypatch.setattr(minimax_api, "configured_minimax_providers", lambda: [])
    channel = audio_node.minimax_speech_channel("speech-2.8-turbo")
    assert channel is not None
    # 站点根不能留 /v1，否则 t2a 会拼成 /v1/v1/t2a_v2
    assert channel.api_root == "https://api.minimax.cn"
    assert channel.api_key == "env-key"


def test_ordinary_gateway_never_routes_direct(monkeypatch) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_media_provider_override",
        lambda model, media_type=None: None,
    )
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_runtime_credentials",
        lambda **_kwargs: ("env-key", "https://gateway.example/v1"),
    )
    assert audio_node.minimax_speech_channel("speech-2.8-turbo") is None


def test_voice_id_prefers_explicit_then_channel_settings() -> None:
    channel = _channel()
    assert audio_node.minimax_speech_voice_id(channel, "English_Graceful_Lady") == (
        "English_Graceful_Lady"
    )
    assert audio_node.minimax_speech_voice_id(channel) == VOICE_ID
    bare = MiniMaxChannel(
        provider="minimax",
        api_root="https://api.minimax.io",
        api_key="k",
        base_url="https://api.minimax.io/v1",
        settings={},
    )
    assert audio_node.minimax_speech_voice_id(bare) == ""


# ---------------------------------------------------------------------------
# 2. 请求体
# ---------------------------------------------------------------------------


def test_t2a_payload_shape_matches_upstream_contract() -> None:
    payload = audio_node.build_minimax_t2a_payload(
        model="speech-2.8-hd",
        text="雨夜的街头，霓虹倒映在水洼里。",
        voice_id=VOICE_ID,
        emotion="calm",
        speed=1.2,
        volume=0.8,
        pitch=-2,
    )
    assert payload == {
        "model": "speech-2.8-hd",
        "text": "雨夜的街头，霓虹倒映在水洼里。",
        "stream": False,
        "output_format": "hex",
        "voice_setting": {
            "voice_id": VOICE_ID,
            "speed": 1.2,
            "vol": 0.8,
            "pitch": -2,
            "emotion": "calm",
        },
        "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1, "bitrate": 128000},
    }


def test_emotion_is_dropped_when_the_text_does_not_map_to_the_enum() -> None:
    assert audio_node.minimax_speech_emotion("用开心一点的语气念") == "happy"
    assert audio_node.minimax_speech_emotion("第三人称旁白视角，客观冷静") == "calm"
    assert audio_node.minimax_speech_emotion("随便念") == ""
    payload = audio_node.build_minimax_t2a_payload(
        model="speech-2.8-hd", text="一句", voice_id=VOICE_ID, emotion="whisper"
    )
    # whisper 只属于 2.6 两条，2.8 传了会被拒 —— 映射表里根本没有它
    assert "emotion" not in payload["voice_setting"]


@pytest.mark.parametrize(
    "kwargs,expect",
    [
        ({"voice_id": ""}, MiniMaxChannelUnavailable),
        ({"model": "music-3.0"}, ValueError),
        ({"text": "   "}, ValueError),
        ({"speed": 3}, ValueError),
        ({"volume": 0}, ValueError),
        ({"pitch": 13}, ValueError),
        ({"sample_rate": 48000}, ValueError),
        ({"bitrate": 96000}, ValueError),
        ({"audio_format": "aiff"}, ValueError),
    ],
)
def test_out_of_tier_requests_are_rejected_before_submitting(kwargs, expect) -> None:
    base = {"model": "speech-2.8-hd", "text": "一句", "voice_id": VOICE_ID}
    base.update(kwargs)
    with pytest.raises(expect):
        audio_node.build_minimax_t2a_payload(**base)


def test_missing_voice_id_message_is_actionable() -> None:
    with pytest.raises(MiniMaxChannelUnavailable) as raised:
        audio_node.build_minimax_t2a_payload(
            model="speech-2.8-hd", text="一句", voice_id=""
        )
    message = str(raised.value)
    assert "设置" in message and "音色" in message
    assert "系统音色" in message


def test_long_text_reports_actual_length() -> None:
    text = "字" * (audio_node.MINIMAX_T2A_TEXT_MAX_CHARS + 1)
    with pytest.raises(ValueError) as raised:
        audio_node.build_minimax_t2a_payload(
            model="speech-2.8-hd", text=text, voice_id=VOICE_ID
        )
    assert str(len(text)) in str(raised.value)


def test_non_mp3_formats_carry_no_bitrate() -> None:
    payload = audio_node.build_minimax_t2a_payload(
        model="speech-2.8-hd",
        text="一句",
        voice_id=VOICE_ID,
        audio_format="wav",
        sample_rate=44100,
    )
    assert payload["audio_setting"] == {"format": "wav", "sample_rate": 44100, "channel": 1}


# ---------------------------------------------------------------------------
# 3 / 4. 落盘、错误与出网
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_synthesis_writes_decoded_hex_and_logs_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_call_log
) -> None:
    payload_bytes = b"\xff\xf1\xfb\x90real-audio"
    stub = _install_stub(monkeypatch, _ok_response(payload_bytes))
    records: list[dict] = []

    async def fake_record(**kwargs):
        records.append(kwargs)

    import novelvideo.generators.indextts2 as indextts2

    monkeypatch.setattr(indextts2, "record_tts_model_call", fake_record)

    output = tmp_path / "voice.mp3"
    result = await audio_node._generate_freezone_audio_speech_via_minimax_t2a(
        requested_model="speech-2.8-hd",
        channel=_channel(),
        clean_text="雨夜的街头",
        emotion_prompt="用平静的语气",
        output_path=output,
        egress_context=None,
    )

    # 只打一次 t2a_v2，请求体就是建单体那份
    assert [path for path, _body in stub.calls] == [PATH_T2A]
    assert stub.calls[0][1]["voice_setting"]["emotion"] == "calm"
    assert stub.calls[0][1]["text"] == "雨夜的街头"
    assert output.read_bytes() == payload_bytes
    assert result.duration_ms == 3210  # 优先取上游 extra_info.audio_length
    assert result.mime_type == "audio/mpeg"
    # 声线来源必须写明是渠道系统音色：本期不做克隆，不能假装用了项目解说人
    assert result.voice_source == f"minimax:{VOICE_ID}"
    assert result.voice_sha256 == ""
    assert records == [{"model": "speech-2.8-hd", "provider_request_id": "mm-trace-1"}]

    request_log = _no_call_log[0]["request_payload"]
    assert request_log["voice_setting"]["emotion"] == "calm"
    assert "note" in request_log
    # 调用日志里绝不能塞几百 KB 的 hex 原文
    response_log = _no_call_log[1]["response_payload"]
    assert response_log["data"]["audio_hex_length"] == len(payload_bytes) * 2
    assert "audio" not in str(response_log["data"]).replace("audio_hex_length", "")


@pytest.mark.asyncio
async def test_business_failure_in_http_200_surfaces_upstream_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_call_log
) -> None:
    _install_stub(
        monkeypatch,
        {"data": None, "base_resp": {"status_code": 1004, "status_msg": "invalid api key"}},
    )
    with pytest.raises(MiniMaxError) as raised:
        await audio_node._generate_freezone_audio_speech_via_minimax_t2a(
            requested_model="speech-2.8-hd",
            channel=_channel(),
            clean_text="一句",
            emotion_prompt="",
            output_path=tmp_path / "voice.mp3",
            egress_context=None,
        )
    assert "1004" in str(raised.value)
    assert "invalid api key" in str(raised.value)
    assert _no_call_log[-1]["error_message"] == "MiniMaxError"


@pytest.mark.asyncio
async def test_empty_audio_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_call_log
) -> None:
    _install_stub(
        monkeypatch,
        {"data": {"audio": "", "status": 2}, "base_resp": {"status_code": 0}},
    )
    with pytest.raises(MiniMaxError, match="没有返回音频数据"):
        await audio_node._generate_freezone_audio_speech_via_minimax_t2a(
            requested_model="speech-2.8-hd",
            channel=_channel(),
            clean_text="一句",
            emotion_prompt="",
            output_path=tmp_path / "voice.mp3",
            egress_context=None,
        )


@pytest.mark.asyncio
async def test_organization_task_is_denied_before_any_egress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_call_log
) -> None:
    stub = _install_stub(monkeypatch, _ok_response())
    with pytest.raises(MiniMaxError) as raised:
        await audio_node._generate_freezone_audio_speech_via_minimax_t2a(
            requested_model="speech-2.8-hd",
            channel=_channel(),
            clean_text="一句",
            emotion_prompt="",
            output_path=tmp_path / "voice.mp3",
            egress_context=SimpleNamespace(is_organization=True),
        )
    assert "ORG_EGRESS_DENIED" in str(raised.value)
    assert stub.calls == []


# ---------------------------------------------------------------------------
# 5. generate_freezone_audio_speech 的路由
# ---------------------------------------------------------------------------


def _patch_voice_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    narrator = tmp_path / "narrator.mp3"
    narrator.write_bytes(b"reference-voice")

    async def fake_resolve(**_kwargs):
        return (
            "third_person",
            SimpleNamespace(
                audio_path=narrator, sha256="sha", source="project_narrator"
            ),
        )

    monkeypatch.setattr(audio_node, "resolve_speech_voice", fake_resolve)


@pytest.mark.asyncio
async def test_generate_routes_speech_model_to_direct_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_call_log
) -> None:
    _patch_voice_resolution(monkeypatch, tmp_path)
    payload_bytes = b"\xff\xfb\x90direct"
    _install_stub(monkeypatch, _ok_response(payload_bytes))

    async def gateway_must_not_run(**_kwargs):
        raise AssertionError("命中 MiniMax 就不该再打网关 audio/speech")

    monkeypatch.setattr(
        audio_node, "_generate_freezone_audio_speech_via_gateway", gateway_must_not_run
    )
    monkeypatch.setattr(
        audio_node, "minimax_speech_channel", lambda _model: _channel()
    )

    result = await audio_node.generate_freezone_audio_speech(
        username="u",
        project="p",
        project_dir=tmp_path,
        job_id="job-1",
        text="雨夜的街头",
        model="speech-2.8-hd",
        egress_context=SimpleNamespace(is_organization=False),
    )
    assert result.audio_path.read_bytes() == payload_bytes
    assert result.model == "speech-2.8-hd"
    assert result.voice_source.startswith("minimax:")


@pytest.mark.asyncio
async def test_generate_keeps_gateway_path_for_other_audio_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_voice_resolution(monkeypatch, tmp_path)
    monkeypatch.setattr(audio_node, "minimax_speech_channel", lambda _model: None)
    seen: dict[str, str] = {}

    async def fake_gateway(**kwargs):
        seen["model"] = str(kwargs["requested_model"])
        return audio_node.FreezoneAudioSpeechResult(
            audio_path=kwargs["output_path"],
            duration_ms=1,
            mime_type="audio/mpeg",
            model=kwargs["requested_model"],
            voice_source="project_narrator",
            voice_sha256="sha",
        )

    monkeypatch.setattr(
        audio_node, "_generate_freezone_audio_speech_via_gateway", fake_gateway
    )
    result = await audio_node.generate_freezone_audio_speech(
        username="u",
        project="p",
        project_dir=tmp_path,
        job_id="job-2",
        text="一句",
        model="some-gateway-tts",
        egress_context=SimpleNamespace(is_organization=False),
    )
    assert seen == {"model": "some-gateway-tts"}
    assert result.voice_source == "project_narrator"


@pytest.mark.asyncio
async def test_indextts2_family_never_touches_the_minimax_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """IndexTTS2 系（含情绪/复刻参数语义）必须仍走专用客户端，一个判定都不多做。"""

    _patch_voice_resolution(monkeypatch, tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("IndexTTS2 不该去解析 MiniMax 渠道")

    monkeypatch.setattr(audio_node, "minimax_speech_channel", forbidden)

    class _Client:
        def __init__(self, **_kwargs) -> None:
            self.model = "indextts2"

        async def generate(self, **_kwargs):
            return SimpleNamespace(
                success=True, duration_seconds=1.0, error="", output_path=str(tmp_path)
            )

    monkeypatch.setattr(audio_node, "IndexTTS2Client", _Client)
    monkeypatch.setattr(
        audio_node,
        "freezone_audio_speech_output_path",
        lambda _project_dir, _job_id: tmp_path / "voice.mp3",
    )
    (tmp_path / "voice.mp3").write_bytes(b"x")
    result = await audio_node.generate_freezone_audio_speech(
        username="u",
        project="p",
        project_dir=tmp_path,
        job_id="job-3",
        text="一句",
        model="indextts2-mini",
        egress_context=SimpleNamespace(is_organization=False),
    )
    assert result.model
