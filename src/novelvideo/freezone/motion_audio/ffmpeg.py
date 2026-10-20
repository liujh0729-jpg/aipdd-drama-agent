# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 代码编曲：响度归一与合轨（都走本机 ffmpeg）。

渲染契约第 6 条把声音交付口径钉死了：**统一到 -14 LUFS、真峰值不高于 -1 dBTP**。这是
流媒体平台的通行档位，也正是 ffmpeg ``loudnorm``（EBU R128）的用途。这里按它推荐的
两遍法走：第一遍只测不写，把 ``print_format=json`` 的量出来的值原样喂给第二遍 —— 单遍
动态归一会在一段里来回推拉，逐镜 MG 的成片长度只有几秒，那种推拉一听就听得出。

合轨用 ``-c:v copy``：画面是刚渲出来的 H.264，重编一遍既慢又会二次损画质，音轨只是
AAC 重新封装。替换是原子的（写临时文件再 ``os.replace``）——任务被取消时不能留下半个
成片。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from novelvideo.freezone.motion_audio.grid import MotionAudioError
from novelvideo.freezone.motion_render.encoder import (
    ffmpeg_executable,
    stderr_tail,
    terminate_process,
)

logger = logging.getLogger(__name__)

# 渲染契约第 6 条的目标值：LUFS / 真峰值 dBTP / 响度范围。
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK_DB = -1.0
TARGET_LRA = 11.0

# 音频处理的等待上限：一条几秒的片子两遍 loudnorm 是秒级，超了就是卡死。
_AUDIO_TIMEOUT_SECONDS = 300.0
# 合轨码率：192k 对 44.1k 立体声绰绰有余，也和成片一样留 faststart。
_AUDIO_BITRATE = "192k"

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


def _finite_or_none(value: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 3) if math.isfinite(number) else None


@dataclass(frozen=True, slots=True)
class LoudnessReport:
    """第一遍量出来的事实，跟着 sidecar 一起落盘，好让「为什么这集听起来响」可查。"""

    measured_input_lufs: float
    measured_input_true_peak_db: float
    measured_input_lra: float
    measured_input_threshold: float
    target_offset: float
    raw: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        # 全静音/极端输入的 input_i 会是真的 -inf，脏值落进 sidecar 会变成 JSON 里
        # 没有的 Infinity —— 落 null 让下游读得出「这项没量到」，而不是写坏整份文件。
        return {
            "measured_input_lufs": _finite_or_none(self.measured_input_lufs),
            "measured_input_true_peak_db": _finite_or_none(self.measured_input_true_peak_db),
            "measured_input_lra": _finite_or_none(self.measured_input_lra),
            "measured_input_threshold": _finite_or_none(self.measured_input_threshold),
            "target_offset": _finite_or_none(self.target_offset),
            "target_lufs": TARGET_LUFS,
            "target_true_peak_db": TARGET_TRUE_PEAK_DB,
        }


def require_ffmpeg(what: str) -> str:
    executable = ffmpeg_executable()
    if not executable:
        raise MotionAudioError(
            f"本机没有 ffmpeg，{what}：桌面版请确认安装包自带 bin/ffmpeg，"
            "web 形态请在镜像里装 ffmpeg 或把它放进 PATH"
        )
    return executable


def _target_args(
    *, target_i: float, target_tp: float, target_lra: float, extra: Sequence[str] = ()
) -> list[str]:
    parts = [
        f"I={target_i}",
        f"TP={target_tp}",
        f"LRA={target_lra}",
        "print_format=json",
        *extra,
    ]
    return ["-af", f"loudnorm={':'.join(parts)}"]


def measure_command(
    input_path: Path,
    *,
    target_i: float = TARGET_LUFS,
    target_tp: float = TARGET_TRUE_PEAK_DB,
    target_lra: float = TARGET_LRA,
) -> list[str]:
    """第一遍：只量不写。``-f null`` 让 ffmpeg 把统计打在 stderr 就走。"""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        str(input_path),
        *_target_args(target_i=target_i, target_tp=target_tp, target_lra=target_lra),
        "-f",
        "null",
        "-",
    ]


def normalize_command(
    input_path: Path,
    output_path: Path,
    measured: Mapping[str, str],
    *,
    target_i: float = TARGET_LUFS,
    target_tp: float = TARGET_TRUE_PEAK_DB,
    target_lra: float = TARGET_LRA,
) -> list[str]:
    """第二遍：把第一遍量到的值原样带回去，用线性增益归一（不引入动态压缩）。"""
    extra = [
        f"measured_I={measured['input_i']}",
        f"measured_TP={measured['input_tp']}",
        f"measured_LRA={measured['input_lra']}",
        f"measured_thresh={measured['input_thresh']}",
        f"offset={measured['target_offset']}",
        "linear=true",
    ]
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        *_target_args(target_i=target_i, target_tp=target_tp, target_lra=target_lra, extra=extra),
        "-ar",
        "44100",
        "-ac",
        "2",
        str(output_path),
    ]


def parse_loudnorm_report(text: str) -> Mapping[str, str]:
    """从 ffmpeg 输出里取出最后一块 ``loudnorm`` 的 JSON 统计。

    只认带 ``input_i`` 的那一块：日志里还有别的 ``{...}``（比如容器元数据），认错就变成
    拿一堆垃圾值去喂第二遍，出片响度会莫名其妙。
    """
    candidates: list[Mapping[str, str]] = []
    for block in _JSON_BLOCK.findall(text or ""):
        try:
            parsed = json.loads(block)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "input_i" in parsed:
            candidates.append({str(k): str(v) for k, v in parsed.items()})
    if not candidates:
        raise MotionAudioError("没能在 ffmpeg 输出里读到 loudnorm 的响度统计")
    report = candidates[-1]
    # 键名以本机 ffmpeg 实际打出来的为准：input_i / input_tp / input_lra / input_thresh /
    # target_offset。第二遍的 measured_* 与 offset 就是按这五个回填的，缺一个都不能开跑。
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    missing = [key for key in required if key not in report]
    if missing:
        raise MotionAudioError(f"ffmpeg 的响度统计缺字段：{'、'.join(missing)}")
    return report


def report_from(measured: Mapping[str, str]) -> LoudnessReport:
    def number(key: str) -> float:
        try:
            return float(measured[key])
        except (KeyError, TypeError, ValueError):
            return float("nan")

    return LoudnessReport(
        measured_input_lufs=number("input_i"),
        measured_input_true_peak_db=number("input_tp"),
        measured_input_lra=number("input_lra"),
        measured_input_threshold=number("input_thresh"),
        target_offset=number("target_offset"),
        raw=dict(measured),
    )


def mux_command(video_path: Path, audio_path: Path, output_path: Path) -> list[str]:
    """画面直通通 copy，只把归好响度的音轨封进 mp4；``-shortest`` 让画面定长度。"""
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


async def _run(executable: str, argv: Sequence[str], *, what: str) -> tuple[int, str]:
    """跑一条 ffmpeg；返回退出码与输出全文（第一遍要靠它读统计）。"""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        raw, _ = await asyncio.wait_for(
            process.communicate(), timeout=_AUDIO_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        terminate_process(process)
        raise MotionAudioError(f"{what}超时") from exc
    text = raw.decode("utf-8", errors="replace") if raw else ""
    code = process.returncode if process.returncode is not None else -1
    if code != 0:
        tail = text[-1200:] if text else "（无输出）"
        raise MotionAudioError(f"{what}失败（退出码 {code}）：{tail}")
    return code, text


async def normalize_loudness(input_path: Path, output_path: Path) -> LoudnessReport:
    """两遍 loudnorm：把 ``input_path`` 归一到 -14 LUFS / -1 dBTP，写出 ``output_path``。"""
    executable = require_ffmpeg("声音响度归一")
    measure_argv = measure_command(input_path)
    measure_argv[0] = executable
    _, measured_text = await _run(executable, measure_argv, what="响度测量")
    measured = parse_loudnorm_report(measured_text)

    apply_argv = normalize_command(input_path, output_path, measured)
    apply_argv[0] = executable
    await _run(executable, apply_argv, what="响度归一")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise MotionAudioError("响度归一说成功了，但没有产出文件")
    return report_from(measured)


async def mux_into_video(video_path: Path, audio_path: Path) -> Path:
    """把音轨合进成片并原地替换；任何一步失败都不碰原片。"""
    executable = require_ffmpeg("合轨")
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{video_path.stem}_mux_", suffix=".mp4", dir=str(video_path.parent)
    )
    os.close(handle)
    temp_path = Path(temp_name)
    argv = mux_command(video_path, audio_path, temp_path)
    argv[0] = executable
    try:
        await _run(executable, argv, what="合轨")
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise MotionAudioError("合轨说成功了，但没有产出文件")
        os.replace(temp_path, video_path)
    finally:
        if temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()
    logger.info("[motion-audio] 配乐已合进成片 %s", video_path.name)
    return video_path
