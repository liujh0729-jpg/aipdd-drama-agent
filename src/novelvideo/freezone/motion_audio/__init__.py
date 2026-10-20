# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片的代码编曲引擎（渲染契约第 3、6 条的音频那一半）。

一句话：**拍点即乐谱** —— MG 页面自报的 ``window.EV`` 已经说了哪一帧该有变化，本模块
把那些时刻折成节拍网格、在网格上排鼓/贝斯/铺底/音效、用 numpy 渲染成 PCM，再按渲染
契约统一到 -14 LUFS / 真峰值 -1 dBTP，最后把音轨封进成片（画面 ``-c:v copy`` 不重编）。

不出网、不调模型、不引采样库：随包的只有代码，桌面包（exe / dmg）与容器形态拿到的
是同一份确定性合成，所以 ``tests/`` 里可以逐位比快照。scipy 只在可选的 ``world``
分组里，这里刻意只用 numpy —— 否则 CE 安装会凭空多一个重依赖。

模块分工（渲染任务只调 :func:`score_and_attach`）：

- `grid`：``window.EV`` → 节拍网格（BPM 从中位间隔推，octave 折进 45~180），音效落点
  比事件时刻**提前一帧**；
- `arrange`：网格 + 事件 → 音符表（drive / pulse / ambient 三档，Am–F–C–G 一格一和弦）；
- `synth`：音符表 → float32 立体声 PCM + 16 位 WAV；
- `ffmpeg`：两遍 loudnorm 归一 + 合轨命令；
- `score`：把上面四步串起来，产出可写进 sidecar 的 :class:`MotionScore`。
"""

from __future__ import annotations

from novelvideo.freezone.motion_audio.arrange import (
    STYLES,
    arrange,
    event_cues,
    midi_hz,
    style_for,
)
from novelvideo.freezone.motion_audio.ffmpeg import (
    TARGET_LRA,
    TARGET_LUFS,
    TARGET_TRUE_PEAK_DB,
    LoudnessReport,
    measure_command,
    mux_command,
    mux_into_video,
    normalize_command,
    normalize_loudness,
    parse_loudnorm_report,
)
from novelvideo.freezone.motion_audio.grid import (
    DEFAULT_BPM,
    TRANSIENT_LEAD_FRAMES,
    BeatGrid,
    MotionAudioError,
    analyze_grid,
    event_times,
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
    render_notes,
    write_wav,
)

__all__ = [
    "CHANNELS",
    "DEFAULT_BPM",
    "SAMPLE_RATE",
    "TARGET_LRA",
    "TARGET_LUFS",
    "TARGET_TRUE_PEAK_DB",
    "TRANSIENT_LEAD_FRAMES",
    "BeatGrid",
    "LoudnessReport",
    "MotionAudioError",
    "MotionScore",
    "STYLES",
    "analyze_grid",
    "arrange",
    "compose_score",
    "event_cues",
    "event_times",
    "measure_command",
    "midi_hz",
    "mux_command",
    "mux_into_video",
    "normalize_command",
    "normalize_loudness",
    "parse_loudnorm_report",
    "raw_path_for",
    "render_notes",
    "score_and_attach",
    "score_path_for",
    "style_for",
    "transient_times",
    "write_wav",
]
