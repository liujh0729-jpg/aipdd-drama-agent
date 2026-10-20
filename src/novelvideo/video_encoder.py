# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本机 ffmpeg 的 H.264 编码器解析：所有出片链路共用这一处。

写死 ``libx264`` 的命令行在桌面装机版会直接失败：随包的 ffmpeg 是 BtbN 的 **LGPL**
构建，出于许可证以 ``--disable-libx264`` 编译，实测同一个二进制 ``-c:v libx264``
退出码 8（``Encoder not found``），而 ``h264_mf`` / ``libopenh264`` 退出码 0。所以
先问本机 ``-encoders``，再按画质优先取第一个可用的 H.264 实现 —— 交给剪辑软件和
平台的仍然必须是 H.264，AV1/VP9 的兼容性不够。

两条纪律：

* **有 x264 时命令行一字不改。** 容器/镜像自带 x264，各链路原本的 ``-preset`` /
  ``-crf`` 参数继续生效，收口前后逐字节相同，避免把主线出片的质量顺手改掉。
* **没有 x264 时只换编码器，并给一条码率。** ``-preset``/``-crf`` 是 x264 的私有
  选项，别的编码器会打「Codec AVOption ... has not been used」并退回自己的默认
  码控，质量不可控；所以这里显式补 ``-b:v``。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

# 候选按画质优先：x264 走 CRF，其余只能按码率。
# 不列 nvenc/qsv 这类硬件编码器：装不装机、驱动新旧都会变，出片要的是可复现。
H264_CANDIDATES: tuple[tuple[str, bool], ...] = (
    ("libx264", True),
    ("h264_videotoolbox", False),
    ("h264_mf", False),
    ("libopenh264", False),
)

# ``VIDEO_CODEC`` 只允许落在这个集合里（探测到才算数）。
_H264_FAMILY: frozenset[str] = frozenset(name for name, _ in H264_CANDIDATES)

# 探测超时：正常是几十毫秒，卡住就是二进制坏了。
_PROBE_TIMEOUT_SECONDS = 20.0
# 码率模式下的兜底宽度：拿不到 VIDEO_BITRATE 或它不是可解析的值时用这个。
_FALLBACK_BITRATE = "4M"


class NoH264Encoder(RuntimeError):
    """本机 ffmpeg 一个可用的 H.264 编码器都没有。"""


_mismatch_warned: set[str] = set()
_off_family_warned: set[str] = set()


def ffmpeg_executable() -> Optional[str]:
    """定位 ffmpeg：先认 ``FFMPEG_PATH``（``config.py`` 与 ffmpeg 指南的仓库口径），再退 PATH。

    指了却指不到就不声不响地换一条：本机编码器清单是按这个二进制 ``-encoders`` 探的，
    悄悄换成另一个 ffmpeg 会让「探到的编码器」和「真正跑的编码器」不是同一回事。
    """
    configured = str(os.environ.get("FFMPEG_PATH") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        resolved = str(candidate) if candidate.is_file() else shutil.which(configured)
        if resolved:
            _warn_if_not_the_path_binary(resolved)
            return resolved
        logger.warning("[ffmpeg] FFMPEG_PATH=%s 既不是文件也不在 PATH 上", configured)
        return None
    return shutil.which("ffmpeg")


def _warn_if_not_the_path_binary(resolved: str) -> None:
    """``FFMPEG_PATH`` 指到 PATH 之外的二进制时提醒一次。

    老链路的 argv[0] 是裸 ``ffmpeg``（只认 PATH），而编码器是按 ``resolved`` 这台探的：
    两台不一致时，那些链路会跑成「探到的清单」里可能并不存在的编码器。桌面版把
    ``resources/bin`` 前置进 PATH，两边是同一台，所以不会走到这里。
    """
    on_path = shutil.which("ffmpeg")
    if not on_path:
        return
    try:
        same = Path(on_path).resolve() == Path(resolved).resolve()
    except OSError:
        return
    if not same and resolved not in _mismatch_warned:
        _mismatch_warned.add(resolved)
        logger.warning(
            "[ffmpeg] FFMPEG_PATH=%s 与 PATH 上的 %s 不是同一个二进制：编码器清单按前者探测，"
            "老链路（argv 里写裸 ffmpeg）按后者执行。要给全系统换一条 ffmpeg，请把它放到 PATH 最前面。",
            resolved,
            on_path,
        )


def parse_encoder_list(output: str) -> set[str]:
    """从 ``ffmpeg -encoders`` 的输出里取出编码器名集合。

    表格行形如 `` V....D libopenh264   OpenH264 ...``：第一个字段固定 6 个标志位，
    第二个才是编码器名；``Encoders:`` / ``Video codecs:`` 这类表头不满足 6 字符就跳过。
    """
    names: set[str] = set()
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or len(parts[0]) != 6 or parts[0][0] not in "VA":
            continue
        names.add(parts[1])
    return names


@lru_cache(maxsize=8)
def _probe(executable: Optional[str]) -> frozenset[str]:
    """问一次本机 ``-encoders``；每个二进制只探一次（清单在进程生命周期内不会变）。

    探不到就当没有 x264 之外的信息：返回空集，调用方据此退回 ``libx264`` 原写法 ——
    那正是收口前每条链路的行为，本机连 ffmpeg 都没有时不该多一类新错误。
    """
    binary = executable or ffmpeg_executable()
    if not binary:
        return frozenset()
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-encoders"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[ffmpeg] 探测编码器清单失败（%s）：按原写法交给 ffmpeg 自己判", exc)
        return frozenset()
    if result.returncode != 0:
        logger.warning(
            "[ffmpeg] 探测编码器清单退出码 %s：按原写法交给 ffmpeg 自己判", result.returncode
        )
        return frozenset()
    return frozenset(parse_encoder_list(result.stdout.decode("utf-8", errors="replace")))


def available_encoders(*, executable: Optional[str] = None) -> frozenset[str]:
    """本机可用的编码器名集合；探不到返回空集。"""
    return _probe(executable)


def pick_h264_encoder(*, executable: Optional[str] = None) -> tuple[str, bool]:
    """挑一个本机可用的 H.264 编码器，返回 ``(名字, 是否支持 CRF)``。

    ``VIDEO_CODEC`` 指到某个具体实现时优先用它（前提是本机真有），这样这个变量对
    所有出片链路都算数，而不只对着整集合成那一处生效。但它只允许在 H.264 家族里
    选：交付口径（剪映草稿、平台上传）要求成片是 H.264，指到 ``mpeg4``/``libvpx``
    这类本机真有的编码器时不能用它出片，否则一条 ``-c:v mpeg4`` 就悄悄交付了
    剪辑软件不认的容器流。
    """
    have = available_encoders(executable=executable)
    if not have:
        return "libx264", True  # 探不到就别改行为：让 ffmpeg 自己按老写法报错或成功
    ladder: Optional[tuple[str, bool]] = next(
        ((name, rateless) for name, rateless in H264_CANDIDATES if name in have), None
    )
    if ladder is None:
        raise NoH264Encoder(
            "本机 ffmpeg 没有可用的 H.264 编码器（试过的候选："
            + "、".join(name for name, _ in H264_CANDIDATES)
            + "）。随包的 LGPL 构建不含 x264，请提供带 h264_mf / libopenh264 的 ffmpeg。"
        )
    configured = str(os.environ.get("VIDEO_CODEC") or "").strip()
    if configured in have:
        if configured in _H264_FAMILY:
            return configured, configured == "libx264"
        if configured not in _off_family_warned:
            _off_family_warned.add(configured)
            logger.warning(
                "[ffmpeg] VIDEO_CODEC=%s 本机确实有它，但它不是 H.264 编码器：交付给剪辑软件/平台"
                "的成片必须是 H.264，这里改用 %s（要用它请自行改链路）",
                configured,
                ladder[0],
            )
    return ladder


def video_encode_args(
    *,
    preset: str = "veryfast",
    crf: Optional[str] = None,
    bitrate: Optional[str] = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """给出 ``-c:v <编码器>`` 及其配套参数，供各链路直接铺进 argv。

    有 x264 时原样返回调用方给的 ``-preset`` / ``-crf``（收口前后命令行一字不改）；
    换成别的编码器就把这两个私有选项摘掉、补一条 ``-b:v``，否则会落到编码器自己的
    默认码率上。
    """
    encoder, rateless = pick_h264_encoder()
    args = ["-c:v", encoder]
    if rateless:
        if preset:
            args += ["-preset", preset]
        if crf:
            args += ["-crf", crf]
    else:
        args += ["-b:v", bitrate or _configured_bitrate()]
    args += list(extra)
    return args


def _configured_bitrate() -> str:
    from novelvideo.config import VIDEO_BITRATE  # 局部 import：config 里读环境变量，避免环

    value = str(VIDEO_BITRATE or "").strip()
    return value or _FALLBACK_BITRATE


def h264_encoder_for(
    available: Iterable[str],
    *,
    width: int,
    height: int,
    fps: int,
    bits_per_pixel: float,
    min_bitrate: int = 2_000_000,
    max_bitrate: int = 12_000_000,
) -> tuple[str, list[str]]:
    """按候选顺序挑编码器，并按像素数算出码率（逐帧渲染这类「素材自己生成」的链路用）。

    码率上下限由调用方给：那是各条链路自己的画质预算，不该写死在解析器里。
    """
    have = set(available)
    for name, rateless in H264_CANDIDATES:
        if name not in have:
            continue
        if rateless:
            return name, ["-preset", "veryfast", "-crf", "18"]
        raw = int(width) * int(height) * int(fps) * bits_per_pixel
        bitrate = int(max(min_bitrate, min(max_bitrate, raw)))
        return name, ["-b:v", str(bitrate)]
    raise NoH264Encoder(
        "本机 ffmpeg 没有可用的 H.264 编码器（试过的候选："
        + "、".join(name for name, _ in H264_CANDIDATES)
        + "）。随包的 LGPL 构建不含 x264，请提供带 h264_mf / libopenh264 的 ffmpeg。"
    )


def reset_probe_cache() -> None:
    """丢掉编码器探测缓存（换二进制或测试用）。"""
    _probe.cache_clear()
