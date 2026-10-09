"""[aipdd fork delta] 科普讲解视频策划层单测。

钉住 ``novelvideo.freezone.explainer`` 的纯函数：入参闸门、解说词分章解析、
字幕块切分、分镜 JSON 的词汇归一与编号纠偏、以及「按朗读权重分配帧」的时间轴
——模型只负责选图元，帧号/章起点一律本地确定性推导。全程不触网、不调模型。
"""

from __future__ import annotations

import pytest

from novelvideo.freezone import explainer as module


def _chapters() -> list[module.ExplainerChapter]:
    return module.parse_explainer_narration(
        "# CHAPTER 1 什么是 RAG\n"
        "RAG 先把文档切块入库。检索时取回最相关的几段。\n"
        "# CHAPTER 2 为什么有效\n"
        "模型不必记住全部知识。答案可以追溯到出处。"
    )


# ============================================================
# 归一化闸门
# ============================================================


def test_normalize_duration_clamps_and_falls_back() -> None:
    assert module.normalize_explainer_duration_s(240) == 240
    assert module.normalize_explainer_duration_s(1200) == module.MAX_DURATION_S
    assert module.normalize_explainer_duration_s(10) == module.MIN_DURATION_S
    assert module.normalize_explainer_duration_s("300") == 300
    for junk in (None, "", "abc", 0, -5):
        assert module.normalize_explainer_duration_s(junk) == module.DEFAULT_DURATION_S


def test_normalize_background() -> None:
    assert module.normalize_explainer_background("STARS") == "stars"
    assert module.normalize_explainer_background("dots") == "dots"
    assert module.normalize_explainer_background("neon") == "dots"
    assert module.normalize_explainer_background(None) == "dots"


def test_resolve_reference_content_gate() -> None:
    assert module.resolve_reference_content("  一段资料  ") == "一段资料"
    with pytest.raises(ValueError, match="required"):
        module.resolve_reference_content("   ")
    with pytest.raises(ValueError, match="12000"):
        module.resolve_reference_content("字" * (module.MAX_REFERENCE_CHARS + 1))


def test_duration_hints_scale_with_target() -> None:
    assert module.narration_char_budget(180) == int(180 * module.NARRATION_CHARS_PER_SECOND)
    assert module.chapter_count_hint(120) == 1
    assert module.chapter_count_hint(200) == 2
    assert module.chapter_count_hint(300) == 3
    # 非法时长先夹到默认档再给提示，不抛错。
    assert module.chapter_count_hint(None) == module.chapter_count_hint(module.DEFAULT_DURATION_S)


def test_prompts_stay_inside_the_reference() -> None:
    narration = module.build_explainer_narration_prompt(
        "RAG 把文档切块后入库，检索时取回相关片段。", duration_s=180
    )
    assert "RAG 把文档切块后入库" in narration
    assert "CHAPTER" in narration
    assert "180 秒" in narration

    numbered = [(1, "第一句。"), (2, "第二句。")]
    storyboard = module.build_explainer_storyboard_prompt(numbered, title_hint="RAG 是什么")
    # 句子以编号形式给模型，模型只回编号，不回帧号（对位一律本地做）。
    assert "1. 第一句。" in storyboard
    assert "2. 第二句。" in storyboard
    assert "RAG 是什么" in storyboard
    for primitive in sorted(module.EXPLAINER_PRIMITIVES):
        assert primitive in storyboard


# ============================================================
# 解说词解析 / 字幕块
# ============================================================


def test_parse_narration_chapters_and_sentences() -> None:
    chapters = _chapters()
    assert [chapter.name for chapter in chapters] == ["什么是 RAG", "为什么有效"]
    assert chapters[0].sentences == (
        "RAG 先把文档切块入库。",
        "检索时取回最相关的几段。",
    )
    assert len(chapters[1].sentences) == 2


def test_parse_narration_without_markers_is_one_chapter() -> None:
    chapters = module.parse_explainer_narration("第一句。第二句。\n第三句。")
    assert len(chapters) == 1
    assert chapters[0].name == "第1节"
    assert chapters[0].sentences == ("第一句。", "第二句。", "第三句。")
    assert module.parse_explainer_narration("") == []
    assert module.parse_explainer_narration(None) == []


def test_parse_narration_merges_chapter_overflow() -> None:
    raw = "".join(f"# CHAPTER {i} 第{i}章\n句子{i}。\n" for i in range(1, 7))
    chapters = module.parse_explainer_narration(raw)
    # 章数闸门：超出上限的章把句子并回前一章，顺序不变。
    assert len(chapters) == module.MAX_CHAPTERS
    assert sum(len(chapter.sentences) for chapter in chapters) == 6


def test_flatten_sentences_numbers_globally() -> None:
    flat = module.flatten_sentences(_chapters())
    assert [index for _, index, _ in flat] == [1, 2, 3, 4]
    assert [chapter for chapter, _, _ in flat] == [0, 0, 1, 1]


def test_split_subtitle_blocks_respects_width_budget() -> None:
    assert module.split_subtitle_blocks("短句。") == ["短句。"]
    assert module.split_subtitle_blocks("") == []
    long_sentence = (
        "检索增强生成先把文档切成小块并写入向量库，"
        "提问时先取回最相关的几段，再连同问题一起交给模型。"
    )
    blocks = module.split_subtitle_blocks(long_sentence)
    assert len(blocks) > 1
    assert "".join(blocks).replace(" ", "") == long_sentence
    for block in blocks:
        assert module._text_weight(block) <= module.BLOCK_MAX_WEIGHT


# ============================================================
# 分镜 JSON：词汇归一 / 编号纠偏 / 兜底
# ============================================================


def test_parse_storyboard_normalizes_vocabulary_and_payload() -> None:
    chapters = _chapters()
    raw = (
        "```json\n"
        '{"title":{"big":"RAG","rest":"检索增强生成","en":"Retrieval Augmented",'
        '"tagline":"让模型带着资料回答"},'
        '"shots":[{"sentence":1,"primitive":"flow","headline":"一条很长的标题用来测试裁剪是否生效",'
        '"items":["切块","入库","检索","重排","生成","额外","再额外"],"motion":"travel"},'
        '{"sentence":3,"primitive":"hologram","headline":"为什么","items":["可追溯"],"motion":"spin"}]}\n'
        "```"
    )
    title, shots = module.parse_explainer_storyboard(raw, chapters=chapters)
    assert title.big == "RAG"
    assert len(title.tagline) <= module.HEADLINE_MAX_CHARS + 14
    assert [shot.sentence_start for shot in shots] == [1, 3]
    # 首个镜头起点强制 1，末镜头吃掉余下的句子：句子全覆盖，不留空洞。
    assert shots[-1].sentence_end == 4
    assert shots[0].primitive == "flow"
    assert len(shots[0].items) == module.MAX_ITEMS
    assert len(shots[0].headline) <= module.HEADLINE_MAX_CHARS
    # 词表外的图元 / 动效一律归一化，渲染器永远不会拿到画不了的东西。
    assert shots[1].primitive == module.DEFAULT_PRIMITIVE
    assert shots[1].motion == module.DEFAULT_MOTION
    assert shots[0].motion == "travel"


def test_parse_storyboard_sorts_and_drops_non_increasing_starts() -> None:
    chapters = _chapters()
    raw = (
        '{"shots":[{"sentence":3,"primitive":"list","items":["\u7532","\u4e59"]},'
        '{"sentence":1,"primitive":"bars","items":["\u7532","\u4e59","\u4e19"],'
        '"numbers":["10","20","30"]},'
        '{"sentence":1,"primitive":"cycle","items":["\u7532","\u4e59"]},'
        '{"sentence":99,"primitive":"timeline","items":["\u7532","\u4e59","\u4e19"]}]}'
    )
    _, shots = module.parse_explainer_storyboard(raw, chapters=chapters)
    # 乱序先按编号排序；编号不递增的重复条目丢弃（否则镜头区间依次覆盖）；
    # 越界的 99 夹回最后一句，仍各成一镜。
    assert [shot.sentence_start for shot in shots] == [1, 3, 4]
    assert [shot.primitive for shot in shots] == ["bars", "list", "timeline"]
    assert shots[-1].sentence_end == 4


def test_parse_storyboard_falls_back_on_garbage() -> None:
    chapters = _chapters()
    title, shots = module.parse_explainer_storyboard("模型没给 JSON", chapters=chapters)
    assert title.big == ""
    assert shots
    assert shots[0].sentence_start == 1
    assert shots[-1].sentence_end == 4
    assert all(shot.primitive in module.EXPLAINER_PRIMITIVES for shot in shots)
    # 兜底镜头也得有可画的内容（不留空画面）。
    assert all(shot.headline or shot.items for shot in shots)


def test_parse_storyboard_downgrades_unloadable_primitive() -> None:
    # 流程图元一个步骤都没给，从解说词也补不出两条短标签 → 降级成 term，
    # 渲染器永远拿不到一个「画不出来」的镜头。
    chapters = module.parse_explainer_narration("一句话说完。")
    _, shots = module.parse_explainer_storyboard(
        '{"shots":[{"sentence":1,"primitive":"flow"}]}', chapters=chapters
    )
    assert len(shots) == 1
    assert shots[0].primitive == module.DEFAULT_PRIMITIVE
    assert shots[0].headline


def test_parse_storyboard_empty_chapters_yields_nothing() -> None:
    assert module.parse_explainer_storyboard('{"shots":[{"sentence":1}]}', chapters=[]) == (
        module.ExplainerTitle(),
        [],
    )


# ============================================================
# 时间轴：帧分配守恒 + 节奏硬约束
# ============================================================


def _shot(chapter_index: int, start: int, end: int, *, primitive: str = "term") -> module.ExplainerShot:
    return module.ExplainerShot(
        chapter_index=chapter_index,
        sentence_start=start,
        sentence_end=end,
        sentences=(),
        primitive=primitive,
        headline="要点",
        items=("甲", "乙"),
    )


def test_plan_timeline_one_fps_and_empty_inputs() -> None:
    empty = module.plan_explainer_timeline(chapters=[], shots=[], duration_ms=8000, fps=30)
    assert empty.scenes == ()
    assert empty.chapter_starts == (0,)
    assert empty.total_frames == 240

    # 时长为 0 也至少留一秒的片长，不产出 0 帧时间轴。
    assert module.plan_explainer_timeline(chapters=[], shots=[], duration_ms=0).total_frames == 30


def test_plan_timeline_conserves_frames_and_marks_chapters() -> None:
    chapters = _chapters()
    shots = [
        _shot(0, 1, 2, primitive="flow"),
        _shot(1, 3, 4, primitive="list"),
    ]
    timeline = module.plan_explainer_timeline(
        chapters=chapters, shots=shots, duration_ms=20_000, fps=30
    )
    assert timeline.total_frames == 600
    assert timeline.chapter_names == ("什么是 RAG", "为什么有效")
    assert timeline.scenes[0].start_frame == 0
    assert timeline.scenes[-1].end_frame == timeline.total_frames
    for first, second in zip(timeline.scenes, timeline.scenes[1:]):
        assert first.end_frame == second.start_frame
    # 章起点 = 该章首个镜头的起点（HUD 与章节卡都靠它）。
    assert timeline.chapter_starts == (
        timeline.scenes[0].start_frame,
        timeline.scenes[1].start_frame,
    )


def test_plan_timeline_merges_shots_below_the_floor() -> None:
    chapters = _chapters()
    shots = [_shot(0, index, index) for index in (1, 2)]
    # 6 秒 / 30fps = 180 帧分给两个镜头：单个 90 帧低于 120 帧下限，必须并掉。
    timeline = module.plan_explainer_timeline(
        chapters=chapters, shots=shots, duration_ms=6_000, fps=30
    )
    assert len(timeline.scenes) == 1
    scene = timeline.scenes[0]
    assert scene.duration_frames >= module.MIN_SHOT_FRAMES
    assert scene.shot.sentence_start == 1
    assert scene.shot.sentence_end == 2


def test_plan_timeline_cues_cover_scene_and_settle_at_the_end() -> None:
    chapters = _chapters()
    shots = [
        _shot(0, 1, 2, primitive="flow"),
        _shot(1, 3, 4, primitive="bars"),
    ]
    timeline = module.plan_explainer_timeline(
        chapters=chapters, shots=shots, duration_ms=30_000, fps=30
    )
    for scene in timeline.scenes:
        assert scene.cues
        assert scene.cues[0].start_frame == scene.start_frame
        assert scene.cues[-1].end_frame == scene.end_frame
        for first, second in zip(scene.cues, scene.cues[1:]):
            assert first.end_frame == second.start_frame
        # 镜头的最后一拍必须停得住（落位即切是参考方法论里点名的缺陷）。
        last = scene.cues[-1]
        assert last.end_frame - last.start_frame >= module.LAST_CUE_MIN_FRAMES
        assert all(block for block in (cue.text for cue in scene.cues))


def test_plan_timeline_durations_follow_reading_weight() -> None:
    chapters = module.parse_explainer_narration(
        "短句。\n" + "这是一句明显更长、信息密度更高、需要更久才能读完的长句。"
    )
    shots = [_shot(0, 1, 1), _shot(0, 2, 2)]
    # 60 秒留足余量，避免短句被 4 秒下限并框，只看权重本身。
    timeline = module.plan_explainer_timeline(
        chapters=chapters, shots=shots, duration_ms=60_000, fps=30
    )
    assert len(timeline.scenes) == 2
    assert timeline.scenes[1].duration_frames > timeline.scenes[0].duration_frames
