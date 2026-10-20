"""[aipdd fork delta] 口播智能剪辑：纯本地算子（不碰模型、不出网、不取凭证）。

本模块只放**决策与本地 ffmpeg**两类纯逻辑，可脱离任务管线单测：

- 静音/停顿剪（借鉴 auto-editor 的方法论）：``detect_silences`` 跑
  ``ffmpeg -af silencedetect`` 拿源时间轴上的静音区间，``kept_ranges`` 反向求
  保留段（合并相邻、去极短）；``cut_and_concat`` 逐段裁剪重编码再 concat 出正片底。
- 时间轴对位：网关 ASR 只能稳定拿到**整段文本**（各 ASR 通道是否回分段时间戳不一），
  于是把语音段（= 保留段）当骨架，用 ``align_text_to_segments`` 按段时长把整段文本
  比例切给各段，得到「段 → 源时间区间 + 文本」。这是计划里明确允许的对位回退口径，
  好处是任意 ASR 通道都能跑、不依赖 verbose_json 分段时间戳。
- 瑕疵剪（借鉴 video-use 的 grade/挑错方法论）：``apply_defect_drops`` 按模型给出的
  「整段丢弃」下标从保留段里剔除对应区间（段粒度，够用且不猜词级时间点）。
- 成片时间轴换算：``remap_source_to_final`` 把源时间映射到「剪后拼接」的最终时间轴，
  供字幕落位与 B-roll 槽位使用。
- BGM 垫底：``mix_bgm`` 本地把背景音乐压低混进正片底（与讲解链 ``mux_audio`` 同思路）。

出网判定与凭证一律在上层 runner 经分发器完成，这里只认本地文件路径。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from novelvideo.video_encoder import video_encode_args


@dataclass(frozen=True, slots=True)
class Interval:
    """一段左闭右开的时间区间（秒）。"""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class Segment:
    """语音段：源时间轴区间 + 对齐到的文本。"""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg 探测与静音检测（subprocess，纯本地）
# ---------------------------------------------------------------------------

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def parse_duration_seconds(ffprobe_stdout: str) -> float:
    """从 ``ffprobe -show_entries format=duration`` 的输出解析总时长（秒）。

    与真实落盘解耦：单独解析便于喂样本字符串单测。解析不出返回 0.0。
    """
    for line in str(ffprobe_stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("duration="):
            try:
                return max(0.0, float(stripped.split("=", 1)[1].strip()))
            except ValueError:
                return 0.0
    return 0.0


def probe_duration(video_path: str) -> float:
    """ffprobe 探测媒体总时长（秒）；失败返回 0.0（调用方据此退化）。"""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=0",
        str(video_path),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    return parse_duration_seconds(proc.stdout)


def parse_silencedetect(stderr: str) -> list[Interval]:
    """解析 ``ffmpeg -af silencedetect`` 的 stderr，成对收集 silence_start/end。

    未闭合的尾段（结尾仍静音）以 None 结束，交由调用方用总时长补齐。
    """
    starts = [float(m) for m in _SILENCE_START_RE.findall(stderr or "")]
    ends = [float(m) for m in _SILENCE_END_RE.findall(stderr or "")]
    ranges: list[Interval] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else None
        ranges.append(Interval(start=start, end=end if end is not None else start))
    return ranges


def detect_silences(
    video_path: str, *, noise_db: float = -35.0, min_silence_s: float = 0.4
) -> list[Interval]:
    """跑 ffmpeg silencedetect 拿源时间轴上的静音区间。

    失败或 ffmpeg 缺失返回空列表（调用方按「无静音可剪」继续，绝不阻断成片）。
    """
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={noise_db:.1f}dB:d={min_silence_s:.3f}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return parse_silencedetect(proc.stderr)


# ---------------------------------------------------------------------------
# 保留段 / 丢弃 / 时间轴换算（纯逻辑，可单测）
# ---------------------------------------------------------------------------

def kept_ranges(
    total_duration: float,
    silences: list[Interval],
    *,
    min_keep_s: float = 0.15,
    pad_s: float = 0.12,
) -> list[Interval]:
    """由静音区间反推「保留（有语音）」区间。

    - ``total_duration<=0`` 视为探测失败 → 全片保留（一个整段）。
    - 每段静音两侧各留 ``pad_s`` 余量，避免把语气词/呼吸切死（auto-editor 同款留白）。
    - 相邻保留段若被 < ``min_keep_s`` 的非语音间隙分隔，合并；丢弃过短的碎片段。
    - 结果保证落在 ``[0, total_duration]`` 且互不重叠、按时间升序。
    """
    total = float(total_duration or 0.0)
    if total <= 0.0:
        return [Interval(0.0, 0.0)] if total == 0.0 else [Interval(0.0, total)]

    # 规范化并排序静音区间，裁剪到 [0, total]。
    norm: list[Interval] = []
    for sil in sorted(silences, key=lambda r: r.start):
        s = max(0.0, sil.start)
        e = min(total, sil.end)
        if e > s:
            norm.append(Interval(s, e))

    kept: list[Interval] = []
    cursor = 0.0
    for sil in norm:
        gap_end = min(total, sil.start + pad_s)
        if gap_end > cursor:
            kept.append(Interval(cursor, gap_end))
        cursor = max(cursor, max(0.0, sil.end - pad_s))
    if total > cursor:
        kept.append(Interval(cursor, total))

    # 去极短碎片。
    kept = [seg for seg in kept if seg.duration >= min_keep_s]
    if not kept:
        # 整片都被判静音（或阈值过激）→ 保底全留，别产出空片。
        return [Interval(0.0, total)]
    # 合并相邻（前段结束==后段开始本就连续，这里主要处理被 pad 咬合后仍贴合的段）。
    merged: list[Interval] = [kept[0]]
    for seg in kept[1:]:
        prev = merged[-1]
        if seg.start - prev.end < min_keep_s:
            merged[-1] = Interval(prev.start, max(prev.end, seg.end))
        else:
            merged.append(seg)
    return merged


def drop_ranges(kept: list[Interval], drop_indices: list[int]) -> list[Interval]:
    """按「保留段下标」剔除被模型判为瑕疵的整段，返回新的保留段。"""
    drops = {int(i) for i in drop_indices if isinstance(i, (int, float))}
    result = [seg for index, seg in enumerate(kept) if index not in drops]
    return result or []


def segments_from_ranges(kept: list[Interval]) -> list[Segment]:
    """把保留段转成无文本的语音段骨架（后续对齐填文本）。"""
    return [Segment(start=seg.start, end=seg.end, text="") for seg in kept]


def split_sentences(text: str) -> list[str]:
    """把整段转写文本按中英文句读切成句子（保留句子主体，去空白）。

    切不出多句时退化为「整段一句」，交由比例对齐按逗号/空格再分。
    """
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[。！？；!?;])|(?<=\. )", cleaned)
    sentences = [part.strip() for part in parts if part and part.strip()]
    return sentences or [cleaned]


def _allocate_counts(weights: list[float], total: int) -> list[int]:
    """把 ``total`` 个原子项按 ``weights`` 占比整数分配到各桶（最大余数法）。

    权重全为 0（探测不到时长）时按桶数均分。结果之和恒等于 ``total``，桶数不变。
    """
    m = len(weights)
    if m == 0:
        return []
    if total <= 0:
        return [0] * m
    weight_sum = sum(w for w in weights if w > 0.0)
    if weight_sum <= 0.0:
        weights = [1.0] * m
        weight_sum = float(m)
    raw = [(w / weight_sum) * total for w in weights]
    floors = [int(r) for r in raw]
    remainder = total - sum(floors)
    # 余数按小数部分从大到小补齐（稳定：同小数保持原序）。
    order = sorted(
        range(m), key=lambda i: (-(raw[i] - floors[i]), i)
    )
    for k in range(remainder):
        floors[order[k % m]] += 1
    return floors


def align_text_to_segments(
    text: str,
    segments: list[Segment],
) -> list[Segment]:
    """把整段文本按各语音段时长比例分配到段上，返回填好文本的新段列表。

    句子是原子单位、不可切分，于是用**最大余数法**按段时长把「句子数」分配到各段
    （总段时长探不到时按段数均分），保证所有句子按原顺序被覆盖、不多不少。段数多于
    句子数时，多余的段留空文本；句子多于段时并入最后一段。空文本或空段列表原样返回。
    """
    if not segments:
        return []
    sentences = split_sentences(text)
    if not sentences:
        return [Segment(s.start, s.end, "") for s in segments]

    weights = [max(0.0, seg.duration) for seg in segments]
    counts = _allocate_counts(weights, len(sentences))
    out: list[Segment] = []
    cursor = 0
    last = len(segments) - 1
    for index, seg in enumerate(segments):
        take = counts[index]
        if index == last:
            piece = "".join(sentences[cursor:])
        else:
            piece = "".join(sentences[cursor: cursor + take])
            cursor += take
        out.append(Segment(seg.start, seg.end, piece.strip()))
    return out


def remap_source_to_final(source_time: float, kept: list[Interval]) -> float:
    """把源时间轴上的时刻映射到「剪后拼接」最终时间轴上的时刻。

    落在被删区间内的时刻，映射到其后方第一个保留段的起点（即最近的可放映时刻）。
    """
    t = max(0.0, float(source_time or 0.0))
    acc = 0.0
    for seg in kept:
        if t < seg.start:
            return acc
        if t <= seg.end:
            return acc + (t - seg.start)
        acc += seg.duration
    return acc


def final_duration(kept: list[Interval]) -> float:
    """剪后拼接正片的总时长（秒）= 各保留段时长之和。"""
    return round(sum(seg.duration for seg in kept), 3)


def remap_segment_to_final(seg: Segment, kept: list[Interval]) -> Segment:
    """把一个源时间轴语音段整体映射到最终时间轴（起止各自换算，文本不变）。"""
    return Segment(
        start=round(remap_source_to_final(seg.start, kept), 3),
        end=round(remap_source_to_final(seg.end, kept), 3),
        text=seg.text,
    )


# ---------------------------------------------------------------------------
# 字幕（SRT）产物
# ---------------------------------------------------------------------------

def _srt_timestamp(seconds: float) -> str:
    total = max(0.0, float(seconds or 0.0))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = int(total % 60)
    millis = int(round((total - int(total)) * 1000))
    if millis >= 1000:
        millis, secs = 0, secs + 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(segments: list[Segment]) -> str:
    """由最终时间轴的语音段列表生成 SRT 文本（供草稿字幕轨导入）。空段跳过。"""
    blocks: list[str] = []
    index = 1
    for seg in segments:
        text = str(seg.text or "").strip()
        if not text or seg.end <= seg.start:
            continue
        blocks.append(
            f"{index}\n{_srt_timestamp(seg.start)} --> {_srt_timestamp(seg.end)}\n{text}\n"
        )
        index += 1
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# 瑕疵剪 / B-roll 判定：模型 JSON 提示与解析（纯文本逻辑，可单测；不出网）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """从模型输出里稳健地抠出一个 JSON 对象（容忍 ```json 代码栏与前后散文）。

    先取代码栏里的内容，再退而求其此扫第一个平衡的 ``{...}``。解不出或不是
    对象时返回 ``None``（调用方据此「无瑕疵 / 无 B-roll」降级，绝不致命）。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(raw)):
        char = raw[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : index + 1]
                try:
                    data = json.loads(candidate)
                except ValueError:
                    return None
                return data if isinstance(data, dict) else None
    return None


def build_defect_prompt(segments: list[Segment]) -> str:
    """把带编号的口播分段交给模型，要它挑出口误/废话/重复/严重卡顿该整段删的编号。

    输出契约：一个 JSON 对象 ``{"drop": [编号, ...]}``（编号从 0 起）。只给编号、
    不猜时间点，删与不删都是段粒度，避免幻觉词级时间。
    """
    lines = "\n".join(
        f"{index}: {seg.text.strip()}" for index, seg in enumerate(segments) if seg.text.strip()
    )
    return (
        "你是短视频口播剪辑助手。下面是一段口播的分段转写（编号从 0 起）：\n"
        f"{lines}\n\n"
        "请挑出口误、语气词废话、无意义重复、明显卡顿需要整段删掉的编号。"
        "宁缺毋滥：不确定就不删。只输出一个 JSON 对象，形如 "
        '{"drop": [编号, ...]}'
        "，不要多余文字。"
    )


def parse_defect_drops(text: str, seg_count: int) -> list[int]:
    """解析瑕疵剪模型输出为合法的待删下标列表（去重、裁到 ``[0, seg_count)``）。"""
    data = extract_json(text)
    if not data:
        return []
    raw = data.get("drop")
    if not isinstance(raw, list):
        return []
    drops: set[int] = set()
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < seg_count:
            drops.add(index)
    return sorted(drops)


def build_storyboard_prompt(
    segments: list[Segment], *, final_duration: float, aspect: str
) -> str:
    """把剪后正片的分段口播交给模型，要它拆镜头并标出适合插 B-roll 的时间槽。

    输出契约：JSON ``{"broll_slots": [{"t_start": 秒, "t_end": 秒, "prompt": "画面描述"}]}``，
    时间为剪后最终时间轴（秒）。提示词是给生图模型的英文/中文画面描述。
    """
    lines = "\n".join(
        f"[{round(seg.start, 1)}s-{round(seg.end, 1)}s] {seg.text.strip()}"
        for seg in segments
        if seg.text.strip()
    )
    return (
        f"你是短视频分镜与 B-roll 策划助手。成片画幅 {aspect}、总时长约 "
        f"{round(final_duration, 1)} 秒。剪后口播分段（最终时间轴）：\n"
        f"{lines}\n\n"
        "请理解内容，挑出适合插入画面补充（B-roll）的时段：当口播描述某个具体"
        "事物/场景/数据/动作时。为每个时段给一个生图提示词。只输出一个 JSON 对象，"
        '形如 {"broll_slots": [{"t_start": 秒, "t_end": 秒, "prompt": "画面描述"}]}，'
        "时间必须落在最终时间轴上且递增，不超过总时长。"
    )


def clamp_slot(
    item: dict, *, final_duration: float, min_s: float = 0.6, max_s: float = 8.0
) -> dict | None:
    """把一条 B-roll 槽位裁剪到合法范围（落在 [0, final_duration]、时长有上下界）。

    非法/越界/描述缺失时返回 ``None``（该槽位丢弃，不致命）。
    """
    try:
        start = float(item.get("t_start"))
        end = float(item.get("t_end"))
    except (TypeError, ValueError):
        return None
    prompt = str(item.get("prompt") or "").strip()
    if not prompt:
        return None
    start = max(0.0, min(start, final_duration))
    end = max(0.0, min(end, final_duration))
    if end <= start:
        return None
    if end - start < min_s:
        end = min(final_duration, start + min_s)
    if end - start > max_s:
        end = start + max_s
    if end <= start:
        return None
    return {"t_start": round(start, 3), "t_end": round(end, 3), "prompt": prompt}


def parse_broll_plan(text: str, *, final_duration: float, max_slots: int = 8) -> list[dict]:
    """解析分镜模型输出为已裁剪、按时间升序、上限保护的 B-roll 槽位列表。"""
    data = extract_json(text)
    if not data:
        return []
    raw = data.get("broll_slots")
    if not isinstance(raw, list):
        return []
    slots: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        clamped = clamp_slot(item, final_duration=final_duration)
        if clamped:
            slots.append(clamped)
    slots.sort(key=lambda slot: slot["t_start"])
    return slots[: max(0, int(max_slots))]


def join_transcript(segments: list[Segment]) -> str:
    """把分段文本拼回一整行纯文本（供归档取名 / 内容理解摘要）。"""
    return " ".join(str(seg.text or "").strip() for seg in segments if str(seg.text or "").strip()).strip()


# ---------------------------------------------------------------------------
# 本地 ffmpeg：切割 / 拼接 / 混 BGM（线程内调用，由 runner 用 to_thread 包）
# ---------------------------------------------------------------------------

def _encode_segment_command(
    video_path: str, start: float, end: float, out_path: str
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-to",
        f"{max(0.0, end):.3f}",
        "-i",
        str(video_path),
        *video_encode_args(),
        "-c:a",
        "aac",
        "-avoid_negative_ts",
        "make_zero",
        str(out_path),
    ]


def _concat_command(part_paths: list[str], list_path: str, out_path: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(out_path),
    ]


def cut_and_concat(
    video_path: str, kept: list[Interval], out_path: str, *, work_dir: str
) -> bool:
    """按保留段逐段裁剪重编码，再 concat 成一条正片底（无 B-roll）。

    单段直通（不重编码复制）；多段用 concat demuxer。返回是否成功落盘。
    无保留段 / ffmpeg 缺失 → False（调用方回退用原片）。
    """
    import os

    segs = [s for s in kept if s.duration > 0.001]
    if not segs:
        return False
    os.makedirs(work_dir, exist_ok=True)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    if len(segs) == 1:
        proc = subprocess.run(
            _encode_segment_command(str(video_path), segs[0].start, segs[0].end, str(out_path)),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return proc.returncode == 0 and os.path.getsize(out_path) > 0

    part_paths: list[str] = []
    for index, seg in enumerate(segs):
        part = os.path.join(work_dir, f"seg_{index:04d}.mp4")
        proc = subprocess.run(
            _encode_segment_command(str(video_path), seg.start, seg.end, part),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if proc.returncode != 0 or not os.path.exists(part) or os.path.getsize(part) <= 0:
            return False
        part_paths.append(part)

    list_path = os.path.join(work_dir, "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as handle:
        for part in part_paths:
            handle.write(f"file '{os.path.abspath(part)}'\n")
    proc = subprocess.run(
        _concat_command(part_paths, list_path, str(out_path)),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    return proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _mix_bgm_command(
    video_src: str, bgm_path: str, out_path: str, *, bgm_gain: float
) -> list[str]:
    gain = max(0.0, min(1.0, float(bgm_gain)))
    filter_complex = (
        f"[1:a]volume={gain:.3f},loop=loop=-1:size=2147483647:start=0[bg];"
        "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(video_src),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(out_path),
    ]


def mix_bgm(
    video_src: str, bgm_path: str, out_path: str, *, bgm_gain: float = 0.2
) -> bool:
    """把 BGM 压低混进正片底（本地，BGM 循环补齐时长、随正片结束截断）。

    无 BGM / ffmpeg 缺失 → 直接把正片底拷到 out_path（返回拷贝是否成功），
    保证「有正片无 BGM」时输出仍完整。
    """
    import os
    import shutil

    if not bgm_path or not os.path.exists(bgm_path):
        try:
            if os.path.abspath(video_src) != os.path.abspath(out_path):
                shutil.copyfile(video_src, out_path)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0
        except OSError:
            return False

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    proc = subprocess.run(
        _mix_bgm_command(str(video_src), str(bgm_path), str(out_path), bgm_gain=bgm_gain),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        # 混音失败不致命：退回无 BGM 的正片底。
        try:
            if os.path.abspath(video_src) != os.path.abspath(out_path):
                shutil.copyfile(video_src, out_path)
        except OSError:
            return False
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    return True
