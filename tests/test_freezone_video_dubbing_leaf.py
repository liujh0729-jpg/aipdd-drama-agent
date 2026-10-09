# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 视频配音执行叶聚焦测试。

钉住 ``novelvideo.freezone.dubbing.run_freezone_video_dubbing`` 的四步编排：
视频源先解封装抽音轨 → 本地音频送 ASR 取原台词 → 参考音色零样本合成新声
（``language`` 作发音提示透传）→ **新增** 用新声替换原视频整条音轨落 mp4。
所有网络 / 媒体叶子（解封装 / ASR / TTS / mux）全部打桩，零网络、零真实 ffmpeg。

另钉 ``jobs._replace_audio_cmd`` 的纯命令形状（可零 IO 断言）。出网分类
（NETWORK / EG-15a、组织直连 fail-closed）与 voice_replace 同族，由叶分类测试覆盖。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone import dubbing as module
from novelvideo.freezone import jobs as jobs_module
from novelvideo.freezone.paths import outputs_dir

# 默认候选清单：只有一个已就绪的网关候选（不含豆包），用于不关心选择顺序的用例。
_GATEWAY_ONLY_OPTIONS = [
    {"id": "gateway:volcengine:whisper-1", "kind": "gateway", "ready": True},
]


def _patch_leaf(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_dir: Path,
    asr_text: str = "原始的台词内容",
    options: list[dict] | None = None,
) -> dict:
    """把四步的外部叶子换成桩实现，返回记录调用顺序与入参的字典。

    ASR 候选选择不桩掉（走真实的 ``pick_asr_candidate_for_local_audio``），只桩它
    依赖的两个只读接口，这样选择口径也能被钉住。
    """

    seen: dict = {
        "order": [],
        "separate": None,
        "transcribe": None,
        "speech": None,
        "mux": None,
    }
    resolved_options = _GATEWAY_ONLY_OPTIONS if options is None else options

    speech_dir = project_dir / "speech_stem"
    speech_dir.mkdir(parents=True, exist_ok=True)
    speech_out = speech_dir / "out.mp3"

    async def fake_separate(**kwargs):
        seen["order"].append("separate")
        seen["separate"] = kwargs
        extracted = speech_dir / "extracted.mp3"
        extracted.write_bytes(b"audio")
        return {"audio_path": extracted}

    async def fake_transcribe(cand, **kwargs):
        seen["order"].append("transcribe")
        seen["transcribe"] = {"candidate": cand, **kwargs}
        return asr_text, "语音识别（Whisper-1）"

    async def fake_speech(**kwargs):
        seen["order"].append("speech")
        seen["speech"] = kwargs
        speech_out.write_bytes(b"newvoice")
        return SimpleNamespace(
            audio_path=speech_out,
            duration_ms=1234,
            mime_type="audio/mpeg",
            model="doubao-seed-audio-1-0",
            voice_source="user_custom",
            voice_sha256="abc",
        )

    async def fake_mux(**kwargs):
        seen["order"].append("mux")
        seen["mux"] = kwargs
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"dubbed-mp4")
        return out

    def fake_resolve(model: str) -> dict:
        for opt in resolved_options:
            if str(model) == str(opt.get("id") or ""):
                return {
                    "id": str(opt["id"]),
                    "kind": str(opt.get("kind") or ""),
                    "provider": str(opt.get("provider") or ""),
                    "model": str(opt.get("model") or opt.get("id") or ""),
                    "display": str(opt.get("label") or opt.get("id") or ""),
                }
        raise ValueError(f"无法识别的 ASR 模型候选: {model}")

    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_audio_separate", fake_separate
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.replace_video_audio_track", fake_mux
    )
    monkeypatch.setattr(
        "novelvideo.freezone.media_copywrite._transcribe", fake_transcribe
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech", fake_speech
    )
    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.resolve_douyin_asr_candidate",
        fake_resolve,
    )
    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.list_douyin_asr_models",
        lambda: list(resolved_options),
    )
    return seen


async def test_video_source_demuxes_then_replaces_audio_track(
    tmp_path: Path, monkeypatch
) -> None:
    """视频源：解封装→ASR→零样本合成（语言透传）→音轨回填，产物是 mp4 视频。"""

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    video = project_dir / "clip.mp4"
    video.write_bytes(b"video")
    seen = _patch_leaf(monkeypatch, project_dir=project_dir)

    result = await module.run_freezone_video_dubbing(
        store=object(),
        username="alice",
        project="demo",
        account_voice_username="alice",
        project_dir=project_dir,
        job_id="job-1",
        source_path=str(video),
        source_kind="video",
        voice_ref={"scope": "user_custom", "voice_id": "fv_x"},
        asr_model="gateway:volcengine:whisper-1",
        language="vi",
        model="doubao-seed-audio-1-0",
    )

    # 四步顺序编排。
    assert seen["order"] == ["separate", "transcribe", "speech", "mux"]
    # 视频先解封装抽音轨，再把抽出的音轨送 ASR。
    assert seen["separate"]["source_path"] == str(video)
    assert Path(seen["transcribe"]["audio_path"]).name == "extracted.mp3"
    assert seen["transcribe"]["audio_url"] == ""
    # 合成拿原始台词、voice_ref，并把 language 作发音提示透传。
    assert seen["speech"]["text"] == "原始的台词内容"
    assert seen["speech"]["voice_ref"] == {"scope": "user_custom", "voice_id": "fv_x"}
    assert seen["speech"]["language"] == "vi"
    assert seen["speech"]["model"] == "doubao-seed-audio-1-0"
    # 回填：用配音新声（speech.audio_path）替换原视频画面输入。
    assert seen["mux"]["source_video_path"] == str(video)
    assert Path(seen["mux"]["dub_audio_path"]).name == "out.mp3"
    # 成片落本任务专属目录。
    expected = outputs_dir(project_dir, "freezone_video_dubbing") / "job-1.mp4"
    assert Path(result["video_path"]) == expected
    assert Path(seen["mux"]["output_path"]) == expected
    assert result["text"] == "原始的台词内容"
    assert result["duration_ms"] == 1234
    assert result["mime_type"] == "video/mp4"
    assert result["voice_source"] == "user_custom"


async def test_language_defaults_empty_when_omitted(
    tmp_path: Path, monkeypatch
) -> None:
    """不传 language：叶把空串透给合成（支持的渠道才会带上 metadata）。"""

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    video = project_dir / "clip.mp4"
    video.write_bytes(b"video")
    seen = _patch_leaf(monkeypatch, project_dir=project_dir)

    await module.run_freezone_video_dubbing(
        store=object(),
        username="alice",
        project="demo",
        account_voice_username="alice",
        project_dir=project_dir,
        job_id="job-1b",
        source_path=str(video),
        source_kind="video",
        voice_ref={"scope": "user_custom", "voice_id": "fv_x"},
        asr_model="",
    )

    assert seen["speech"]["language"] == ""


async def test_missing_voice_ref_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="voice_ref"):
        await module.run_freezone_video_dubbing(
            store=object(),
            username="alice",
            project="demo",
            account_voice_username="alice",
            project_dir=tmp_path,
            job_id="job-2",
            source_path=str(tmp_path / "clip.mp4"),
            source_kind="video",
            voice_ref=None,
            asr_model="",
        )


async def test_empty_asr_text_fails(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    video = project_dir / "clip.mp4"
    video.write_bytes(b"video")
    _patch_leaf(monkeypatch, project_dir=project_dir, asr_text="   ")

    with pytest.raises(RuntimeError, match="未能识别原始台词"):
        await module.run_freezone_video_dubbing(
            store=object(),
            username="alice",
            project="demo",
            account_voice_username="alice",
            project_dir=project_dir,
            job_id="job-3",
            source_path=str(video),
            source_kind="video",
            voice_ref={"scope": "user_custom", "voice_id": "fv_w"},
            asr_model="",
        )


# ============================================================
# 音轨回填命令构造（纯函数，零 ffmpeg 调用）
# ============================================================


def test_replace_audio_cmd_shape() -> None:
    cmd = jobs_module._replace_audio_cmd(
        "/in/clip.mp4", "/in/dub.mp3", "/out/final.mp4"
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    # 两个输入：视频在前（0），配音音频在后（1）。
    assert cmd[cmd.index("-i") + 1] == "/in/clip.mp4"
    assert cmd.count("-i") == 2
    # 画面取 0:v、声轨取 1:a、视频流 copy、aac 音频、取较短。
    assert "-map" in cmd
    assert "0:v:0" in cmd
    assert "1:a:0" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-shortest" in cmd
    assert "-movflags" in cmd and "+faststart" in cmd
    # 输出路径在末尾。
    assert cmd[-1] == "/out/final.mp4"
