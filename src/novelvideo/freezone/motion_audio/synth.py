# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 代码编曲：把音符表渲染成 PCM（纯 numpy，不引采样与第三方 DSP）。

为什么不用 scipy / 不用现成音源库：随包体积与许可证都要算进桌面形态（exe / dmg），而这里
需要的只有「一条能被 loudnorm 归一的节奏床」，用 numpy 的加法合成 + FFT 塑形就够，
且完全确定 —— 同一份音符表在任何机器上渲染出同一串样本，这才敢拿去做快照测试。

采样率固定 44100、立体声：剪映对素材采样率不挑，但两条声比单声道更能把铺底与音效的
错位感摊开，也留出 pan 的落点。
"""

from __future__ import annotations

import math
import wave
from collections.abc import Sequence
from typing import Any

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
# 片尾留一点余韵：最后一个音效的衰减不该被成片长度硬切。
TAIL_SECONDS = 0.35
_FADE_IN_SECONDS = 0.006
_FADE_OUT_SECONDS = 0.2
# 峰值超过这个数就先整体压下来：加法合成容易叠出削顶，削顶是坏声音，压峰不是。
_HEADROOM_PEAK = 0.98

DEFAULT_SEED = 20260419


def _noise(length: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(length)


def _band_shape(noise: np.ndarray, *, low: float, high: float, sample_rate: int) -> np.ndarray:
    """FFT 带通：把白噪削成听得出的镲/军鼓质感（low/high 给 0 表示不裁那一侧）。"""
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(len(noise), d=1.0 / sample_rate)
    width = max(20.0, (high - low) * 0.35) if low and high else 0.0
    if low and high:
        mask = np.exp(-(((freqs - (low + high) / 2.0) / width) ** 2))
    elif high:
        mask = 1.0 / (1.0 + (freqs / max(high, 1.0)) ** 6)
    else:
        mask = 1.0 / (1.0 + (max(low, 1.0) / np.maximum(freqs, 1.0)) ** 6)
    return np.fft.irfft(spectrum * mask, n=len(noise))


def _adsr(length: int, *, attack: float, decay: float, sustain: float, release: float, sample_rate: int) -> np.ndarray:
    """简单包络：attack/decay/release 以秒计，sustain 是平台高度。"""
    env = np.ones(length, dtype=np.float64)
    a = max(1, int(attack * sample_rate))
    d = max(1, int(decay * sample_rate))
    r = max(1, int(release * sample_rate))
    if a < length:
        env[:a] = np.linspace(0.0, 1.0, a)
    body_end = min(length, a + d)
    if body_end > a:
        span = np.linspace(1.0, sustain, body_end - a)
        env[a:body_end] = span
    if length > body_end:
        tail = min(r, length - body_end)
        env[length - tail :] = np.linspace(sustain, 0.0, tail)
    return env


def _sine_sweep(length: int, *, start_hz: float, end_hz: float, curve: float, sample_rate: int) -> np.ndarray:
    """指数趋近的扫频正弦：底鼓的「咚」和 riser 的上行都靠它。"""
    t = np.arange(length, dtype=np.float64) / float(sample_rate)
    span = end_hz - start_hz
    if t.size == 0 or abs(curve) < 1e-6:
        hz = np.full(t.shape, start_hz, dtype=np.float64)
    else:
        hz = start_hz + span * (1.0 - np.exp(-abs(curve) * t / max(float(t[-1]), 1e-9)))
    phase = 2.0 * np.pi * np.cumsum(hz) / float(sample_rate)
    return np.sin(phase)


def _additive(length: int, hz: float, *, harmonics: int, sample_rate: int, detune: float = 0.0) -> np.ndarray:
    """谐波叠加：贝斯/铺底/拨弦共用，靠谐波数与 detune 拉开质感。"""
    t = np.arange(length, dtype=np.float64) / float(sample_rate)
    out = np.zeros(length, dtype=np.float64)
    for order in range(1, max(1, harmonics) + 1):
        for shift in ((-detune, detune) if detune else (0.0,)):
            out += np.sin(2.0 * np.pi * hz * order * (1.0 + shift) * t) / float(order)
    return out


def _voice(note: dict[str, Any], sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """一个音符 → 单声道波形；长度就是它自己该占的时间。"""
    kind = str(note.get("instrument") or "")
    gain = float(note.get("gain") or 1.0)
    seconds = float(note.get("duration") or 0.0)
    hz = float(note.get("hz") or 0.0)

    if kind == "kick":
        length = int(0.26 * sample_rate)
        body = _sine_sweep(length, start_hz=132.0, end_hz=46.0, curve=6.0, sample_rate=sample_rate)
        env = _adsr(length, attack=0.001, decay=0.07, sustain=0.35, release=0.16, sample_rate=sample_rate)
        click = _band_shape(_noise(int(0.006 * sample_rate), rng), low=1200.0, high=0.0, sample_rate=sample_rate)
        out = body * env
        out[: len(click)] += click * 0.35
        return out * gain * 0.9
    if kind == "impact":
        length = int(0.55 * sample_rate)
        body = _sine_sweep(length, start_hz=96.0, end_hz=38.0, curve=5.0, sample_rate=sample_rate)
        env = _adsr(length, attack=0.002, decay=0.14, sustain=0.4, release=0.34, sample_rate=sample_rate)
        burst = _band_shape(_noise(length, rng), low=180.0, high=2600.0, sample_rate=sample_rate)
        burst_env = _adsr(length, attack=0.001, decay=0.09, sustain=0.1, release=0.28, sample_rate=sample_rate)
        return (body * env + burst * burst_env * 0.5) * gain
    if kind == "tick":
        length = int(0.06 * sample_rate)
        tone = np.sin(2.0 * np.pi * (hz or 2200.0) * np.arange(length) / sample_rate)
        noise = _band_shape(_noise(length, rng), low=6000.0, high=0.0, sample_rate=sample_rate)
        env = _adsr(length, attack=0.0008, decay=0.012, sustain=0.0, release=0.04, sample_rate=sample_rate)
        return (tone * 0.6 + noise * 0.5) * env * gain
    if kind == "snare":
        length = int(0.2 * sample_rate)
        noise = _band_shape(_noise(length, rng), low=1400.0, high=7200.0, sample_rate=sample_rate)
        tone = np.sin(2.0 * np.pi * 192.0 * np.arange(length) / sample_rate)
        env = _adsr(length, attack=0.001, decay=0.05, sustain=0.12, release=0.12, sample_rate=sample_rate)
        return (noise * env + tone * env * 0.35) * gain
    if kind == "hat":
        length = int(0.07 * sample_rate)
        noise = _band_shape(_noise(length, rng), low=7800.0, high=0.0, sample_rate=sample_rate)
        env = _adsr(length, attack=0.0008, decay=0.016, sustain=0.0, release=0.045, sample_rate=sample_rate)
        return noise * env * gain
    if kind == "bass":
        length = int(max(0.12, seconds) * sample_rate)
        out = _additive(length, hz or 55.0, harmonics=4, sample_rate=sample_rate)
        env = _adsr(length, attack=0.006, decay=0.05, sustain=0.7, release=0.08, sample_rate=sample_rate)
        return out * env * gain * 0.5
    if kind == "pad":
        length = int(max(0.4, seconds) * sample_rate)
        out = _additive(length, hz or 220.0, harmonics=3, sample_rate=sample_rate, detune=0.006)
        env = _adsr(length, attack=0.25, decay=seconds * 0.3, sustain=0.55, release=0.3, sample_rate=sample_rate)
        return out * env * gain * 0.35
    if kind == "pluck":
        length = int(max(0.12, seconds) * sample_rate)
        out = _additive(length, hz or 440.0, harmonics=6, sample_rate=sample_rate)
        env = _adsr(length, attack=0.002, decay=0.09, sustain=0.05, release=0.12, sample_rate=sample_rate)
        return out * env * gain * 0.5
    if kind == "riser":
        length = int(max(0.4, seconds) * sample_rate)
        sweep = _sine_sweep(length, start_hz=220.0, end_hz=1760.0, curve=3.0, sample_rate=sample_rate)
        noise = _band_shape(_noise(length, rng), low=900.0, high=9000.0, sample_rate=sample_rate)
        ramp = np.linspace(0.0, 1.0, length) ** 1.6
        return (sweep * 0.5 + noise * 0.6) * ramp * gain
    return np.zeros(1, dtype=np.float64)


def render_notes(
    notes: Sequence[dict[str, Any]],
    *,
    duration_seconds: float,
    sample_rate: int = SAMPLE_RATE,
    seed: int = DEFAULT_SEED,
) -> np.ndarray:
    """音符表 → ``float32`` 立体声波形（长度 = 时长 + :data:`TAIL_SECONDS` 余韵）。"""
    rate = int(sample_rate)
    if rate <= 0:
        raise ValueError(f"采样率非法（{sample_rate}）")
    total = int(round(max(0.0, float(duration_seconds)) * rate)) + int(round(TAIL_SECONDS * rate))
    if total <= 0:
        return np.zeros((0, CHANNELS), dtype=np.float32)
    left = np.zeros(total, dtype=np.float64)
    right = np.zeros(total, dtype=np.float64)
    rng = np.random.default_rng(seed)
    for index, note in enumerate(notes or ()):
        try:
            offset = int(round(float(note.get("time") or 0.0) * rate))
        except (TypeError, ValueError):
            continue
        if offset < 0 or offset >= total:
            continue
        # 每个音符独立的随机流：整体重跑一次必须逐位一致，否则快照测试没意义。
        mono = _voice(dict(note), rate, np.random.default_rng(seed + index * 7919))
        if mono.size == 0:
            continue
        span = min(mono.size, total - offset)
        pan = max(-1.0, min(1.0, float(note.get("pan") or 0.0)))
        theta = (pan + 1.0) * math.pi / 4.0
        left[offset : offset + span] += mono[:span] * math.cos(theta)
        right[offset : offset + span] += mono[:span] * math.sin(theta)
    buffer = np.stack([left, right], axis=1)
    fade_in = min(int(_FADE_IN_SECONDS * rate), total)
    if fade_in > 1:
        buffer[:fade_in] *= np.linspace(0.0, 1.0, fade_in)[:, None]
    fade_out = min(int(_FADE_OUT_SECONDS * rate), total)
    if fade_out > 1:
        buffer[total - fade_out :] *= np.linspace(1.0, 0.0, fade_out)[:, None]
    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > _HEADROOM_PEAK:
        buffer *= _HEADROOM_PEAK / peak
    return buffer.astype(np.float32, copy=False)


def write_wav(path: Any, samples: np.ndarray, *, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> int:
    """写 16 位 PCM WAV；返回落盘字节数。单声道输入会被铺成双声道。"""
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1) if channels == 2 else data[:, None]
    if data.ndim != 2 or data.shape[1] != channels:
        raise ValueError(f"波形形状不对（{data.shape}，应为 (-1, {channels})）")
    frames = np.clip(data, -1.0, 1.0)
    pcm = (frames * 32767.0).astype("<i2")
    target = str(path)
    with wave.open(target, "wb") as handle:
        handle.setnchannels(int(channels))
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return int(np.prod(pcm.shape)) * 2 if pcm.size else 0
