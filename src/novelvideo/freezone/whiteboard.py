# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 白板动画逐笔揭示渲染核心（纯本地、不出网）。

把一张「暖米黄底 + 深色线稿」的静态图渲染成一段白板手绘风的逐笔揭示 MP4：
以暖米黄底 ``#F5EBD7`` 为参考色，按颜色距离取前景墨迹掩码 → 连通域切分区域
→ 阅读顺序（上→下、左→右）排序 → 按前景面积把整段时长分配给各区域 →
每帧在**持久画布**上按方向性遮罩（从左到右水平扫描）逐笔揭示当前区域，已揭示
区域保持可见、未开始区域完全隐藏 → 可选在揭示前沿叠加笔尖光标贴图 →
ffmpeg ``rawvideo`` 管道编码为单幕 clip，交由合成层拼接。

与参考包 ``cs-board`` 预览台的手标注不同，这里**自动分区、无需人工标注**；揭示
质量以「区域依次勾勒 + 笔尖前沿 + 持久画布」达标即可，不追求骨架/网格连续墨迹。

设计约束：纯本地 ffmpeg + OpenCV/numpy，不出网、不取凭证，登记为
``LeafEgress.LOCAL``。掩码 / 分区 / 时长分配 / 帧数都是可单测的纯函数，编码用
``subprocess`` 兜底；单元测试不依赖真实模型，也不触发 ffmpeg（渲染帧数用纯函数
校验，端到端 ffmpeg 用例留给集成层）。
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 暖米黄底（#F5EBD7 的 BGR 分量）——前景判定的参考色。
WARM_BEIGE_BGR = (215, 235, 245)
# 距暖米黄底的 BGR 欧氏距离超过此阈值算前景墨迹（深色线稿）。
_INK_COLOR_DISTANCE = 48.0
# 连通域面积小于此像素数当噪点忽略。
_MIN_REGION_AREA = 24
# 阅读顺序按画布高度切分的行带数（同带内按 x 左→右）。
_READING_ROW_BANDS = 8
# 区域揭示前沿笔尖光标贴图的默认位置（相对区域）。
_PEN_ASSET = Path(__file__).resolve().parent.parent / "assets" / "whiteboard" / "drawing-hand.png"

# 单幕时长上下限（毫秒）与帧率兜底，避免异常入参把渲染拖垮。
_MIN_SCENE_MS = 200
_MAX_SCENE_MS = 600_000
_DEFAULT_FPS = 30


@dataclass(frozen=True, slots=True)
class _Region:
    """一个连通域：包围盒 + 面积，用于阅读顺序与时长分配。"""

    x0: int
    y0: int
    x1: int
    y1: int
    area: int


def clamp_duration_ms(duration_ms: object) -> int:
    """把时长夹到合理区间（毫秒），非法值回退 4 秒。"""
    try:
        value = int(float(duration_ms))
    except (TypeError, ValueError):
        return 4000
    return max(_MIN_SCENE_MS, min(_MAX_SCENE_MS, value))


def clamp_fps(fps: object) -> int:
    try:
        value = int(fps)
    except (TypeError, ValueError):
        return _DEFAULT_FPS
    return max(1, min(60, value))


def frame_count(duration_ms: int, fps: int) -> int:
    """总时长换算帧数（至少 1 帧）。渲染与单测共用同一口径。"""
    duration_ms = clamp_duration_ms(duration_ms)
    fps = clamp_fps(fps)
    return max(1, int(round(duration_ms / 1000.0 * fps)))


def foreground_mask(image_bgr: np.ndarray) -> np.ndarray:
    """以暖米黄底为参考，按颜色距离取前景墨迹掩码（uint8 0/255）。

    先做轻度闭运算填补线稿断点，再开运算去孤立噪点，得到实心可用的掩码。
    """
    diff = image_bgr.astype(np.int16) - np.array(WARM_BEIGE_BGR, dtype=np.int16)
    distance = np.sqrt(np.sum(diff.astype(np.float32) ** 2, axis=2))
    mask = (distance > _INK_COLOR_DISTANCE).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def _reading_order(regions: list[_Region], canvas_height: int) -> list[_Region]:
    """按阅读顺序排序：主键行带（上→下），次键左边界（左→右）。"""
    if not regions:
        return []
    band_height = max(1, canvas_height // _READING_ROW_BANDS)

    def _key(region: _Region) -> tuple[int, int]:
        return (region.y0 // band_height, region.x0)

    return sorted(regions, key=_key)


def segment_regions(mask: np.ndarray) -> list[_Region]:
    """连通域切分前景区域并按阅读顺序排序。

    掩码为空（或全是噪点）时回退成覆盖整幅的单一区域，保证逐笔揭示不空转。
    """
    height, width = mask.shape[:2]
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    regions: list[_Region] = []
    for index in range(1, count):  # 0 是背景
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < _MIN_REGION_AREA:
            continue
        x0 = int(stats[index, cv2.CC_STAT_LEFT])
        y0 = int(stats[index, cv2.CC_STAT_TOP])
        w = int(stats[index, cv2.CC_STAT_WIDTH])
        h = int(stats[index, cv2.CC_STAT_HEIGHT])
        regions.append(_Region(x0=x0, y0=y0, x1=min(width, x0 + w), y1=min(height, y0 + h), area=area))
    if not regions:
        return [_Region(x0=0, y0=0, x1=width, y1=height, area=max(1, width * height))]
    return _reading_order(regions, height)


def plan_reveal_frames(regions: list[_Region], total_frames: int) -> list[tuple[int, int]]:
    """按前景面积把 ``total_frames`` 分配给各区域，返回每个区域的 ``[start, end)``。

    面积加权 + 最大余数法保证边界单调递增、且各区域帧数之和恰为 ``total_frames``
    （时长守恒）。每个至少 1 帧的区域在帧数不足时按面积降序优先。
    """
    total_frames = max(0, int(total_frames))
    if not regions:
        return []
    areas = [max(1, region.area) for region in regions]
    area_sum = sum(areas)
    # 理想帧数（浮点），再取整并补齐余数到 total_frames。
    raw = [total_frames * area / area_sum for area in areas]
    floors = [int(value) for value in raw]
    remainder = total_frames - sum(floors)
    # 面积大者优先补 1 帧。
    order = sorted(range(len(regions)), key=lambda i: raw[i] - floors[i], reverse=True)
    for slot in range(max(0, remainder)):
        floors[order[slot % len(floors)]] += 1
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for frames in floors:
        bounds.append((cursor, cursor + frames))
        cursor += frames
    return bounds


def _fit_onto_canvas(image_bgr: np.ndarray, width: int, height: int) -> np.ndarray:
    """把源图等比 contain 到 ``width x height`` 的暖米黄底画布上（居中留边）。"""
    src_h, src_w = image_bgr.shape[:2]
    if (src_w, src_h) == (width, height):
        return image_bgr.copy()
    scale = min(width / src_w, height / src_h)
    resized_w = max(1, int(round(src_w * scale)))
    resized_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(image_bgr, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:] = WARM_BEIGE_BGR
    offset_x = (width - resized_w) // 2
    offset_y = (height - resized_h) // 2
    canvas[offset_y : offset_y + resized_h, offset_x : offset_x + resized_w] = resized
    return canvas


def _reveal_bool_mask(
    fg_mask: np.ndarray,
    region: _Region,
    progress: float,
) -> np.ndarray:
    """区域内自左向右水平扫描的方向性揭示遮罩（progress∈[0,1]）。"""
    height, width = fg_mask.shape[:2]
    revealed = np.zeros((height, width), dtype=bool)
    span_x = max(1, region.x1 - region.x0)
    edge = region.x0 + int(round(progress * span_x))
    edge = max(region.x0, min(region.x1, edge))
    sub = fg_mask[region.y0 : region.y1, region.x0 : edge]
    revealed[region.y0 : region.y1, region.x0 : edge] = sub > 0
    return revealed


def _compose_frame(
    ink_canvas: np.ndarray,
    fg_mask: np.ndarray,
    regions: list[_Region],
    bounds: list[tuple[int, int]],
    frame_index: int,
) -> tuple[np.ndarray, tuple[int, int] | None]:
    """持久画布合成第 ``frame_index`` 帧，返回 (frame, 笔尖前沿坐标 or None)。

    已完成区域整体保留，当前区域按遮罩部分揭示，未开始区域隐藏（露出暖米黄底）。
    """
    height, width = fg_mask.shape[:2]
    reveal = np.zeros((height, width), dtype=bool)
    pen: tuple[int, int] | None = None
    for region, (start, end) in zip(regions, bounds):
        if end <= start:
            continue
        if frame_index >= end:
            reveal[region.y0 : region.y1, region.x0 : region.x1] |= (
                fg_mask[region.y0 : region.y1, region.x0 : region.x1] > 0
            )
        elif frame_index >= start:
            progress = (frame_index - start + 1) / (end - start)
            partial = _reveal_bool_mask(fg_mask, region, progress)
            reveal |= partial
            span_x = max(1, region.x1 - region.x0)
            edge_x = region.x0 + int(round(progress * span_x))
            pen = (min(width - 1, edge_x), (region.y0 + region.y1) // 2)
    frame = np.where(reveal[..., None], ink_canvas, np.array(WARM_BEIGE_BGR, dtype=np.uint8))
    return frame.astype(np.uint8), pen


def _overlay_pen_cursor(frame: np.ndarray, pen: tuple[int, int], asset: Path) -> None:
    """把笔尖光标贴图按 alpha 叠加到揭示前沿（就地修改 frame）。缺资产/无 alpha 则跳过。"""
    if asset is None or not Path(asset).exists():
        return
    overlay = cv2.imread(str(asset), cv2.IMREAD_UNCHANGED)
    if overlay is None:
        return
    height, width = frame.shape[:2]
    if overlay.ndim == 3 and overlay.shape[2] == 4:
        alpha = overlay[:, :, 3].astype(np.float32) / 255.0
        overlay_bgr = overlay[:, :, :3]
    else:
        alpha = np.ones(overlay.shape[:2], dtype=np.float32)
        overlay_bgr = overlay if overlay.ndim == 3 else cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)
    scale = max(0.06, min(0.2, height / 1080 * 0.12))
    target_w = max(16, int(width * scale))
    target_h = max(16, int(overlay_bgr.shape[0] / overlay_bgr.shape[1] * target_w))
    overlay_bgr = cv2.resize(overlay_bgr, (target_w, target_h))
    alpha = cv2.resize(alpha, (target_w, target_h))
    x, y = pen
    ox0 = max(0, min(width - target_w, x - target_w // 3))
    oy0 = max(0, min(height - target_h, y - target_h // 2))
    ox1, oy1 = ox0 + target_w, oy0 + target_h
    region = frame[oy0:oy1, ox0:ox1]
    alpha3 = alpha[..., None]
    frame[oy0:oy1, ox0:ox1] = (
        region.astype(np.float32) * (1 - alpha3)
        + overlay_bgr.astype(np.float32) * alpha3
    ).astype(np.uint8)


def _encode_frames_to_mp4(frames: list[np.ndarray], width: int, height: int, fps: int, out_path: Path) -> None:
    """用 ffmpeg ``rawvideo`` 管道把 RGB 帧序列编码为无声 H.264 clip。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
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
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for frame in frames:
            assert process.stdin is not None
            process.stdin.write(frame.tobytes())
        assert process.stdin is not None
        process.stdin.close()
    except BrokenPipeError:
        pass
    stderr = (process.stderr.read() if process.stderr is not None else b"") or b""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"whiteboard encode failed: {stderr.decode(errors='ignore')[-1000:]}")


def render_whiteboard_scene(
    *,
    image_path: str | Path,
    out_path: str | Path,
    duration_ms: object,
    width: int,
    height: int,
    fps: object = _DEFAULT_FPS,
    pen_cursor: bool = True,
    on_progress: Callable[[float, str], None] | None = None,
) -> Path:
    """把一张线稿渲染成逐笔揭示的无声单幕 clip，返回 ``out_path``。

    纯本地（OpenCV/numpy + ffmpeg），不出网、不取凭证。同步实现，调用方用
    ``asyncio.to_thread`` 包裹。
    """
    source = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(f"whiteboard source image unreadable: {image_path}")
    width = max(2, int(width))
    height = max(2, int(height))
    ink_canvas = _fit_onto_canvas(source, width, height)
    fg_mask = foreground_mask(ink_canvas)
    regions = segment_regions(fg_mask)
    total_frames = frame_count(duration_ms, fps)
    bounds = plan_reveal_frames(regions, total_frames)

    frames: list[np.ndarray] = []
    for index in range(total_frames):
        frame, pen = _compose_frame(ink_canvas, fg_mask, regions, bounds, index)
        if pen_cursor and pen is not None:
            _overlay_pen_cursor(frame, pen, _PEN_ASSET)
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if on_progress is not None and total_frames:
            on_progress(0.9 * (index + 1) / total_frames, f"逐笔揭示 {index + 1}/{total_frames}")

    _encode_frames_to_mp4(frames, width, height, clamp_fps(fps), Path(out_path))
    if on_progress is not None:
        on_progress(1.0, "单幕揭示完成")
    return Path(out_path)


async def render_whiteboard_scene_async(
    *,
    image_path: str | Path,
    out_path: str | Path,
    duration_ms: object,
    width: int,
    height: int,
    fps: object = _DEFAULT_FPS,
    pen_cursor: bool = True,
) -> Path:
    """``render_whiteboard_scene`` 的异步封装（线程内跑 OpenCV/ffmpeg，不阻塞事件循环）。"""
    import asyncio

    return await asyncio.to_thread(
        render_whiteboard_scene,
        image_path=image_path,
        out_path=out_path,
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        pen_cursor=pen_cursor,
    )


# ============================================================ #
# 分镜规划（纯函数，可单测）：文案 → N 幕 {scenePrompt,keyText,weight}
# ============================================================ #

WHITEBOARD_MAX_SCENES = 12
# 每幕 keyText 上限（字数），对标参考包重点文字简短口径，避免画面拥挤。
_KEY_TEXT_MAX_CHARS = 12
# 出图规范：暖米黄底极简手绘线稿、画面内不含任何文字（文字本地叠加）。
WHITEBOARD_IMAGE_STYLE_GUIDE = (
    "极简手绘白板线稿风格：暖米黄色背景（约 #F5EBD7），只用深灰近黑的细线条"
    "勾勒简笔示意图形与少量几何元素，画面里绝不出现任何文字、字母、数字或标点，"
    "留白充足、线条疏朗，便于后续逐笔描绘动画。"
)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    return "\n".join(
        line for line in stripped.splitlines() if not line.strip().startswith("```")
    ).strip()


def _span_json_candidate(text: str) -> str | None:
    """从夹带说明文字的模型输出里截出 JSON 根数组 / 对象子串。"""
    best: tuple[int, str] | None = None
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        end = text.rfind(closer)
        if end > start and (best is None or start < best[0]):
            best = (start, text[start : end + 1])
    return best[1] if best is not None else None


def _load_json(raw_text: object) -> Any:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = _strip_code_fence(text)
    candidates = [text, fenced]
    span = _span_json_candidate(fenced)
    if span is not None:
        candidates.append(span)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def build_whiteboard_script_prompt(text: str, max_scenes: int = WHITEBOARD_MAX_SCENES) -> str:
    """把口播文案交给文本叶，切成分镜规划 JSON 数组。"""
    cap = max(1, min(int(max_scenes), WHITEBOARD_MAX_SCENES))
    narration = str(text or "").strip()
    return (
        "你是白板动画分镜师。把下面的口播文案切分成不超过 "
        f"{cap} 个连续画面（幕），用于生成白板手绘线稿并配逐幕配音。\n"
        "只输出 JSON 数组，不要任何解释文字或代码块围栏。每个元素形如\n"
        '{"scenePrompt": "该幕白板线稿的画面描述（不含任何文字）", '
        '"keyText": "该幕顶部叠加的重点字词，4-10 个汉字", '
        '"weight": 正数，该幕相对时长权重}\n'
        "幕数随文案长度自适应，短文案可 1-2 幕；scenePrompt 里明确“画面不要出现文字”。\n"
        "\n口播文案：\n"
        f"{narration}"
    )


def parse_whiteboard_scenes(raw_text: object) -> list[dict[str, Any]]:
    """解析模型分镜 JSON，规整成 ``[{scenePrompt,keyText,weight}]``。

    无法解析或全空时返回空列表（调用方回退单幕）。weight 强制为正数。
    """
    data = _load_json(raw_text)
    if isinstance(data, dict):
        data = data.get("scenes") or data.get("items") or [data]
    if not isinstance(data, list):
        return []
    scenes: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("scenePrompt") or item.get("prompt") or "").strip()
        key = str(item.get("keyText") or item.get("key") or "").strip()[:_KEY_TEXT_MAX_CHARS]
        if not prompt and not key:
            continue
        try:
            weight = float(item.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if not math.isfinite(weight) or weight <= 0:
            weight = 1.0
        scenes.append({"scenePrompt": prompt, "keyText": key, "weight": weight})
    return scenes[:WHITEBOARD_MAX_SCENES]


def compose_scene_image_prompt(scene_prompt: str) -> str:
    """把单幕描述与白板出图规范拼成最终 image prompt（强调无文字）。"""
    clean = str(scene_prompt or "").strip()
    parts = [WHITEBOARD_IMAGE_STYLE_GUIDE]
    if clean:
        parts.append(f"画面内容：{clean}")
    parts.append("再次强调：画面中不要出现任何文字、字母或数字。")
    return " ".join(parts)


def allocate_scene_durations_ms(
    scenes: list[dict[str, Any]], narration_ms: int
) -> list[int]:
    """按各幕 ``weight`` 把配音总时长分配为每幕时长（毫秒）。

    最大余数法保证之和恰为 ``narration_ms``，且每幕不低于 ``_MIN_SCENE_MS``；
    幕数过多导致下限超出总时长时，按幕数均摊（时长守恒优先）。
    """
    count = len(scenes)
    if count == 0:
        return []
    narration_ms = max(0, int(narration_ms))
    if count == 1:
        return [max(_MIN_SCENE_MS, narration_ms)]
    weights = [max(1e-6, float(s.get("weight") or 1.0)) for s in scenes]
    total_weight = sum(weights)
    raw = [narration_ms * w / total_weight for w in weights]
    floors = [max(_MIN_SCENE_MS, int(value)) for value in raw]
    # 时长守恒：把 floor 后的差额按余数补回（在下限允许范围内）。
    delta = narration_ms - sum(floors)
    if delta >= 0:
        order = sorted(range(count), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        slot = 0
        while delta > 0:
            floors[order[slot % count]] += 1
            delta -= 1
            slot += 1
    else:
        # 每幕下限之和已超总时长：均摊并夹到下限。
        per = max(_MIN_SCENE_MS, narration_ms // count)
        floors = [per] * count
    return floors


# ============================================================ #
# 重点文字本地叠加（Pillow + 部署环境 CJK 字体，不进图像模型）
# ============================================================ #

_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
)


def resolve_cjk_font_path() -> str | None:
    """[aipdd fork delta] 探测一个可用的中文字体文件；命中候选或 ``fc-match`` 结果，否则 None。

    由 ``_resolve_cjk_font_path`` 提升为公开：科普讲解视频的 MG 渲染器
    (``explainer_motion``) 与白板叠字共用同一套字体探测口径，避免两个模块各自
    维护一份候选路径而和镜像里实际安装的 ``fonts-noto-cjk`` 脱节。
    """
    for candidate in _CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    try:
        completed = subprocess.run(
            ["fc-match", "--format", "%{file}", "Noto Sans CJK SC:lang=zh-cn"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    matched = str(completed.stdout).strip()
    return matched if matched and Path(matched).exists() else None


def overlay_key_text(
    *,
    image_path: str | Path,
    out_path: str | Path,
    text: str,
    width: int,
    height: int,
) -> Path:
    """把 ``keyText`` 叠到幕顶居中（对标参考 ``add_key_text``），不进图像模型。

    无可用 CJK 字体或空文本时直接拷贝原图（不报错，保证出片链路不断）。
    """
    from PIL import Image, ImageDraw, ImageFont

    source = Path(image_path)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = str(text or "").strip()[:_KEY_TEXT_MAX_CHARS]
    image = cv2.imread(str(source))
    if image is None:
        raise FileNotFoundError(f"whiteboard scene image unreadable: {source}")
    if image.shape[1] != width or image.shape[0] != height:
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    if clean:
        font_path = resolve_cjk_font_path()
        font_size = max(28, int(round(min(width, height) * 0.07)))
        try:
            font = (
                ImageFont.truetype(font_path, font_size)
                if font_path
                else ImageFont.load_default()
            )
        except OSError:
            font = ImageFont.load_default()
        draw = ImageDraw.Draw(pil)
        try:
            left, top, right, bottom = draw.textbbox((0, 0), clean, font=font)
            tw, _th = right - left, bottom - top
        except Exception:  # noqa: BLE001 - 兜底到粗估尺寸
            tw, _th = font_size * len(clean), font_size
        x = (width - tw) // 2 - left
        y = max(8, int(height * 0.04)) - top
        draw.text((x + 2, y + 2), clean, fill=(120, 108, 84), font=font)
        draw.text((x, y), clean, fill=(40, 40, 40), font=font)
    pil.save(str(target), "PNG")
    return target
