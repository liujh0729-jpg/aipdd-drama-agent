"""[aipdd fork delta req46] MiniMax 音乐直连：渠道解析、双错误形态、五模式请求形状。

零网络零浏览器：所有上游调用都打桩 ``minimax_api.post_json``，只钉两件事——

1. **我们发出去的请求体长什么样**（五种模式的必填/选填/互斥是上游契约，写错一个
   字段就是 2013 参数错误，必须逐字段断言）；
2. **上游两种错误形态怎么变成人能看懂的一句话**（V1 音乐接口 HTTP 恒 200，失败写
   在 ``base_resp``；V2 才是真状态码 + ``error.message``。只按 HTTP 状态判断会把
   「鉴权失败 / 余额不足」读成成功）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone import minimax_api
from novelvideo.freezone import minimax_music
from novelvideo.freezone.minimax_api import (
    CHANNEL_NOT_CONFIGURED_MESSAGE,
    PATH_MUSIC_COVER_PREPROCESS,
    PATH_MUSIC_GENERATION,
    MiniMaxChannel,
    MiniMaxChannelUnavailable,
    MiniMaxError,
)


def _channel(provider: str = "minimax") -> MiniMaxChannel:
    return MiniMaxChannel(
        provider=provider,
        api_root="https://api.minimax.io",
        api_key="test-key",
        base_url="https://api.minimax.io/v1",
        settings={},
    )


class _StubPost:
    """打桩上游 POST：记录每次请求的端点与请求体，按端点返回预设响应。"""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def __call__(
        self, channel: MiniMaxChannel, path: str, payload: dict, *, timeout: float = 0.0
    ) -> dict:
        # 深拷贝一份：断言用的是「发出去那一刻」的形状，不能被后续复用污染。
        self.calls.append((path, json.loads(json.dumps(payload, default=str))))
        if path not in self.responses:
            raise AssertionError(f"未预设的上游端点: {path}")
        body = self.responses[path]
        # 与真实 post_json 同口径：先消化两种错误形态，再返回响应体。
        return minimax_api._ensure_business_ok(body, http_status=200, endpoint=path)


def _install_stub(monkeypatch: pytest.MonkeyPatch, responses: dict[str, dict]) -> _StubPost:
    stub = _StubPost(responses)
    monkeypatch.setattr(minimax_api, "post_json", stub)
    monkeypatch.setattr(
        minimax_music, "resolved_music_channel", lambda provider="": _channel()
    )

    async def _fake_probe(_path: Path) -> float:
        return 30.0

    monkeypatch.setattr(minimax_music, "probe_reference_audio_seconds", _fake_probe)
    return stub


def test_api_root_strips_version_suffix_and_is_idempotent() -> None:
    assert (
        minimax_api.api_root_from_base_url("https://api.minimax.io/v1")
        == "https://api.minimax.io"
    )
    assert (
        minimax_api.api_root_from_base_url("https://api.minimax.cn/v1/")
        == "https://api.minimax.cn"
    )
    root = minimax_api.api_root_from_base_url("https://api.minimax.io/v1")
    assert minimax_api.api_root_from_base_url(root) == root


def test_provider_and_host_detection_cover_both_sites() -> None:
    assert minimax_api.is_minimax_provider("MiniMax_CN")
    assert not minimax_api.is_minimax_provider("openai")
    assert minimax_api.is_minimax_host("https://api.minimax.cn/v1")
    assert minimax_api.is_minimax_host("https://api.minimaxi.com/v1")
    assert not minimax_api.is_minimax_host("https://example.com/v1")


def test_channel_is_read_from_the_settings_channel_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel",
        lambda _provider: {
            "upstreamKey": " sk-1 ",
            "baseUrl": "https://api.minimax.cn/v1",
            "settings": {"minimax": {"voiceId": "voice-1"}},
        },
    )
    channel = minimax_api.resolve_minimax_channel("minimax_cn")
    assert channel.provider == "minimax_cn"
    assert channel.api_key == "sk-1"
    assert channel.api_root == "https://api.minimax.cn"
    assert minimax_api.minimax_channel_extra(channel) == {"voiceId": "voice-1"}


def test_missing_channel_gives_an_actionable_setup_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel", lambda _p: {}
    )
    with pytest.raises(MiniMaxChannelUnavailable) as excinfo:
        minimax_api.resolve_minimax_channel()
    assert str(excinfo.value) == CHANNEL_NOT_CONFIGURED_MESSAGE
    assert "设置" in str(excinfo.value) and "API Key" in str(excinfo.value)


def test_unconfigured_named_site_names_the_site_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel", lambda _p: {}
    )
    with pytest.raises(MiniMaxChannelUnavailable) as excinfo:
        minimax_api.resolve_minimax_channel("minimax_cn")
    assert "minimax_cn" in str(excinfo.value)


def test_unknown_provider_is_rejected_with_the_available_list() -> None:
    with pytest.raises(MiniMaxError) as excinfo:
        minimax_api.resolve_minimax_channel("minimax_eu")
    assert "minimax_eu" in str(excinfo.value)
    assert "minimax_cn" in str(excinfo.value)


def test_v1_base_resp_error_keeps_status_code_and_upstream_text() -> None:
    failure = minimax_api._failure_from_payload(
        {"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}},
        http_status=200,
        endpoint=PATH_MUSIC_GENERATION,
    )
    assert isinstance(failure, MiniMaxError)
    assert failure.status_code == 1004
    assert "1004" in str(failure)
    assert "invalid api key" in str(failure)


def test_v1_error_without_status_msg_still_says_what_to_check() -> None:
    with pytest.raises(MiniMaxError) as excinfo:
        minimax_api._ensure_business_ok(
            {"base_resp": {"status_code": 1008}},
            http_status=200,
            endpoint=PATH_MUSIC_GENERATION,
        )
    assert "1008" in str(excinfo.value)
    assert "API Key" in str(excinfo.value)


def test_v2_error_shape_uses_http_status_and_openai_message() -> None:
    with pytest.raises(MiniMaxError) as excinfo:
        minimax_api._ensure_business_ok(
            {"error": {"code": 1008, "message": "insufficient balance"}},
            http_status=401,
            endpoint=PATH_MUSIC_GENERATION,
        )
    assert "insufficient balance" in str(excinfo.value)
    assert excinfo.value.http_status == 401
    assert excinfo.value.status_code == 1008


def test_http_failure_without_readable_fields_reports_endpoint_and_status() -> None:
    with pytest.raises(MiniMaxError) as excinfo:
        minimax_api._ensure_business_ok(
            {}, http_status=503, endpoint=PATH_MUSIC_COVER_PREPROCESS
        )
    assert "503" in str(excinfo.value)
    assert excinfo.value.endpoint == PATH_MUSIC_COVER_PREPROCESS


def test_success_payload_passes_through_unchanged() -> None:
    body = {"data": {"audio": "6162"}, "base_resp": {"status_code": 0}}
    assert minimax_api._ensure_business_ok(body, http_status=200, endpoint="/v1/x") is body


def test_decode_hex_audio_returns_exact_bytes() -> None:
    raw = bytes([0xFF, 0x00, 0x1A, 0x7B])
    assert minimax_api.decode_hex_audio(raw.hex()) == raw


async def test_write_hex_audio_ignores_whitespace_and_creates_dirs(
    tmp_path: Path,
) -> None:
    raw = bytes([0x01, 0x02, 0x03, 0x04])
    target = tmp_path / "deep" / "job-1.mp3"
    await minimax_api.write_hex_audio(
        f"{raw.hex()[:4]}\n {raw.hex()[4:]}", target, endpoint=PATH_MUSIC_GENERATION
    )
    assert target.read_bytes() == raw


def test_invalid_hex_reports_length_not_raw_binascii_noise() -> None:
    with pytest.raises(MiniMaxError) as excinfo:
        minimax_api.decode_hex_audio("zzzz", endpoint=PATH_MUSIC_GENERATION)
    assert "十六进制" in str(excinfo.value)
    assert "4" in str(excinfo.value)


def test_empty_hex_audio_is_an_error_not_an_empty_file() -> None:
    with pytest.raises(MiniMaxError):
        minimax_api.decode_hex_audio("   ", endpoint=PATH_MUSIC_GENERATION)


def test_one_sentence_mode_sends_prompt_plus_optimizer() -> None:
    payload = minimax_music.build_music_payload(
        mode="one_sentence", model="music-3.0", prompt="轻快的流行摇滚，副歌开阔"
    )
    assert payload["model"] == "music-3.0"
    assert payload["prompt"] == "轻快的流行摇滚，副歌开阔"
    assert payload["lyrics_optimizer"] is True
    assert "lyrics" not in payload
    assert "is_instrumental" not in payload
    assert payload["output_format"] == "hex"
    assert payload["audio_setting"] == {
        "format": "mp3",
        "sample_rate": 44100,
        "bitrate": 256000,
    }


def test_lyrics_mode_sends_lyrics_and_keeps_prompt_optional() -> None:
    lyrics = "[Verse]\n夜色落在窗台\n[Chorus]\n我们一起唱"
    payload = minimax_music.build_music_payload(
        mode="lyrics", model="music-3.0", lyrics=lyrics
    )
    assert payload["lyrics"] == lyrics
    assert "prompt" not in payload
    assert "lyrics_optimizer" not in payload
    with_prompt = minimax_music.build_music_payload(
        mode="lyrics", model="music-3.0", prompt="温暖的民谣", lyrics=lyrics
    )
    assert with_prompt["prompt"] == "温暖的民谣"


def test_instrumental_mode_requires_prompt_and_never_sends_lyrics() -> None:
    payload = minimax_music.build_music_payload(
        mode="instrumental",
        model="music-3.0",
        prompt="钢琴与弦乐的纯音乐，舒缓",
        lyrics="[Verse]\n不该出现的词",
        audio_format="wav",
        sample_rate=32000,
        bitrate=128000,
    )
    assert payload["is_instrumental"] is True
    assert "lyrics" not in payload
    assert payload["audio_setting"] == {
        "format": "wav",
        "sample_rate": 32000,
        "bitrate": 128000,
    }
    with pytest.raises(minimax_music.MusicRequestInvalid) as excinfo:
        minimax_music.build_music_payload(mode="instrumental", model="music-3.0")
    assert "纯音乐必须提供描述" in str(excinfo.value)


def test_cover_mode_needs_cover_model_prompt_and_reference() -> None:
    payload = minimax_music.build_music_payload(
        mode="cover",
        model="music-cover",
        prompt="把这首歌唱成轻快的民谣版本",
        has_reference=True,
    )
    assert payload["model"] == "music-cover"
    assert payload["prompt"] == "把这首歌唱成轻快的民谣版本"
    assert "lyrics" not in payload
    assert "cover_feature_id" not in payload
    with pytest.raises(minimax_music.MusicRequestInvalid) as wrong_model:
        minimax_music.build_music_payload(
            mode="cover", model="music-3.0", prompt="把这首歌唱成轻快的民谣版本"
        )
    assert "cover" in str(wrong_model.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as no_reference:
        minimax_music.build_music_payload(
            mode="cover", model="music-cover", prompt="把这首歌唱成轻快的民谣版本"
        )
    assert "参考音频" in str(no_reference.value)


def test_two_step_cover_with_feature_id_requires_edited_lyrics_and_no_reference() -> None:
    payload = minimax_music.build_music_payload(
        mode="humming",
        model="music-cover",
        prompt="把这段哼唱做成轻快的民谣翻唱版本",
        lyrics="[Verse]\n改过后的歌词",
        cover_feature_id="cf-1",
    )
    assert payload["cover_feature_id"] == "cf-1"
    assert payload["lyrics"] == "[Verse]\n改过后的歌词"
    with pytest.raises(minimax_music.MusicRequestInvalid) as also_reference:
        minimax_music.build_music_payload(
            mode="humming",
            model="music-cover",
            prompt="把这段哼唱做成轻快的民谣翻唱版本",
            lyrics="[Verse]\n改过后的歌词",
            cover_feature_id="cf-1",
            has_reference=True,
        )
    assert "cover_feature_id" in str(also_reference.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as missing_lyrics:
        minimax_music.build_music_payload(
            mode="humming",
            model="music-cover",
            prompt="把这段哼唱做成轻快的民谣翻唱版本",
            cover_feature_id="cf-1",
        )
    assert "改词长度" in str(missing_lyrics.value)


def test_length_bounds_are_enforced_before_sending() -> None:
    with pytest.raises(minimax_music.MusicRequestInvalid) as prompt_too_long:
        minimax_music.build_music_payload(
            mode="one_sentence", model="music-3.0", prompt="词" * 2001
        )
    assert "2000" in str(prompt_too_long.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as cover_prompt_short:
        minimax_music.build_music_payload(
            mode="cover", model="music-cover", prompt="太短了", has_reference=True
        )
    assert "10–300" in str(cover_prompt_short.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as lyrics_too_long:
        minimax_music.build_music_payload(
            mode="lyrics", model="music-3.0", lyrics="词" * 3501
        )
    assert "3500" in str(lyrics_too_long.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as no_lyrics:
        minimax_music.build_music_payload(mode="lyrics", model="music-3.0", prompt="民谣")
    assert "歌词" in str(no_lyrics.value)


def test_enum_and_shape_mistakes_are_rejected_at_submit_time() -> None:
    with pytest.raises(minimax_music.MusicRequestInvalid) as bad_rate:
        minimax_music.build_music_payload(
            mode="one_sentence", model="music-3.0", prompt="轻快", sample_rate=48000
        )
    assert "采样率" in str(bad_rate.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as bad_bitrate:
        minimax_music.build_music_payload(
            mode="one_sentence", model="music-3.0", prompt="轻快", bitrate=999
        )
    assert "码率" in str(bad_bitrate.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as unknown_mode:
        minimax_music.build_music_payload(mode="karaoke", model="music-3.0")
    assert "未知的音乐创作模式" in str(unknown_mode.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as no_model:
        minimax_music.build_music_payload(mode="one_sentence", model="", prompt="轻快")
    assert "音乐模型" in str(no_model.value)
    with pytest.raises(minimax_music.MusicRequestInvalid) as wrong_family:
        minimax_music.build_music_payload(
            mode="one_sentence", model="music-cover", prompt="轻快的流行摇滚"
        )
    assert "翻唱模型" in str(wrong_family.value)


def test_reference_audio_constraints_use_the_probed_duration(tmp_path: Path) -> None:
    missing = tmp_path / "没有这个文件.mp3"
    with pytest.raises(minimax_music.MusicRequestInvalid) as excinfo:
        minimax_music.validate_reference_audio(audio_path=missing)
    assert "不存在" in str(excinfo.value)

    wrong_suffix = tmp_path / "参考.m4a"
    wrong_suffix.write_bytes(b"fake")
    with pytest.raises(minimax_music.MusicRequestInvalid) as suffix_error:
        minimax_music.validate_reference_audio(audio_path=wrong_suffix)
    assert "mp3 / wav / flac" in str(suffix_error.value)

    too_short = tmp_path / "短.flac"
    too_short.write_bytes(b"fake")
    with pytest.raises(minimax_music.MusicRequestInvalid) as duration_error:
        minimax_music.validate_reference_audio(audio_path=too_short, duration_seconds=3.0)
    assert "6 秒–6 分钟" in str(duration_error.value)

    # 探不到时长（环境没 ffprobe）只按大小与格式判，不该拦掉合法素材。
    minimax_music.validate_reference_audio(audio_path=too_short, duration_seconds=None)

    with pytest.raises(minimax_music.MusicRequestInvalid) as nothing:
        minimax_music.validate_reference_audio()
    assert "缺少参考音频" in str(nothing.value)


def test_reference_audio_fields_pick_url_or_base64(tmp_path: Path) -> None:
    assert minimax_music.reference_audio_fields(
        audio_url="https://cdn.example.com/a.mp3"
    ) == {"audio_url": "https://cdn.example.com/a.mp3"}
    local = tmp_path / "a.mp3"
    local.write_bytes(b"audio-bytes")
    fields = minimax_music.reference_audio_fields(audio_path=local)
    assert set(fields) == {"audio_base64"}
    assert fields["audio_base64"]


def test_music_output_path_follows_the_chosen_format(tmp_path: Path) -> None:
    mp3 = minimax_music.music_output_path(tmp_path, "job-1", "mp3")
    wav = minimax_music.music_output_path(tmp_path, "job-1", "wav")
    unknown = minimax_music.music_output_path(tmp_path, "job-1", "")
    assert mp3.suffix == ".mp3"
    assert wav.suffix == ".wav"
    assert unknown.suffix == ".mp3"
    assert minimax_music.music_output_path_for_job(tmp_path, "job-1").suffix == ".mp3"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"wav")
    assert minimax_music.music_output_path_for_job(tmp_path, "job-1") == wav


def test_cover_model_detection_is_a_name_check_not_a_hardcoded_family() -> None:
    assert minimax_music.is_cover_model("music-cover")
    assert not minimax_music.is_cover_model("music-3.0")


def _music_body(audio: bytes) -> dict:
    return {
        "data": {"audio": audio.hex(), "status": 2},
        "extra_info": {"music_duration": 12500, "music_sample_rate": 44100},
        "base_resp": {"status_code": 0, "status_msg": ""},
    }


async def test_humming_runs_preprocess_once_then_generates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = b"\xff\x00\x1afake-mp3"
    stub = _install_stub(
        monkeypatch,
        {
            PATH_MUSIC_COVER_PREPROCESS: {
                "cover_feature_id": "cf-1",
                "formatted_lyrics": "[Verse]\n哼出来的词",
                "audio_duration": 12.5,
                "base_resp": {"status_code": 0},
            },
            PATH_MUSIC_GENERATION: _music_body(raw),
        },
    )
    result = await minimax_music.run_freezone_music_generate(
        project_dir=tmp_path,
        job_id="job-1",
        mode="humming",
        model="music-cover",
        provider="minimax",
        prompt="把这段哼唱做成轻快的民谣翻唱版本",
        audio_url="https://cdn.example.com/hum.mp3",
    )
    assert [path for path, _payload in stub.calls] == [
        PATH_MUSIC_COVER_PREPROCESS,
        PATH_MUSIC_GENERATION,
    ]
    preprocess_payload = stub.calls[0][1]
    assert preprocess_payload["model"] == "music-cover"
    assert preprocess_payload["audio_url"] == "https://cdn.example.com/hum.mp3"
    assert "prompt" not in preprocess_payload
    generate_payload = stub.calls[1][1]
    assert generate_payload["cover_feature_id"] == "cf-1"
    assert generate_payload["lyrics"] == "[Verse]\n哼出来的词"
    # 第二步只带特征 id：再带一份参考音频上游会判互斥错误。
    assert "audio_url" not in generate_payload
    assert "audio_base64" not in generate_payload
    assert result.audio_path.read_bytes() == raw
    assert result.duration_ms == 12500
    assert result.model == "music-cover"
    assert result.provider == "minimax"
    assert result.mode == "humming"
    assert result.mime_type == "audio/mpeg"
    assert result.audio_path.parent.name == minimax_music.MUSIC_TASK_TYPE


async def test_humming_keeps_user_edited_lyrics_over_asr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub = _install_stub(
        monkeypatch,
        {
            PATH_MUSIC_COVER_PREPROCESS: {
                "cover_feature_id": "cf-2",
                "formatted_lyrics": "[Verse]\n机器听的词",
                "base_resp": {"status_code": 0},
            },
            PATH_MUSIC_GENERATION: _music_body(b"abc"),
        },
    )
    await minimax_music.run_freezone_music_generate(
        project_dir=tmp_path,
        job_id="job-2",
        mode="humming",
        model="music-cover",
        prompt="把这段哼唱做成轻快的民谣翻唱版本",
        lyrics="[Chorus]\n用户改过的词",
        audio_url="https://cdn.example.com/hum.mp3",
    )
    assert stub.calls[1][1]["lyrics"] == "[Chorus]\n用户改过的词"


async def test_cover_with_local_reference_uploads_base64_in_one_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference = tmp_path / "ref.mp3"
    reference.write_bytes(b"reference-audio-bytes")
    stub = _install_stub(monkeypatch, {PATH_MUSIC_GENERATION: _music_body(b"xyz")})
    result = await minimax_music.run_freezone_music_generate(
        project_dir=tmp_path,
        job_id="job-3",
        mode="cover",
        model="music-cover",
        prompt="把这首歌唱成摇滚版本，鼓点更重",
        audio_path=reference,
        audio_format="wav",
    )
    assert [path for path, _payload in stub.calls] == [PATH_MUSIC_GENERATION]
    payload = stub.calls[0][1]
    assert payload["audio_base64"]
    assert "audio_url" not in payload
    assert "cover_feature_id" not in payload
    assert result.audio_path.suffix == ".wav"
    assert result.mime_type == "audio/wav"


async def test_one_sentence_posts_exactly_once_and_lands_in_task_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """一句话生成 = 一次 POST：歌词由上游 lyrics_optimizer 从描述里生成。"""

    raw = b"\x00\x01\x02fake-audio"
    stub = _install_stub(monkeypatch, {PATH_MUSIC_GENERATION: _music_body(raw)})
    result = await minimax_music.run_freezone_music_generate(
        project_dir=tmp_path,
        job_id="job-once",
        mode="one_sentence",
        model="music-3.0",
        prompt="轻快的夏日民谣，副歌要有合唱",
    )
    assert [path for path, _payload in stub.calls] == [PATH_MUSIC_GENERATION]
    assert stub.calls[0][1]["lyrics_optimizer"] is True
    assert not stub.calls[0][1].get("lyrics")
    assert result.audio_path.parent.name == minimax_music.MUSIC_TASK_TYPE
    assert result.audio_path.read_bytes() == raw
    assert result.duration_ms == 12500


async def test_organization_task_is_denied_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """组织任务 fail-closed：拦住时一次上游请求都不能发出去。"""

    stub = _install_stub(monkeypatch, {PATH_MUSIC_GENERATION: _music_body(b"no")})
    with pytest.raises(MiniMaxError) as excinfo:
        await minimax_music.run_freezone_music_generate(
            project_dir=tmp_path,
            job_id="job-org",
            mode="one_sentence",
            model="music-3.0",
            prompt="任何描述",
            egress_context=SimpleNamespace(is_organization=True),
        )
    assert "ORG_EGRESS_DENIED" in str(excinfo.value)
    assert stub.calls == []


async def test_upstream_failure_is_surfaced_with_the_upstream_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """V1 的 HTTP 200 + base_resp 失败：状态码与上游原话都要透到界面，不落空文件。"""

    stub = _install_stub(
        monkeypatch,
        {
            PATH_MUSIC_GENERATION: {
                "base_resp": {"status_code": 2013, "status_msg": "invalid prompt"}
            }
        },
    )
    with pytest.raises(MiniMaxError) as excinfo:
        await minimax_music.run_freezone_music_generate(
            project_dir=tmp_path,
            job_id="job-bad",
            mode="one_sentence",
            model="music-3.0",
            prompt="任何描述",
        )
    assert "2013" in str(excinfo.value)
    assert "invalid prompt" in str(excinfo.value)
    assert excinfo.value.endpoint == PATH_MUSIC_GENERATION
    assert len(stub.calls) == 1
    assert not minimax_music.music_output_path(tmp_path, "job-bad", "mp3").exists()


async def test_missing_audio_in_response_is_reported_not_silently_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上游改了返回形态（没有 data.audio）也不能装作成功交付一个空产物。"""

    _install_stub(
        monkeypatch,
        {PATH_MUSIC_GENERATION: {"data": {"status": 1}, "base_resp": {"status_code": 0}}},
    )
    with pytest.raises(MiniMaxError) as excinfo:
        await minimax_music.run_freezone_music_generate(
            project_dir=tmp_path,
            job_id="job-noaudio",
            mode="one_sentence",
            model="music-3.0",
            prompt="任何描述",
        )
    assert "data.audio" in str(excinfo.value)
