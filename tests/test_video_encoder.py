"""[aipdd fork delta] 共享 H.264 编码器解析器单测。

这条模块存在的理由有两半，测试就各钉一半：

* **装机版能出片**：随包的 ffmpeg 是 LGPL 构建、没有 x264（实测 ``-c:v libx264``
  退出码 8，``h264_mf`` / ``libopenh264`` 退出码 0），老链路过去把 ``libx264`` 写死在
  argv 里，改 ``VIDEO_CODEC`` 也不生效。
* **容器/镜像侧一字不改**：那边本来就有 x264，收口前后交给 ffmpeg 的参数必须逐字节
  相同，否则等于顺手改了主线出片的画质与耗时。
"""

from __future__ import annotations

import pytest

from novelvideo import config, video_encoder
from novelvideo.video_encoder import NoH264Encoder, video_encode_args


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让环境像一台「什么都没配」的机器：变量用例自己覆自己那份。"""
    monkeypatch.delenv("VIDEO_CODEC", raising=False)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    # 码率用例钉的是这个默认值；本机 .env 里改过的话别让测试跟着漂。
    monkeypatch.setattr(config, "VIDEO_BITRATE", "4M")
    video_encoder._mismatch_warned.clear()
    video_encoder._off_family_warned.clear()


def _with_encoder(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(video_encoder, "available_encoders", lambda **_kw: frozenset(names))


# ---------------------------------------------------------------- 有 x264 时

def test_x264_keeps_the_callers_preset_and_crf(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_encoder(monkeypatch, "libx264", "aac")
    assert video_encode_args(preset="fast", crf="23") == [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
    ]


def test_x264_without_crf_stays_quality_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """老写法只给 ``-preset`` 的链路，收口后仍然只给 ``-preset``（不擅自补 -crf）。"""
    _with_encoder(monkeypatch, "libx264")
    assert video_encode_args() == ["-c:v", "libx264", "-preset", "veryfast"]


def test_probe_failure_leaves_the_old_command_line_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """问不到清单（没装 ffmpeg / 探测超时）就当什么都没变：别把新失败模式带进老链路。"""
    monkeypatch.setattr(video_encoder, "available_encoders", lambda **_kw: frozenset())
    assert video_encode_args(preset="slow", crf="18") == [
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
    ]


# ------------------------------------------------------- 没有 x264（装机版）

def test_lgpl_build_switches_to_media_foundation_with_a_bitrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_encoder(monkeypatch, "h264_mf", "libopenh264", "aac")
    args = video_encode_args(preset="veryfast", crf="18")
    assert args[0:2] == ["-c:v", "h264_mf"]
    # 这两个是 x264 的私有选项：留着 ffmpeg 只会打「has not been used」并退回自己的默认码控
    assert "-preset" not in args and "-crf" not in args
    assert args[2:4] == ["-b:v", "4M"]


def test_bitrate_follows_video_bitrate(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_encoder(monkeypatch, "libopenh264")
    monkeypatch.setattr(config, "VIDEO_BITRATE", "8M")
    assert video_encode_args() == ["-c:v", "libopenh264", "-b:v", "8M"]


def test_caller_can_price_its_own_bitrate(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_encoder(monkeypatch, "h264_mf")
    assert video_encode_args(bitrate="2M") == ["-c:v", "h264_mf", "-b:v", "2M"]


# ------------------------------------------------------------ VIDEO_CODEC

def test_video_codec_is_honoured_when_the_build_has_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_encoder(monkeypatch, "libx264", "h264_mf")
    monkeypatch.setenv("VIDEO_CODEC", "h264_mf")
    assert video_encode_args(preset="fast") == ["-c:v", "h264_mf", "-b:v", "4M"]


def test_video_codec_beats_the_ladder_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """videotoolbox 排在 h264_mf 之前，但显式指了它就该用显式的那个。"""
    _with_encoder(monkeypatch, "h264_videotoolbox", "h264_mf")
    monkeypatch.setenv("VIDEO_CODEC", "h264_mf")
    assert video_encode_args()[1] == "h264_mf"


def test_a_video_codec_the_build_lacks_falls_back_to_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_encoder(monkeypatch, "h264_mf")
    monkeypatch.setenv("VIDEO_CODEC", "libx264")
    assert video_encode_args()[1] == "h264_mf"


def test_a_video_codec_outside_the_h264_family_is_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``VIDEO_CODEC=mpeg4`` 本机真有它，但交付口径要的是 H.264：不能用它出片。"""
    _with_encoder(monkeypatch, "mpeg4", "h264_mf")
    monkeypatch.setenv("VIDEO_CODEC", "mpeg4")
    with caplog.at_level("WARNING"):
        assert video_encode_args() == ["-c:v", "h264_mf", "-b:v", "4M"]
        assert video_encode_args()[1] == "h264_mf"
    # 每铺一条 argv 就调这里一次，所以同一个值只提醒一次
    assert len([r for r in caplog.records if "不是 H.264 编码器" in r.getMessage()]) == 1


def test_audio_codecs_are_never_offered_as_the_video_codec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """手滑写成 ``VIDEO_CODEC=aac`` 时不该把 ``-c:v aac`` 塞进命令：ffmpeg 只会报无关的错。"""
    _with_encoder(monkeypatch, "aac", "libx264")
    monkeypatch.setenv("VIDEO_CODEC", "aac")
    assert video_encode_args() == ["-c:v", "libx264", "-preset", "veryfast"]


def test_no_h264_at_all_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_encoder(monkeypatch, "aac", "mpeg4")
    with pytest.raises(NoH264Encoder) as caught:
        video_encode_args()
    assert "h264_mf" in str(caught.value)


# ------------------------------------------------------- MG 的按像素算码率

def test_h264_encoder_for_clamps_to_the_callers_budget() -> None:
    name, args = video_encoder.h264_encoder_for(
        ["h264_mf"], width=640, height=360, fps=10, bits_per_pixel=0.10
    )
    assert name == "h264_mf"
    assert args == ["-b:v", "2000000"]  # 640×360×10×0.1 = 230k → 抬到下限 2M
    _big = video_encoder.h264_encoder_for(
        ["h264_mf"], width=3840, height=2160, fps=60, bits_per_pixel=0.10, max_bitrate=9_000_000
    )
    assert _big[1] == ["-b:v", "9000000"]


def test_h264_encoder_for_prefers_x264_quality_mode() -> None:
    name, args = video_encoder.h264_encoder_for(
        ["libopenh264", "libx264"], width=1280, height=720, fps=30, bits_per_pixel=0.10
    )
    assert (name, args) == ("libx264", ["-preset", "veryfast", "-crf", "18"])


# --------------------------------------------------------------- 探测缓存

def test_probe_is_cached_per_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = b"  V....D libx264     libx264 H.264\n  A....D aac        AAC\n"

    def _fake_run(cmd: list[str], **_kw: object) -> _Result:
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(video_encoder.subprocess, "run", _fake_run)
    video_encoder.reset_probe_cache()
    try:
        assert video_encoder.available_encoders(executable="/fake/ffmpeg") == {"libx264", "aac"}
        assert video_encoder.available_encoders(executable="/fake/ffmpeg") == {"libx264", "aac"}
        assert len(calls) == 1
    finally:
        video_encoder.reset_probe_cache()


def test_a_broken_probe_does_not_invent_an_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测本身失败（二进制坏了）时不能猜：空集让调用方保持老写法。"""

    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr(video_encoder.subprocess, "run", _boom)
    video_encoder.reset_probe_cache()
    try:
        assert video_encoder.available_encoders(executable="/fake/ffmpeg") == frozenset()
    finally:
        video_encoder.reset_probe_cache()


# ------------------------------------------------------- 用哪一台二进制

def test_ffmpeg_path_that_matches_the_path_binary_is_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog
) -> None:
    """桌面版就是这种：resources/bin 前置进 PATH，FFMPEG_PATH 与 PATH 上是同一台。"""
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_PATH", str(binary))
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: str(binary))
    with caplog.at_level("WARNING"):
        assert video_encoder.ffmpeg_executable() == str(binary)
    assert not [r for r in caplog.records if "同一个二进制" in r.getMessage()]


def test_ffmpeg_path_outside_path_is_reported_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog
) -> None:
    """老链路的 argv 跑的是裸 ``ffmpeg``：探的台与跑的台不一致时得留下日志。"""
    probed = tmp_path / "custom-ffmpeg.exe"
    probed.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_PATH", str(probed))
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: str(tmp_path / "system-ffmpeg.exe"))
    with caplog.at_level("WARNING"):
        assert video_encoder.ffmpeg_executable() == str(probed)
        assert video_encoder.ffmpeg_executable() == str(probed)
    warned = [r for r in caplog.records if "同一个二进制" in r.getMessage()]
    assert len(warned) == 1


def test_ffmpeg_path_that_resolves_to_nothing_reports_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指了却指不到：返回 None，让调用方报「找不到 ffmpeg」，不去悄悄换一台。"""
    monkeypatch.setenv("FFMPEG_PATH", "/definitely/not/here/ffmpeg")
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: None)
    assert video_encoder.ffmpeg_executable() is None
