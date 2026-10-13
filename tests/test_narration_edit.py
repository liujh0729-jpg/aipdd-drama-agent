"""[aipdd fork delta] 口播智能剪辑纯本地算子单测（不依赖 ffmpeg 真跑）。

覆盖：silencedetect 解析、保留段反推（含 pad / 去极短 / 全留退化 / 全静音保底）、
瑕疵整段剔除、文本→段比例对齐、源→最终时间轴换算、SRT 生成，以及切割/混音
命令构造与「无 BGM 拷贝」降级。真实落盘的 subprocess 路径不在本单测跑。
"""

from __future__ import annotations

from novelvideo.freezone.narration_edit import (
    Interval,
    Segment,
    align_text_to_segments,
    build_srt,
    cut_and_concat,
    drop_ranges,
    extract_json,
    final_duration,
    kept_ranges,
    mix_bgm,
    parse_broll_plan,
    parse_defect_drops,
    parse_duration_seconds,
    parse_silencedetect,
    remap_segment_to_final,
    remap_source_to_final,
    segments_from_ranges,
    split_sentences,
)

# ---------------------------------------------------------------------------
# ffprobe / silencedetect 解析
# ---------------------------------------------------------------------------

def test_parse_duration_seconds_reads_format_duration():
    assert parse_duration_seconds("duration=12.34\n") == 12.34
    assert parse_duration_seconds("N/A") == 0.0
    assert parse_duration_seconds("") == 0.0


def test_parse_silencedetect_pairs_start_end():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.0\n"
        "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.5\n"
        "[silencedetect @ 0x1] silence_start: 6.0\n"
    )
    ranges = parse_silencedetect(stderr)
    assert ranges[0] == Interval(1.0, 2.5)
    # 未闭合尾段以 start 兜底 end（duration 0）。
    assert ranges[1] == Interval(6.0, 6.0)


# ---------------------------------------------------------------------------
# 保留段
# ---------------------------------------------------------------------------

def test_kept_ranges_drops_silence_with_pad_and_keeps_speech():
    total = 10.0
    silences = [Interval(2.0, 4.0)]
    kept = kept_ranges(total, silences, min_keep_s=0.1, pad_s=0.0)
    assert kept == [Interval(0.0, 2.0), Interval(4.0, 10.0)]


def test_kept_ranges_pads_silence_edges():
    kept = kept_ranges(10.0, [Interval(4.0, 6.0)], min_keep_s=0.1, pad_s=0.5)
    # 静音 [4,6] 两侧各留 0.5 → 保留段止于 4.5、起于 5.5。
    assert kept[0].end == 4.5
    assert kept[-1].start == 5.5


def test_kept_ranges_all_silence_falls_back_to_full():
    kept = kept_ranges(8.0, [Interval(0.0, 8.0)], min_keep_s=0.5, pad_s=0.0)
    assert kept == [Interval(0.0, 8.0)]


def test_kept_ranges_zero_duration_is_full_passthrough():
    assert kept_ranges(0.0, []) == [Interval(0.0, 0.0)]
    assert kept_ranges(5.0, []) == [Interval(0.0, 5.0)]


def test_kept_ranges_drops_tiny_fragments():
    # 两段之间只留 0.05s 语音，低于 min_keep_s → 被丢弃。
    kept = kept_ranges(10.0, [Interval(0.0, 5.0), Interval(5.05, 10.0)], min_keep_s=0.5, pad_s=0.0)
    for seg in kept:
        assert seg.duration >= 0.5


# ---------------------------------------------------------------------------
# 瑕疵整段剔除
# ---------------------------------------------------------------------------

def test_drop_ranges_removes_flagged_indices():
    kept = [Interval(0, 1), Interval(2, 3), Interval(4, 5)]
    assert drop_ranges(kept, [1]) == [Interval(0, 1), Interval(4, 5)]
    # 全丢光也不返回 None；调用方据此判断「无正片」。
    assert drop_ranges(kept, [0, 1, 2]) == []


# ---------------------------------------------------------------------------
# 文本切句 + 比例对齐
# ---------------------------------------------------------------------------

def test_split_sentences_chinese_and_english():
    # 中英文句读均切；英文 ". " 处断句，末尾无标点的孤词自成一句。
    assert split_sentences("你好。世界！OK. next") == ["你好。", "世界！", "OK.", "next"]
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_segments_from_ranges_builds_empty_text_skeleton():
    kept = [Interval(0, 2), Interval(4, 7)]
    segs = segments_from_ranges(kept)
    assert [Segment(s.start, s.end, s.text) for s in segs] == [
        Segment(0, 2, ""),
        Segment(4, 7, ""),
    ]


def test_align_text_proportional_over_two_segments():
    segs = [Segment(0, 3, ""), Segment(3, 5, "")]
    aligned = align_text_to_segments("第一句话。第二句话。", segs)
    assert len(aligned) == 2
    assert aligned[0].text and aligned[1].text
    # 覆盖式分配：两句都落进某段、顺序不变。
    assert "第一句话" in aligned[0].text
    assert "第二句话" in aligned[1].text


def test_align_text_empty_segments_returns_empty():
    assert align_text_to_segments("abc", []) == []


def test_align_text_zero_duration_spreads_by_sentence_count():
    segs = [Segment(0, 0, ""), Segment(0, 0, "")]
    aligned = align_text_to_segments("一。二。", segs)
    assert [s.text for s in aligned] == ["一。", "二。"]


# ---------------------------------------------------------------------------
# 源→最终时间轴换算
# ---------------------------------------------------------------------------

def test_remap_source_to_final_within_and_gap():
    kept = [Interval(0, 2), Interval(4, 7)]
    assert remap_source_to_final(1.0, kept) == 1.0  # 落在第一段内
    assert remap_source_to_final(5.0, kept) == 3.0  # 4→0(偏移2)+1
    assert remap_source_to_final(3.0, kept) == 2.0  # 落在被删区间 → 下一保留段起点
    assert final_duration(kept) == 5.0


def test_remap_segment_shifts_both_bounds():
    kept = [Interval(0, 2), Interval(4, 6)]
    seg = Segment(4.5, 5.5, "hi")
    out = remap_segment_to_final(seg, kept)
    assert out.text == "hi"
    assert out.start == 2.5 and out.end == 3.5


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------

def test_build_srt_numbers_and_timestamps():
    segs = [Segment(0.0, 1.5, "你好"), Segment(1.5, 3.0, "世界"), Segment(3.0, 3.0, "")]
    srt = build_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\n你好" in srt
    assert "2\n00:00:01,500 --> 00:00:03,000\n世界" in srt
    assert srt.count("-->") == 2  # 空段被跳过


# ---------------------------------------------------------------------------
# 本地 ffmpeg：命令构造 + 降级
# ---------------------------------------------------------------------------

def test_cut_and_concat_no_segments_returns_false(tmp_path):
    out = tmp_path / "base.mp4"
    assert cut_and_concat("in.mp4", [], str(out), work_dir=str(tmp_path / "w")) is False


def test_mix_bgm_without_bgm_copies_source(tmp_path):
    src = tmp_path / "base.mp4"
    src.write_bytes(b"video")
    out = tmp_path / "final.mp4"
    assert mix_bgm(str(src), "", str(out)) is True
    assert out.read_bytes() == b"video"


# ---------------------------------------------------------------------------
# 模型 JSON 解析（瑕疵剪 / B-roll）
# ---------------------------------------------------------------------------

def test_extract_json_fenced_and_prose():
    assert extract_json('```json\n{"drop": [1, 2]}\n```') == {"drop": [1, 2]}
    assert extract_json('前面废话 {"a": {"b": 1}} 后面废话') == {"a": {"b": 1}}
    assert extract_json("没有大括号") is None
    assert extract_json("") is None


def test_parse_defect_drops_validates_range():
    text = '{"drop": [0, 2, 5, -1, "x"]}'
    # seg_count=3 → 只留 0、2；5 越界、-1 负、"x" 非法均丢。
    assert parse_defect_drops(text, 3) == [0, 2]
    assert parse_defect_drops("not json", 3) == []
    assert parse_defect_drops('{"drop": 1}', 3) == []


def test_parse_broll_plan_clamps_sorts_and_caps():
    text = (
        '{"broll_slots": ['
        '{"t_start": 9, "t_end": 11, "prompt": "B"},'
        '{"t_start": 1, "t_end": 3, "prompt": "A"},'
        '{"t_start": 20, "t_end": 25, "prompt": "越界"},'
        '{"t_start": 4, "t_end": 4.2, "prompt": ""}'
        "]}"
    )
    slots = parse_broll_plan(text, final_duration=10.0, max_slots=8)
    # 越界（20>10）与空 prompt 丢弃；其余裁到 [0,10]、按 t_start 升序。
    assert [s["prompt"] for s in slots] == ["A", "B"]
    assert slots[0]["t_start"] == 1.0
    assert slots[1]["t_end"] <= 10.0
    # 上限保护：max_slots=1 只留最靠前的一个。
    assert len(parse_broll_plan(text, final_duration=10.0, max_slots=1)) == 1
