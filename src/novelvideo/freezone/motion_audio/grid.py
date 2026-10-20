# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 代码编曲：把 MG 页面自报的拍点翻译成节拍网格。

渲染契约第 3 条要的是「配乐先进节拍分析得到网格，镜头切换与关键动作落在网格上」，而页面
能给出的节奏信息只有 ``window.EV`` 那几个时刻。所以网格只从事件间隔推；推不出来才退到
案例文本里写的 BPM，再退到 120。不猜风格、不出网、不调模型，可以纯单测。

``transients`` 是**提前一帧**的事件时刻：画面在第 N 帧画出变化，耳朵要在那一帧看见时已经
听到声音，落在第 N+1 帧就永远慢一帧（30fps 下约 33ms，卡点听感直接散）。剪映时间轴上的
打点仍然写原始 EV 时刻（那边是 ``motion_draft``），两件事各管各的。
"""

from __future__ import annotations

import math
from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
from dataclasses import dataclass
from statistics import median
from typing import Any

# 推不出来时的退路：图册案例里最常出现的档位。
DEFAULT_BPM = 120.0
# 合奏网格的合理区间：低于 45 一拍要两秒多，高于 180 已经听不出「拍」。
MIN_BPM = 45.0
MAX_BPM = 180.0
# 瞬态相对 EV 提前多少帧（见模块头的理由）。
TRANSIENT_LEAD_FRAMES = 1
# 相位搜索的样本上限：EV 最多 4096 个点，全 pairwise 打分太慢，而相位只需要几个代表点。
_MAX_PHASE_CANDIDATES = 12
_MAX_PHASE_SCORE_POINTS = 48


class MotionAudioError(ValueError):
    """编曲输入不可用（时长非法 / 采样率非法）——消息直接给用户看。"""


@dataclass(frozen=True, slots=True)
class BeatGrid:
    """一拍一拍排出来的网格。"""

    bpm: float
    seconds_per_beat: float
    phase_seconds: float
    events: tuple[float, ...] = ()
    transients: tuple[float, ...] = ()
    # 网格从哪来：events（间隔推出来的）/ hint（案例文本给的 BPM）/ default（兜底）。
    source: str = "default"

    def beat_times(self, duration_seconds: float, *, bars: int = 1) -> tuple[float, ...]:
        """网格点（秒），落在 ``[0, duration]`` 内。

        ``bars`` > 1 时取「每 ``bars`` 拍一个」，用来铺小节级的低音与和声。
        """
        if self.seconds_per_beat <= 0 or duration_seconds <= 0:
            return ()
        step = self.seconds_per_beat * max(1, int(bars))
        start = self.phase_seconds
        while start < 0:
            start += step
        # 从第一个非负网格点往前补齐 0 附近可能漏掉的一拍。
        points: list[float] = []
        cursor = start
        while cursor <= duration_seconds + 1e-6:
            if cursor >= -1e-6:
                points.append(round(cursor, 6))
            cursor += step
        return tuple(points)

    def seconds_to_beat(self, seconds: float) -> int:
        """把秒换算成「第几拍」（从 0 起，向下取整）：小节里的强弱位置靠它判。"""
        if self.seconds_per_beat <= 0:
            return 0
        return int(math.floor(max(0.0, seconds) / self.seconds_per_beat))


def event_times(events: Any, *, duration_seconds: float) -> tuple[float, ...]:
    """``window.EV`` → 升序去重的秒数，越界与非数一律丢掉。

    与 ``motion_draft.project.beats_to_microseconds`` 同口径：既收 ``{"t": 0.5}`` 也收
    裸 ``0.5``，脏值静默跳过——编曲少一个音效不至于让整条片子失败。
    """
    out: list[float] = []
    if isinstance(events, (str, bytes)) or not isinstance(events, ABCSequence):
        return ()
    for item in events:
        raw: Any = item
        if isinstance(item, ABCMapping):
            raw = item.get("t", item.get("time", item.get("ts")))
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        if duration_seconds > 0 and value > duration_seconds:
            continue
        if value not in out:
            out.append(value)
    return tuple(sorted(out))


def transient_times(events: Any, *, duration_seconds: float, fps: int) -> tuple[float, ...]:
    """音效该落的时刻：EV 时刻提前 :data:`TRANSIENT_LEAD_FRAMES` 帧，负数归零。"""
    lead = TRANSIENT_LEAD_FRAMES / float(fps) if fps and fps > 0 else 0.0
    out: list[float] = []
    for value in event_times(events, duration_seconds=duration_seconds):
        shifted = round(max(0.0, value - lead), 6)
        if shifted not in out:
            out.append(shifted)
    return tuple(sorted(out))


def normalize_bpm(bpm: float) -> float:
    """把推断出的速度折进可听区间： octave 歧义（每两拍打一次点）在这里解掉。"""
    value = float(bpm)
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_BPM
    while value < MIN_BPM:
        value *= 2.0
    while value > MAX_BPM:
        value /= 2.0
    return round(value, 2)


def infer_bpm(times: tuple[float, ...]) -> float | None:
    """从事件间隔推 BPM：取中位间隔当一拍。少于两个点推不出来就交给上层退路。"""
    if len(times) < 2:
        return None
    gaps = [b - a for a, b in zip(times, times[1:]) if b - a > 1e-3]
    if not gaps:
        return None
    period = float(median(gaps))
    if period <= 0:
        return None
    bpm = normalize_bpm(60.0 / period)
    return bpm


def _phase_score(phase: float, period: float, points: tuple[float, ...]) -> float:
    total = 0.0
    for value in points:
        offset = (value - phase) % period
        total += min(offset, period - offset)
    return total


def align_phase(times: tuple[float, ...], period: float) -> float:
    """挑一个相位，让网格尽量穿过这些点（卡点片子的网格必须贴着 EV 走）。"""
    if period <= 0 or not times:
        return 0.0
    candidates: list[float] = []
    stride = max(1, len(times) // _MAX_PHASE_CANDIDATES)
    for value in times[::stride][:_MAX_PHASE_CANDIDATES]:
        candidates.append(float(value) % period)
    scored = sorted(
        (_phase_score(phase % period, period, times[:_MAX_PHASE_SCORE_POINTS]), phase % period)
        for phase in candidates
    )
    return round(scored[0][1], 6) if scored else 0.0


def analyze_grid(
    events: Any,
    *,
    duration_seconds: float,
    fps: int,
    bpm_hint: float | None = None,
) -> BeatGrid:
    """编排用的入口：事件 + 时长（+ 可选案例 BPM）→ :class:`BeatGrid`。

    时长非法就直接拒：网格铺不出头，后面所有时刻都会挤在 0 上，不如当场喊出来。
    """
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise MotionAudioError("编曲没有时长：请给出这一镜的秒数") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MotionAudioError(f"编曲时长非法（{duration_seconds}）")

    times = event_times(events, duration_seconds=duration)
    transients = transient_times(events, duration_seconds=duration, fps=fps)
    inferred = infer_bpm(times)
    if inferred is not None:
        bpm, source = inferred, "events"
    elif bpm_hint is not None and float(bpm_hint) > 0:
        bpm, source = normalize_bpm(float(bpm_hint)), "hint"
    else:
        bpm, source = DEFAULT_BPM, "default"
    period = 60.0 / bpm
    phase = align_phase(transients or times, period)
    return BeatGrid(
        bpm=bpm,
        seconds_per_beat=round(period, 6),
        phase_seconds=phase,
        events=times,
        transients=transients,
        source=source,
    )
