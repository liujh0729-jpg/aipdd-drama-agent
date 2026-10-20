# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：把逐帧 PNG 流喂给内置 ffmpeg 编码成片。

帧不落地：一条 30 秒 1080p 的片子约 900 张 PNG，写盘再读一遍既慢又要在项目目录里
留一堆临时文件，所以直接把 PNG 字节流进 ffmpeg 的 ``pipe:0``（``-f image2pipe``），
与 ``explainer_motion`` 用 rawvideo 管道同一个理由 —— 攒列表会把内存吃掉几个 GB。

ffmpeg 的定位与编码器选择都住在 :mod:`novelvideo.video_encoder`，这里只保留 MG 的
那一份口径：**码率按像素数算**（大面积纯色 + 锐利文字，0.1 bit/pixel 够压住字边缘），
以及渲染任务的异步探测。

``_probe_cache`` 按二进制缓存探测结果，与共享模块的进程级探测各管各的：前者省掉
MG 每次渲染的一次子进程，后者保证所有出片链路问的是同一个问题。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import AsyncIterator, Iterable, Sequence

from novelvideo.freezone.motion_render.capturer import MotionRenderError
from novelvideo.video_encoder import (
    NoH264Encoder,
    ffmpeg_executable,
    h264_encoder_for as _shared_h264_encoder_for,
    parse_encoder_list,
)

logger = logging.getLogger(__name__)

# ffmpeg 收尾时最多再等多久（编码尾巴很短，超了就是卡死）。
_WAIT_TIMEOUT_SECONDS = 120.0
# 探测本机编码器清单的超时：正常是几十毫秒，卡住就是二进制坏了。
_PROBE_TIMEOUT_SECONDS = 20.0

# MG 用 x264 之外的编码器时只能按码率：0.1 bit/pixel 够压住字边缘。
_BITS_PER_PIXEL = 0.10
_MIN_BITRATE = 2_000_000
_MAX_BITRATE = 12_000_000


def _bitrate_for(width: int, height: int, fps: int) -> int:
    raw = int(width) * int(height) * int(fps) * _BITS_PER_PIXEL
    return int(max(_MIN_BITRATE, min(_MAX_BITRATE, raw)))


def h264_encoder_for(
    available: Iterable[str], *, width: int, height: int, fps: int
) -> tuple[str, list[str]]:
    """MG 口径的编码器挑选：委托共享解析器，把「没有 H.264」翻成渲染错误。"""
    try:
        return _shared_h264_encoder_for(
            available,
            width=width,
            height=height,
            fps=fps,
            bits_per_pixel=_BITS_PER_PIXEL,
            min_bitrate=_MIN_BITRATE,
            max_bitrate=_MAX_BITRATE,
        )
    except NoH264Encoder as exc:
        raise MotionRenderError(str(exc)) from exc


def build_encoder_command(
    *,
    executable: str,
    fps: int,
    out_path: Path,
    width: int,
    height: int,
    encoder: str,
    encoder_args: Sequence[str],
) -> list[str]:
    """PNG 流 → H.264 MP4。

    ``executable`` 必须是 ``ffmpeg_executable()`` 解析出来的那一个：编码器清单是按它
    ``-encoders`` 探的，argv[0] 写成裸 ``ffmpeg`` 会让「探到的编码器」和「真正跑的
    二进制」不是同一台（``FFMPEG_PATH`` 指向 PATH 之外的构建时就是这么裂开的）。

    ``-vf scale=trunc(iw/2)*2:...`` 是为了把宽高抹成偶数：yuv420p 不收奇数尺寸，
    而截图尺寸由调用方给（画布预设都是偶数），这里只是防一手不自家报错。
    """
    return [
        executable,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        encoder,
        *encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(out_path),
    ]


# 每个 ffmpeg 二进制只探测一次编码器：清单在进程生命周期内不会变。
_probe_cache: dict[str, tuple[str, list[str]]] = {}


async def _probe_h264_encoder(*, executable: str, width: int, height: int, fps: int) -> tuple[str, list[str]]:
    cached = _probe_cache.get(executable)
    if cached is not None:
        return cached
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-hide_banner",
            "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        raw, _ = await asyncio.wait_for(process.communicate(), timeout=_PROBE_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError) as exc:
        raise MotionRenderError(f"无法向本机 ffmpeg 询问编码器清单：{exc}") from exc
    chosen = h264_encoder_for(
        parse_encoder_list(raw.decode("utf-8", errors="replace")), width=width, height=height, fps=fps
    )
    _probe_cache[executable] = chosen
    logger.info("[motion-render] 使用 H.264 编码器 %s", chosen[0])
    return chosen


async def encode_frames(
    frames: AsyncIterator[bytes],
    *,
    fps: int,
    out_path: Path,
    width: int,
    height: int,
) -> Path:
    """边收帧边写 ffmpeg 的 stdin；出错时把 ffmpeg 的 stderr 尾巴带进异常。"""
    executable = ffmpeg_executable()
    if not executable:
        raise MotionRenderError(
            "本机没有 ffmpeg：桌面版请确认安装包自带 bin/ffmpeg，"
            "web 形态请在镜像里装 ffmpeg 或把它放进 PATH"
        )
    encoder, encoder_args = await _probe_h264_encoder(
        executable=executable, width=width, height=height, fps=fps
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *build_encoder_command(
            executable=executable,
            fps=fps,
            out_path=out_path,
            width=width,
            height=height,
            encoder=encoder,
            encoder_args=encoder_args,
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # stderr 一边喂帧一边收到缓冲区里：ffmpeg 的报错一旦塞满管道缓冲，它会阻塞在写
    # 上，而我们阻塞在 stdin.drain() 上 —— 两边互等，任务挂着不放 Chrome。
    stderr_pump = asyncio.create_task(_pump_stderr(process))
    written = 0
    try:
        async for payload in frames:
            stdin = process.stdin
            if stdin is None:
                raise MotionRenderError("ffmpeg 没有接上输入管道")
            try:
                stdin.write(payload)
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                # ffmpeg 提前退出（参数/滤镜报错）时管道会断，真实原因在 stderr 里。
                raise MotionRenderError(f"ffmpeg 提前退出：{await _collect_stderr(stderr_pump)}") from exc
            written += 1
        if process.stdin is not None:
            process.stdin.close()
            with contextlib.suppress(Exception):
                await process.stdin.wait_until_closed()
    except BaseException:
        terminate_process(process)
        await _collect_stderr(stderr_pump)
        _discard_partial(out_path)
        raise
    if written == 0:
        terminate_process(process)
        await _collect_stderr(stderr_pump)
        _discard_partial(out_path)
        raise MotionRenderError("没有帧可编码")

    try:
        code = await asyncio.wait_for(process.wait(), timeout=_WAIT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        terminate_process(process)
        stderr_pump.cancel()
        _discard_partial(out_path)
        raise MotionRenderError("ffmpeg 编码超时") from exc
    tail = await _collect_stderr(stderr_pump)
    if code != 0:
        _discard_partial(out_path)
        raise MotionRenderError(f"ffmpeg 退出码 {code}：{tail}")
    if not out_path.is_file() or out_path.stat().st_size <= 0:
        _discard_partial(out_path)
        raise MotionRenderError("ffmpeg 报成功但没有产出文件")
    logger.info("[motion-render] 成片 %s：%d 帧", out_path.name, written)
    return out_path


async def _pump_stderr(process: asyncio.subprocess.Process) -> bytes:
    """把 stderr 读到 EOF（``read()`` 不带长度就是读到关流）。"""
    if process.stderr is None:
        return b""
    try:
        return await process.stderr.read()
    except (OSError, ValueError):
        return b""


async def _collect_stderr(pump: asyncio.Task[bytes]) -> str:
    """取已收到的 stderr 文本；读不出来就回一句占位，别让错误变成空白。"""
    try:
        raw = await asyncio.wait_for(asyncio.shield(pump), timeout=10.0)
    except (asyncio.TimeoutError, asyncio.CancelledError, OSError, ValueError):
        return "（stderr 读取失败）"
    text = (raw or b"").decode("utf-8", errors="replace").strip()
    return text[-1200:] if text else "（stderr 为空）"


def _discard_partial(out_path: Path) -> None:
    """失败/超时时删掉半截成片：留着它，资产扫描会把它当成一条可用成片列进草稿。"""
    with contextlib.suppress(OSError):
        out_path.unlink()



def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass


async def stderr_tail(process: asyncio.subprocess.Process, *, limit: int = 1200) -> str:
    """取 ffmpeg 的 stderr 尾部；读不到就回一句占位，别让错误变成空白。"""
    if process.stderr is None:
        return "（无 stderr）"
    try:
        raw = await asyncio.wait_for(process.stderr.read(limit), timeout=10.0)
    except (asyncio.TimeoutError, OSError, ValueError):
        return "（stderr 读取失败）"
    text = raw.decode("utf-8", errors="replace").strip()
    return text[-limit:] if text else "（stderr 为空）"
