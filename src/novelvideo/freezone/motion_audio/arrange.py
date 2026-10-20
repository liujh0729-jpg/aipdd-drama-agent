# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 代码编曲：在节拍网格上排鼓、贝斯、铺底与音效。

为什么要自己排：MG 成片交付的是「卡点画面」，静音的成片在剪映里只是一段素材，用户还得
自己找配乐、自己对点。图册案例的 ``window.EV`` 已经把「哪一帧该有变化」说清楚了，缺的
只是那一声。这里用规则把它排出来——不采样、不引素材库、不出网，因此桌面包里没有版权
与体积问题，也能纯单测。

规则来源是渲染契约第 3、6 条：动作落在一帧上、音效放在动作发生的那一帧、响度后面统一
交 loudnorm。风格只有三档（drive / pulse / ambient），由案例分类或推出来的速度决定，
不做「无限风格库」那种看起来像功能其实是坑的东西。
"""

from __future__ import annotations

import math
from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
from typing import Any

from novelvideo.freezone.motion_audio.grid import BeatGrid, transient_times

# 四四拍：小节内的强弱位置按这个数取。
BEATS_PER_BAR = 4
# 一次排片最多写多少个音符（8 秒 120BPM 的满编约 200 个，这里只是防失控）。
MAX_NOTES = 4000
# 收尾：最后一个拍点离片尾超过这么久，就补一声把片子收住。
_TAIL_GAP_SECONDS = 0.5
# 渐起（riser）的长度；短片不铺，否则一半篇幅在爬坡。
_RISER_SECONDS = 1.6
_RISER_MIN_DURATION = 6.0

STYLE_DRIVE = "drive"
STYLE_PULSE = "pulse"
STYLE_AMBIENT = "ambient"
STYLES = (STYLE_DRIVE, STYLE_PULSE, STYLE_AMBIENT)

# 案例分类 → 风格。键是图册里出现的分类名片段，匹配不上就走速度兜底。
# 顺序按「更具体的先判」：图册里"科普动画"和"风格动画"都带"动画"两字，把"科普"排在
# "动画"前面才不会把科普片也排成满编鼓。
CATEGORY_STYLES: tuple[tuple[str, str], ...] = (
    ("音乐", STYLE_DRIVE),
    ("mv", STYLE_DRIVE),
    ("舞蹈", STYLE_DRIVE),
    ("数据", STYLE_AMBIENT),
    ("可视化", STYLE_AMBIENT),
    ("白板", STYLE_AMBIENT),
    ("科普", STYLE_AMBIENT),
    ("动画", STYLE_PULSE),
    ("产品", STYLE_PULSE),
    ("展示", STYLE_PULSE),
)

# Am–F–C–G：一格里一个和弦，够用且不会跟任何画面打架。
_PROGRESSION: tuple[tuple[int, tuple[int, ...]], ...] = (
    (45, (45, 48, 52)),  # Am：根 / 小三 / 纯五
    (41, (41, 45, 48)),  # F
    (48, (48, 52, 55)),  # C
    (43, (43, 47, 50)),  # G
)
_PAD_OFFSET = 12  # 铺底比根音高一个八度，才不跟贝斯糊在一起
_PLUCK_OFFSET = 24

# EV 的 type → (用哪种音效, 力度)。认不出来按普通一击处理：拍点是事实，标签只是提示。
_EVENT_CUES: tuple[tuple[tuple[str, ...], str, float], ...] = (
    (("drop", "impact", "crash", "爆", "落"), "impact", 1.0),
    (("hit", "beat", "punch", "卡", "点"), "impact", 0.85),
    (("switch", "transition", "cut", "切", "转"), "impact", 0.7),
    (("click", "pop", "tap", "tick", "弹"), "tick", 0.5),
)
_DEFAULT_CUE = ("impact", 0.7)


def midi_hz(note: int) -> float:
    """MIDI 音号 → 频率（A4 = 69 = 440Hz）。"""
    return round(440.0 * (2.0 ** ((float(note) - 69.0) / 12.0)), 4)


def note_key(
    time_seconds: float,
    instrument: str,
    *,
    hz: float = 0.0,
    gain: float = 1.0,
    duration_seconds: float = 0.0,
    pan: float = 0.0,
) -> dict[str, Any]:
    """一个音符：``instrument`` 决定用哪个声部，``hz`` 只对需要音高的声部有意义。"""
    return {
        "time": round(max(0.0, float(time_seconds)), 6),
        "instrument": instrument,
        "hz": float(hz),
        "gain": round(float(gain), 4),
        "duration": round(max(0.0, float(duration_seconds)), 6),
        "pan": round(float(pan), 3),
    }


def style_for(category: str = "", bpm: float = 0.0) -> str:
    """分类优先，其次速度：慢到 90 以下按铺底走，快到 140 以上按满编鼓走。"""
    text = str(category or "").strip().lower()
    for keyword, style in CATEGORY_STYLES:
        if keyword.lower() in text:
            return style
    if bpm and bpm >= 140.0:
        return STYLE_DRIVE
    if bpm and bpm <= 90.0:
        return STYLE_AMBIENT
    return STYLE_PULSE


def _cue_for(raw_event: Any) -> tuple[str, float]:
    label = ""
    if isinstance(raw_event, ABCMapping):
        label = str(raw_event.get("type") or "").strip().lower()
    for keywords, kind, gain in _EVENT_CUES:
        if any(item in label for item in keywords):
            return kind, gain
    return _DEFAULT_CUE


def event_cues(
    events: Any, *, duration_seconds: float, fps: int
) -> tuple[tuple[float, str, float], ...]:
    """``window.EV`` → (落点时刻, 音效种类, 力度)，落点与其它层同口径提前一帧。

    收不下裸秒数：裸数字没有 ``type``，只能一律当普通一击，而「哪一声该多重」恰恰是
    拍点标签存在的意义 —— 宁可只认 ``{"t":…, "type":…}``，也不假装能推出力度。
    """
    if isinstance(events, (str, bytes)) or not isinstance(events, ABCSequence):
        return ()
    lead = 1.0 / float(fps) if fps and fps > 0 else 0.0
    out: list[tuple[float, str, float]] = []
    for item in events:
        if not isinstance(item, ABCMapping):
            continue
        try:
            value = float(item.get("t", item.get("time", item.get("ts"))))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0 or value > duration_seconds:
            continue
        kind, gain = _cue_for(item)
        out.append((round(max(0.0, value - lead), 6), kind, gain))
    return tuple(sorted(out, key=lambda entry: entry[0]))


def _bar_density(times: ABCSequence[float], *, period: float, bars: int) -> list[int]:
    """每一格里有多少个拍点：密度决定这一格要不要加踩镲与加花。"""
    counts = [0] * max(1, bars)
    if period <= 0:
        return counts
    for value in times:
        index = int(float(value) / (period * BEATS_PER_BAR))
        if 0 <= index < len(counts):
            counts[index] += 1
    return counts


def arrange(
    grid: BeatGrid,
    *,
    events: ABCSequence[Any] = (),
    duration_seconds: float,
    fps: int = 30,
    style: str = "",
    category: str = "",
) -> tuple[dict[str, Any], ...]:
    """在 :class:`BeatGrid` 上排一整段配乐，返回按时间排序的音符列表。

    时长非法或网格铺不出头就给空表：调用方据此把这一镜留成静音，而不是出半条音轨。
    """
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"编曲时长非法（{duration_seconds}）") from exc
    if duration <= 0 or grid.seconds_per_beat <= 0:
        return ()
    resolved = style if style in STYLES else style_for(category, grid.bpm)
    period = grid.seconds_per_beat
    bar_seconds = period * BEATS_PER_BAR
    notes: list[dict[str, Any]] = []

    cues = event_cues(events, duration_seconds=duration, fps=fps)
    if not cues:
        cues = tuple((value, "impact", 0.7) for value in grid.transients)
    density = _bar_density(
        [item[0] for item in cues], period=period, bars=int(duration / bar_seconds) + 1
    )
    loud = sum(density) / max(1, len(density))
    busy_threshold = max(3.0, loud * 1.4)

    for index, at in enumerate(grid.beat_times(duration)):
        beat_in_bar = index % BEATS_PER_BAR
        root, chord = _PROGRESSION[(index // BEATS_PER_BAR) % len(_PROGRESSION)]
        bar_busy = density[index // BEATS_PER_BAR] >= busy_threshold

        if resolved == STYLE_DRIVE:
            notes.append(note_key(at, "kick", gain=0.95 if beat_in_bar == 0 else 0.8))
        elif resolved == STYLE_PULSE:
            if beat_in_bar in (0, 2):
                notes.append(note_key(at, "kick", gain=0.9 if beat_in_bar == 0 else 0.7))
        elif beat_in_bar == 0:
            notes.append(note_key(at, "kick", gain=0.72))

        if resolved in (STYLE_DRIVE, STYLE_PULSE) and beat_in_bar in (1, 3):
            notes.append(note_key(at, "snare", gain=0.6 if bar_busy else 0.5))

        if resolved == STYLE_DRIVE:
            notes.append(note_key(at + period / 2.0, "hat", gain=0.26))
            if bar_busy:
                notes.append(note_key(at, "hat", gain=0.2))
        elif resolved == STYLE_PULSE:
            notes.append(note_key(at + period / 2.0, "hat", gain=0.16))

        if beat_in_bar in ((0, 2) if resolved != STYLE_AMBIENT else (0,)):
            notes.append(
                note_key(at, "bass", hz=midi_hz(root), duration_seconds=period * 0.9, gain=0.55)
            )
        if resolved == STYLE_DRIVE and beat_in_bar == 2:
            notes.append(
                note_key(
                    at + period / 2.0,
                    "bass",
                    hz=midi_hz(root + 7),
                    duration_seconds=period * 0.4,
                    gain=0.4,
                )
            )

        if beat_in_bar == 0:
            notes.append(
                note_key(
                    at,
                    "pad",
                    hz=midi_hz(chord[0] + _PAD_OFFSET),
                    duration_seconds=bar_seconds,
                    gain=0.22 if resolved != STYLE_AMBIENT else 0.3,
                    pan=-0.25,
                )
            )
            notes.append(
                note_key(
                    at,
                    "pad",
                    hz=midi_hz(chord[-1] + _PAD_OFFSET),
                    duration_seconds=bar_seconds,
                    gain=0.18 if resolved != STYLE_AMBIENT else 0.26,
                    pan=0.25,
                )
            )

        # 拨弦只给中低密度风格用：满编鼓再加琶音就糊成一片了。
        if resolved in (STYLE_PULSE, STYLE_AMBIENT) and index % 2 == 1:
            tone = chord[(index // 2) % len(chord)] + _PLUCK_OFFSET
            notes.append(
                note_key(
                    at,
                    "pluck",
                    hz=midi_hz(tone),
                    duration_seconds=period * 0.6,
                    gain=0.3 if resolved == STYLE_PULSE else 0.24,
                    pan=-0.35 if index % 4 == 1 else 0.35,
                )
            )

    for at, kind, gain in cues:
        notes.append(note_key(at, kind, gain=gain))

    if resolved != STYLE_AMBIENT and duration >= _RISER_MIN_DURATION:
        tail = [item for item in cues if item[0] >= duration * 0.55]
        anchor = tail[0][0] if tail else duration
        notes.append(
            note_key(
                max(0.0, anchor - _RISER_SECONDS),
                "riser",
                duration_seconds=_RISER_SECONDS,
                gain=0.34,
            )
        )

    last_hit = cues[-1][0] if cues else 0.0
    if last_hit < duration - _TAIL_GAP_SECONDS:
        notes.append(note_key(max(0.0, duration - 0.12), "impact", gain=0.9))

    notes = [item for item in notes if item["time"] <= duration + 1e-6]
    notes.sort(key=lambda item: (item["time"], item["instrument"]))
    return tuple(notes[:MAX_NOTES])
