# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：一页 MG 脚本 → 一条 MP4 的总装。

三步串起来：起 Chrome（`chrome`）→ 把页面时间轴钉住逐帧截图（`capturer`）→
帧流边截边喂 ffmpeg 编码（`encoder`）。这一层只做**取舍与换算**：页面自报的
时长与我们要的长度不一致时怎么处理、帧数怎么落到整帧、产出多大。

时长口径：页面 `window.DUR` 是权威，调用方给的 `duration_seconds` 只是上限。
要得更短就截断（分镜时长比片子短是常态），要得更长就以页面为准 —— 换算细节都
留在 `FramePlan` 里由上层判断。宁可出一段稍短的片子，也不靠重复最后一帧把时间
硬凑出来，那是肉眼可见的卡顿。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from novelvideo.freezone.motion_render.capturer import (
    MotionPage,
    MotionRenderError,
    with_bootstrap,
)
from novelvideo.freezone.motion_render.chrome import (
    CDP_URL_ENV,
    chrome_endpoint,
    chrome_executable,
)
from novelvideo.freezone.motion_render.encoder import encode_frames, ffmpeg_executable

logger = logging.getLogger(__name__)

DEFAULT_FPS = 30
MAX_FPS = 60
# 单次渲染的帧数上限：60fps 下约 5 分钟，再长就该是分镜拆条而不是单条 MG。
MAX_FRAMES = 18_000
# 渲染契约第 4 条「先出静帧再出成片」的默认张数上限：拍点可以很密（120 BPM 一分钟
# 两张），一次预检只看得住二十来张，再多就退化成"翻不完的相册"而不是检查表。
DEFAULT_MAX_STILLS = 24
# 两个拍点近到这张程度就当作同一帧：截出来逐字节相同，白占一次截图与一个格子。
_MIN_STILL_GAP_SECONDS = 0.02


@dataclass(frozen=True)
class FramePlan:
    """一次渲染的帧数与时长换算结果。

    两个时长留成字段而不是塞一个布尔标记：``duration_seconds`` 短于
    ``page_duration_seconds`` 就是被请求截断了，短于调用方给的上限就是页面本身
    没那么长 —— 上层两个减法都能算，不必在这里替它预设立场。
    """

    frames: int
    fps: int
    duration_seconds: float
    page_duration_seconds: float


@dataclass(frozen=True)
class MotionRenderResult:
    path: Path
    width: int
    height: int
    plan: FramePlan
    events: tuple[dict, ...]


def clamp_fps(value: object) -> int:
    try:
        fps = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FPS
    return max(1, min(MAX_FPS, fps))


def clamp_max_stills(value: object) -> int:
    """静帧预检的张数上限：给坏了就回默认档，最多 120 张（一屏一屏翻就没意义了）。"""
    try:
        parsed = int(str(value if value not in (None, "") else "").strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_STILLS
    return max(1, min(120, parsed))


def plan_frames(*, page_duration: float, fps: int, requested: Optional[float]) -> FramePlan:
    """把「页面时长 + 请求时长」换成整帧数。

    帧数取 `round`（不是 floor）：29.0s×30fps=870 帧，而 0.9999s×30fps 也该给 30 帧，
    不然末尾会少一帧导致片长短一截。
    """
    effective = float(page_duration)
    if requested is not None and requested > 0:
        effective = min(effective, float(requested))
    frames = max(1, int(round(effective * fps)))
    if frames > MAX_FRAMES:
        raise MotionRenderError(
            f"这次要渲 {frames} 帧（上限 {MAX_FRAMES}）：请缩短时长或降低帧率，"
            "或把长片拆成分镜逐条生成"
        )
    return FramePlan(
        frames=frames,
        fps=fps,
        duration_seconds=frames / float(fps),
        page_duration_seconds=float(page_duration),
    )


def page_source_path(page_path: Path) -> Path:
    """渲染用页面旁边那份**原始源码**的落盘位置（同名 `.source.html`）。

    Chrome 加载的那份带引导脚本（把 rAF 与两个时钟钉死），拿去改再重渲不该连着这份
    一起改 —— 引导脚本是我们注入的，不是页面的一部分。所以两份分开落：
    ``page.html`` 用来复现某一帧，``page.source.html`` 用来编辑。
    """
    return page_path.with_suffix(".source.html")


def _write_page(html: str, page_path: Path) -> Path:
    page_path.parent.mkdir(parents=True, exist_ok=True)
    # 原始源码先落：引导脚本注入失败也不至于让「这一页到底写了什么」无处可查。
    page_source_path(page_path).write_text(html, encoding="utf-8")
    page_path.write_text(with_bootstrap(html), encoding="utf-8")
    return page_path


async def render_motion_video(
    html: str,
    out_path: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: object = DEFAULT_FPS,
    duration_seconds: Optional[float] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> MotionRenderResult:
    """把一页 MG 脚本渲成 ``out_path``（mp4）并回报换算细节。

    ``html`` 是模型按渲染契约产出的**单文件**页面（自带 `window.render/DUR`）；
    渲染引导脚本由本模块注入，调用方不必自己拼。页面落在 ``out_path`` 同名 .html
    里，出片后留着 —— 排查「为什么这一帧是这个样子」时，能直接用浏览器打开它复现。
    同名还多一份 .source.html，是不带引导脚本的原始源码，给「改完再渲一遍」当起点。
    """
    if not str(html or "").strip():
        raise MotionRenderError("MG 页面内容为空")
    target = Path(out_path)
    resolved_fps = clamp_fps(fps)
    page_path = _write_page(str(html), target.with_suffix(".html"))

    async with chrome_endpoint(window_size=(width, height)) as cdp_url:
        page = await MotionPage.open(
            cdp_url, page_path.resolve().as_uri(), width=width, height=height
        )
        try:
            plan = plan_frames(
                page_duration=page.timing.duration_seconds,
                fps=resolved_fps,
                requested=duration_seconds,
            )
            await encode_frames(
                page.iter_frames(plan.frames, fps=plan.fps, on_progress=on_progress),
                fps=plan.fps,
                width=width,
                height=height,
                out_path=target,
            )
        finally:
            await page.close()

    logger.info(
        "[motion-render] 成片 %s：%.2fs / %d 帧 / %dfps",
        target.name,
        plan.duration_seconds,
        plan.frames,
        plan.fps,
    )
    return MotionRenderResult(
        path=target,
        width=width,
        height=height,
        plan=plan,
        events=tuple(dict(item) for item in page.timing.events),
    )


@dataclass(frozen=True)
class MotionStill:
    """预检用的一张静帧：落在哪一秒、是什么拍点、文件在哪。"""

    path: Path
    at_seconds: float
    event_type: str


@dataclass(frozen=True)
class MotionStillsResult:
    stills: tuple[MotionStill, ...]
    page_duration_seconds: float
    events: tuple[dict, ...]
    # 页面没写 window.EV 时按等间隔网格出图：这既不是失败也不该静默，
    # 上层要能告诉用户"这张图对的是网格，不是页面自报的拍点"。
    beat_grid: bool


def still_times(
    events: list[dict],
    *,
    duration_seconds: float,
    max_stills: int = DEFAULT_MAX_STILLS,
) -> list[dict]:
    """把页面自报的拍点换成「该截哪几秒」的有序列表（渲染契约第 4 条）。

    四条口径都在这里定死，预检与任务只认这一份换算：

    - 只截落在 ``[0, DUR]`` 内的拍点（契约第 2 条：超出 DUR 的一帧都不生成）；
    - 开场那张一定要截（``0.0``）——M43 那类片子要求最后一帧回到第一帧，
      开场构图不看就检不出来；
    - 相距小于 :data:`_MIN_STILL_GAP_SECONDS` 的拍点算同一帧，截第二张纯属浪费；
    - 拍点比 ``max_stills`` 还密时等间隔抽样并**保住最后一张**（收尾是循环片的
      检查点），而不是砍掉尾部。
    """
    duration = max(0.0, float(duration_seconds))
    limit = clamp_max_stills(max_stills)
    picked: list[dict] = [{"t": 0.0, "type": "open"}]
    seen: list[float] = [0.0]
    for item in sorted(
        (entry for entry in events if isinstance(entry, dict)),
        key=lambda entry: float(entry.get("t") or 0.0),
    ):
        at = float(item.get("t") or 0.0)
        if at < 0.0 or at > duration:
            continue
        if any(abs(at - done) < _MIN_STILL_GAP_SECONDS for done in seen):
            continue
        seen.append(at)
        picked.append({"t": at, "type": str(item.get("type") or "")})
    if len(picked) > limit:
        if limit <= 1:
            # 只留一张时没有"等间隔抽样"可言（分母是 limit-1），而开场那张是硬口径，
            # 所以收口成第一帧，而不是回退去保尾帧。
            return picked[:1]
        step = (len(picked) - 1) / float(limit - 1)
        sampled = [picked[int(round(index * step))] for index in range(limit)]
        if sampled[-1]["t"] != picked[-1]["t"]:
            sampled[-1] = picked[-1]
        picked = sampled
    return picked


def _grid_times(duration_seconds: float, max_stills: object) -> list[dict]:
    """页面没给拍点时的等间隔网格（含 0 与 DUR 之前最后一格）。"""
    limit = clamp_max_stills(max_stills)
    count = max(2, min(limit, int(math.ceil(duration_seconds))))
    step = duration_seconds / float(count)
    return [
        {"t": round(index * step, 6), "type": "grid"} for index in range(count)
    ]


async def render_motion_stills(
    html: str,
    out_dir: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    max_stills: object = DEFAULT_MAX_STILLS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> MotionStillsResult:
    """渲染契约第 4 条的执行侧：按拍点各出一张静帧，先检查再精修整片。

    走的是与成片**同一个** Chrome 通道、同一个 :meth:`MotionPage.capture_at`：
    预检看到的就是成片会有的画面，不会出现"静帧好看出片翻车"这种两套实现才有的偏差。
    页面源码同样落在 ``out_dir`` 里，任何一张图都能拿浏览器打开这一页回到那一秒复现。
    """
    if not str(html or "").strip():
        raise MotionRenderError("MG 页面内容为空")
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    page_path = _write_page(str(html), root / "page.html")
    limit = clamp_max_stills(max_stills)

    async with chrome_endpoint(window_size=(width, height)) as cdp_url:
        page = await MotionPage.open(
            cdp_url, page_path.resolve().as_uri(), width=width, height=height
        )
        try:
            duration = page.timing.duration_seconds
            beat_grid = not bool(page.timing.events)
            beats = (
                _grid_times(duration, limit)
                if beat_grid
                else still_times(
                    page.timing.events, duration_seconds=duration, max_stills=limit
                )
            )
            stills: list[MotionStill] = []
            for index, entry in enumerate(beats):
                at = float(entry["t"])
                blob = await page.capture_at(at)
                path = root / f"beat-{index + 1:02d}-{at:.3f}s.png"
                path.write_bytes(blob)
                stills.append(
                    MotionStill(
                        path=path, at_seconds=at, event_type=str(entry.get("type") or "")
                    )
                )
                if on_progress is not None:
                    on_progress(index + 1, len(beats))
            events = tuple(dict(item) for item in page.timing.events)
        finally:
            await page.close()

    logger.info(
        "[motion-render] 静帧预检 %s：%d 张 / 页面 %.2fs / %s",
        root.name,
        len(stills),
        duration,
        "等间隔网格（页面未给拍点）" if beat_grid else "按拍点",
    )
    return MotionStillsResult(
        stills=tuple(stills),
        page_duration_seconds=float(duration),
        events=events,
        beat_grid=beat_grid,
    )


def render_preflight() -> None:
    """提交前检查本机到底渲不渲得动，别把注定失败的任务排进队列。

    两条依赖各有各的缺法：web/容器镜像里通常没装 Chrome（要么自备调试端口
    `ST_CHROME_CDP_URL`），桌面装机版则随包自带；ffmpeg 同理。报错文案直接把
    出路写全，用户不必去翻文档。
    """
    if not chrome_available():
        raise MotionRenderError(
            "本机找不到可用的 Chrome：桌面版请用自带 Chrome 的安装包，"
            "或设 ST_CHROME_PATH 指到浏览器可执行文件、"
            "设 ST_CHROME_CDP_URL 连一个现成的调试端口"
        )
    if not ffmpeg_executable():
        raise MotionRenderError(
            "本机找不到 ffmpeg：桌面版请确认安装包自带 bin/ffmpeg，"
            "web 形态请在镜像里装 ffmpeg 或把它放进 PATH"
        )


def chrome_available() -> bool:
    """本机有没有一条能用的 Chrome 通道（预检的判定收在这一处）。"""
    return bool(
        str(os.environ.get(CDP_URL_ENV) or "").strip() or chrome_executable()
    )


def stills_preflight() -> None:
    """静帧预检只依赖 Chrome：它不编码，所以不该因为缺 ffmpeg 就拒收。

    与 :func:`render_preflight` 共用同一条 Chrome 判定；报错只说 Chrome，
    免得用户为了看二十张图先去装编码器。
    """
    if not chrome_available():
        raise MotionRenderError(
            "本机找不到可用的 Chrome：桌面版请用自带 Chrome 的安装包，"
            "或设 ST_CHROME_PATH 指到浏览器可执行文件、"
            "设 ST_CHROME_CDP_URL 连一个现成的调试端口"
        )
