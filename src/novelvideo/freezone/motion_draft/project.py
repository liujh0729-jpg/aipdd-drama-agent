"""[aipdd fork delta] 动效大片交付：把「渲染结果」翻译成草稿规格（纯本地，不出网）。

渲染任务把每镜的画幅/时长/拍点同名落了一份 ``*.motion.json`` sidecar，但任务记录会过期、
前端手上的结果也可能只覆盖最近几镜，所以这里两边都认：**sidecar 优先，缺字段时用调用方
payload 里带的值兜底**。这样「导出剪映草稿」既能整批按目录里的成片走，也能只带路径就出草稿。

路径口径与渲染侧一致：只收本机绝对路径、项目相对路径与同源静态 URL（``/static/...``），
外链一律拒——草稿是要写进剪映工程目录的本地文件引用，指向 http 的素材在剪映里就是丢失。
"""

from __future__ import annotations

import json
from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from novelvideo.freezone.motion_draft.schema import (
    MICROSECOND,
    SUBTITLE_SIZE,
    SUBTITLE_Y_RATIO,
    AudioClip,
    MotionDraftSpec,
    TextClip,
    VideoClip,
    to_microseconds,
)


class MotionDraftInputError(ValueError):
    """输入不可用（路径不存在 / 时长取不到 / 外链）——消息直接给用户看。"""


# MG 渲染会把程序生成的配乐封进成片（渲染契约第 3/6 条）。草稿里再叠旁白或用户自选 BGM 时，
# 这条自带分轨必须让位，否则人声听不清、两首曲子打架：有人声压到 0.2，只有外部 BGM 压到 0.35
# （不能归零——剪映里删掉外部 BGM 后这一镜就彻底没节拍了）。
SCORE_GAIN_UNDER_NARRATION = 0.2
SCORE_GAIN_UNDER_BGM = 0.35


def sidecar_path_for(video_path: Path) -> Path:
    """成片旁边的渲染元数据：``xxx.mp4`` → ``xxx.motion.json``。"""
    return video_path.with_suffix(".motion.json")


def load_sidecar(video_path: Path) -> dict[str, Any]:
    """读取渲染元数据 sidecar；没有或读坏就回空字典（不报错，字段有 payload 兜底）。"""
    path = sidecar_path_for(video_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def beats_to_microseconds(events: Sequence[Any]) -> tuple[int, ...]:
    """``window.EV`` 的拍点 → 微秒整数。

    页面里写的是 ``{t: 秒, type: "…"}``，也可能退化成一串纯秒数；两种都收，非法值跳过。
    """
    out: list[int] = []
    for item in events or ():
        seconds: Any = item
        if isinstance(item, ABCMapping):
            seconds = item.get("t", item.get("time", item.get("ts")))
        try:
            value = float(seconds)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        microseconds = to_microseconds(value)
        if microseconds and microseconds not in out:
            out.append(microseconds)
    return tuple(sorted(out))


# 同源静态地址的前缀（见 freezone.paths.resolve_static_url_to_path）。在 POSIX 上
# ``/static/...`` 也是绝对路径，必须先按 URL 形态认出来，否则会被当成本机文件去 stat。
_SAME_ORIGIN_PREFIXES = ("/static/", "/api/v1/projects/", "/api/v1/style-gallery/")


def resolve_media_path(source: Any, project_dir: Path, *, what: str) -> Path:
    """把 payload 里的素材引用解析成本地文件路径；拿不到就给中文原因。"""
    from novelvideo.freezone.paths import resolve_static_url_to_path

    text = str(source or "").strip()
    if not text:
        raise MotionDraftInputError(f"{what}：没有给素材路径")
    candidate = Path(text)
    # 同源 URL 要先于 is_absolute() 判断：它从根开始写，在 POSIX 上是「绝对路径」，
    # 顺序反了就会拿 ``/static/<u>/<p>/...`` 去 stat 本机文件，永远报文件不存在。
    if candidate.is_absolute() and not text.startswith(_SAME_ORIGIN_PREFIXES):
        # 绝对路径只可能是「本机有这个文件」或「文件没了」，不会再是外链/相对引用；
        # 混在一起报会让用户以为是自己写错了 URL 形态。
        if candidate.is_file():
            return candidate
        raise MotionDraftInputError(f"{what}：素材文件不存在（{candidate}）")
    try:
        resolved = resolve_static_url_to_path(text, project_dir)
    except ValueError as exc:
        raise MotionDraftInputError(
            f"{what}：只认本机路径或项目内的相对引用，收到的是外链或越界路径（{text}）"
        ) from exc
    if not resolved.is_file():
        raise MotionDraftInputError(f"{what}：素材文件不存在（{resolved}）")
    return resolved


@dataclass(frozen=True, slots=True)
class ClipInput:
    """payload 里一条成片引用（尚未补上时长/拍点）。"""

    video_path: Path
    name: str
    duration_seconds: float | None
    width: int
    height: int
    beat_us: tuple[int, ...]
    # 该镜自带的旁白（已解析成本地路径），贴在该片段的起点上。
    narration: Mapping[str, Any] | None = None
    # 成片 mp4 里是否已封了配乐分轨（决定草稿要不要给它让位）。
    has_audio: bool = False
    # 该镜的字幕文本：贴在该片段的窗口上，与整片字幕互斥（同一条文字轨不允许交叠）。
    caption: str = ""


def sidecar_has_score(sidecar: Mapping[str, Any]) -> bool:
    """渲染 sidecar 里这条成片有没有自带配乐：只认 ``audio.engine == "code-arranged"``。

    渲染任务降级成无声片时写的是 ``silent`` / ``off``，那时 mp4 根本没有音轨，
    草稿里就不该再拿"让位音量"去压它。
    """
    audio = sidecar.get("audio")
    return (
        isinstance(audio, ABCMapping)
        and str(audio.get("engine") or "").strip() == "code-arranged"
    )


def read_clips(
    items: Sequence[Mapping[str, Any]],
    *,
    project_dir: Path,
    fallback_width: int,
    fallback_height: int,
) -> tuple[ClipInput, ...]:
    """逐条把成片引用读成 :class:`ClipInput`；sidecar 缺字段时用 payload 值兜底。"""
    clips: list[ClipInput] = []
    for index, item in enumerate(items or (), start=1):
        video_path = resolve_media_path(
            item.get("source") or item.get("video_path") or item.get("url"),
            project_dir,
            what=f"第 {index} 镜",
        )
        sidecar = load_sidecar(video_path)
        duration = pick_float(sidecar.get("duration_seconds"), item.get("duration_seconds"))
        if duration is None or duration <= 0:
            raise MotionDraftInputError(
                f"第 {index} 镜（{video_path.name}）取不到时长：请重新渲染该镜头，"
                "或在请求里带上 duration_seconds"
            )
        beats_raw: Any = (
            sidecar.get("beats")
            if sidecar.get("beats") is not None
            else item.get("beats")
        )
        usable = (
            beats_raw
            if isinstance(beats_raw, ABCSequence) and not isinstance(beats_raw, (str, bytes))
            else ()
        )
        beats = beats_to_microseconds(usable)
        narration = item.get("narration")
        clips.append(
            ClipInput(
                video_path=video_path,
                name=str(item.get("name") or sidecar.get("title") or video_path.name),
                duration_seconds=duration,
                width=pick_int(sidecar.get("width"), item.get("width"), fallback_width),
                height=pick_int(sidecar.get("height"), item.get("height"), fallback_height),
                beat_us=beats,
                narration=narration if isinstance(narration, ABCMapping) else None,
                has_audio=sidecar_has_score(sidecar),
                caption=str(item.get("caption") or "").strip(),
            )
        )
    return tuple(clips)


def pick_float(*values: Any) -> float | None:
    for value in values:
        try:
            text = str(value if value is not None else "").strip()
            if text in ("", "None"):
                continue
            return float(text)
        except (TypeError, ValueError):
            continue
    return None


def pick_int(*values: Any) -> int:
    """把 sidecar / payload 里的整数口径统一容错转换（脏值归 0，绝不抛）。"""
    for value in values:
        try:
            text = str(value if value is not None else "").strip()
            if text in ("", "None"):
                continue
            return int(float(text))
        except (TypeError, ValueError):
            continue
    return 0


def build_spec(
    *,
    name: str,
    width: int,
    height: int,
    ratio: str,
    fps: int,
    clips: Sequence[ClipInput],
    narration: Mapping[str, Any] | None = None,
    bgm: Mapping[str, Any] | None = None,
    subtitles: Sequence[Mapping[str, Any]] | None = None,
    text_style: Mapping[str, Any] | None = None,
    project_dir: Path,
) -> MotionDraftSpec:
    """拼出草稿规格：镜头顺次贴轨（不留空隙），旁白与 BGM 各占一条音频轨。

    旁白有两种给法，只能二选一：**逐镜旁白**跟着自己那条片段起（精品剧逐镜配音，一条
    整片音频对不上镜），**整片旁白**从 0 起垫一条。同时给会让同一条音频轨上的片段交叠，
    而剪映的轨道不允许交叠，所以当场拒掉——宁可不写，也不给一盘打不开的草稿。
    没给时长的音频按它该覆盖的范围垫满：整片旁白垫满全片，逐镜旁白对齐该镜时长；
    草稿阶段不引 ffprobe 探音频真实长度，而"垫满该镜"正是用户预期的默认效果。
    反过来，逐镜旁白**显式给了**一个比该镜还长的时长时按镜窗截短：这些片段挤在同一条音频
    轨上，交叠会让整盘草稿打不开，而剪掉一句尾巴用户在剪映里还能补，草稿打不开就没得补。
    反过来，带配乐分轨的成片（sidecar 里 ``audio.engine == "code-arranged"``）一旦叠了
    旁白或外部 BGM，就要按 ``SCORE_GAIN_UNDER_*`` 把自己压低——这是渲染契约第 6 条「声音
    交付统一」在时间轴上的那一半。
    字幕同样有两种给法，也只能二选一：**逐镜字幕**（``clips[].caption``）跟着自己那一镜的
    窗口起落，天然不交叠；**整片字幕**（``subtitles``）由调用方逐条给起始秒数。两者同给会
    让同一条文字轨上的片段交叠，剪映打不开，所以当场拒掉。整片字幕没给时长就垫到下一条
    开始处（最后一条垫到全片结束）——手打字幕时只想写文字，不想算秒数；给了又超过这个间隔
    的按间隔截短，理由与逐镜旁白一致：剪掉半行字用户在剪映里还能补，草稿打不开就没得补。
    """
    if not clips:
        raise MotionDraftInputError("没有可用的 MG 成片：请先渲染至少一个镜头")
    cursor = 0
    placed: list[tuple[ClipInput, int, int]] = []
    per_beat_narration: list[AudioClip] = []
    for clip in clips:
        duration_us = to_microseconds(clip.duration_seconds or 0.0)
        # 视频片段要等旁白/BGM 都定下来才知道自带配乐该让位到几档，所以这一趟只记落轨位置。
        placed.append((clip, duration_us, cursor))
        if clip.narration is not None:
            segment = _audio(
                clip.narration,
                project_dir=project_dir,
                default_duration=duration_us / float(MICROSECOND),
                what=f"旁白（{clip.name}）",
                timeline_start_us=cursor,
                max_duration_us=duration_us,
            )
            if segment is not None:
                per_beat_narration.append(segment)
        cursor += duration_us
    total_seconds = cursor / float(MICROSECOND)
    audio = _audio(narration, project_dir=project_dir, default_duration=total_seconds, what="旁白")
    if audio is not None and per_beat_narration:
        raise MotionDraftInputError(
            "旁白只能选一种给法：整片一条，或逐镜各一条，不能同时给"
        )
    music = _audio(bgm, project_dir=project_dir, default_duration=total_seconds, what="BGM")
    # 音频轨上的片段按时间轴先后排，数组顺序稳定才好排查。
    narration_clips = sorted(
        [*per_beat_narration, *([audio] if audio is not None else [])],
        key=lambda item: item.timeline_start_us,
    )
    score_gain = (
        SCORE_GAIN_UNDER_NARRATION
        if narration_clips
        else (SCORE_GAIN_UNDER_BGM if music is not None else 1.0)
    )
    video_clips = [
        VideoClip(
            path=str(clip.video_path),
            name=clip.name,
            duration_us=duration_us,
            width=clip.width or width,
            height=clip.height or height,
            timeline_start_us=start_us,
            beat_us=clip.beat_us,
            # 只压真带配乐的成片：无声片把音量拧到 0.2 会让用户以为草稿坏了。
            gain=score_gain if clip.has_audio else 1.0,
        )
        for clip, duration_us, start_us in placed
    ]
    texts = _texts(
        placed=placed,
        subtitles=subtitles or (),
        total_us=cursor,
        style=_text_style(text_style),
    )
    return MotionDraftSpec(
        name=name,
        width=width,
        height=height,
        ratio=ratio,
        fps=fps,
        clips=tuple(video_clips),
        narration=tuple(narration_clips),
        bgm=music,
        texts=texts,
    )


def _audio(
    item: Mapping[str, Any] | None,
    *,
    project_dir: Path,
    default_duration: float,
    what: str,
    timeline_start_us: int = 0,
    max_duration_us: int | None = None,
) -> AudioClip | None:
    if not item:
        return None
    source = item.get("source") or item.get("path") or item.get("url")
    if not str(source or "").strip():
        return None
    path = resolve_media_path(source, project_dir, what=what)
    duration = pick_float(item.get("duration_seconds"))
    duration_us = to_microseconds(duration if duration and duration > 0 else default_duration)
    if max_duration_us is not None and duration_us > max_duration_us:
        duration_us = int(max_duration_us)
    gain = pick_float(item.get("gain"))
    if gain is None or not 0 <= gain <= 4:
        gain = 1.0
    return AudioClip(
        path=str(path),
        name=str(item.get("name") or path.name),
        duration_us=duration_us,
        timeline_start_us=int(timeline_start_us),
        # 0 是「静音」这个有效档位（schema 写了 ge=0），只有脏值才回落到原音量。
        gain=gain,
    )


def _text_style(item: Mapping[str, Any] | None) -> dict[str, Any]:
    """字幕样式：只认字号/加粗/垂直位置三项，脏值一律回到默认档。

    位置写的是「字幕中心距画布顶部占多高」（0~1），要换算成草稿里的
    ``clip.transform.y``——那个字段以半个画布高为单位、向上为正，所以是 ``1 - 2y``。
    默认 0.75 档换算出来是 -0.5，正落在竖屏下三分之一安全区。
    """
    source = item or {}
    size = pick_float(source.get("size"))
    y_ratio = pick_float(source.get("y_ratio"))
    bold = source.get("bold")
    return {
        "size": size if size is not None and 1 <= size <= 40 else SUBTITLE_SIZE,
        "bold": bool(bold) if isinstance(bold, bool) else True,
        "transform_y": 1.0
        - 2.0 * (y_ratio if y_ratio is not None and 0 <= y_ratio <= 1 else SUBTITLE_Y_RATIO),
    }


def _texts(
    *,
    placed: Sequence[tuple[ClipInput, int, int]],
    subtitles: Sequence[Mapping[str, Any]],
    total_us: int,
    style: Mapping[str, Any],
) -> tuple[TextClip, ...]:
    """把两种字幕给法算成同一条文字轨上的片段序列；同时给则报错。"""
    captions = [
        TextClip(
            text=clip.caption,
            timeline_start_us=start_us,
            duration_us=duration_us,
            **dict(style),
        )
        for clip, duration_us, start_us in placed
        if clip.caption
    ]
    if captions:
        if list(subtitles or ()):
            raise MotionDraftInputError(
                "字幕只能选一种给法：逐镜各一条，或整片逐条排，不能同时给"
            )
        return tuple(captions)
    return tuple(_subtitle_rows(subtitles, total_us=total_us, style=style))


def _subtitle_rows(
    items: Sequence[Mapping[str, Any]],
    *,
    total_us: int,
    style: Mapping[str, Any],
) -> list[TextClip]:
    """整片字幕：按起始秒排序，逐条算出它能占的那段间隔，再落成片段。"""
    rows: list[tuple[int, int, str]] = []
    for index, item in enumerate(items or (), start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = pick_float(item.get("start_seconds"))
        if start is None or start < 0:
            raise MotionDraftInputError(f"第 {index} 条字幕没有合法的起始秒数")
        start_us = to_microseconds(start)
        if start_us >= total_us:
            raise MotionDraftInputError(
                f"第 {index} 条字幕从 {start:.1f}s 开始，已经超出全片 "
                f"{total_us / float(MICROSECOND):.1f}s：请先渲染更长的镜头，"
                "或把这条字幕往前挪"
            )
        # 时长在读取阶段就换成微秒：下面要按起始秒重排，重排后就再也对不上原数组了。
        rows.append((start_us, to_microseconds(pick_float(item.get("duration_seconds")) or 0.0), text))
    rows.sort(key=lambda row: row[0])
    clips: list[TextClip] = []
    for index, (start_us, duration_us, text) in enumerate(rows):
        # 一条字幕能铺到哪儿，只取决于下一条从哪儿起；最后一条铺到全片结束。
        limit_us = rows[index + 1][0] if index + 1 < len(rows) else total_us
        if limit_us <= start_us:
            raise MotionDraftInputError(
                f"第 {index + 1} 条字幕与下一条撞在同一秒"
                f"（{start_us / float(MICROSECOND):.1f}s）：同一条文字轨上的片段不能交叠，"
                "否则整盘草稿在剪映里打不开"
            )
        clips.append(
            TextClip(
                text=text,
                timeline_start_us=start_us,
                duration_us=min(duration_us or (limit_us - start_us), limit_us - start_us),
                **dict(style),
            )
        )
    return clips
