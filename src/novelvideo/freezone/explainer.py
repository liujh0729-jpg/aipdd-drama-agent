# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 科普讲解视频（MG explainer）策划层：参考内容 → 解说词 → 分镜 → 时间轴。

这一层只做**可单测的纯计算与模型契约**，一个像素都不画（渲染在
``explainer_motion.py``）。口径：

- 用户交进来的是**参考资料**（一段主题说明、一篇文章、一份文档），网关文本模型
  把它改写成解说词，再按句子编号产出分镜；配音走网关音频模型；画面全部由本地
  代码逐帧绘制。不引入浏览器、不引入新的模型类型。
- **模型只决定「说什么、怎么分组、画哪种图元」**：字幕切块、句/镜头帧号、章起点
  一律本地按朗读权重确定性分配。交给模型写时间轴等于把整片对位交给幻觉。
- 图元与动效是**受限词汇**（``EXPLAINER_PRIMITIVES`` / ``EXPLAINER_MOTIONS``）：
  词汇外的值一律归一化到安全默认，渲染器因此永远拿得到它能画的规格。

与任何第三方 MG 讲解工程无代码关系：这里的图元词汇、参数、判据、prompt 表述都是
按本仓渲染能力自研的（思想层面的「先解说词、后分镜、画面跟着节拍」属于公开方法
论，不复制任何表达）。
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 词汇与规模上限
# --------------------------------------------------------------------------- #

# 渲染器能画的图元（超出即归一化为 term）。每种都是一个「画面单元」的画法，
# 不是一段代码——模型的输出空间被压到可校验、可渲染的范围内。
EXPLAINER_PRIMITIVES = frozenset(
    {
        "term",  # 核心术语大字登场
        "flow",  # 横向流程：卡片 + 箭头
        "compare",  # 左右两栏对比
        "bars",  # 柱状对比
        "bignumber",  # 单个大数字
        "list",  # 要点清单
        "cycle",  # 环形循环
        "timeline",  # 时间轴
    }
)
# 撑到下一拍的持续动作（超出即归一化为 softin）。
EXPLAINER_MOTIONS = frozenset(
    {"softin", "typewriter", "lightup", "travel", "countup"}
)
DEFAULT_PRIMITIVE = "term"
DEFAULT_MOTION = "softin"

EXPLAINER_FPS = 30
MAX_CHAPTERS = 4
MIN_CHAPTERS = 1
MAX_SHOTS = 24
MAX_ITEMS = 6
MAX_NUMBERS = 6
# 单镜头帧数下限（4 秒）：短于它说明「一句一镜」的碎片化切分，必须并掉。
MIN_SHOT_FRAMES = 120
# 末拍落位后的稳定期下限：入场 + 稳定 + 离场之外的余量，静止不算完成。
MIN_STEADY_FRAMES = 30
ENTRY_FRAMES = 12
EXIT_FRAMES = 8
# 一条字幕块的最短停留（帧）。
MIN_CUE_FRAMES = 21
# 末块的最短停留：入场 12 + 落位稳定 30 + 离场 8——镜头的最后一拍必须停得住，
# 否则画面会「落位即切」（参考方法论里明确的缺陷，这里做成硬约束而不是事后 QC）。
LAST_CUE_MIN_FRAMES = ENTRY_FRAMES + MIN_STEADY_FRAMES + EXIT_FRAMES

HEADLINE_MAX_CHARS = 14
ITEM_MAX_CHARS = 10
CHAPTER_NAME_MAX_CHARS = 8
# 字幕块权重上限（中文按 1、拉丁按 0.55 折算）：与混剪字幕的 CUE_MAX_WEIGHT 同口径，
# 18 个汉字在 1280 宽、44px 字幕带里单行放得下（≈792px），不会折进行内容区。
BLOCK_MAX_WEIGHT = 18.0

# 成片时长区间（秒）与默认档。
MIN_DURATION_S = 90
MAX_DURATION_S = 330
DEFAULT_DURATION_S = 180
# 朗读密度：中文 TTS 原速约 5.2 字/秒，再留 20% 给停留与章节留白。
NARRATION_CHARS_PER_SECOND = 4.2
# 参考内容长度上限（防御：超长资料先截断，不静默吞）。
MAX_REFERENCE_CHARS = 12_000

_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?；;…])")
_CLAUSE_RE = re.compile(r"(?<=[，,、：:])")
_CHAPTER_LINE_RE = re.compile(r"^\s*#{1,6}\s*CHAPTER\s*(\d+)\s*(.*)$", re.I)


# --------------------------------------------------------------------------- #
# 归一化（面板/网关传进来的松散的标量都过一遍）
# --------------------------------------------------------------------------- #


def normalize_explainer_background(value: object) -> str:
    """幕底归一化：``dots`` 点阵波（默认）/ ``stars`` 星点。"""
    clean = str(value or "").strip().lower()
    return clean if clean in {"dots", "stars"} else "dots"


def normalize_explainer_duration_s(value: object, *, min_s: int | None = None) -> int:
    """目标时长归一化并夹到 [min_s, 330] 秒；非法值回默认档。

    [aipdd fork delta] ``min_s`` 让渠道短视频把下限压到比讲解片（90s）更短；
    留空即沿用讲解片的 ``MIN_DURATION_S``，既有调用方行为逐字不变。
    """
    floor = MIN_DURATION_S if min_s is None else max(1, int(min_s))
    try:
        seconds = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_DURATION_S
    if seconds <= 0:
        return DEFAULT_DURATION_S
    return max(floor, min(MAX_DURATION_S, seconds))


def narration_char_budget(duration_s: object, *, min_s: int | None = None) -> int:
    """目标时长 -> 解说词字数预算（含停留预算，不是净朗读字数）。"""
    seconds = normalize_explainer_duration_s(duration_s, min_s=min_s)
    return int(round(seconds * NARRATION_CHARS_PER_SECOND))


def chapter_count_hint(duration_s: object) -> int:
    """目标时长 -> 建议章数：短片不分章，长片最多 4 章。"""
    seconds = normalize_explainer_duration_s(duration_s)
    if seconds < 170:
        return 1
    if seconds < 250:
        return 2
    return 3


def resolve_reference_content(value: object) -> str:
    """参考内容清洗与长度闸门（路由层据此回 400，不静默截断）。"""
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("reference content is required")
    if len(clean) > MAX_REFERENCE_CHARS:
        raise ValueError(f"reference content must be <= {MAX_REFERENCE_CHARS} characters")
    return clean


# --------------------------------------------------------------------------- #
# Prompt（文本叶的两个调用点：解说词 / 分镜）
# --------------------------------------------------------------------------- #


def build_explainer_narration_prompt(
    reference_content: str,
    *,
    duration_s: object = DEFAULT_DURATION_S,
    title_hint: str = "",
    min_s: int | None = None,
    orientation_label: str = "横屏 MG 科普讲解视频",
) -> str:
    """参考资料 -> 解说词 prompt（分章标记随文本一起产出）。

    [aipdd fork delta] ``min_s`` / ``orientation_label`` 供渠道短视频复用本链：
    把时长下限压到比讲解片更短，并按渠道画幅改写片种提示；留空即沿用讲解片原样。
    """
    seconds = normalize_explainer_duration_s(duration_s, min_s=min_s)
    budget = narration_char_budget(seconds, min_s=min_s)
    low = int(budget * 0.85)
    high = int(budget * 1.15)
    chapters = chapter_count_hint(seconds)
    if chapters == 1:
        chapter_rule = "- 全片不分章：只输出一章（# CHAPTER 1 开头的总标题可以不写）。"
    else:
        chapter_rule = (
            f"- 分 {chapters} 章，每章 6 句以上；章名是进度条上的导航标签："
            "≤6 个汉字的名词短语，写清这章讲什么，不要用比喻、悬念或评价句。"
        )
    hint_line = (
        f"【片名提示】{str(title_hint or '').strip()}\n" if str(title_hint or "").strip() else ""
    )
    return (
        f"你是{orientation_label}的编导。请把下面的参考资料改写成一条讲解视频的解说词。\n"
        f"【目标成片时长】约 {seconds} 秒（按 {NARRATION_CHARS_PER_SECOND} 字/秒的成片密度计，"
        f"正文总字数请落在 {low}-{high} 字）\n"
        "【输出格式】严格如下，不要 Markdown 代码块、不要说明文字、不要序号：\n"
        "# CHAPTER 1 章名\n"
        "第一句。\n"
        "第二句。\n"
        "\n"
        "# CHAPTER 2 章名\n"
        "第若干句。\n"
        "（章与章之间空一行；每句独占一行，句末保留标点。）\n"
        "【硬性要求】\n"
        f"{chapter_rule}\n"
        "- 只用参考资料里出现过的事实，不要补任何资料里没有的数字、年份、人名、机构或结论；\n"
        "- 第一句从观众真实遇到的具体问题切入，禁止「大家好」「今天我们来聊聊」；\n"
        "- 全片用一个贯穿的例子（同一场景、同一对象），不要中途换语境；\n"
        "- 先讲为什么会出问题，再讲机制，最后讲边界与代价；术语先用大白话讲透，再给原名；\n"
        "- 抽象数字换算成能感知的尺度，比喻要能承重（后面的画面会直接用这个比喻）；\n"
        f"- 每句 ≤34 字，口语顺读；共 {chapters} 章时章末留一句钩子，"
        "下一章首句先回指上一章的结论再开题，不要「接下来我们看」这类只报幕的过渡；\n"
        "- 结尾回扣开头的那个问题。\n"
        f"{hint_line}"
        "【参考资料】\n"
        f"{reference_content.strip()}\n"
    )


def build_explainer_storyboard_prompt(
    numbered_sentences: list[tuple[int, str]],
    *,
    title_hint: str = "",
) -> str:
    """带编号的句子 -> 分镜 prompt（模型只能选词汇表内的图元与动效）。"""
    lines = "\n".join(f"{index}. {text}" for index, text in numbered_sentences)
    hint_line = (
        f"【片名提示】{str(title_hint or '').strip()}\n" if str(title_hint or "").strip() else ""
    )
    return (
        "你是 MG 讲解片的分镜师。下面是一条解说词，每句都带全局编号。"
        "请按「画面单元」而不是按句切镜头：连续 2-4 句共用一个镜头，"
        "并为每个镜头指定一种图元。\n"
        "【可选图元】只能取以下值：\n"
        "- term：核心术语大字登场（headline 放术语，items 给 1-2 条注解）\n"
        "- flow：横向流程（items 为 3-6 个步骤名，逐格点亮）\n"
        "- compare：左右两栏对比（items 为 2-4 组「A|B」，竖线分隔两侧）\n"
        "- bars：柱状对比（items 是标签，numbers 是同长度的数值字符串）\n"
        "- bignumber：单个大数字（numbers[0] 是数字，headline 是它说明的事）\n"
        "- list：要点清单（items 为 2-6 行）\n"
        "- cycle：环形循环（items 为 3-6 个节点）\n"
        "- timeline：时间轴（items 为 2-6 个阶段名或年份）\n"
        "【可选持续动作】只能取：softin / typewriter / lightup / travel / countup\n"
        "【硬性约束】\n"
        "- 每个镜头用它的**首句编号**（sentence，从 1 开始，第一个镜头必须是 1）；"
        "镜头自动延伸到下一个镜头之前，编号必须严格递增；\n"
        "- 一个镜头至少 4 秒的内容量（不足 2 句就并到相邻镜头）；\n"
        "- items 每项 ≤10 字，只能用解说词里出现过的词；headline ≤14 字；\n"
        "- 一个镜头最多一处强调：emphasis 是 items 的下标（从 0 开始），没有就写 -1；\n"
        "- 有先后顺序用 flow / timeline / cycle，有对照用 compare / bars，"
        "只有概念用 term / list；数字滚动只在 bignumber、bars 上用 countup。\n"
        "【输出格式】只输出一个 JSON 对象，不要代码块、不要任何解释：\n"
        '{"title": {"big": "缩写或英文词", "rest": "中文片名", '
        '"en": "英文全称或留空", "tagline": "一句话钩子"},\n'
        ' "shots": [{"sentence": 1, "primitive": "flow", "headline": "", '
        '"items": ["", ""], "numbers": [], "emphasis": 0, "motion": "lightup"}]}\n'
        f"{hint_line}"
        "【解说词】\n"
        f"{lines}\n"
    )


# --------------------------------------------------------------------------- #
# 模型产物的容错解析（与 idea/whiteboard 同样的松散输出假设）
# --------------------------------------------------------------------------- #


def _strip_code_fence(text: str) -> str:
    """剥掉 ```json ... ``` 包裹（模型常见习惯，不影响正常文本）。"""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().endswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _json_candidate(text: str) -> str | None:
    """从松散文本里取第一个平衡的 JSON 对象片段。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _load_json_object(raw_text: object) -> dict[str, Any]:
    """松散模型文本 -> JSON 对象；解析失败返回空 dict（调用方走兜底）。"""
    text = _strip_code_fence(str(raw_text or ""))
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        candidate = _json_candidate(text)
        if not candidate:
            return {}
        try:
            loaded = json.loads(candidate)
        except (TypeError, ValueError):
            return {}
    return loaded if isinstance(loaded, dict) else {}


def _text_weight(text: str) -> float:
    """朗读权重：中日韩字符算 1，其余可见字符算 0.55（与混剪字幕同一口径）。"""
    weight = 0.0
    for char in text:
        if char.isspace():
            continue
        weight += 1.0 if ord(char) > 0x2E80 else 0.55
    return weight


def _clip(text: object, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)] + "…"


def _hard_wrap(text: str, limit: float) -> list[str]:
    """无标点长串的兜底切分（按累计权重切，不切进词里）。"""
    pieces: list[str] = []
    buffer: list[str] = []
    current = 0.0
    for char in text:
        weight = 0.0 if char.isspace() else (1.0 if ord(char) > 0x2E80 else 0.55)
        if buffer and current + weight > limit:
            pieces.append("".join(buffer).strip())
            buffer = []
            current = 0.0
        buffer.append(char)
        current += weight
    if buffer:
        pieces.append("".join(buffer).strip())
    return [piece for piece in pieces if piece]


def split_subtitle_blocks(sentence: str) -> list[str]:
    """一句解说词 -> 字幕块序列（先按次级标点贪心合并，超预算再硬切）。

    块长度是**渲染契约**：一块只在一行字幕带里显示，超预算就会压进内容区，
    所以这里宁可不漂亮也要保证不折行。
    """
    clean = " ".join(str(sentence or "").split())
    if not clean:
        return []
    if _text_weight(clean) <= BLOCK_MAX_WEIGHT:
        return [clean]
    clauses = [part.strip() for part in _CLAUSE_RE.split(clean) if part.strip()]
    blocks: list[str] = []
    buffer = ""
    for clause in clauses:
        candidate = f"{buffer}{clause}" if buffer else clause
        if buffer and _text_weight(candidate) > BLOCK_MAX_WEIGHT:
            blocks.extend(_hard_wrap(buffer, BLOCK_MAX_WEIGHT))
            buffer = clause
        else:
            buffer = candidate
    if buffer:
        blocks.extend(_hard_wrap(buffer, BLOCK_MAX_WEIGHT))
    return blocks


def parse_explainer_narration(raw_text: object) -> list[ExplainerChapter]:
    """解说词文本 -> 章节列表（章名/句子清洗、章数与总量闸门）。"""
    text = str(raw_text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    chapters: list[ExplainerChapter] = []
    current_name = ""
    current_lines: list[str] = []

    def _flush() -> None:
        if not current_lines:
            return
        sentences = tuple(
            part.strip()
            for line in current_lines
            for part in _SENTENCE_END_RE.split(line.strip())
            if part and part.strip()
        )
        if not sentences:
            return
        chapters.append(
            ExplainerChapter(
                name=_clip(current_name or f"第{len(chapters) + 1}节", CHAPTER_NAME_MAX_CHARS),
                sentences=sentences,
            )
        )

    for line in text.split("\n"):
        matched = _CHAPTER_LINE_RE.match(line)
        if matched:
            _flush()
            current_lines = []
            current_name = matched.group(2).strip()
            continue
        clean = line.strip()
        if not clean:
            continue
        if re.match(r"^#{1,6}\s", clean):
            # 模型自己加的中文小节标题：当章名，忽略编号。
            _flush()
            current_lines = []
            current_name = re.sub(r"^#{1,6}\s*", "", clean).strip()
            continue
        current_lines.append(clean)
    _flush()

    if not chapters:
        return []
    # 章数闸门：超过上限的章并回前一章（句子顺序不变）。
    while len(chapters) > MAX_CHAPTERS:
        last = chapters.pop()
        previous = chapters[-1]
        chapters[-1] = ExplainerChapter(
            name=previous.name, sentences=(*previous.sentences, *last.sentences)
        )
    return chapters


@dataclass(frozen=True, slots=True)
class ExplainerChapter:
    """一章：导航标签 + 该章的句子序列（顺序即成片顺序）。"""

    name: str
    sentences: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplainerTitle:
    """片头卡：宽体词 + 中文片名 + 英文全称 + 一句话钩子。"""

    big: str = ""
    rest: str = ""
    en: str = ""
    tagline: str = ""


@dataclass(frozen=True, slots=True)
class ExplainerShot:
    """一个画面单元：覆盖连续若干句，指定图元与动效。"""

    chapter_index: int
    sentence_start: int  # 全局句子编号，1 起
    sentence_end: int
    sentences: tuple[str, ...]
    primitive: str = DEFAULT_PRIMITIVE
    headline: str = ""
    items: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    emphasis: int = -1
    motion: str = DEFAULT_MOTION


def flatten_sentences(chapters: list[ExplainerChapter]) -> list[tuple[int, int, str]]:
    """章节 -> 全局句子序列 [(章下标, 编号, 文本)]，编号从 1 起。"""
    flat: list[tuple[int, int, str]] = []
    index = 0
    for chapter_index, chapter in enumerate(chapters):
        for sentence in chapter.sentences:
            index += 1
            flat.append((chapter_index, index, sentence))
    return flat


def _fallback_shots(flat: list[tuple[int, int, str]]) -> list[dict[str, Any]]:
    """分镜缺失时的确定性兜底：每 3 句一镜，图元按内容形状轮转。"""
    shots: list[dict[str, Any]] = []
    rotation = ["term", "list", "flow"]
    for offset in range(0, len(flat), 3):
        start = flat[offset][1]
        shots.append(
            {
                "sentence": start,
                "primitive": rotation[(offset // 3) % len(rotation)],
                "headline": "",
                "items": [],
                "numbers": [],
                "emphasis": -1,
                "motion": "softin",
            }
        )
    return shots


def parse_explainer_storyboard(
    raw_text: object,
    *,
    chapters: list[ExplainerChapter],
) -> tuple[ExplainerTitle, list[ExplainerShot]]:
    """分镜 JSON -> 标题 + 镜头序列（词汇归一、编号纠偏、上限截断）。"""
    flat = flatten_sentences(chapters)
    if not flat:
        return ExplainerTitle(), []
    total = len(flat)
    loaded = _load_json_object(raw_text)

    title_payload = loaded.get("title") if isinstance(loaded.get("title"), dict) else {}
    title = ExplainerTitle(
        big=_clip(title_payload.get("big"), 24),
        rest=_clip(title_payload.get("rest"), 20),
        en=_clip(title_payload.get("en"), 48),
        tagline=_clip(title_payload.get("tagline"), 28),
    )

    raw_shots = loaded.get("shots")
    entries = [item for item in raw_shots if isinstance(item, dict)] if isinstance(raw_shots, list) else []
    if not entries:
        # 分镜失败不致命：画面退化为兜底图元序列，成片照样出来（与白板同口径）。
        logger.warning("explainer storyboard empty; falling back to deterministic grouping")
        entries = _fallback_shots(flat)

    def _entry_start(entry: dict[str, Any]) -> int:
        try:
            return int(entry.get("sentence"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    # 模型可能乱序返回：先按编号排序，再单调化（重复/越界的条目丢弃）。
    entries = sorted(entries, key=_entry_start)
    normalized: list[dict[str, Any]] = []
    cursor = 0
    for entry in entries:
        start = _entry_start(entry)
        if start < 1:
            start = 1
        if start <= cursor:
            continue  # 编号不递增的条目一律丢弃（否则镜头区间会互相覆盖）
        cursor = start
        entry = {**entry, "sentence": start}
        normalized.append(entry)
        if len(normalized) >= MAX_SHOTS:
            break
    if not normalized:
        normalized = _fallback_shots(flat)

    # 镜头自动延伸到下一个镜头之前：缺口由前一个镜头吸收，保证句子全覆盖。
    starts = [int(entry["sentence"]) for entry in normalized]
    starts = [max(1, min(total, start)) for start in starts]
    if starts[0] != 1:
        starts = [1, *starts]
        normalized = [{"sentence": 1, "primitive": DEFAULT_PRIMITIVE}, *normalized]
    if len(starts) > MAX_SHOTS:
        starts = starts[:MAX_SHOTS]
        normalized = normalized[:MAX_SHOTS]

    shot_index_by_sentence = {index: chapter_index for chapter_index, index, _ in flat}
    sentence_text = {index: text for _, index, text in flat}

    shots: list[ExplainerShot] = []
    for position, entry in enumerate(normalized):
        start = starts[position]
        end = starts[position + 1] - 1 if position + 1 < len(starts) else total
        if end < start:
            continue
        primitive = str(entry.get("primitive") or "").strip().lower()
        if primitive not in EXPLAINER_PRIMITIVES:
            primitive = DEFAULT_PRIMITIVE
        motion = str(entry.get("motion") or "").strip().lower()
        if motion not in EXPLAINER_MOTIONS:
            motion = DEFAULT_MOTION
        items_raw = entry.get("items")
        items = tuple(
            _clip(item, ITEM_MAX_CHARS)
            for item in (items_raw if isinstance(items_raw, list) else [])[:MAX_ITEMS]
            if _clip(item, ITEM_MAX_CHARS)
        )
        numbers_raw = entry.get("numbers")
        numbers = tuple(
            _clip(number, 12)
            for number in (numbers_raw if isinstance(numbers_raw, list) else [])[:MAX_NUMBERS]
            if _clip(number, 12)
        )
        try:
            emphasis = int(entry.get("emphasis"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            emphasis = -1
        if emphasis is not None and (emphasis < 0 or emphasis >= len(items)):
            emphasis = -1
        shots.append(
            ExplainerShot(
                chapter_index=shot_index_by_sentence.get(start, 0),
                sentence_start=start,
                sentence_end=end,
                sentences=tuple(
                    sentence_text[index]
                    for index in range(start, min(end, total) + 1)
                    if index in sentence_text
                ),
                primitive=primitive,
                headline=_clip(entry.get("headline"), HEADLINE_MAX_CHARS),
                items=items,
                numbers=numbers,
                emphasis=emphasis,
                motion=motion,
            )
        )
    shots = [
        _backfill_shot_payload(
            shot,
            chapters[shot.chapter_index].name if shot.chapter_index < len(chapters) else "",
        )
        for shot in shots
    ]
    return title, shots


# 需要负载（items）才能成立的图元；缺负载时从解说词自己补，渲染器因此永远不会
# 拿到一个「画不出来」的镜头。
_PAYLOAD_PRIMITIVES = frozenset({"flow", "compare", "bars", "list", "cycle", "timeline"})
_PAYLOAD_ITEM_COUNT = {"flow": 4, "compare": 3, "bars": 4, "list": 4, "cycle": 4, "timeline": 3}


def _first_clause(text: str) -> str:
    """取一句的首个短句（没有标点就整句），作为卡片/条目的短标签。"""
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    for separator in ("，", "、", "：", "；", ",", ":", ";", "。"):
        head, _, _tail = clean.partition(separator)
        if head.strip():
            return head.strip()
    return clean


def _backfill_shot_payload(shot: ExplainerShot, chapter_name: str) -> ExplainerShot:
    """给缺负载的镜头从解说词补 items/headline；补不出图元就降级成 term。

    降级是有意的：模型偶尔会给出「flow 但一个步骤都没有」这种规格，与其渲染出
    一个空流程，不如退回「术语大字 + 注解」，画面仍然成立。
    """
    items = shot.items
    headline = shot.headline
    primitive = shot.primitive
    if primitive in _PAYLOAD_PRIMITIVES and not items:
        derived: list[str] = []
        for sentence in shot.sentences:
            phrase = _clip(_first_clause(sentence), ITEM_MAX_CHARS)
            if phrase and phrase not in derived:
                derived.append(phrase)
            if len(derived) >= _PAYLOAD_ITEM_COUNT.get(primitive, 4):
                break
        if len(derived) >= 2:
            items = tuple(derived)
        else:
            primitive = DEFAULT_PRIMITIVE
    if not headline:
        leading = _first_clause(shot.sentences[0]) if shot.sentences else chapter_name
        headline = _clip(leading, HEADLINE_MAX_CHARS) or _clip(chapter_name, HEADLINE_MAX_CHARS)
    emphasis = shot.emphasis if 0 <= shot.emphasis < len(items) else (-1 if not items else 0)
    if (
        shot.primitive == primitive
        and shot.items == items
        and shot.headline == headline
        and shot.emphasis == emphasis
    ):
        return shot
    return ExplainerShot(
        chapter_index=shot.chapter_index,
        sentence_start=shot.sentence_start,
        sentence_end=shot.sentence_end,
        sentences=shot.sentences,
        primitive=primitive,
        headline=headline,
        items=items,
        numbers=shot.numbers,
        emphasis=emphasis,
        motion=shot.motion,
    )


# --------------------------------------------------------------------------- #
# 时间轴（纯函数）：句权重 -> 句帧 -> 镜头帧（含短镜头并框）-> 字幕块帧
# --------------------------------------------------------------------------- #


def _allocate_frames(weights: list[float], total: int, minimum: int = 0) -> list[int]:
    """最大余数法把 ``total`` 帧按权重分配，保证求和逐帧精确。

    下限不可行时（total 太小）自动放弃下限而不是溢出总时长——时长由配音决定，
    任何分配都不许改动它。
    """
    count = len(weights)
    if count == 0:
        return []
    if total <= 0:
        return [0] * count
    if minimum * count > total:
        minimum = 0
    base = [minimum] * count
    rest = total - minimum * count
    weight_sum = sum(value for value in weights if value > 0) or float(count)
    safe = [value if value > 0 else 0.0 for value in weights]
    shares = [rest * (value / weight_sum) for value in safe]
    floors = [int(math.floor(value)) for value in shares]
    leftover = rest - sum(floors)
    order = sorted(range(count), key=lambda index: shares[index] - floors[index], reverse=True)
    for slot in range(max(0, leftover)):
        floors[order[slot % count]] += 1
    return [base_value + floor for base_value, floor in zip(base, floors)]


@dataclass(frozen=True, slots=True)
class ExplainerCue:
    """一条字幕块：帧区间 + 文本（渲染层按帧区间绘制字幕带）。"""

    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True, slots=True)
class ExplainerScene:
    """渲染用的镜头：帧区间 + 入场/离场预算 + 该镜头内的字幕块。"""

    shot: ExplainerShot
    start_frame: int
    end_frame: int
    cues: tuple[ExplainerCue, ...]

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def steady_frames(self) -> int:
        """落位后的稳定期（不含入场与离场）。"""
        return max(0, self.duration_frames - ENTRY_FRAMES - EXIT_FRAMES)


@dataclass(frozen=True, slots=True)
class ExplainerTimeline:
    """整片时间轴：镜头序列 + 章起点 + 章名 + 片头预留。"""

    fps: int
    total_frames: int
    scenes: tuple[ExplainerScene, ...]
    chapter_starts: tuple[int, ...]
    # 章名（HUD 与章节卡要念得出「这一节叫什么」，只靠章号会丢掉信息）。
    chapter_names: tuple[str, ...] = ()
    title: ExplainerTitle = ExplainerTitle()

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / float(self.fps or 1)


def plan_explainer_timeline(
    *,
    chapters: list[ExplainerChapter],
    shots: list[ExplainerShot],
    title: ExplainerTitle | None = None,
    duration_ms: int,
    fps: int = EXPLAINER_FPS,
) -> ExplainerTimeline:
    """把配音时长落到镜头与字幕块上（全部按朗读权重，模型不参与对位）。

    三步：句按权重分帧 → 镜头区间求和并把不足 4 秒的短镜头并掉 → 句内字幕块
    按权重分帧（低于最短停留的并回前一块）。
    """
    resolved_fps = max(1, int(fps or EXPLAINER_FPS))
    total_frames = max(resolved_fps, int(round(max(0, int(duration_ms or 0)) / 1000.0 * resolved_fps)))
    flat = flatten_sentences(chapters)
    title = title or ExplainerTitle()
    if not flat or not shots:
        return ExplainerTimeline(
            fps=resolved_fps,
            total_frames=total_frames,
            scenes=(),
            chapter_starts=(0,),
            chapter_names=tuple(chapter.name for chapter in chapters),
            title=title,
        )

    weights = [_text_weight(text) or 1.0 for _, _, text in flat]
    sentence_frames = _allocate_frames(weights, total_frames)
    frame_by_sentence = {index: sentence_frames[position] for position, (_, index, _) in enumerate(flat)}
    text_by_sentence = {index: text for position, (_, index, text) in enumerate(flat)}

    # 1) 镜头帧区间 = 覆盖句的帧和。
    scenes: list[list[Any]] = []
    for shot in shots:
        start_frame = sum(
            frame_by_sentence.get(index, 0) for index in range(shot.sentence_start, shot.sentence_end + 1)
        )
        scenes.append([shot, start_frame])
    # 首镜头起点从 0 开始，其余依次衔接。
    cursor = 0
    positioned: list[tuple[ExplainerShot, int, int]] = []
    for shot, span in scenes:
        span = max(1, int(span))
        positioned.append((shot, cursor, cursor + span))
        cursor += span
    # 校正末镜头到 total_frames（取整误差）。
    if positioned:
        last_shot, last_start, _ = positioned[-1]
        positioned[-1] = (last_shot, last_start, total_frames)

    # 2) 短镜头并框：不足 MIN_SHOT_FRAMES 就并回同章前一个镜头，首镜头则并给后一个。
    changed = True
    while changed and len(positioned) > 1:
        changed = False
        for index in range(len(positioned)):
            shot, start, end = positioned[index]
            if end - start >= MIN_SHOT_FRAMES:
                continue
            if index == 0:
                next_shot, next_start, next_end = positioned[1]
                merged = _merge_shot(shot, next_shot)
                positioned[1] = (merged, start, next_end)
            else:
                previous_shot, previous_start, _ = positioned[index - 1]
                merged = _merge_shot(previous_shot, shot)
                positioned[index - 1] = (merged, previous_start, end)
            positioned.pop(index)
            changed = True
            break

    # 3) 镜头内字幕块。
    built: list[ExplainerScene] = []
    for shot, start, end in positioned:
        cues: list[ExplainerCue] = []
        blocks: list[str] = []
        block_weights: list[float] = []
        for index in range(shot.sentence_start, shot.sentence_end + 1):
            sentence = text_by_sentence.get(index, "")
            for block in split_subtitle_blocks(sentence) or ([sentence] if sentence else []):
                blocks.append(block)
                block_weights.append(_text_weight(block) or 1.0)
        if blocks:
            spans = _ensure_last_cue_settle(
                _allocate_frames(block_weights, end - start, MIN_CUE_FRAMES)
            )
            cursor = start
            for block, span in zip(blocks, spans):
                cue_end = min(end, cursor + max(1, span))
                if cues and cue_end - cursor < MIN_CUE_FRAMES and cursor - start > 0:
                    # 已保底仍过短：并回上一条（末条吃掉镜头余量）。
                    previous = cues[-1]
                    cues[-1] = ExplainerCue(previous.start_frame, cue_end, f"{previous.text}{block}")
                else:
                    cues.append(ExplainerCue(cursor, cue_end, block))
                cursor = cue_end
            if cues:
                cues[-1] = ExplainerCue(cues[-1].start_frame, end, cues[-1].text)
        built.append(ExplainerScene(shot=shot, start_frame=start, end_frame=end, cues=tuple(cues)))

    chapter_starts: list[int] = []
    for chapter_index in range(len(chapters)):
        for scene in built:
            if scene.shot.chapter_index == chapter_index:
                chapter_starts.append(scene.start_frame)
                break
    if not chapter_starts:
        chapter_starts = [0]
    return ExplainerTimeline(
        fps=resolved_fps,
        total_frames=total_frames,
        scenes=tuple(built),
        chapter_starts=tuple(chapter_starts),
        chapter_names=tuple(chapter.name for chapter in chapters),
        title=title,
    )


def _ensure_last_cue_settle(spans: list[int]) -> list[int]:
    """把末块往前“抽”足至少一拍的稳定期（末块 ≥ LAST_CUE_MIN_FRAMES）。

    只有字幕块层面能保证这个约束：镜头总长已被 MIN_SHOT_FRAMES 守住，但块按权重
    分发后末块可能只拿到 21 帧——落位后还没停够就离场→切镜头。不够就从前一块逐块
    抽帧（保持各自 ≥ MIN_CUE_FRAMES）；实在抽不出时保持原样，不反总时长。
    """
    if len(spans) < 2:
        return spans
    deficit = LAST_CUE_MIN_FRAMES - spans[-1]
    if deficit <= 0:
        return spans
    for index in range(len(spans) - 2, -1, -1):
        if deficit <= 0:
            break
        spare = spans[index] - MIN_CUE_FRAMES
        if spare <= 0:
            continue
        take = min(spare, deficit)
        spans[index] -= take
        spans[-1] += take
        deficit -= take
    return spans


def _merge_shot(first: ExplainerShot, second: ExplainerShot) -> ExplainerShot:
    """并框：保留前一个镜头的图元与动效，句区间与文本取并。

    图元负载（items/numbers/headline）取前一个镜头——它是这一段画面的主体；
    前一个镜头没有负载才借用后一个的，同时把 emphasis 重新校验到新负载的下标内。
    """
    items = first.items or second.items
    numbers = first.numbers or second.numbers
    headline = first.headline or second.headline
    sentences = tuple(dict.fromkeys((*first.sentences, *second.sentences)))
    emphasis = first.emphasis if first.items else second.emphasis
    if not isinstance(emphasis, int) or emphasis < 0 or emphasis >= len(items):
        emphasis = -1
    return ExplainerShot(
        chapter_index=first.chapter_index,
        sentence_start=first.sentence_start,
        sentence_end=second.sentence_end,
        sentences=sentences,
        primitive=first.primitive,
        headline=headline,
        items=items[:MAX_ITEMS],
        numbers=numbers[:MAX_NUMBERS],
        emphasis=emphasis,
        motion=first.motion if first.motion != DEFAULT_MOTION else second.motion,
    )
