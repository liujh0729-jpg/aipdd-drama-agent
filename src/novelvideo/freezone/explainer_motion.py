# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 科普讲解视频的 MG 渲染核心（纯本地、不出网）。

把策划层（``explainer.py``）算好的时间轴逐帧画成一条横屏 MG 动画并编码成片：
黑底 → 幕底（点阵波 / 星点）→ 镜头图元（8 种）→ 顶部胶囊 HUD → 底部章节进度条 →
字幕带 → 片头卡与章节卡。每一帧都是代码画的（Pillow + numpy），**不依赖浏览器、
不依赖任何前端渲染框架、不需要额外安装**。

节奏规则做进代码而不是留给事后人工复检：元素随字幕块入场、动词（逐格点亮 /
光点沿线跑 / 打字机 / 数字滚动）持续到下一拍、末拍落位后停满稳定期再整体淡出；
没有其它动作的镜头自动补一个 1.0→1.05 的慢推，避免整段静止。

设计约束：纯本地 Pillow/numpy + ffmpeg ``rawvideo`` **流式**管道——帧不落盘、
不累积成列表（一条 3 分钟的 720p 片若把帧全部留在内存里约 24 GB），编码与
配音/BGM 混音在一次 ffmpeg 调用里完成。``compose_explainer_frame`` 是无副作用的
纯函数（返回 RGB ndarray），可被单测逐帧断言；``pytest`` 用例不依赖真实模型。
"""

from __future__ import annotations

import bisect
import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from novelvideo.freezone.explainer import (
    ENTRY_FRAMES,
    EXIT_FRAMES,
    MIN_SHOT_FRAMES,
    MIN_STEADY_FRAMES,
    ExplainerCue,
    ExplainerScene,
    ExplainerShot,
    ExplainerTimeline,
)
from novelvideo.freezone.whiteboard import resolve_cjk_font_path
from novelvideo.video_encoder import video_encode_args

# --------------------------------------------------------------------------- #
# 设计坐标（1280×720）与安全区：所有几何先按设计坐标写，再统一缩放到画布。
# --------------------------------------------------------------------------- #

DESIGN_WIDTH = 1280
DESIGN_HEIGHT = 720
CONTENT_LEFT = 72
CONTENT_RIGHT = 1208
CONTENT_TOP = 128
CONTENT_BOTTOM = 604
SUBTITLE_CENTER_Y = 662
PROGRESS_TOP = 688
PROGRESS_BOTTOM = 720
HUD_CENTER_X = 640
HUD_TOP = 26
HUD_HEIGHT = 50

# 片头卡与章节卡的占位帧数（配音从第 0 帧就开始，所以卡片是覆盖层而不是空镜头）。
TITLE_FRAMES = 60
CHAPTER_CARD_FRAMES = 54
# 收尾压黑的帧数。
TAIL_FADE_FRAMES = 20

# 调色板：黑底 + 白线条 + 紫重点（讲解片的视觉主线，配色自定）。
COLOR_BG = (0, 0, 0)
COLOR_INK = (255, 255, 255)
COLOR_DIM = (156, 158, 168)
COLOR_PURPLE = (122, 74, 245)
COLOR_PURPLE_LIGHT = (168, 132, 250)
COLOR_WARM = (242, 118, 82)
COLOR_DOT = (206, 224, 255)

_TAU = math.tau


def _rgba(color: tuple[int, int, int], alpha: float) -> tuple[int, int, int, int]:
    """颜色 + 不透明度 -> RGBA 元组（alpha 取 0-1）。"""
    return (color[0], color[1], color[2], int(max(0.0, min(1.0, alpha)) * 255))


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


def ease_out_cubic(value: float) -> float:
    t = clamp01(value)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(value: float) -> float:
    t = clamp01(value)
    return 3.0 * t * t - 2.0 * t * t * t


@lru_cache(maxsize=256)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """字号 -> 字体对象（进程内缓存）。无可用 CJK 字体时退 Pillow 默认位图字体。"""
    resolved = max(10, int(size))
    path = resolve_cjk_font_path()
    if path:
        try:
            return ImageFont.truetype(path, resolved)
        except OSError:  # pragma: no cover - 字体损坏兜底
            pass
    return ImageFont.load_default()


@dataclass(frozen=True, slots=True)
class _Geom:
    """设计坐标 -> 画布坐标（含运镜缩放）。

    运镜用「坐标缩放」而不是整层 resize：HUD / 字幕 / 进度条必须不随运镜动，
    只有内容层跟着推近——缩放坐标做得到，整幅 resize 会把覆盖层一起拖动。
    """

    width: int
    height: int
    zoom: float = 1.0

    @property
    def scale(self) -> float:
        return self.width / float(DESIGN_WIDTH)

    @property
    def _anchor_x(self) -> float:
        return self.width * 0.5

    @property
    def _anchor_y(self) -> float:
        return self.height * 0.44

    def x(self, value: float) -> float:
        return self._anchor_x + (value * self.scale - self._anchor_x) * self.zoom

    def y(self, value: float) -> float:
        return self._anchor_y + (value * self.scale - self._anchor_y) * self.zoom

    def size(self, value: float) -> float:
        return value * self.scale * self.zoom

    def pt(self, x_value: float, y_value: float) -> tuple[float, float]:
        return (self.x(x_value), self.y(y_value))

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (
            int(round(self.x(x0))),
            int(round(self.y(y0))),
            int(round(self.x(x1))),
            int(round(self.y(y1))),
        )

    def fs(self, value: float) -> int:
        return max(9, int(round(value * self.scale * self.zoom)))


# --------------------------------------------------------------------------- #
# 幕底：点阵波（默认）与星点，都是帧号的确定性纯函数
# --------------------------------------------------------------------------- #

_DOT_SPACING = 46.0
_DOT_WAVE_PERIOD_SECONDS = 8.5
_STAR_COUNT = 76
_STAR_SEED = 20261005


@lru_cache(maxsize=4)
def _star_field(seed: int) -> tuple[tuple[float, float, float, float, float], ...]:
    """星点参数 (x, y, 半径, 漂移 px/帧, 闪烁相位)：固定种子，逐帧可复现。"""
    rng = np.random.default_rng(seed)
    grid = rng.random((_STAR_COUNT, 5))
    return tuple(
        (
            float(row[0]),
            float(row[1]),
            float(1.2 + 2.0 * row[2]),
            float(0.35 + 1.8 * row[3]),
            float(_TAU * row[4]),
        )
        for row in grid
    )


def draw_backdrop(
    layer: ImageDraw.ImageDraw,
    geom: _Geom,
    frame: int,
    fps: int,
    background: str,
) -> None:
    """画幕底：点阵波是斜向推进的波前，星点是缓慢漂移 + 逐帧闪烁。"""
    if background == "stars":
        span = float(PROGRESS_TOP - 20)
        for x_ratio, y_ratio, radius, drift, phase in _star_field(_STAR_SEED):
            design_x = (x_ratio * DESIGN_WIDTH + drift * frame) % (DESIGN_WIDTH + 40.0) - 20.0
            design_y = y_ratio * span + 10.0
            twinkle = 0.40 + 0.30 * math.sin(frame / max(1, fps) * 2.0 + phase)
            size = geom.size(radius)
            center_x, center_y = geom.pt(design_x, design_y)
            layer.ellipse(
                (center_x - size, center_y - size, center_x + size, center_y + size),
                fill=_rgba(COLOR_DOT, twinkle),
            )
        return
    step = frame / float(max(1, fps)) / _DOT_WAVE_PERIOD_SECONDS
    for design_y in range(0, int(PROGRESS_TOP), int(_DOT_SPACING)):
        for design_x in range(0, DESIGN_WIDTH, int(_DOT_SPACING)):
            wave = 0.5 + 0.5 * math.sin(
                _TAU * (step - (design_x * 0.0022 + design_y * 0.00128))
            )
            edge = 1.0 - 0.55 * (
                ((design_x - 640.0) / 640.0) ** 2 + ((design_y - 330.0) / 420.0) ** 2
            )
            alpha = clamp01((0.10 + 0.46 * wave * wave) * max(0.15, edge))
            radius = geom.size(1.25 + 1.05 * wave)
            center_x, center_y = geom.pt(design_x, design_y)
            layer.ellipse(
                (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                fill=_rgba(COLOR_DOT, alpha),
            )


# --------------------------------------------------------------------------- #
# 文本与图形小工具（画进独立 RGBA 图层，靠 alpha 合成实现淡入淡出）
# --------------------------------------------------------------------------- #


def _measure(draw: ImageDraw.ImageDraw, text: str, size: int) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=_font(size))


def draw_text(
    layer: ImageDraw.ImageDraw,
    geom: _Geom,
    text: str,
    *,
    design_x: float,
    design_y: float,
    size: float,
    fill: tuple[int, int, int] = COLOR_INK,
    anchor: str = "mm",
    stroke: float = 0.0,
    stroke_fill: tuple[int, int, int] = COLOR_BG,
    alpha: float = 1.0,
) -> None:
    """在画布坐标里画一行文字（讲解片字幕一律带黑描边，保证压在任何幕底都可读）。"""
    clean = str(text or "")
    if not clean:
        return
    pixel_size = geom.fs(size)
    stroke_px = int(round(geom.size(stroke))) if stroke > 0 else 0
    layer.text(
        (geom.x(design_x), geom.y(design_y)),
        clean,
        font=_font(pixel_size),
        fill=_rgba(fill, alpha),
        anchor=anchor,
        stroke_width=stroke_px,
        stroke_fill=_rgba(stroke_fill, alpha) if stroke_px else None,
    )


def draw_squeezed_text(
    layer: Image,
    geom: _Geom,
    text: str,
    *,
    design_x: float,
    design_y: float,
    size: float,
    fill: tuple[int, int, int] = COLOR_INK,
    squeeze: float = 0.88,
    alpha: float = 1.0,
    glow: tuple[int, int, int] | None = None,
) -> None:
    """压窄大字（超粗黑体横向压 0.85-0.9 是讲解片的标题观感），可选紫色柔光。

    走「临时小图 + resize」而不是直接画：Pillow 没有水平缩放字体的 API，而对整幅
    画布做 resize 会把 HUD/字幕一起推走。临时图只包住这一行字，成本恒定。
    """
    clean = str(text or "")
    if not clean:
        return
    scratch = ImageDraw.Draw(layer)
    pixel_size = geom.fs(size)
    left, top, right, bottom = _measure(scratch, clean, pixel_size)
    pad = max(4, int(pixel_size * 0.35))
    temp = Image.new("RGBA", (max(1, right - left) + pad * 2, max(1, bottom - top) + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(temp).text((pad - left, pad - top), clean, font=_font(pixel_size), fill=_rgba(fill, alpha))
    if squeeze < 1.0:
        target_width = max(1, int(round(temp.width * clamp01(squeeze))))
        if target_width != temp.width:
            temp = temp.resize((target_width, temp.height), Image.BICUBIC)
    center_x = geom.x(design_x)
    center_y = geom.y(design_y)
    if glow is not None:
        halo = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).text(
            (center_x - temp.width / 2.0 + 3.0, center_y - temp.height / 2.0 + 3.0),
            clean,
            font=_font(pixel_size),
            fill=_rgba(glow, 0.9 * alpha),
        )
        halo = halo.filter(ImageFilter.GaussianBlur(radius=max(3.0, geom.size(13.0))))
        layer.alpha_composite(halo)
    layer.alpha_composite(
        temp,
        (int(round(center_x - temp.width / 2.0)), int(round(center_y - temp.height / 2.0))),
    )


def _rounded_box(
    layer: ImageDraw.ImageDraw,
    geom: _Geom,
    box: tuple[float, float, float, float],
    *,
    fill: tuple[int, int, int] | None = None,
    outline: tuple[int, int, int] = COLOR_INK,
    stroke_ratio: float = 2.6,
    radius_ratio: float = 0.2,
    alpha: float = 1.0,
) -> None:
    """黑填充 + 白描边的卡片框（讲解片图元的基本单元）。"""
    x0, y0, x1, y1 = geom.box(*box)
    radius = max(2.0, (y1 - y0) * radius_ratio)
    layer.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        fill=_rgba(fill, alpha) if fill is not None else None,
        outline=_rgba(outline, alpha),
        width=max(1, int(round(geom.size(stroke_ratio)))),
    )


def _apply_alpha(layer: Image.Image, factor: float) -> None:
    """整层淡入淡出：256 项 LUT 走 C 循环（逐像素回调在这个帧量级会拖垮渲染）。"""
    clean = clamp01(factor)
    if clean >= 0.999:
        return
    table = [int(round(value * clean)) for value in range(256)]
    layer.putalpha(layer.getchannel("A").point(table))


def _shown_count(total: int, action: float) -> int:
    """持续动作下「已点亮的元素数」（至少 1，避免整屏空等）。"""
    if total <= 0:
        return 0
    return max(1, min(total, int(math.ceil(clamp01(action) * total))))


def _typewriter(items: tuple[str, ...] | list[str], action: float) -> list[str]:
    """整段文字按动作进度逐字吐出（跨元素连续计数，视觉上是一次成文）。"""
    total_chars = sum(len(item) for item in items)
    if total_chars <= 0:
        return list(items)
    remaining = int(round(clamp01(action) * total_chars))
    out: list[str] = []
    for item in items:
        if remaining <= 0:
            out.append("")
            continue
        take = min(len(item), remaining)
        out.append(item[:take])
        remaining -= take
    return out


def _leading_number(text: str) -> float | None:
    """从「0.05元」「七成」这类字符串里取首个数值；取不到返回 None。"""
    matched = _NUMBER_RE.search(str(text or ""))
    if not matched:
        return None
    try:
        return float(matched.group())
    except ValueError:  # pragma: no cover - 正则已保证形状
        return None


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _count_text(value: str, progress: float) -> str:
    """数字滞轮：按进度从 0 数到原值，小数位数跟原文一致，前后缀（「约」「元」）保留。"""
    clean = str(value or "")
    matched = _NUMBER_RE.search(clean)
    if matched is None:
        return clean
    try:
        target = float(matched.group())
    except ValueError:  # pragma: no cover - 正则已保证形状
        return clean
    literal = matched.group()
    decimals = len(literal.split(".")[1]) if "." in literal else 0
    current = target * ease_out_cubic(progress)
    rendered = f"{current:.{decimals}f}" if decimals else str(int(round(current)))
    return f"{clean[: matched.start()]}{rendered}{clean[matched.end() :]}"



# --------------------------------------------------------------------------- #
# 图元绘制：8 种受限词汇，每个都是一个「画面单元」的画法
# --------------------------------------------------------------------------- #

BODY_MID_Y = 406.0
_BODY_TOP = 212.0
_BODY_BOTTOM = 600.0
_HEADLINE_Y = 150.0
_AVAILABLE = CONTENT_RIGHT - CONTENT_LEFT
_CARD_GAP = 22.0

# 只有这些动词才真的做「逐格点亮 / 持续跑动」；softin 是一次性浮现。
_REVEAL_MOTIONS = frozenset({"lightup", "travel", "typewriter", "countup"})


@dataclass(slots=True)
class _Canvas:
    """一次绘制用到的三件套：RGBA 图层 + 画笔 + 坐标变换。"""

    layer: Image.Image
    draw: ImageDraw.ImageDraw
    geom: _Geom


def _reveal(action: float, motion: str) -> float:
    return clamp01(action) if motion in _REVEAL_MOTIONS else 1.0


def _label_size(text_length: int, box_width: float) -> float:
    """按卡片宽度反推字号：6 卡横排时 10 字要点也不至于溢出或小到看不清。"""
    if text_length <= 0:
        return 24.0
    return max(17.0, min(26.0, box_width / text_length * 1.02))


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    pixel_size: int,
    max_width_px: float,
    *,
    max_lines: int = 2,
) -> list[str]:
    """按像素宽度逐字折行（CJK 无空格，不能按词切），最多 ``max_lines`` 行。"""
    clean = str(text or "")
    if not clean:
        return []
    font = _font(pixel_size)
    lines: list[str] = []
    current = ""
    for char in clean:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width_px:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    else:
        if current:
            lines.append(current)
    if len(lines) > max_lines:  # pragma: no cover - for-else 已限幅
        lines = lines[:max_lines]
    return lines


def _label(
    canvas: _Canvas,
    text: str,
    *,
    center_x: float,
    center_y: float,
    box_width: float,
    size: float | None = None,
    color: tuple[int, int, int] = COLOR_INK,
    alpha: float = 1.0,
) -> None:
    """在卡片/行内居中排版一段要点文字（可两行，行高 1.2）。"""
    clean = str(text or "")
    if not clean or alpha <= 0.01:
        return
    resolved_size = float(size) if size else _label_size(len(clean), box_width)
    pixel = canvas.geom.fs(resolved_size)
    lines = _wrap_text(canvas.draw, clean, pixel, box_width * canvas.geom.scale)
    line_height = resolved_size * 1.2
    top = center_y - (line_height * (len(lines) - 1)) / 2.0
    for offset, line in enumerate(lines):
        draw_text(
            canvas.draw,
            canvas.geom,
            line,
            design_x=center_x,
            design_y=top + offset * line_height,
            size=resolved_size,
            fill=color,
            anchor="mm",
            alpha=alpha,
        )


def _headline(canvas: _Canvas, text: str, *, entry: float, alpha: float) -> None:
    """镜头标题：居中 46px + 紫色下划线（下划线随入场展开，是镜头的起手式）。"""
    clean = str(text or "")
    if not clean:
        return
    geom = canvas.geom
    size = 46.0
    rise = (1.0 - ease_out_cubic(entry)) * 20.0
    draw_text(
        canvas.draw,
        geom,
        clean,
        design_x=HUD_CENTER_X,
        design_y=_HEADLINE_Y + rise,
        size=size,
        anchor="mm",
        stroke=1.6,
        alpha=alpha * clamp01(entry),
    )
    pixel = geom.fs(size)
    width_px = canvas.draw.textbbox((0, 0), clean, font=_font(pixel))[2]
    underline = clamp01(entry * 1.35)
    if underline > 0.02:
        half = (width_px / 2.0) * underline / max(geom.scale * geom.zoom, 1e-6)
        canvas.draw.rounded_rectangle(
            geom.box(HUD_CENTER_X - half, _HEADLINE_Y + 34.0, HUD_CENTER_X + half, _HEADLINE_Y + 39.0),
            radius=max(1.0, geom.size(2.0)),
            fill=_rgba(COLOR_PURPLE, alpha * underline),
        )


def _arrow(
    canvas: _Canvas,
    design_x: float,
    design_y: float,
    *,
    alpha: float,
    angle: float = 0.0,
) -> None:
    """流程箭头（两段折线构成的 ``>``），只在两侧卡片都已点亮时出现。

    ``angle`` 是切线方向（弧度）：环形/斜向流程里箭头必须跟着路径转向，
    否则一律朝右的 ``>`` 在圆环上看起来像画错了。
    """
    if alpha <= 0.01:
        return
    geom = canvas.geom
    size = 15.0
    local = ((-size, -size), (size * 0.35, 0.0), (-size, size))
    cosine, sine = math.cos(angle), math.sin(angle)
    points = [
        geom.pt(design_x + dx * cosine - dy * sine, design_y + dx * sine + dy * cosine)
        for dx, dy in local
    ]
    canvas.draw.line(
        points,
        fill=_rgba(COLOR_DIM, alpha),
        width=max(2, int(round(geom.size(3.2)))),
        joint="curve",
    )


def _dot_marker(canvas: _Canvas, design_x: float, design_y: float, *, radius: float, color: tuple[int, int, int], filled: bool, alpha: float, width: float = 2.6) -> None:
    geom = canvas.geom
    box = geom.box(design_x - radius, design_y - radius, design_x + radius, design_y + radius)
    canvas.draw.ellipse(
        box,
        fill=_rgba(color, alpha) if filled else None,
        outline=_rgba(color, alpha),
        width=max(1, int(round(geom.size(width)))),
    )


def _draw_term(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """核心术语大字登场：逐字浮现 + 紫色柔光 + 轻微上推。"""
    words = [str(item) for item in (shot.items or shot.numbers) if str(item)]
    if not words:
        first = str(next(iter(shot.sentences), ""))[:12]
        words = [first] if first else []
    if not words:
        return
    words = words[:2]
    shown = _typewriter(words, action) if shot.motion == "typewriter" else words
    single = len(shown) == 1
    for index, word in enumerate(shown):
        center_y = BODY_MID_Y if single else 356.0 + index * 108.0
        size = 92.0 if single else 68.0
        if not word:
            continue
        draw_squeezed_text(
            canvas.layer,
            canvas.geom,
            word,
            design_x=HUD_CENTER_X,
            design_y=center_y - (1.0 - ease_out_cubic(entry)) * 26.0,
            size=size * (0.94 + 0.06 * ease_out_cubic(entry)),
            squeeze=0.85,
            fill=COLOR_INK if index else COLOR_PURPLE_LIGHT,
            glow=COLOR_PURPLE if index == 0 else None,
            alpha=clamp01(entry),
        )


def _draw_flow(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """横向流程：卡片依次点亮 + 箭头衔接，travel 时紫点沿线跑。"""
    items = [str(item) for item in shot.items if str(item)][:6]
    if not items:
        return
    count = len(items)
    card_width = (_AVAILABLE - _CARD_GAP * (count - 1)) / count
    card_height = 168.0 if count <= 3 else 150.0
    shown = _shown_count(count, _reveal(action, shot.motion))
    for index, text in enumerate(items):
        if index >= shown:
            continue
        revealed = clamp01(_reveal(action, shot.motion) * count - index)
        alpha = clamp01(entry) * (0.25 + 0.75 * revealed)
        x0 = CONTENT_LEFT + index * (card_width + _CARD_GAP)
        emphasized = index == shot.emphasis
        _rounded_box(
            canvas.draw,
            canvas.geom,
            (x0, BODY_MID_Y - card_height / 2.0, x0 + card_width, BODY_MID_Y + card_height / 2.0),
            fill=COLOR_PURPLE if emphasized else None,
            outline=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
            alpha=alpha,
        )
        _label(
            canvas,
            text,
            center_x=x0 + card_width / 2.0,
            center_y=BODY_MID_Y,
            box_width=card_width - 24.0,
            alpha=alpha,
        )
        if index:
            _arrow(canvas, x0 - _CARD_GAP / 2.0, BODY_MID_Y, alpha=alpha)
    if shot.motion == "travel":
        track_y = BODY_MID_Y + card_height / 2.0 + 34.0
        head_x = CONTENT_LEFT + _AVAILABLE * clamp01(action)
        canvas.draw.line(
            [canvas.geom.pt(CONTENT_LEFT, track_y), canvas.geom.pt(CONTENT_RIGHT, track_y)],
            fill=_rgba(COLOR_DIM, 0.35 * clamp01(entry)),
            width=max(1, int(round(canvas.geom.size(2.0)))),
        )
        _dot_marker(
            canvas,
            head_x,
            track_y,
            radius=9.0,
            color=COLOR_PURPLE_LIGHT,
            filled=True,
            alpha=clamp01(entry),
        )


def _split_pair(text: str) -> tuple[str, str]:
    """「A|B」（全角竖线也认）拆成左右两侧；没有分隔符时右侧为空。"""
    for separator in ("|", "｜"):
        head, matched, tail = text.partition(separator)
        if matched:
            return head.strip(), tail.strip()
    return text.strip(), ""


def _draw_compare(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """左右两栅对比：中缝分隔 + 逐行点亮。

    模型按分镜 prompt 的口径给「A|B」成对条目时按对分栅；否则退化成
    「前一半在左、后一半在右」（分镜兼容模型不听话时也能画出成立的对比）。"""
    items = [str(item) for item in shot.items if str(item)][:6]
    pairs = [_split_pair(item) for item in items]
    if items and all(right for _, right in pairs):
        columns: tuple[list[str], list[str]] = (
            [left for left, _ in pairs],
            [right for _, right in pairs],
        )
    else:
        if len(items) < 2:
            items = items + [""]
        middle = (len(items) + 1) // 2
        columns = (items[:middle], items[middle:])
    rows = max(len(columns[0]), len(columns[1]))
    column_width = (_AVAILABLE - 176.0) / 2.0
    row_height = min(112.0, (_BODY_BOTTOM - _BODY_TOP - 40.0) / max(1, rows))
    grid_top = BODY_MID_Y - (row_height * rows) / 2.0 + row_height / 2.0
    shown = _shown_count(rows, _reveal(action, shot.motion))
    for row in range(rows):
        alpha = clamp01(entry) * (1.0 if row < shown else 0.0)
        if alpha <= 0.01:
            continue
        for side in (0, 1):
            texts = columns[side]
            if row >= len(texts) or not texts[row]:
                continue
            x0 = CONTENT_LEFT if side == 0 else CONTENT_LEFT + column_width + 176.0
            center_y = grid_top + row * row_height
            emphasized = side == 1 and row == rows - 1
            _rounded_box(
                canvas.draw,
                canvas.geom,
                (
                    x0,
                    center_y - row_height / 2.0 + 8.0,
                    x0 + column_width,
                    center_y + row_height / 2.0 - 8.0,
                ),
                fill=COLOR_PURPLE if emphasized else None,
                outline=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
                alpha=alpha,
            )
            _label(
                canvas,
                texts[row],
                center_x=x0 + column_width / 2.0,
                center_y=center_y,
                box_width=column_width - 28.0,
                alpha=alpha,
            )
    seam_x = CONTENT_LEFT + column_width + 88.0
    canvas.draw.line(
        [
            canvas.geom.pt(seam_x, grid_top - row_height / 2.0 - 6.0),
            canvas.geom.pt(seam_x, grid_top + row_height * (rows - 1) + row_height / 2.0 + 6.0),
        ],
        fill=_rgba(COLOR_DIM, 0.5 * clamp01(entry)),
        width=max(1, int(round(canvas.geom.size(2.2)))),
    )
    draw_text(
        canvas.draw,
        canvas.geom,
        "VS",
        design_x=seam_x,
        design_y=grid_top - row_height / 2.0 - 30.0,
        size=26.0,
        fill=COLOR_WARM,
        anchor="mm",
        alpha=clamp01(entry),
    )


def _draw_bars(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """柱状对比：数值按进度长出，数字同步滚动（无解析值时退化为等差柱高）。"""
    labels = [str(item) for item in shot.items if str(item)][:6]
    count = len(labels) or len([n for n in shot.numbers if str(n)])
    if count < 2:
        return
    labels = (labels + [""] * count)[:count]
    raw_values = [str(n) for n in shot.numbers if str(n)]
    parsed = [_leading_number(text) for text in (raw_values[:count] or labels)]
    if all(value is None for value in parsed):
        parsed = [float(count - index) for index in range(count)]
    values = [abs(value) if value is not None else 0.0 for value in parsed]
    value_texts = (raw_values[:count] if len(raw_values) >= count else list(labels)) + [""] * count
    value_texts = value_texts[:count]
    peak = max(values) or 1.0
    slot = _AVAILABLE / count
    bar_width = min(118.0, slot * 0.62)
    baseline = 556.0
    max_height = 292.0
    layer_alpha = clamp01(entry)
    canvas.draw.line(
        [canvas.geom.pt(CONTENT_LEFT, baseline), canvas.geom.pt(CONTENT_RIGHT, baseline)],
        fill=_rgba(COLOR_DIM, 0.55 * layer_alpha),
        width=max(1, int(round(canvas.geom.size(2.2)))),
    )
    progress_scale = clamp01(action / 0.75) if shot.motion in _REVEAL_MOTIONS else 1.0
    for index, value in enumerate(values):
        stagger = clamp01(progress_scale * count - index)
        if stagger <= 0.01:
            continue
        height = max(4.0, max_height * (value / peak) * ease_out_cubic(stagger))
        center_x = CONTENT_LEFT + slot * (index + 0.5)
        emphasized = index == shot.emphasis
        _rounded_box(
            canvas.draw,
            canvas.geom,
            (
                center_x - bar_width / 2.0,
                baseline - height,
                center_x + bar_width / 2.0,
                baseline - 2.0,
            ),
            fill=COLOR_PURPLE if emphasized else None,
            outline=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
            radius_ratio=0.12,
            alpha=layer_alpha,
        )
        literal = value_texts[index]
        if literal:
            draw_text(
                canvas.draw,
                canvas.geom,
                _count_text(literal, stagger),
                design_x=center_x,
                design_y=baseline - height - 26.0,
                size=26.0,
                fill=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
                anchor="mm",
                stroke=1.4,
                alpha=layer_alpha * stagger,
            )
        _label(
            canvas,
            labels[index],
            center_x=center_x,
            center_y=baseline + 26.0,
            box_width=slot - 12.0,
            size=22.0,
            color=COLOR_DIM,
            alpha=layer_alpha,
        )


def _draw_bignumber(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """单个大数字：数字滚动到 75% 处落位，剩余时间留给稳定期读它。"""
    literal = next((str(n) for n in shot.numbers if str(n)), "")
    if not literal:
        literal = next((str(item) for item in shot.items if str(item)), "")
    if not literal:
        return
    labels = [str(item) for item in shot.items if str(item)]
    caption = labels[0] if labels and _leading_number(literal) is not None else ""
    progress = clamp01(action / 0.75) if _leading_number(literal) is not None else 1.0
    text = _count_text(literal, progress)
    draw_squeezed_text(
        canvas.layer,
        canvas.geom,
        text,
        design_x=HUD_CENTER_X,
        design_y=BODY_MID_Y - 24.0 - (1.0 - ease_out_cubic(entry)) * 18.0,
        size=128.0,
        squeeze=0.82,
        fill=COLOR_INK,
        glow=COLOR_PURPLE,
        alpha=clamp01(entry),
    )
    if caption:
        _label(
            canvas,
            caption,
            center_x=HUD_CENTER_X,
            center_y=BODY_MID_Y + 92.0,
            box_width=760.0,
            size=30.0,
            color=COLOR_DIM,
            alpha=clamp01(entry) * progress,
        )


def _draw_list(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """要点清单：逐行滑入 + 序号点亮起，打字机时文字逐字出。"""
    items = [str(item) for item in shot.items if str(item)][:6]
    count = len(items)
    if not count:
        return
    row_height = min(96.0, (_BODY_BOTTOM - _BODY_TOP - 24.0) / count)
    top = BODY_MID_Y - (row_height * count) / 2.0 + row_height / 2.0
    reveal = _reveal(action, shot.motion)
    for index, text in enumerate(items):
        stagger = clamp01(reveal * count - index)
        alpha = clamp01(entry) * (0.0 if stagger <= 0.0 else 0.35 + 0.65 * stagger)
        if alpha <= 0.01:
            continue
        center_y = top + index * row_height
        emphasized = index == shot.emphasis
        slide = (1.0 - ease_out_cubic(stagger)) * 30.0
        _dot_marker(
            canvas,
            CONTENT_LEFT + 12.0 + slide,
            center_y,
            radius=11.0,
            color=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
            filled=stagger > 0.55,
            alpha=alpha,
        )
        body = text
        if shot.motion == "typewriter":
            typed = _typewriter([text], stagger)
            body = typed[0] if typed else ""
        draw_text(
            canvas.draw,
            canvas.geom,
            body,
            design_x=CONTENT_LEFT + 52.0 + slide,
            design_y=center_y,
            size=min(36.0, row_height * 0.52),
            fill=COLOR_INK,
            anchor="lm",
            stroke=1.5,
            alpha=alpha,
        )


def _draw_cycle(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """环形循环：节点沿圆环依次出现，travel 时紫点绕环跑一圈。"""
    items = [str(item) for item in shot.items if str(item)][:6]
    count = len(items)
    if count < 2:
        return
    radius = 150.0 if count <= 4 else 166.0
    center_y_design = 402.0
    layer_alpha = clamp01(entry)
    center = canvas.geom.pt(HUD_CENTER_X, center_y_design)
    ring_radius = canvas.geom.size(radius)
    canvas.draw.ellipse(
        (
            center[0] - ring_radius,
            center[1] - ring_radius,
            center[0] + ring_radius,
            center[1] + ring_radius,
        ),
        outline=_rgba(COLOR_DIM, 0.45 * layer_alpha),
        width=max(1, int(round(canvas.geom.size(2.2)))),
    )
    shown = _shown_count(count, _reveal(action, shot.motion))
    node_width = 148.0 if count <= 4 else 128.0
    node_height = 62.0
    for index in range(count):
        angle = -math.pi / 2.0 + _TAU * index / count
        center_x = HUD_CENTER_X + radius * math.cos(angle)
        center_y = center_y_design + radius * math.sin(angle)
        if index >= shown:
            # 未轮到的节点**不画空盒子**：空胶囊看起来像渲染坏了，而不是「还没到」。
            continue
        stagger = clamp01(_reveal(action, shot.motion) * count - index)
        alpha = layer_alpha * (0.45 + 0.55 * stagger)
        emphasized = index == shot.emphasis
        _rounded_box(
            canvas.draw,
            canvas.geom,
            (
                center_x - node_width / 2.0,
                center_y - node_height / 2.0,
                center_x + node_width / 2.0,
                center_y + node_height / 2.0,
            ),
            fill=COLOR_PURPLE if emphasized else COLOR_BG,
            outline=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
            radius_ratio=0.32,
            alpha=alpha,
        )
        _label(
            canvas,
            items[index],
            center_x=center_x,
            center_y=center_y,
            box_width=node_width - 20.0,
            alpha=alpha,
        )
        if index + 1 < shown:
            next_angle = -math.pi / 2.0 + _TAU * (index + 1) / count
            mid_angle = (angle + next_angle) / 2.0
            _arrow(
                canvas,
                HUD_CENTER_X + radius * math.cos(mid_angle),
                center_y_design + radius * math.sin(mid_angle),
                alpha=layer_alpha * 0.85,
                angle=mid_angle + math.pi / 2.0,
            )
    if shot.motion == "travel":
        # 跑点走内圈：外圈正上节点盒子，压上去会把要点文字挡住。
        orbit = radius - 52.0
        angle = -math.pi / 2.0 + _TAU * clamp01(action)
        _dot_marker(
            canvas,
            HUD_CENTER_X + orbit * math.cos(angle),
            center_y_design + orbit * math.sin(angle),
            radius=9.0,
            color=COLOR_WARM,
            filled=True,
            alpha=layer_alpha,
        )


def _draw_timeline(canvas: _Canvas, shot: ExplainerShot, *, entry: float, action: float) -> None:
    """时间轴：轴带随进度变紫，节点标签上下交错出现。"""
    items = [str(item) for item in shot.items if str(item)][:6]
    count = len(items)
    if count < 2:
        return
    layer_alpha = clamp01(entry)
    line_y = BODY_MID_Y
    left_x = CONTENT_LEFT + 24.0
    right_x = CONTENT_RIGHT - 24.0
    canvas.draw.line(
        [canvas.geom.pt(left_x, line_y), canvas.geom.pt(right_x, line_y)],
        fill=_rgba(COLOR_DIM, 0.5 * layer_alpha),
        width=max(2, int(round(canvas.geom.size(2.6)))),
    )
    reveal = _reveal(action, shot.motion)
    shown = _shown_count(count, reveal)
    slot = (right_x - left_x) / count
    head_x = left_x + slot * max(0.5, shown - 0.5)
    if shown > 1 or reveal > 0.02:
        canvas.draw.line(
            [canvas.geom.pt(left_x, line_y), canvas.geom.pt(head_x, line_y)],
            fill=_rgba(COLOR_PURPLE, 0.9 * layer_alpha),
            width=max(2, int(round(canvas.geom.size(3.4)))),
        )
    for index, text in enumerate(items):
        center_x = left_x + slot * (index + 0.5)
        revealed = index < shown
        stagger = clamp01(reveal * count - index)
        alpha = layer_alpha * (0.35 + 0.65 * stagger if revealed else 0.0)
        emphasized = index == shot.emphasis
        _dot_marker(
            canvas,
            center_x,
            line_y,
            radius=12.0 if emphasized else 9.0,
            color=COLOR_PURPLE_LIGHT if emphasized else COLOR_INK,
            filled=revealed,
            alpha=layer_alpha if revealed else 0.25 * layer_alpha,
        )
        if not revealed:
            continue
        above = index % 2 == 0
        label_y = line_y - 62.0 if above else line_y + 62.0
        stem_top, stem_bottom = (
            (line_y - 16.0, label_y + 22.0) if above else (line_y + 16.0, label_y - 22.0)
        )
        canvas.draw.line(
            [canvas.geom.pt(center_x, stem_top), canvas.geom.pt(center_x, stem_bottom)],
            fill=_rgba(COLOR_DIM, 0.55 * alpha),
            width=max(1, int(round(canvas.geom.size(1.8)))),
        )
        _label(
            canvas,
            text,
            center_x=center_x,
            center_y=label_y,
            box_width=slot - 16.0,
            size=24.0,
            alpha=alpha,
        )


_PRIMITIVE_DRAWERS: dict[str, Callable[..., None]] = {
    "term": _draw_term,
    "flow": _draw_flow,
    "compare": _draw_compare,
    "bars": _draw_bars,
    "bignumber": _draw_bignumber,
    "list": _draw_list,
    "cycle": _draw_cycle,
    "timeline": _draw_timeline,
}


# --------------------------------------------------------------------------- #
# 时间轴取样：镜头/字幕块定位 + 节拍相位（入场、持续动作、运镜、离场）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Phase:
    """一帧在镜头内的相位。"""

    entry: float
    action: float
    local: float
    zoom: float
    fade: float


def _scene_index(timeline: ExplainerTimeline, frame: int) -> int:
    """帧号 -> 镜头下标（越界返回 -1）。"""
    if not timeline.scenes:
        return -1
    index = bisect.bisect_right([scene.start_frame for scene in timeline.scenes], frame) - 1
    if index < 0:
        return -1
    scene = timeline.scenes[index]
    return index if frame < scene.end_frame else -1


def _scene_at(timeline: ExplainerTimeline, frame: int) -> ExplainerScene | None:
    index = _scene_index(timeline, frame)
    return timeline.scenes[index] if index >= 0 else None


def _cue_at(scene: ExplainerScene, frame: int) -> ExplainerCue | None:
    for cue in scene.cues:
        if cue.start_frame <= frame < cue.end_frame:
            return cue
    return None


def _chapter_index_at(timeline: ExplainerTimeline, frame: int) -> int:
    if not timeline.chapter_starts:
        return 0
    return max(0, bisect.bisect_right(list(timeline.chapter_starts), frame) - 1)


def _lead_frames(timeline: ExplainerTimeline, index: int) -> int:
    """镜头开头被覆盖层占用的帧数（片头卡 / 章节卡）。

    配音从第 0 帧就开始，所以卡片是**盖在内容上的覆盖层**而不是空镜头：这里只让
    内容层晚一点入场。镜头太短时不预留，否则入场永远走不完（宁可少一张卡）。
    """
    scene = timeline.scenes[index]
    duration = scene.end_frame - scene.start_frame
    if index == 0:
        if duration >= TITLE_FRAMES + MIN_SHOT_FRAMES:
            return TITLE_FRAMES
        return 0
    if duration >= CHAPTER_CARD_FRAMES + MIN_SHOT_FRAMES:
        if scene.start_frame in timeline.chapter_starts and scene.start_frame > 0:
            return CHAPTER_CARD_FRAMES
    return 0


def _shot_phase(scene: ExplainerScene, frame: int, *, lead: int, motion: str) -> _Phase:
    """把帧号换成入场/持续动作/运镜/离场四条曲线。"""
    content_start = scene.start_frame + lead
    action_start = content_start + ENTRY_FRAMES
    # 动作窗口终点 = 镜头终点 - 离场 - 稳定期：落位后必须停得住再切。
    action_end = max(action_start + 1, scene.end_frame - EXIT_FRAMES - MIN_STEADY_FRAMES)
    if frame < content_start:
        entry = 0.0
        action = 0.0
        local = 0.0
    else:
        entry = clamp01((frame - content_start) / float(max(1, ENTRY_FRAMES)))
        action = clamp01((frame - action_start) / float(max(1, action_end - action_start)))
        local = clamp01((frame - content_start) / float(max(1, scene.end_frame - content_start)))
    fade = clamp01((scene.end_frame - frame) / float(max(1, EXIT_FRAMES)))
    # 没有任何持续动词的镜头补一个 1.0→1.05 的慢推，避免整段静止。
    zoom = 1.0 + 0.05 * ease_in_out(local) if motion == "softin" else 1.0
    return _Phase(entry=entry, action=action, local=local, zoom=zoom, fade=fade)


# --------------------------------------------------------------------------- #
# 覆盖层：HUD 胶囊 / 章节进度条 / 字幕带 / 片头卡 / 章节卡
# --------------------------------------------------------------------------- #


def _draw_hud(layer: Image.Image, text: str, *, alpha: float) -> None:
    """顶部胶囊 HUD：当前章名（不随运镜缩放）。"""
    clean = str(text or "").strip()
    if not clean or alpha <= 0.01:
        return
    geom = _Geom(layer.width, layer.height)
    draw = ImageDraw.Draw(layer)
    pixel = geom.fs(24.0)
    bbox = draw.textbbox((0, 0), clean, font=_font(pixel))
    text_width = bbox[2] - bbox[0]
    pill_height = geom.size(HUD_HEIGHT)
    pad_x = geom.size(28.0)
    top = geom.y(HUD_TOP)
    draw.rounded_rectangle(
        (
            geom.x(HUD_CENTER_X) - text_width / 2.0 - pad_x,
            top,
            geom.x(HUD_CENTER_X) + text_width / 2.0 + pad_x,
            top + pill_height,
        ),
        radius=pill_height / 2.0,
        fill=_rgba(COLOR_BG, 0.55 * alpha),
        outline=_rgba(COLOR_INK, 0.8 * alpha),
        width=max(1, int(round(geom.size(2.0)))),
    )
    draw_text(
        draw,
        geom,
        clean,
        design_x=HUD_CENTER_X,
        design_y=HUD_TOP + HUD_HEIGHT / 2.0,
        size=24.0,
        anchor="mm",
        alpha=alpha,
    )


def _draw_progress(layer: Image.Image, timeline: ExplainerTimeline, frame: int, *, alpha: float) -> None:
    """底部章节进度条：过完的章整段紫，当前章按帧推进，未到的章压暗。"""
    if alpha <= 0.01 or timeline.total_frames <= 0:
        return
    geom = _Geom(layer.width, layer.height)
    draw = ImageDraw.Draw(layer)
    total = max(1, int(timeline.total_frames))
    bounds = [int(start) for start in timeline.chapter_starts] if timeline.chapter_starts else [0]
    bounds = [value for value in bounds if 0 <= value <= total]
    if not bounds or bounds[0] != 0:
        bounds = [0] + bounds
    if bounds[-1] != total:
        bounds.append(total)
    deduped = sorted(dict.fromkeys(bounds))
    current = _chapter_index_at(timeline, frame)
    thickness = max(2, int(round(geom.size(5.0))))
    for index in range(len(deduped) - 1):
        seg_start, seg_end = deduped[index], deduped[index + 1]
        if seg_end <= seg_start:
            continue
        x0 = geom.x(CONTENT_LEFT + (CONTENT_RIGHT - CONTENT_LEFT) * seg_start / total)
        x1 = geom.x(CONTENT_LEFT + (CONTENT_RIGHT - CONTENT_LEFT) * seg_end / total)
        y = geom.y((PROGRESS_TOP + PROGRESS_BOTTOM) / 2.0)
        draw.rounded_rectangle(
            (x0, y - thickness / 2.0, x1, y + thickness / 2.0),
            radius=thickness / 2.0,
            fill=_rgba(COLOR_DIM, 0.28 * alpha),
        )
        if index < current:
            continue
        reached = x0 if index > current else x0 + (x1 - x0) * clamp01((frame - seg_start) / float(max(1, seg_end - seg_start)))
        if reached > x0 + 0.5:
            draw.rounded_rectangle(
                (x0, y - thickness / 2.0, reached, y + thickness / 2.0),
                radius=thickness / 2.0,
                fill=_rgba(COLOR_PURPLE, 0.95 * alpha),
            )


def _draw_subtitle(layer: Image.Image, text: str, *, alpha: float, rise: float) -> None:
    """字幕带：44px 白字黑边 + 半透明底衬（压在任何幕底都可读）。"""
    clean = str(text or "")
    if not clean or alpha <= 0.01:
        return
    geom = _Geom(layer.width, layer.height)
    draw = ImageDraw.Draw(layer)
    size = 44.0
    pixel = geom.fs(size)
    bbox = draw.textbbox((0, 0), clean, font=_font(pixel))
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    pad_x = geom.size(22.0)
    pad_y = geom.size(12.0)
    center_x = geom.x(HUD_CENTER_X)
    center_y = geom.y(SUBTITLE_CENTER_Y - rise)
    draw.rounded_rectangle(
        (
            center_x - text_width / 2.0 - pad_x,
            center_y - text_height / 2.0 - pad_y,
            center_x + text_width / 2.0 + pad_x,
            center_y + text_height / 2.0 + pad_y,
        ),
        radius=geom.size(12.0),
        fill=_rgba(COLOR_BG, 0.62 * alpha),
    )
    draw_text(
        draw,
        geom,
        clean,
        design_x=HUD_CENTER_X,
        design_y=SUBTITLE_CENTER_Y - rise,
        size=size,
        anchor="mm",
        stroke=2.2,
        alpha=alpha,
    )


def _card_alpha(progress: float) -> float:
    """覆盖卡自己的淡入淡出曲线（前 1/4 进、后 1/4 出）。"""
    clean = clamp01(progress)
    return min(ease_out_cubic(clean * 4.0), ease_out_cubic((1.0 - clean) * 4.0))


def _draw_title_card(layer: Image.Image, title, *, progress: float) -> None:
    """片头卡：宽体词 + 中文片名 + 英文全称 + 一句话钩子。"""
    alpha = _card_alpha(progress)
    if alpha <= 0.01 or not (title.big or title.rest):
        return
    geom = _Geom(layer.width, layer.height)
    if title.big:
        draw_squeezed_text(
            layer,
            geom,
            title.big,
            design_x=HUD_CENTER_X,
            design_y=286.0,
            size=104.0,
            squeeze=0.8,
            fill=COLOR_INK,
            glow=COLOR_PURPLE,
            alpha=alpha,
        )
    if title.rest:
        draw_squeezed_text(
            layer,
            geom,
            title.rest,
            design_x=HUD_CENTER_X,
            design_y=384.0,
            size=54.0,
            squeeze=0.9,
            fill=COLOR_INK,
            alpha=alpha,
        )
    if title.en:
        draw_text(
            ImageDraw.Draw(layer),
            geom,
            title.en,
            design_x=HUD_CENTER_X,
            design_y=444.0,
            size=26.0,
            fill=COLOR_DIM,
            anchor="mm",
            alpha=alpha * 0.9,
        )
    if title.tagline:
        draw_text(
            ImageDraw.Draw(layer),
            geom,
            title.tagline,
            design_x=HUD_CENTER_X,
            design_y=518.0,
            size=30.0,
            fill=COLOR_PURPLE_LIGHT,
            anchor="mm",
            stroke=1.6,
            alpha=alpha,
        )


def _draw_chapter_card(
    layer: Image.Image,
    *,
    name: str,
    index: int,
    total: int,
    progress: float,
) -> None:
    """章节卡：大号章号 + 章名，居中压在内容层上。"""
    alpha = _card_alpha(progress)
    if alpha <= 0.01:
        return
    geom = _Geom(layer.width, layer.height)
    draw = ImageDraw.Draw(layer)
    number = f"{index + 1:02d}"
    draw_squeezed_text(
        layer,
        geom,
        number,
        design_x=HUD_CENTER_X,
        design_y=300.0,
        size=112.0,
        squeeze=0.86,
        fill=COLOR_PURPLE_LIGHT,
        glow=COLOR_PURPLE,
        alpha=alpha,
    )
    if total > 1:
        draw_text(
            draw,
            geom,
            f"/ {total:02d}",
            design_x=HUD_CENTER_X,
            design_y=378.0,
            size=26.0,
            fill=COLOR_DIM,
            anchor="mm",
            alpha=alpha * 0.9,
        )
    clean = str(name or "").strip()
    if clean:
        draw_text(
            draw,
            geom,
            clean,
            design_x=HUD_CENTER_X,
            design_y=440.0,
            size=46.0,
            anchor="mm",
            stroke=1.8,
            alpha=alpha,
        )


# --------------------------------------------------------------------------- #
# 逐帧合成 + 流式编码
# --------------------------------------------------------------------------- #


def compose_explainer_frame(
    timeline: ExplainerTimeline,
    frame: int,
    *,
    width: int = DESIGN_WIDTH,
    height: int = DESIGN_HEIGHT,
    background: str = "dots",
) -> np.ndarray:
    """把一帧画成 RGB 数组（无副作用纯函数，单测可逐帧断言）。"""
    fps = max(1, int(timeline.fps or 30))
    canvas = Image.new("RGBA", (max(2, int(width)), max(2, int(height))), COLOR_BG + (255,))

    index = _scene_index(timeline, frame)
    scene = timeline.scenes[index] if index >= 0 else None

    # 1) 幕底永远不跟运镜（否则点阵会跟着镜头漂）。
    backdrop = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_backdrop(
        ImageDraw.Draw(backdrop),
        _Geom(canvas.width, canvas.height),
        frame,
        fps,
        background,
    )
    canvas.alpha_composite(backdrop)

    # 2) 内容层：图元 + 标题，随镜头入场/离场，并带运镜缩放。
    if scene is not None:
        lead = _lead_frames(timeline, index)
        phase = _shot_phase(scene, frame, lead=lead, motion=scene.shot.motion)
        if phase.entry > 0.0:
            content = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            content_geom = _Geom(canvas.width, canvas.height, zoom=phase.zoom)
            drawer = _PRIMITIVE_DRAWERS.get(scene.shot.primitive, _draw_term)
            context = _Canvas(layer=content, draw=ImageDraw.Draw(content), geom=content_geom)
            _headline(context, scene.shot.headline, entry=phase.entry, alpha=1.0)
            drawer(context, scene.shot, entry=phase.entry, action=phase.action)
            _apply_alpha(content, phase.fade)
            canvas.alpha_composite(content)

    # 3) 覆盖层（HUD / 进度条 / 字幕 / 卡片）恒定不缩放。
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    chapter_index = _chapter_index_at(timeline, frame)
    names = timeline.chapter_names or ()
    chapter_name = names[chapter_index] if chapter_index < len(names) else ""
    if scene is not None:
        _draw_hud(overlay, chapter_name or f"第 {chapter_index + 1} 节", alpha=0.9)
        _draw_progress(overlay, timeline, frame, alpha=1.0)
        cue = _cue_at(scene, frame)
        if cue is not None:
            _draw_subtitle(
                overlay,
                cue.text,
                alpha=clamp01((frame - cue.start_frame + 1) / 3.0),
                rise=(1.0 - clamp01((frame - cue.start_frame) / 4.0)) * 6.0,
            )
    # 卡片只在内容层确实预留了 lead 时出现：镜头太短时宁可不叠卡，
    # 也不能让片头字和图元文字重在一起（那是直接可见的穿帮）。
    if scene is not None:
        reserved = _lead_frames(timeline, index)
        if (
            index == 0
            and reserved >= TITLE_FRAMES
            and frame < scene.start_frame + TITLE_FRAMES
        ):
            _draw_title_card(
                overlay,
                timeline.title,
                progress=(frame - scene.start_frame) / float(TITLE_FRAMES),
            )
        elif (
            reserved >= CHAPTER_CARD_FRAMES
            and scene.start_frame > 0
            and frame < scene.start_frame + CHAPTER_CARD_FRAMES
        ):
            _draw_chapter_card(
                overlay,
                name=chapter_name or f"第 {chapter_index + 1} 节",
                index=chapter_index,
                total=max(1, len(timeline.chapter_starts)),
                progress=(frame - scene.start_frame) / float(CHAPTER_CARD_FRAMES),
            )
    canvas.alpha_composite(overlay)

    # 4) 收尾压黑。
    remaining = timeline.total_frames - frame
    if 0 < remaining < TAIL_FADE_FRAMES:
        veil = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(veil).rectangle(
            (0, 0, canvas.width, canvas.height),
            fill=(0, 0, 0, int(round((1.0 - remaining / float(TAIL_FADE_FRAMES)) * 255))),
        )
        canvas.alpha_composite(veil)
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8)


def _build_encoder_command(
    *,
    width: int,
    height: int,
    fps: int,
    out_path: Path,
    duration_seconds: float,
    narration_path: str | Path | None,
    bgm_path: str | Path | None,
    bgm_volume: float,
) -> tuple[list[str], str | None]:
    """拼一次 ffmpeg 调用：rawvideo 进管道 + 配音/BGM 混音（BGM 无限循环裁到片长）。"""
    command: list[str] = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
    ]
    if narration_path:
        command += ["-i", str(narration_path)]
    if bgm_path:
        command += ["-stream_loop", "-1", "-i", str(bgm_path)]
    filters: list[str] = []
    labels: list[str] = []
    stream = 1
    if narration_path:
        filters.append(
            f"[{stream}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[va]"
        )
        labels.append("[va]")
        stream += 1
    if bgm_path:
        volume = max(0.02, min(0.9, float(bgm_volume)))
        filters.append(
            f"[{stream}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,volume={volume:.3f}[vb]"
        )
        labels.append("[vb]")
        stream += 1
    graph: str | None = None
    if len(labels) == 2:
        graph = ";".join(filters) + f";{labels[0]}{labels[1]}amix=inputs=2:duration=longest:normalize=0[aout]"
    elif labels:
        graph = ";".join(filters) + f";{labels[0]}anull[aout]"
    command += ["-map", "0:v:0"]
    if graph:
        command += ["-filter_complex", graph, "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    else:
        command += ["-an"]
    command += [
        *video_encode_args(crf="18"),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        f"{duration_seconds + 0.04:.3f}",
        str(out_path),
    ]
    return command, graph


def render_explainer_video(
    *,
    timeline: ExplainerTimeline,
    out_path: str | Path,
    width: int = DESIGN_WIDTH,
    height: int = DESIGN_HEIGHT,
    fps: int | None = None,
    background: str = "dots",
    narration_path: str | Path | None = None,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.2,
    on_progress: Callable[[float, str], None] | None = None,
) -> Path:
    """逐帧代码绘制并流式编码成片，返回 ``out_path``。

    同步实现（调用方用 ``asyncio.to_thread`` 包裹）。帧**不落盘也不攒列表**：一帧
    720p RGB 约 2.7 MB，一条 3 分钟的片攒起来是 ~24 GB，所以直接写 ffmpeg 的
    ``pipe:0``。
    """
    if timeline.total_frames <= 0:
        raise ValueError("explainer timeline is empty")
    resolved_fps = max(1, int(fps or timeline.fps or 30))
    width = max(2, int(width))
    height = max(2, int(height))
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = timeline.total_frames / float(resolved_fps)
    command, _graph = _build_encoder_command(
        width=width,
        height=height,
        fps=resolved_fps,
        out_path=target,
        duration_seconds=duration_seconds,
        narration_path=narration_path,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    total = timeline.total_frames
    try:
        for frame in range(total):
            payload = compose_explainer_frame(
                timeline,
                frame,
                width=width,
                height=height,
                background=background,
            )
            assert process.stdin is not None
            process.stdin.write(payload.tobytes())
            if on_progress is not None and (frame % 5 == 0 or frame == total - 1):
                on_progress(0.95 * (frame + 1) / total, f"逐帧绘制 {frame + 1}/{total}")
    except BrokenPipeError:
        pass
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except BrokenPipeError:  # pragma: no cover - 编码器已退出
                pass
    stderr = (process.stderr.read() if process.stderr is not None else b"") or b""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"explainer encode failed: {stderr.decode(errors='ignore')[-1000:]}")
    if on_progress is not None:
        on_progress(1.0, "讲解视频编码完成")
    return target


async def render_explainer_video_async(
    *,
    timeline: ExplainerTimeline,
    out_path: str | Path,
    width: int = DESIGN_WIDTH,
    height: int = DESIGN_HEIGHT,
    fps: int | None = None,
    background: str = "dots",
    narration_path: str | Path | None = None,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.2,
) -> Path:
    """``render_explainer_video`` 的异步封装（线程内跑 Pillow + ffmpeg，不阻塞事件循环）。"""
    import asyncio

    return await asyncio.to_thread(
        render_explainer_video,
        timeline=timeline,
        out_path=out_path,
        width=width,
        height=height,
        fps=fps,
        background=background,
        narration_path=narration_path,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
    )
