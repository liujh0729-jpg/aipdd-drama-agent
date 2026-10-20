# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 代码编曲：一镜 MG 成片的配乐成品（分析 → 排 → 渲染 → 归一 → 合轨）。

串起来的是这几件事的顺序，而不是某一步的技巧：拍点先定网格，网格上排音符，音符渲染成
PCM，PCM 两遍 loudnorm 到 -14 LUFS / -1 dBTP，最后把音轨封进成片（画面 copy 不重编）。
分轨 ``*.score.wav`` 会留在成片旁边：剪映里想单独拧音乐音量、或整集只留一条音乐，都
需要这一条现成的干声。
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from novelvideo.freezone.motion_audio.arrange import STYLES, arrange, style_for
from novelvideo.freezone.motion_audio.ffmpeg import (
    LoudnessReport,
    mux_into_video,
    normalize_loudness,
)
from novelvideo.freezone.motion_audio.grid import MotionAudioError, analyze_grid
from novelvideo.freezone.motion_audio.synth import SAMPLE_RATE, render_notes, write_wav

logger = logging.getLogger(__name__)

SCORE_SUFFIX = ".score.wav"
RAW_SUFFIX = ".score.raw.wav"


@dataclass(frozen=True, slots=True)
class MotionScore:
    """一镜的配乐结果：写进 sidecar 的就是它。"""

    score_path: Path
    bpm: float
    grid_source: str
    style: str
    notes: int
    duration_seconds: float
    sample_rate: int
    loudness: LoudnessReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": "code-arranged",
            "score_path": str(self.score_path),
            "bpm": round(self.bpm, 2),
            "grid_source": self.grid_source,
            "style": self.style,
            "notes": self.notes,
            "duration_seconds": round(self.duration_seconds, 3),
            "sample_rate": self.sample_rate,
            "loudness": self.loudness.as_dict(),
        }


def score_path_for(video_path: Path) -> Path:
    """成片 ``xxx.mp4`` 的分轨干声：``xxx.score.wav``（同名同目录，好认也好清理）。"""
    return video_path.with_suffix(SCORE_SUFFIX)


def raw_path_for(video_path: Path) -> Path:
    """归一化之前的中间 PCM：``xxx.score.raw.wav``，只在一遍 loudnorm 时存在。"""
    return video_path.with_suffix(RAW_SUFFIX)


async def compose_score(
    video_path: Path,
    *,
    events: Sequence[Any],
    duration_seconds: float,
    fps: int,
    case_category: str = "",
    bpm_hint: float | None = None,
    style: str = "",
) -> MotionScore:
    """给这一镜排一条配乐并落成分轨，但先不动画面。"""
    grid = analyze_grid(
        events, duration_seconds=duration_seconds, fps=fps, bpm_hint=bpm_hint
    )
    resolved_style = (
        style if style in STYLES else style_for(case_category, grid.bpm)
    )
    notes = arrange(
        grid,
        events=events,
        duration_seconds=duration_seconds,
        fps=fps,
        style=resolved_style,
        category=case_category,
    )
    if not notes:
        raise MotionAudioError(
            f"这一镜排不出配乐（时长 {duration_seconds} 秒）：请确认渲染时长与拍点"
        )
    raw_path = raw_path_for(video_path)
    out_path = score_path_for(video_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = render_notes(notes, duration_seconds=duration_seconds, sample_rate=SAMPLE_RATE)
    write_wav(raw_path, buffer, sample_rate=SAMPLE_RATE)
    try:
        loudness = await normalize_loudness(raw_path, out_path)
    finally:
        # 中间 PCM 只是两遍 loudnorm 的第一遍输入：留下会让每镜多出一份没人要的 wav。
        with contextlib.suppress(OSError):
            raw_path.unlink(missing_ok=True)
    logger.info(
        "[motion-audio] 分轨 %s：%d 个音符 · %.1f BPM · 归一前 %.1f LUFS",
        out_path.name,
        len(notes),
        grid.bpm,
        loudness.measured_input_lufs,
    )
    return MotionScore(
        score_path=out_path,
        bpm=grid.bpm,
        grid_source=grid.source,
        style=resolved_style,
        notes=len(notes),
        duration_seconds=float(duration_seconds),
        sample_rate=SAMPLE_RATE,
        loudness=loudness,
    )


async def score_and_attach(
    video_path: Path,
    *,
    events: Sequence[Any],
    duration_seconds: float,
    fps: int,
    case_category: str = "",
    bpm_hint: float | None = None,
    style: str = "",
) -> MotionScore:
    """排乐、归一，再把音轨合进成片（原地替换，画面编码不变）。"""
    score = await compose_score(
        video_path,
        events=events,
        duration_seconds=duration_seconds,
        fps=fps,
        case_category=case_category,
        bpm_hint=bpm_hint,
        style=style,
    )
    await mux_into_video(video_path, score.score_path)
    return score
