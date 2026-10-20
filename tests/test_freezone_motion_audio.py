# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效大片的代码编曲引擎（motion_audio 包）测试。
"""渲染契约第 3、6 条的音频那一半，按四层各守一遍：

``grid``（拍点 → 节拍网格，音效提前一帧）、``arrange``（网格 + 事件 → 音符表）、
``synth``（音符表 → PCM/WAV，纯 numpy 且逐位确定）、``ffmpeg``（两遍 loudnorm 到
-14 LUFS / -1 dBTP + ``-c:v copy`` 合轨）。最后一层是 ``score`` 的编排顺序。

除了带 ``needs_ffmpeg`` 的那两条，全部不起子进程：命令形状与回填口径可以逐字符断言，
真编解码只在有 ffmpeg 的机器上验一次，缺了就跳过而不是假绿。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from novelvideo.freezone.motion_audio import ffmpeg as audio_ffmpeg
from novelvideo.freezone.motion_audio import score as audio_score
from novelvideo.freezone.motion_audio.arrange import (
    MAX_NOTES,
    STYLE_AMBIENT,
    STYLE_DRIVE,
    STYLE_PULSE,
    arrange,
    event_cues,
    midi_hz,
    style_for,
)
from novelvideo.freezone.motion_audio.grid import (
    DEFAULT_BPM,
    TRANSIENT_LEAD_FRAMES,
    MotionAudioError,
    align_phase,
    analyze_grid,
    event_times,
    infer_bpm,
    normalize_bpm,
    transient_times,
)
from novelvideo.freezone.motion_audio.score import (
    MotionScore,
    compose_score,
    raw_path_for,
    score_and_attach,
    score_path_for,
)
from novelvideo.freezone.motion_audio.synth import (
    CHANNELS,
    SAMPLE_RATE,
    TAIL_SECONDS,
    render_notes,
    write_wav,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="本机 PATH 上没有 ffmpeg"
)

# 一段典型的卡点页拍点：每 0.5 秒一个事件（= 120 BPM）。
_EV = [{"t": round(0.5 * index, 3), "type": "hit"} for index in range(1, 11)]


def _grid(duration: float = 5.0, *, fps: int = 30, events: Any = None) -> Any:
    return analyze_grid(
        _EV if events is None else events, duration_seconds=duration, fps=fps
    )


# --------------------------------------------------------------- 1) 节拍网格


def test_event_times_sorts_dedupes_and_drops_junk() -> None:
    got = event_times(
        [1.0, {"t": 0.5}, {"time": 2.0}, {"ts": "3.0"}, "bad", {"t": -1}, {"t": 9.9}, None],
        duration_seconds=4.0,
    )
    # 0.5/1.0/2.0/3.0 都收到（三种键名都认），负数、脏值、超过时长的 9.9 一律丢。
    assert got == (0.5, 1.0, 2.0, 3.0)
    assert event_times("0.5,1.0", duration_seconds=4.0) == ()
    assert event_times(None, duration_seconds=4.0) == ()
    assert event_times([{"t": float("nan")}, {"t": float("inf")}], duration_seconds=4.0) == ()


def test_transients_land_one_frame_early() -> None:
    """音效要提前一帧：画面在第 N 帧才画出变化，落在第 N 帧就已经晚了。"""
    assert transient_times([{"t": 1.0}], duration_seconds=5.0, fps=30) == (
        round(1.0 - TRANSIENT_LEAD_FRAMES / 30.0, 6),
    )
    # 片头的事件不能被提前到负时间轴上。
    assert transient_times([{"t": 0.0}, {"t": 0.01}], duration_seconds=5.0, fps=30) == (0.0,)
    assert TRANSIENT_LEAD_FRAMES == 1


def test_normalize_bpm_folds_octaves_into_the_audible_band() -> None:
    # 每两拍打一次点 → 推出来 60，但网格该按 120 排。
    assert normalize_bpm(60.0) == 60.0
    assert normalize_bpm(30.0) == 60.0
    assert normalize_bpm(240.0) == 120.0
    assert normalize_bpm(500.0) == 125.0
    assert normalize_bpm(0.0) == DEFAULT_BPM
    assert normalize_bpm(float("nan")) == DEFAULT_BPM


def test_infer_bpm_uses_the_median_gap_not_the_extremes() -> None:
    assert infer_bpm((0.5, 1.0, 1.5, 2.0)) == 120.0
    # 中位数让一次意外的长间隔（转场留白）不至于把整条片子拖慢一倍。
    assert infer_bpm((0.0, 0.5, 1.0, 3.0)) == 120.0
    assert infer_bpm((0.5,)) is None
    assert infer_bpm(()) is None


def test_analyze_grid_reports_where_the_tempo_came_from() -> None:
    from_events = _grid()
    assert from_events.source == "events"
    assert from_events.bpm == 120.0
    assert from_events.seconds_per_beat == 0.5
    assert from_events.events == (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)

    from_hint = analyze_grid([], duration_seconds=4.0, fps=30, bpm_hint=96.0)
    assert from_hint.source == "hint"
    assert from_hint.bpm == 96.0

    fallback = analyze_grid([{"t": 1.2}], duration_seconds=4.0, fps=30)
    assert fallback.source == "default"
    assert fallback.bpm == DEFAULT_BPM


def test_analyze_grid_refuses_an_unusable_duration() -> None:
    for bad in (0.0, -1.0, "abc", None):
        with pytest.raises(MotionAudioError):
            analyze_grid(_EV, duration_seconds=bad, fps=30)  # type: ignore[arg-type]


def test_beat_times_stay_inside_the_film_and_on_the_grid() -> None:
    grid = _grid(duration=2.0)
    points = grid.beat_times(2.0)
    assert points and points[-1] <= 2.0
    assert all(round((value - grid.phase_seconds) / 0.5, 4) % 1 == 0 for value in points)
    assert grid.beat_times(0.0) == ()
    # bars=4 → 一格一个点，铺底与和弦用它。
    assert len(grid.beat_times(2.0, bars=4)) == 1
    assert grid.seconds_to_beat(1.26) == 2


def test_align_phase_puts_the_grid_on_the_action() -> None:
    """网格必须穿过拍点，否则「镜头切换落在网格上」这句契约就是空话。"""
    grid = _grid(duration=5.0)
    period = grid.seconds_per_beat
    for value in grid.transients[:4]:
        offset = (value - grid.phase_seconds) % period
        assert min(offset, period - offset) < 1e-6
    assert align_phase((), 0.5) == 0.0
    assert align_phase((0.3,), 0.0) == 0.0


# --------------------------------------------------------------- 2) 编曲规则


def test_midi_hz_uses_a4_equal_temperament() -> None:
    assert midi_hz(69) == 440.0
    assert midi_hz(57) == 220.0
    assert midi_hz(45) == 110.0


def test_style_for_prefers_the_case_category_then_tempo() -> None:
    assert style_for("音乐MV") == STYLE_DRIVE
    assert style_for("产品宣传片") == STYLE_PULSE
    # 图册里"科普动画"和"风格动画"都带"动画"：更具体的分类先判，科普片不该排成满编鼓。
    assert style_for("科普动画") == STYLE_AMBIENT
    assert style_for("风格动画") == STYLE_PULSE
    assert style_for("", 150.0) == STYLE_DRIVE
    assert style_for("", 80.0) == STYLE_AMBIENT
    assert style_for("", 120.0) == STYLE_PULSE


def test_event_cues_maps_labels_to_weights_and_leads_a_frame() -> None:
    cues = event_cues(
        [
            {"t": 2.0, "type": "drop"},
            {"t": 1.0, "type": "hit"},
            {"t": 3.0, "type": "switch"},
            {"t": 4.0, "type": "click"},
            {"t": 5.0, "type": "unknown-thing"},
            {"t": 99.0, "type": "hit"},
        ],
        duration_seconds=6.0,
        fps=50,
    )
    lead = 1.0 / 50.0
    assert [round(item[0] + lead, 3) for item in cues] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [item[1] for item in cues] == ["impact", "impact", "impact", "tick", "impact"]
    # 认不出的标签按普通一击处理：拍点是事实，标签只是提示。
    assert [item[2] for item in cues] == [0.85, 1.0, 0.7, 0.5, 0.7]


def test_event_cues_ignores_bare_numbers_and_junk() -> None:
    """裸秒数没有 type，推不出力度，所以这一层只认 ``{"t":…, "type":…}``。"""
    assert event_cues([0.5, 1.0], duration_seconds=5.0, fps=30) == ()
    assert event_cues([{"t": "bad"}, {"type": "hit"}], duration_seconds=5.0, fps=30) == ()
    assert event_cues(None, duration_seconds=5.0, fps=30) == ()
    assert event_cues("0.5", duration_seconds=5.0, fps=30) == ()
    # 帧率非法时不提前，而不是崩在除零上。
    assert event_cues([{"t": 1.0, "type": "hit"}], duration_seconds=5.0, fps=0) == (
        (1.0, "impact", 0.85),
    )


def test_arrange_is_deterministic_and_ordered_by_time() -> None:
    grid = _grid()
    first = arrange(grid, events=_EV, duration_seconds=5.0, fps=30)
    second = arrange(grid, events=_EV, duration_seconds=5.0, fps=30)
    assert first == second
    assert first
    times = [note["time"] for note in first]
    assert times == sorted(times)
    assert all(
        set(note) == {"time", "instrument", "hz", "gain", "duration", "pan"} for note in first
    )
    assert all(note["time"] <= 5.0 for note in first)


def test_arrange_lands_a_sound_on_every_action_frame() -> None:
    """契约第 3 条的下半句：音效放在动作发生的那一帧（提前一帧）。"""
    grid = _grid()
    notes = arrange(grid, events=_EV, duration_seconds=5.0, fps=30, style=STYLE_PULSE)
    hit_times = {note["time"] for note in notes if note["instrument"] in ("impact", "tick")}
    for value in grid.transients:
        assert value in hit_times


def test_arrange_harmonises_when_no_events_are_available() -> None:
    """页面没自报拍点时也要出乐：音效退到网格点上，别让配乐变成哑的。"""
    grid = analyze_grid([], duration_seconds=4.0, fps=30)
    notes = arrange(grid, events=[], duration_seconds=4.0, fps=30)
    assert notes
    assert any(note["instrument"] == "impact" for note in notes)


def test_arrange_reserves_the_style_by_tempo_and_length() -> None:
    grid = _grid()
    drive = arrange(grid, events=_EV, duration_seconds=8.0, fps=30, style=STYLE_DRIVE)
    ambient = arrange(grid, events=_EV, duration_seconds=8.0, fps=30, style=STYLE_AMBIENT)
    assert any(note["instrument"] == "snare" for note in drive)
    assert not any(note["instrument"] == "snare" for note in ambient)
    # 满编鼓不再叠琶音，铺底也不给军鼓：三档的差别要在声部上看得见。
    assert any(note["instrument"] == "pluck" for note in ambient)
    assert not any(note["instrument"] == "pluck" for note in drive)
    # 短片不爬坡（一半篇幅在 riser 上很难听），长片才铺。
    assert not any(
        note["instrument"] == "riser"
        for note in arrange(grid, events=_EV, duration_seconds=3.0, fps=30)
    )
    assert any(
        note["instrument"] == "riser"
        for note in arrange(grid, events=_EV, duration_seconds=8.0, fps=30)
    )


def test_arrange_ends_on_a_hit_when_the_last_beat_is_far_from_the_tail() -> None:
    short_events = [{"t": 0.5, "type": "hit"}, {"t": 1.0, "type": "hit"}]
    grid = analyze_grid(short_events, duration_seconds=4.0, fps=30)
    notes = arrange(grid, events=short_events, duration_seconds=4.0, fps=30)
    # 片尾收一声：最后一个拍点在 1 秒处，剩下 3 秒不能戛然而止。
    closing = [note for note in notes if note["time"] == pytest.approx(3.88, abs=1e-6)]
    assert [note["instrument"] for note in closing] == ["impact"]


def test_arrange_caps_the_note_count_and_refuses_a_bad_duration() -> None:
    grid = _grid(duration=600.0)
    assert len(arrange(grid, events=_EV, duration_seconds=600.0, fps=30)) <= MAX_NOTES
    assert arrange(grid, events=_EV, duration_seconds=0.0, fps=30) == ()
    assert arrange(grid, events=_EV, duration_seconds="-3", fps=30) == ()
    with pytest.raises(ValueError, match="编曲时长非法"):
        arrange(grid, events=_EV, duration_seconds="abc", fps=30)


# --------------------------------------------------------------- 3) 合成


def test_render_notes_shape_peak_and_dtype() -> None:
    notes = arrange(_grid(), events=_EV, duration_seconds=2.0, fps=30)
    buffer = render_notes(notes, duration_seconds=2.0)
    expected_rows = int(round(2.0 * SAMPLE_RATE)) + int(round(TAIL_SECONDS * SAMPLE_RATE))
    assert buffer.shape == (expected_rows, CHANNELS)
    assert buffer.dtype == np.float32
    # 加法合成会叠出削顶，所以交付前先整体压峰（压峰不是坏声音，削顶是）。压峰在
    # float64 上做，转成 float32 后最后一位可能高出 1 ulp（0.9800000190…），故留 1e-6。
    assert float(np.max(np.abs(buffer))) <= 0.98 + 1e-6
    assert float(np.max(np.abs(buffer))) > 0.1


def test_render_notes_is_bitwise_deterministic() -> None:
    notes = arrange(_grid(), events=_EV, duration_seconds=1.0, fps=30)
    first = render_notes(notes, duration_seconds=1.0)
    second = render_notes(notes, duration_seconds=1.0)
    assert np.array_equal(first, second)
    assert not np.array_equal(first, render_notes(notes, duration_seconds=1.0, seed=7))


def test_render_notes_survives_junk_and_silence() -> None:
    assert render_notes([], duration_seconds=1.0).shape[0] > 0
    assert render_notes([{"instrument": "no-such-voice", "time": 0.2}], duration_seconds=1.0).size
    assert np.array_equal(
        render_notes([{"instrument": "no-such-voice", "time": 0.2}], duration_seconds=1.0),
        np.zeros_like(render_notes([], duration_seconds=1.0)),
    )
    assert render_notes([{"time": "bad", "instrument": "kick"}], duration_seconds=1.0).shape
    # 时长为 0 不报错，只剩一段尾韵静音：调用方（compose）会在音符表为空时先拒掉。
    silent = render_notes([], duration_seconds=0.0)
    assert silent.shape == (int(round(TAIL_SECONDS * SAMPLE_RATE)), CHANNELS)
    assert not np.any(silent)
    with pytest.raises(ValueError, match="采样率非法"):
        render_notes([], duration_seconds=1.0, sample_rate=0)


def test_pan_spreads_a_note_across_the_two_ears() -> None:
    center = render_notes([{"instrument": "kick", "time": 0.0, "pan": 0.0}], duration_seconds=0.5)
    hard_left = render_notes(
        [{"instrument": "kick", "time": 0.0, "pan": -1.0}], duration_seconds=0.5
    )
    assert float(np.max(np.abs(hard_left[:, 1]))) == pytest.approx(0.0, abs=1e-6)
    assert float(np.max(np.abs(hard_left[:, 0]))) > 0.0
    assert float(np.max(np.abs(center[:, 0]))) == pytest.approx(
        float(np.max(np.abs(center[:, 1]))), rel=1e-6
    )


def test_write_wav_roundtrips_through_the_stdlib_reader(tmp_path: Path) -> None:
    buffer = render_notes(arrange(_grid(), events=_EV, duration_seconds=1.0, fps=30), duration_seconds=1.0)
    target = tmp_path / "score.wav"
    written = write_wav(target, buffer, sample_rate=SAMPLE_RATE)
    assert target.is_file()
    with wave.open(str(target), "rb") as handle:
        assert handle.getnchannels() == CHANNELS
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnframes() == buffer.shape[0]
    # 返回值只算 PCM 数据（44 字节的 RIFF 头另计），差一个头长才对得上。
    assert target.stat().st_size == written + 44


def test_write_wav_accepts_mono_and_rejects_a_wrong_shape(tmp_path: Path) -> None:
    mono = np.zeros(4410, dtype=np.float32)
    assert write_wav(tmp_path / "mono.wav", mono) > 0
    with pytest.raises(ValueError, match="波形形状不对"):
        write_wav(tmp_path / "bad.wav", np.zeros((10, 5), dtype=np.float32))


# --------------------------------------------------------------- 4) ffmpeg 口径


def test_measure_command_carries_the_contract_targets() -> None:
    argv = audio_ffmpeg.measure_command(Path("/tmp/a.wav"))
    text = " ".join(argv)
    # 渲染契约第 6 条：-14 LUFS、真峰值不高于 -1 dBTP。这两个数改动要同时改契约。
    assert "I=-14.0" in text and "TP=-1.0" in text and "LRA=11.0" in text
    assert "print_format=json" in text
    assert argv[:2] == ["ffmpeg", "-hide_banner"]
    assert argv[-3:] == ["-f", "null", "-"]
    assert audio_ffmpeg.TARGET_LUFS == -14.0
    assert audio_ffmpeg.TARGET_TRUE_PEAK_DB == -1.0


def test_normalize_command_feeds_back_the_first_pass_measurements() -> None:
    argv = audio_ffmpeg.normalize_command(
        Path("/tmp/a.raw.wav"),
        Path("/tmp/a.wav"),
        {
            "input_i": "-18.20",
            "input_tp": "-3.10",
            "input_lra": "9.40",
            "input_thresh": "-28.50",
            "target_offset": "0.60",
        },
    )
    text = " ".join(argv)
    assert "measured_I=-18.20" in text
    assert "measured_TP=-3.10" in text
    assert "measured_LRA=9.40" in text
    assert "measured_thresh=-28.50" in text
    assert "offset=0.60" in text
    # 线性增益：单遍动态归一会在一段里来回推拉，几秒的 MG 一听就听得出。
    assert "linear=true" in text
    assert "-ar" in argv and "-ac" in argv
    assert argv[-1] == str(Path("/tmp/a.wav"))


def test_mux_command_copies_the_picture_and_only_encodes_audio() -> None:
    argv = audio_ffmpeg.mux_command(Path("/v.mp4"), Path("/a.wav"), Path("/o.mp4"))
    text = " ".join(argv)
    assert "-c:v copy" in text
    assert "-c:a aac" in text
    assert "-shortest" in text
    assert "+faststart" in text
    assert "-map 0:v:0" in text and "-map 1:a:0" in text


def test_parse_loudnorm_report_takes_the_last_statistics_block() -> None:
    text = (
        "some noise {\"unrelated\": 1}\n"
        "Output #0, wav, to 'a.wav':\n"
        "{\n  \"input_i\" : \"-20.15\",\n  \"input_tp\" : \"-6.02\",\n"
        "  \"input_lra\" : \"5.30\",\n  \"input_thresh\" : \"-30.55\",\n"
        "  \"target_offset\" : \"-3.85\"\n}\n"
        "ab\n"
        "{\n  \"input_i\" : \"-14.02\",\n  \"input_tp\" : \"-0.98\",\n"
        "  \"input_lra\" : \"6.10\",\n  \"input_thresh\" : \"-24.70\",\n"
        "  \"target_offset\" : \"0.02\"\n}\n"
    )
    report = audio_ffmpeg.parse_loudnorm_report(text)
    assert report["input_i"] == "-14.02"
    assert audio_ffmpeg.report_from(report).measured_input_lufs == pytest.approx(-14.02)
    assert audio_ffmpeg.report_from(report).as_dict()["target_lufs"] == -14.0


def test_parse_loudnorm_reports_what_it_could_not_read() -> None:
    with pytest.raises(MotionAudioError, match="没能在"):
        audio_ffmpeg.parse_loudnorm_report("no json here")
    with pytest.raises(MotionAudioError, match="缺字段"):
        audio_ffmpeg.parse_loudnorm_report('{"input_i": "-14", "input_tp": "-1"}')


def test_a_silent_measurement_lands_as_null_not_infinity() -> None:
    """ffmpeg 对全静音给的是 ``-inf``：它不是合法 JSON，落进 sidecar 会写坏整份文件。"""
    report = audio_ffmpeg.report_from(
        {
            "input_i": "-inf",
            "input_tp": "-inf",
            "input_lra": "0.00",
            "input_thresh": "-inf",
            "target_offset": "nan",
        }
    )
    assert report.measured_input_lufs == float("-inf")
    payload = {"audio": {"engine": "code-arranged", "loudness": report.as_dict()}}
    dumped = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert json.loads(dumped)["audio"]["loudness"]["measured_input_lufs"] is None
    assert json.loads(dumped)["audio"]["loudness"]["target_offset"] is None
    assert json.loads(dumped)["audio"]["loudness"]["measured_input_lra"] == 0.0
    assert json.loads(dumped)["audio"]["loudness"]["target_lufs"] == -14.0


_MEASURE_JSON = (
    "size=N/A time=00:00:02.00\n"
    "{\n  \"input_i\" : \"-18.20\",\n  \"input_tp\" : \"-3.10\",\n"
    "  \"input_lra\" : \"9.40\",\n  \"input_thresh\" : \"-28.50\",\n"
    "  \"target_offset\" : \"4.20\"\n}\n"
)


async def test_normalize_loudness_writes_the_second_pass_from_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "raw.wav"
    source.write_bytes(b"RIFF")
    target = tmp_path / "score.wav"
    calls: list[list[str]] = []

    async def runner(executable: str, argv, *, what: str):
        calls.append(list(argv))
        if what == "响度归一":
            target.write_bytes(b"RIFF-normalized")
        return 0, _MEASURE_JSON

    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(audio_ffmpeg, "_run", runner)
    report = await audio_ffmpeg.normalize_loudness(source, target)

    assert len(calls) == 2
    assert calls[0][0] == "/fake/ffmpeg"
    assert "measured_I=-18.20" in " ".join(calls[1])
    assert report.measured_input_lufs == pytest.approx(-18.2)
    assert target.is_file()


async def test_normalize_loudness_says_so_when_the_output_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "raw.wav"
    source.write_bytes(b"RIFF")
    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: "/fake/ffmpeg")

    async def runner(executable: str, argv, *, what: str):
        return 0, _MEASURE_JSON  # 报成功但没写文件

    monkeypatch.setattr(audio_ffmpeg, "_run", runner)
    with pytest.raises(MotionAudioError, match="没有产出文件"):
        await audio_ffmpeg.normalize_loudness(source, tmp_path / "score.wav")


async def test_mux_replaces_the_film_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "job.mp4"
    video.write_bytes(b"ORIGINAL")
    audio = tmp_path / "score.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: "/fake/ffmpeg")

    calls: list[list[str]] = []

    async def failing(executable: str, argv, *, what: str):
        calls.append(list(argv))
        raise MotionAudioError(f"{what}失败：编不了 aac")

    monkeypatch.setattr(audio_ffmpeg, "_run", failing)
    with pytest.raises(MotionAudioError, match="合轨失败"):
        await audio_ffmpeg.mux_into_video(video, audio)
    # 失败不许留下半个成片，也不许留下临时文件。
    assert video.read_bytes() == b"ORIGINAL"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["job.mp4", "score.wav"]

    async def succeeding(executable: str, argv, *, what: str):
        # 真 ffmpeg 会把产物写到 argv[-1]（临时文件），这里照写。
        Path(argv[-1]).write_bytes(b"WITH-AUDIO")
        return 0, ""

    monkeypatch.setattr(audio_ffmpeg, "_run", succeeding)
    await audio_ffmpeg.mux_into_video(video, audio)
    assert video.read_bytes() == b"WITH-AUDIO"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["job.mp4", "score.wav"]
    assert calls  # 失败那次确实起过合轨命令


async def test_audio_steps_refuse_loudly_without_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: None)
    with pytest.raises(MotionAudioError, match="本机没有 ffmpeg"):
        await audio_ffmpeg.normalize_loudness(tmp_path / "a.wav", tmp_path / "b.wav")
    with pytest.raises(MotionAudioError, match="合轨"):
        await audio_ffmpeg.mux_into_video(tmp_path / "a.mp4", tmp_path / "a.wav")


# --------------------------------------------------------------- 5) 编排顺序


async def test_compose_score_lays_out_the_track_and_cleans_the_intermediate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "job.mp4"
    video.write_bytes(b"mp4")
    calls: list[list[str]] = []

    async def runner(executable: str, argv, *, what: str):
        calls.append(list(argv))
        if what == "响度归一":
            Path(argv[-1]).write_bytes(b"RIFF")
        return 0, _MEASURE_JSON

    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(audio_ffmpeg, "_run", runner)

    score = await compose_score(
        video, events=_EV, duration_seconds=5.0, fps=30, case_category="音乐MV"
    )
    assert isinstance(score, MotionScore)
    assert score.bpm == 120.0
    assert score.grid_source == "events"
    # 分类决定档位：音乐 MV 走满编鼓。
    assert score.style == STYLE_DRIVE
    assert score.notes > 20
    assert score.score_path == score_path_for(video)
    assert score.score_path.is_file()
    # 中间那份未归一的 PCM 不该留在成片旁边。
    assert not raw_path_for(video).exists()
    assert len(calls) == 2

    payload = score.as_dict()
    assert payload["engine"] == "code-arranged"
    assert payload["loudness"]["target_lufs"] == -14.0
    assert json.dumps(payload, ensure_ascii=False)


async def test_compose_score_can_be_forced_to_a_style_and_a_tempo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "job.mp4"
    video.write_bytes(b"mp4")

    async def runner(executable: str, argv, *, what: str):
        if what == "响度归一":
            Path(argv[-1]).write_bytes(b"RIFF")
        return 0, _MEASURE_JSON

    monkeypatch.setattr(audio_ffmpeg, "ffmpeg_executable", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(audio_ffmpeg, "_run", runner)
    score = await compose_score(
        video,
        events=[],
        duration_seconds=4.0,
        fps=30,
        bpm_hint=90.0,
        style=STYLE_AMBIENT,
    )
    assert score.grid_source == "hint"
    assert score.bpm == 90.0
    assert score.style == STYLE_AMBIENT


async def test_compose_score_refuses_to_write_an_empty_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "job.mp4"
    monkeypatch.setattr(audio_score, "arrange", lambda *_a, **_k: ())
    with pytest.raises(MotionAudioError, match="排不出配乐"):
        await compose_score(video, events=_EV, duration_seconds=5.0, fps=30)
    assert not score_path_for(video).exists()


async def test_score_and_attach_muxes_after_the_score_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "job.mp4"
    order: list[str] = []

    async def fake_compose(target, **kwargs):
        order.append("compose")
        score_path = score_path_for(target)
        score_path.write_bytes(b"RIFF")
        return MotionScore(
            score_path=score_path,
            bpm=120.0,
            grid_source="events",
            style=STYLE_PULSE,
            notes=42,
            duration_seconds=5.0,
            sample_rate=SAMPLE_RATE,
            loudness=audio_ffmpeg.LoudnessReport(-18.2, -3.1, 9.4, -28.5, 4.2),
        )

    async def fake_mux(target, audio):
        order.append("mux")
        assert audio == score_path_for(target)
        return target

    monkeypatch.setattr(audio_score, "compose_score", fake_compose)
    monkeypatch.setattr(audio_score, "mux_into_video", fake_mux)
    score = await score_and_attach(video, events=_EV, duration_seconds=5.0, fps=30)
    assert order == ["compose", "mux"]
    assert score.notes == 42


# --------------------------------------------------------------- 6) 真 ffmpeg


@needs_ffmpeg
async def test_real_loudnorm_lands_on_the_contract_target(tmp_path: Path) -> None:
    """两遍 loudnorm 真的把这段程序生成的配乐推到 -14 LUFS（契约第 6 条）。"""
    notes = arrange(_grid(duration=4.0), events=_EV, duration_seconds=4.0, fps=30)
    raw = tmp_path / "score.raw.wav"
    buffer = render_notes(notes, duration_seconds=4.0)
    write_wav(raw, buffer)
    out = tmp_path / "score.wav"
    report = await audio_ffmpeg.normalize_loudness(raw, out)
    assert out.is_file()
    assert report.measured_input_lufs < audio_ffmpeg.TARGET_LUFS

    # 再量一遍产物：归一后的整体响度要落在 -14 附近，真峰值不许越过 -1 dBTP。
    # loudnorm 的 JSON 打在 stderr 上（生产路径用 STDOUT 合并把这条流收进 text）。
    process = await asyncio.create_subprocess_exec(
        *audio_ffmpeg.measure_command(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, measured = await process.communicate()
    after = audio_ffmpeg.parse_loudnorm_report(measured.decode("utf-8", errors="replace"))
    assert float(after["input_i"]) == pytest.approx(audio_ffmpeg.TARGET_LUFS, abs=1.5)
    assert float(after["input_tp"]) <= audio_ffmpeg.TARGET_TRUE_PEAK_DB + 0.5


@needs_ffmpeg
async def test_real_mux_keeps_the_picture_and_adds_one_audio_stream(tmp_path: Path) -> None:
    """合轨只动容器：画面字节原样 copy，mp4 里多出一条 aac。"""
    video = tmp_path / "film.mp4"
    audio = tmp_path / "score.wav"
    buffer = render_notes(
        arrange(_grid(), events=_EV, duration_seconds=2.0, fps=30), duration_seconds=2.0
    )
    write_wav(audio, buffer)
    # 画面用 lavfi 出两秒黑场：本用例要验的是 copy 语义，不是编码器选型。
    generator = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=64x64:r=15:d=2",
        "-pix_fmt",
        "yuv420p",
        str(video),
    )
    # 没接管管道时 communicate() 不会等进程退出（Windows 上 returncode 会是 None）。
    code = await generator.wait()
    if code != 0:
        pytest.skip(f"本机 ffmpeg 出不了测试画面（退出码 {code}）")
    before = video.stat().st_size
    await audio_ffmpeg.mux_into_video(video, audio)
    assert video.stat().st_size > before
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("本机没有 ffprobe，只验了合轨没报错")
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-of",
        "json",
        str(video),
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    streams = json.loads(stdout.decode("utf-8", errors="replace"))["streams"]
    assert [item["codec_type"] for item in streams] == ["video", "audio"]
    assert any(item["codec_name"] == "aac" for item in streams)
