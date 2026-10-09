"""[aipdd fork delta] 混剪短视频（``freezone_video_mix``）的纯逻辑原语。

参考 MoneyPrinterTurbo 的 local 素材流水线：素材按选择顺序切段、循环铺满配音
时长；图片转动态（Ken Burns 缩放）、视频裁段适配画布。本模块只放不碰 ffmpeg
的规划与文本逻辑（可单测），实际渲染在
``jobs.run_freezone_video_mix_render``（纯本地 ffmpeg，不出网、不取凭证）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

# 画布像素档位：(比例, 清晰度) -> (宽, 高)。
CANVAS_SIZES: dict[tuple[str, str], tuple[int, int]] = {
    ("9:16", "720p"): (720, 1280),
    ("9:16", "1080p"): (1080, 1920),
    ("16:9", "720p"): (1280, 720),
    ("16:9", "1080p"): (1920, 1080),
    ("1:1", "720p"): (720, 720),
    ("1:1", "1080p"): (1080, 1080),
}
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_RESOLUTION = "720p"
DEFAULT_SUBTITLE_FONT_SIZE = "medium"
# 成片统一帧率（与 compose/逐段渲染同口径）。
MIX_FPS = 30
# 循环填充的兜底上限：任何一次规划都不许超过这个片段数（防御死循环）。
MAX_MIX_CLIPS = 600
# 素材循环填充的兜底上限：成片最长秒数（配音更长的部分裁掉）。
MAX_MIX_DURATION_SECONDS = 600.0
# 视频素材的最短可用时长（秒）：低于此值无法切段，规划时跳过。
MIN_VIDEO_MATERIAL_SECONDS = 0.5
# 视频素材尾部残段低于此值时不单独成段（并回上一段，与 MPT 的 subclip 口径一致）。
MIN_VIDEO_TAIL_SECONDS = 0.2


def normalize_mix_aspect_ratio(value: str | None) -> str:
    """成片比例归一化；未知值回退竖屏 9:16（短视频默认）。"""
    clean = str(value or "").strip()
    for aspect in ("9:16", "16:9", "1:1"):
        if clean == aspect:
            return aspect
    return DEFAULT_ASPECT_RATIO


def normalize_mix_resolution(value: str | None) -> str:
    """清晰度档位归一化；未知值回退 720p。"""
    clean = str(value or "").strip().lower()
    return clean if clean in {"720p", "1080p"} else DEFAULT_RESOLUTION


def normalize_mix_subtitle_font_size(value: str | None) -> str:
    """字幕字号档位归一化；未知值回退 medium（原自动换算口径）。"""
    clean = str(value or "").strip().lower()
    return (
        clean if clean in {"small", "medium", "large"} else DEFAULT_SUBTITLE_FONT_SIZE
    )


def resolve_mix_canvas(
    aspect_ratio: str | None, resolution: str | None
) -> tuple[int, int]:
    """(比例, 清晰度) -> 画布像素 (宽, 高)。"""
    aspect = normalize_mix_aspect_ratio(aspect_ratio)
    tier = normalize_mix_resolution(resolution)
    return CANVAS_SIZES[(aspect, tier)]


# --------------------------------------------------------------------------- #
# 口播文案
# --------------------------------------------------------------------------- #


def build_mix_script_prompt(*, subject: str) -> str:
    """主题 -> LLM 口播文案 prompt（对齐 MPT generate_script 的产物口径）。"""
    clean_subject = str(subject or "").strip()
    return (
        "你是短视频口播文案编导。请以「"
        f"{clean_subject}"
        "」为主题写一段适合混剪短视频的口播文案。\n"
        "要求：\n"
        "- 直接输出口播正文，不要标题、说明、序号或 Markdown 标记；\n"
        "- 使用与主题一致的语言（主题为中文时用中文），全文 120-220 字；\n"
        "- 开头 1-2 句抛出钩子吸引停留，中段 2-3 个要点层层递进，"
        "结尾给一句自然的行动号召；\n"
        "- 句子短促、口语化、有节奏感，适合配音朗读；\n"
        "- 不要出现「大家好」「欢迎收看」这类套话。"
    )


# --------------------------------------------------------------------------- #
# 字幕（按文本比例估算时间轴）
# --------------------------------------------------------------------------- #

# 句末标点：切句后标点保留在句尾。
_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?；;…])")
# 次级断句标点：句子过长时按这些标点切开。
_CLAUSE_RE = re.compile(r"(?<=[，,、：:])")
# 单个字幕条目的权重上限（中文按 1、拉丁字符按 0.55 折算）。
CUE_MAX_WEIGHT = 18.0
# 字幕条目最短展示时长（毫秒）：低于此值并回相邻条目。
CUE_MIN_MS = 700


@dataclass(frozen=True)
class MixSubtitleCue:
    """一条字幕：毫秒时间轴 + 文本。"""

    start_ms: int
    end_ms: int
    text: str


def _cue_weight(text: str) -> float:
    """文本朗读权重：中日韩字符算 1，其余可见字符算 0.55。"""
    weight = 0.0
    for char in text:
        if char.isspace():
            continue
        weight += 1.0 if ord(char) > 0x2E80 else 0.55
    return weight


def _hard_split(text: str, max_weight: float) -> list[str]:
    """无标点长串的兜底硬切（按权重累计切分）。"""
    pieces: list[str] = []
    current: list[str] = []
    current_weight = 0.0
    for char in text:
        char_weight = 0.0 if char.isspace() else (1.0 if ord(char) > 0x2E80 else 0.55)
        if current and current_weight + char_weight > max_weight:
            pieces.append("".join(current))
            current = []
            current_weight = 0.0
        current.append(char)
        current_weight += char_weight
    if current:
        pieces.append("".join(current))
    return pieces


def _split_into_pieces(text: str) -> list[str]:
    """口播文本 -> 字幕条目文本序列（句末标点切句、逗号切分、超长硬切）。"""
    pieces: list[str] = []
    for sentence in _SENTENCE_END_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _cue_weight(sentence) <= CUE_MAX_WEIGHT:
            pieces.append(sentence)
            continue
        # 长句按逗号等次级标点切开后贪心合并。
        clauses = [part.strip() for part in _CLAUSE_RE.split(sentence) if part.strip()]
        buffer = ""
        for clause in clauses:
            candidate = f"{buffer}{clause}"
            if buffer and _cue_weight(candidate) > CUE_MAX_WEIGHT:
                pieces.extend(_hard_split(buffer, CUE_MAX_WEIGHT))
                buffer = clause
            else:
                buffer = candidate
        if buffer:
            pieces.extend(_hard_split(buffer, CUE_MAX_WEIGHT))
    return pieces


def build_mix_subtitle_cues(text: str, duration_ms: int) -> list[MixSubtitleCue]:
    """按文本权重比例把整段配音时长切给各字幕条目。

    当前 TTS 链路没有词级时间戳（``FreezoneAudioSpeechResult`` 只回
    ``duration_ms``），这里用与 MPT sentence 模式等价的近似：先按标点切条，
    再按朗读权重分摊时长；过短的条目并回前一条，保证可读性。
    """
    clean = " ".join(str(text or "").split())
    total_ms = int(duration_ms or 0)
    if not clean or total_ms <= 0:
        return []
    pieces = _split_into_pieces(clean)
    if not pieces:
        return []

    weights = [_cue_weight(piece) or 1.0 for piece in pieces]
    total_weight = sum(weights)
    cues: list[MixSubtitleCue] = []
    cursor = 0
    for index, (piece, weight) in enumerate(zip(pieces, weights)):
        if index == len(pieces) - 1:
            end = total_ms
        else:
            end = cursor + max(1, int(round(weight / total_weight * total_ms)))
        cues.append(MixSubtitleCue(cursor, end, piece))
        cursor = end

    # 过短条目并回上一条（首条并入后一条），直到全部达标或只剩一条。
    merged: list[MixSubtitleCue] = []
    for cue in cues:
        if merged and (cue.end_ms - cue.start_ms) < CUE_MIN_MS:
            previous = merged[-1]
            merged[-1] = MixSubtitleCue(
                previous.start_ms, cue.end_ms, f"{previous.text}{cue.text}"
            )
        else:
            merged.append(cue)
    while len(merged) > 1 and (merged[0].end_ms - merged[0].start_ms) < CUE_MIN_MS:
        first = merged.pop(0)
        second = merged[0]
        merged[0] = MixSubtitleCue(
            first.start_ms, second.end_ms, f"{first.text}{second.text}"
        )
    return merged


def _srt_timestamp(milliseconds: int) -> str:
    ms = max(0, int(milliseconds))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_srt(cues: Sequence[MixSubtitleCue]) -> str:
    """字幕条目 -> SRT 文本（``subtitles`` 滤镜直接烧录）。"""
    blocks = [
        f"{index}\n{_srt_timestamp(cue.start_ms)} --> {_srt_timestamp(cue.end_ms)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# --------------------------------------------------------------------------- #
# 素材切段规划（顺序循环铺满时长）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MixMaterial:
    """一个素材：图片或视频（视频带实测时长，图片时长为 0）。"""

    kind: str  # "image" | "video"
    path: str
    duration: float = 0.0


@dataclass(frozen=True)
class MixClipPlan:
    """一个待渲染片段：第几个素材、从哪开始、渲染多久、是否循环输入。"""

    material_index: int
    source_start: float
    duration: float
    loop: bool = False


def _usable_material_indices(materials: Sequence[MixMaterial]) -> list[int]:
    """可用素材在**原列表**中的下标（图片全收；过短的视频跳过）。

    返回原始下标而不是过滤后的子列表：渲染侧按原列表取路径，
    一旦这里重排，plan 的 ``material_index`` 就会指错素材。
    """
    return [
        index
        for index, item in enumerate(materials)
        if item.kind == "image"
        or (item.kind == "video" and item.duration >= MIN_VIDEO_MATERIAL_SECONDS)
    ]


def _material_segments(
    material: MixMaterial, clip_cap: float
) -> list[tuple[float, float]]:
    """一个素材按 ``clip_cap`` 切出的 (起点, 时长) 段序列（MPT subclip 口径）。"""
    if material.kind == "image":
        return [(0.0, clip_cap)]
    segments: list[tuple[float, float]] = []
    start = 0.0
    while start + MIN_VIDEO_TAIL_SECONDS < material.duration:
        duration = min(clip_cap, material.duration - start)
        segments.append((start, duration))
        start += duration
    return segments


def plan_mix_clips(
    materials: Sequence[MixMaterial],
    *,
    clip_cap: float,
    target_seconds: float | None,
) -> list[MixClipPlan]:
    """把素材池规划成按顺序循环的片段序列。

    ``target_seconds`` 给定（配音驱动）时，片段队列循环消费直到铺满目标时长；
    ``None``（静音单遍）时每个素材最多出一段。素材内部先按 ``clip_cap`` 切段、
    依次消费，消费完的素材回到开头进入下一轮循环。``loop`` 标记表示渲染该片段
    时素材长度不足、需要循环输入（只出现在尾部残差吸附超过视频剩余长度时）。
    """
    indices = _usable_material_indices(materials)
    if not indices:
        raise ValueError("no usable mix materials")

    cap = max(0.5, float(clip_cap))
    if target_seconds is None:
        plan: list[MixClipPlan] = []
        for source_index in indices:
            material = materials[source_index]
            if material.kind == "image":
                plan.append(MixClipPlan(source_index, 0.0, cap, False))
            else:
                plan.append(
                    MixClipPlan(source_index, 0.0, min(cap, material.duration), False)
                )
        return plan

    target = max(0.0, float(target_seconds))
    plan = []
    remaining = target
    material_index = 0
    segment_index = 0
    guard = 0
    while remaining > 0.05:
        guard += 1
        if guard > MAX_MIX_CLIPS:
            raise ValueError("mix plan exceeds the clip limit")
        position = material_index % len(indices)
        source_index = indices[position]
        material = materials[source_index]
        segments = _material_segments(material, cap)
        if not segments:  # 视频都至少一段，这里只可能是防御分支
            material_index += 1
            continue
        start, duration = segments[segment_index % len(segments)]
        if segment_index % len(segments) == len(segments) - 1:
            # 该素材最后一段：下一轮消费回到素材开头。
            material_index += 1
            segment_index = 0
        else:
            segment_index += 1
        # 目标剩余不足以铺满整段时直接少取（视频可裁、图片可任取）。
        take = min(duration, remaining)
        plan.append(MixClipPlan(source_index, start, take, False))
        remaining -= take

    # 浮点/最小残差吸附到最后一个片段，避免尾部缺一帧；视频被拉长到超过
    # 剩余素材长度时改走循环输入。
    if plan and remaining > 1e-6:
        last = plan[-1]
        material = materials[last.material_index]
        new_duration = last.duration + remaining
        loop = last.loop
        if (
            material.kind == "video"
            and last.source_start + new_duration > material.duration + 1e-3
        ):
            loop = True
        plan[-1] = MixClipPlan(
            last.material_index,
            last.source_start,
            new_duration,
            loop,
        )
    return plan
