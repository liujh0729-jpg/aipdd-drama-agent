# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 讲解类视频「台词草稿」：两段式出片的中间产物与校验。

为什么要有这一层：科普讲解视频与白板动画原本都是一次提交跑到底——文本、配音、
整片渲染全在一条任务里。台词里只要有一句不顺，用户就只能整片重跑，重跑的时间与
模型费用都花在「已经确认没问题的九成内容」上。草稿把文本阶段单独拎出来：先出逐镜
台词与图元，用户在面板里改完，再配音渲染。改稿因此不再等于重跑。

口径：

- 草稿是**纯数据**。渲染链不知道自己吃的是模型现写的还是用户改过的——锁定稿先经
  这里的校验重建，再交给与原路径完全相同的 TTS / 时间轴 / 绘制。
- 人（和模型）只决定「哪几句一镜、画什么图元」。句子编号、章边界、帧号一律重算：
  交给编辑框去维护编号，等于把时间轴交给手抖。
- 图元 / 动效 / 长度上限复用 ``explainer.py`` 的受限词汇，这里不另立第二份白名单，
  否则渲染器迟早会收到它画不出来的规格。
- 锁定稿不合法时**不静默回退模型重写**：那等于把用户改过的稿子偷偷换掉。宁可让成片
  任务失败，并把哪一项不合法说清楚。

白板动画的草稿形态不同（逐幕文案 + 重点字 + 线稿描述），但同样是「数据在这里校验、
渲染链只认最终形态」，所以两族草稿共用本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novelvideo.freezone.explainer import (
    DEFAULT_DURATION_S,
    DEFAULT_MOTION,
    DEFAULT_PRIMITIVE,
    EXPLAINER_MOTIONS,
    EXPLAINER_PRIMITIVES,
    HEADLINE_MAX_CHARS,
    ITEM_MAX_CHARS,
    MAX_CHAPTERS,
    MAX_ITEMS,
    MAX_NUMBERS,
    MAX_SHOTS,
    NARRATION_CHARS_PER_SECOND,
    ExplainerChapter,
    ExplainerShot,
    ExplainerTitle,
    _backfill_shot_payload,
    _clip,
    _text_weight,
)
from novelvideo.freezone.whiteboard import WHITEBOARD_MAX_SCENES, _KEY_TEXT_MAX_CHARS

# 草稿版本：面板按它判断「这份草稿是不是我能编辑的形态」。升版只加字段不改语义，
# 旧草稿因此仍能提交；真正改语义时才递增，让老草稿明确报「重新生成」而不是错渲。
SCRIPT_DRAFT_VERSION = 1

EXPLAINER_DRAFT_KIND = "explainer"
WHITEBOARD_DRAFT_KIND = "whiteboard"

# 台词总量上限（字）。只用来挡「把一整篇文章粘进编辑框」这类事故，不参与正常裁剪。
# 与白板/混剪成片入口对「口播文案」的 10000 字闸门取齐：入口放行的文案，用户在草稿里
# 逐幕改完后，锁定稿校验必须同样放行——否则 4001~10000 字的文案会走到最后一步才被拒，
# 成了「草稿能出、成片必败」的死路（不带草稿的一次性出片反而能跑，更反直觉）。
MAX_SCRIPT_CHARS = 10_000
# 单句上限（字）。超长句往往是用户误粘整段，截断会丢内容，所以按「太长」报错让
# 用户自己拆句——拆句正是这一层希望他做的事。
MAX_SENTENCE_CHARS = 200


def estimate_script_seconds(texts: Any) -> float:
    """按朗读权重估算成片时长（秒）。

    草稿阶段还没有配音，用与时间轴同一字/秒密度反推，面板显示的「约 N 秒」才和
    成片不会差一个档。只用于展示，真实帧号一律等配音时长回来再算。
    """
    weight = sum(_text_weight(str(item or "")) for item in texts)
    return round(weight / NARRATION_CHARS_PER_SECOND, 1)


# --------------------------------------------------------------------------- #
# 科普讲解视频：逐镜台词草稿
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExplainerScriptDraft:
    """一份可渲染的锁定稿：标题 + 章节 + 镜头（编号已重算）。"""

    title: ExplainerTitle
    chapters: tuple[ExplainerChapter, ...]
    shots: tuple[ExplainerShot, ...]
    estimated_seconds: float

    def as_chapters(self) -> list[ExplainerChapter]:
        return list(self.chapters)

    def as_shots(self) -> list[ExplainerShot]:
        return list(self.shots)

    def narration_text(self) -> str:
        return "".join(sentence for chapter in self.chapters for sentence in chapter.sentences)


def build_explainer_script_draft(
    title: ExplainerTitle,
    chapters: list[ExplainerChapter],
    shots: list[ExplainerShot],
    *,
    duration_s: int,
) -> dict[str, Any]:
    """模型产物 → 面板可编辑的草稿（纯 JSON，可直接进任务 result）。

    章节名挂在每个镜头上，而不是单独一张章表：面板里增删一句、合并两镜都只动
    「这一镜」，章边界随镜头顺序自然重排，不需要用户在两个列表之间对齐编号。
    """
    payload_shots: list[dict[str, Any]] = []
    for shot in shots:
        chapter_name = (
            chapters[shot.chapter_index].name
            if 0 <= shot.chapter_index < len(chapters)
            else ""
        )
        payload_shots.append(
            {
                "chapter": chapter_name,
                "sentences": list(shot.sentences),
                "primitive": shot.primitive,
                "headline": shot.headline,
                "items": list(shot.items),
                "numbers": list(shot.numbers),
                "emphasis": int(shot.emphasis),
                "motion": shot.motion,
            }
        )
    return {
        "draft_version": SCRIPT_DRAFT_VERSION,
        "kind": EXPLAINER_DRAFT_KIND,
        "title": {
            "big": title.big,
            "rest": title.rest,
            "en": title.en,
            "tagline": title.tagline,
        },
        "duration_s": int(duration_s),
        "estimated_seconds": estimate_script_seconds(
            [sentence for chapter in chapters for sentence in chapter.sentences]
        ),
        "shots": payload_shots,
    }


def _parse_title(raw: Any) -> ExplainerTitle:
    data = raw if isinstance(raw, dict) else {}
    return ExplainerTitle(
        big=_clip(data.get("big"), 24),
        rest=_clip(data.get("rest"), 20),
        en=_clip(data.get("en"), 48),
        tagline=_clip(data.get("tagline"), 28),
    )


def _parse_sentences(raw: Any, *, what: str) -> tuple[str, ...]:
    """一镜的句子序列：去空、去首尾空白；超长句直接报错而不是截断。"""
    items = raw if isinstance(raw, list) else []
    sentences: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if len(text) > MAX_SENTENCE_CHARS:
            raise ValueError(
                f"{what}有一句超过 {MAX_SENTENCE_CHARS} 字，请先拆成几句再出片"
                f"（开头是「{text[:24]}…」）"
            )
        sentences.append(text)
    return tuple(sentences)


def _parse_shot_payload(item: dict[str, Any]) -> dict[str, Any]:
    primitive = str(item.get("primitive") or "").strip().lower()
    if primitive not in EXPLAINER_PRIMITIVES:
        primitive = DEFAULT_PRIMITIVE
    motion = str(item.get("motion") or "").strip().lower()
    if motion not in EXPLAINER_MOTIONS:
        motion = DEFAULT_MOTION
    items_raw = item.get("items")
    items = tuple(
        _clip(entry, ITEM_MAX_CHARS)
        for entry in (items_raw if isinstance(items_raw, list) else [])[:MAX_ITEMS]
        if _clip(entry, ITEM_MAX_CHARS)
    )
    numbers_raw = item.get("numbers")
    numbers = tuple(
        _clip(number, 12)
        for number in (numbers_raw if isinstance(numbers_raw, list) else [])[
            :MAX_NUMBERS
        ]
        if _clip(number, 12)
    )
    try:
        emphasis = int(item.get("emphasis"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        emphasis = -1
    if emphasis < 0 or emphasis >= len(items):
        emphasis = -1
    return {
        "primitive": primitive,
        "motion": motion,
        "headline": _clip(item.get("headline"), HEADLINE_MAX_CHARS),
        "items": items,
        "numbers": numbers,
        "emphasis": emphasis,
    }


def parse_explainer_script_draft(
    raw: Any,
    *,
    fallback_duration_s: int = DEFAULT_DURATION_S,
) -> ExplainerScriptDraft:
    """锁定稿 → 可渲染的标题/章节/镜头；不合法就抛 ValueError 说清原因。

    句子编号与章索引全部按镜头顺序重算：面板允许增删句子、合并镜头，编号若沿用
    提交值，第二个镜头之后会整体错位，画面和台词就对不上了。
    """
    data = raw if isinstance(raw, dict) else None
    if not data:
        raise ValueError("台词草稿为空：请先在面板里生成草稿再出片")
    version = data.get("draft_version")
    if version is not None and int(version or 0) > SCRIPT_DRAFT_VERSION:
        raise ValueError("台词草稿来自更新的版本，请重新生成草稿")
    kind = str(data.get("kind") or "").strip()
    if kind and kind != EXPLAINER_DRAFT_KIND:
        raise ValueError(f"台词草稿类型不对（收到「{kind}」），请重新生成")

    shots_raw = data.get("shots")
    entries = [item for item in shots_raw if isinstance(item, dict)] if isinstance(shots_raw, list) else []
    if not entries:
        raise ValueError("台词草稿里没有任何镜头：请重新生成草稿")

    chapters: list[ExplainerChapter] = []
    chapter_sentences: list[list[str]] = []
    shots: list[ExplainerShot] = []
    cursor = 0
    total_chars = 0

    for position, item in enumerate(entries, start=1):
        sentences = _parse_sentences(item.get("sentences"), what=f"第 {position} 镜")
        if not sentences:
            # 空镜头（用户把整镜删空）跳过而不是报错：这是「删掉这一段」的自然写法。
            continue
        total_chars += sum(len(sentence) for sentence in sentences)
        if total_chars > MAX_SCRIPT_CHARS:
            raise ValueError(
                f"台词总长超过 {MAX_SCRIPT_CHARS} 字，讲解片放不下：请删减内容后重新出片"
            )
        chapter_name = _clip(item.get("chapter"), 8)
        if not chapters:
            chapters.append(ExplainerChapter(name=chapter_name or "讲解", sentences=()))
            chapter_sentences.append([])
        elif not chapter_name:
            chapter_name = chapters[-1].name
        elif chapter_name != chapters[-1].name:
            if len(chapters) >= MAX_CHAPTERS:
                # 章数上限：并回最后一章（名字保持不变），句子顺序不受影响。
                chapter_name = chapters[-1].name
            else:
                chapters.append(ExplainerChapter(name=chapter_name, sentences=()))
                chapter_sentences.append([])
        chapter_index = len(chapters) - 1

        shot_payload = _parse_shot_payload(item)
        start = cursor + 1
        cursor += len(sentences)
        chapter_sentences[chapter_index].extend(sentences)
        shots.append(
            ExplainerShot(
                chapter_index=chapter_index,
                sentence_start=start,
                sentence_end=cursor,
                sentences=sentences,
                primitive=str(shot_payload["primitive"]),
                headline=str(shot_payload["headline"]),
                items=tuple(shot_payload["items"]),
                numbers=tuple(shot_payload["numbers"]),
                emphasis=int(shot_payload["emphasis"]),
                motion=str(shot_payload["motion"]),
            )
        )

    if not shots:
        raise ValueError("台词草稿里没有任何句子：请补齐台词后重新出片")
    if len(shots) > MAX_SHOTS:
        raise ValueError(f"镜头数 {len(shots)} 超过上限 {MAX_SHOTS}：请合并镜头后重新出片")

    built_chapters = [
        ExplainerChapter(name=chapter.name, sentences=tuple(chapter_sentences[index]))
        for index, chapter in enumerate(chapters)
    ]
    # 缺负载的镜头从台词补 items/headline（与模型路径同一口径）：用户改台词后，
    # 原来的 items 可能已经和文字对不上，宁可降级成「术语大字」也不渲染空流程。
    built_shots = [
        _backfill_shot_payload(
            shot,
            built_chapters[shot.chapter_index].name
            if shot.chapter_index < len(built_chapters)
            else "",
        )
        for shot in shots
    ]
    return ExplainerScriptDraft(
        title=_parse_title(data.get("title")),
        chapters=tuple(built_chapters),
        shots=tuple(built_shots),
        estimated_seconds=estimate_script_seconds(
            [sentence for chapter in built_chapters for sentence in chapter.sentences]
        )
        or float(max(1, int(fallback_duration_s or DEFAULT_DURATION_S))),
    )


# --------------------------------------------------------------------------- #
# 白板动画：逐幕文案草稿
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WhiteboardScriptDraft:
    """一份可渲染的白板锁定稿：逐幕（文案 + 重点字 + 线稿描述）+ 拼回的整段旁白。"""

    scenes: tuple[dict[str, Any], ...]
    script: str
    estimated_seconds: float

    def as_scenes(self) -> list[dict[str, Any]]:
        return [dict(scene) for scene in self.scenes]


def build_whiteboard_script_draft(
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    """模型/切分产物 → 面板可编辑的逐幕草稿。

    ``weight`` 不进草稿：面板允许改文案，权重再沿用模型当初给的数就与朗读量脱钩了。
    出片时按每一幕的字数现算，长的那幕自然拿更多时长。
    """
    payload_scenes: list[dict[str, Any]] = []
    for scene in scenes:
        text = " ".join(str(scene.get("text") or "").split())
        prompt = str(scene.get("scenePrompt") or "").strip()
        key = _clip(scene.get("keyText"), _KEY_TEXT_MAX_CHARS)
        if not text and not prompt:
            continue
        payload_scenes.append({"text": text, "keyText": key, "scenePrompt": prompt})
    return {
        "draft_version": SCRIPT_DRAFT_VERSION,
        "kind": WHITEBOARD_DRAFT_KIND,
        "estimated_seconds": estimate_script_seconds(
            [scene["text"] for scene in payload_scenes]
        ),
        "scenes": payload_scenes,
    }


def parse_whiteboard_script_draft(raw: Any) -> WhiteboardScriptDraft:
    """锁定稿 → 逐幕配置 + 拼回的整段旁白（配音一次读完，画面按幕切）。

    ``scenePrompt`` 为空时给一句通用兜底而不是丢幕：用户可能只想改文案、不关心
    画面描述，那条幕仍然要出图，否则成片会少一段。
    """
    data = raw if isinstance(raw, dict) else None
    if not data:
        raise ValueError("台词草稿为空：请先在面板里生成草稿再出片")
    kind = str(data.get("kind") or "").strip()
    if kind and kind != WHITEBOARD_DRAFT_KIND:
        raise ValueError(f"台词草稿类型不对（收到「{kind}」），请重新生成")

    scenes_raw = data.get("scenes")
    entries = [item for item in scenes_raw if isinstance(item, dict)] if isinstance(scenes_raw, list) else []
    if not entries:
        raise ValueError("台词草稿里没有任何分幕：请重新生成草稿")

    scenes: list[dict[str, Any]] = []
    total_chars = 0
    for position, item in enumerate(entries, start=1):
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            # 空幕跳过（= 用户删掉了这一段）。
            continue
        total_chars += len(text)
        if total_chars > MAX_SCRIPT_CHARS:
            raise ValueError(
                f"台词总长超过 {MAX_SCRIPT_CHARS} 字：请删减文案后重新出片"
            )
        prompt = str(item.get("scenePrompt") or "").strip()
        if not prompt:
            prompt = f"围绕这一段讲解画一张简笔示意线稿：{_clip(text, 40)}"
        scenes.append(
            {
                "text": text,
                "keyText": _clip(item.get("keyText"), _KEY_TEXT_MAX_CHARS),
                "scenePrompt": prompt,
                # 时长按每幕实际朗读量分配：改完文案后权重自动跟上。
                "weight": float(max(1, len(text))),
            }
        )
        if len(scenes) > WHITEBOARD_MAX_SCENES:
            raise ValueError(
                f"分幕数 {len(scenes)} 超过上限 {WHITEBOARD_MAX_SCENES}：请合并后重新出片"
            )

    if not scenes:
        raise ValueError("台词草稿里没有任何文案：请补齐文案后重新出片")

    script = "".join(scene["text"] for scene in scenes)
    return WhiteboardScriptDraft(
        scenes=tuple(scenes),
        script=script,
        estimated_seconds=estimate_script_seconds(scene["text"] for scene in scenes),
    )
