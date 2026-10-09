"""C2 smoke tests for vendored IndexTTS2 modules.

Verifies the IndexTTS2 client and the seedance2_i2v voice helpers load
under v2.0-storage. The full ``indextts2_beat_audio_task`` import depends on
``project_config.load_narration_style`` which is added in C5 (A4); that
module is imported in ``test_indextts2_beat_audio_task_imports`` (C5).
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest


def test_indextts2_client_module_loads():
    mod = importlib.import_module("novelvideo.generators.indextts2")
    assert hasattr(mod, "IndexTTS2Client")


def test_voice_audio_records_module_loads():
    mod = importlib.import_module("novelvideo.seedance2_i2v.voice_audio_records")
    assert hasattr(mod, "classify_seedance2_voice_audio")
    assert hasattr(mod, "upsert_seedance2_voice_audio_record")


def test_character_voice_storage_module_loads():
    mod = importlib.import_module("novelvideo.seedance2_i2v.character_voice_storage")
    assert hasattr(mod, "persist_character_voice_file")
    assert hasattr(mod, "decode_recorded_audio_data_url")


def test_voice_clone_module_loads_without_oss_client():
    mod = importlib.import_module("novelvideo.seedance2_i2v.voice_clone")
    assert hasattr(mod, "build_reference_audio_url")
    assert hasattr(mod, "MAX_REFERENCE_AUDIO_BYTES")


def test_audio_request_usage_module_loads():
    mod = importlib.import_module("novelvideo.audio_request_usage")
    assert mod is not None


def test_seedance2_i2v_init_exports_stage_a_only():
    pkg = importlib.import_module("novelvideo.seedance2_i2v")
    assert "classify_seedance2_voice_audio" in pkg.__all__
    assert "Seedance2VoiceAudioRecord" in pkg.__all__


def test_indextts2_client_refuses_missing_api_key(tmp_path):
    # 显式传 api_key="" 已完全覆盖网关配置，无需再 setenv 失效防护。
    from novelvideo.generators.indextts2 import IndexTTS2Client

    client = IndexTTS2Client(api_key="", model="index-tts-2")
    result = asyncio.run(
        client.generate(
            prompt="hello",
            audio_url="https://example.com/sample.mp3",
            output_path=tmp_path / "out.mp3",
        )
    )
    assert not result.success
    assert result.error == "DramaClawAPI API key not set"


def test_indextts2_client_rejects_empty_prompt(tmp_path):
    from novelvideo.generators.indextts2 import IndexTTS2Client

    client = IndexTTS2Client(api_key="dummy-key-not-used", model="index-tts-2")
    result = asyncio.run(
        client.generate(
            prompt="",
            audio_url="https://example.com/x.mp3",
            output_path=tmp_path / "out.mp3",
        )
    )
    assert not result.success
    assert result.error == "IndexTTS2 prompt is empty"


def test_build_reference_audio_url_size_guard(tmp_path):
    from novelvideo.seedance2_i2v.voice_clone import (
        MAX_REFERENCE_AUDIO_BYTES,
        build_reference_audio_url,
    )

    big = tmp_path / "big.mp3"
    big.write_bytes(b"\x00" * (MAX_REFERENCE_AUDIO_BYTES + 1))
    with pytest.raises(ValueError, match="Re-encode"):
        build_reference_audio_url(big)


def test_build_reference_audio_url_returns_data_url(tmp_path):
    from novelvideo.seedance2_i2v.voice_clone import build_reference_audio_url

    small = tmp_path / "small.mp3"
    small.write_bytes(b"ID3\x03\x00\x00\x00fake-mp3-bytes")
    url = build_reference_audio_url(small)
    assert url.startswith("data:")
