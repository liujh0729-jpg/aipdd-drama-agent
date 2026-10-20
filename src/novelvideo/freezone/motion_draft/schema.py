"""[aipdd fork delta] 动效大片交付：把逐镜 MG 成片 + 旁白 + BGM + 字幕组装成一张剪映草稿。

为什么要有这一步：MG 渲染一次只出一镜，用户拿到的是散落的 N 个 mp4，还得自己进剪映
一个个拖到时间轴上——整条工作流在最后一步断掉。本模块直接产出剪映工程目录，剪映里打开
就是排好的多镜时间线（含每个 MG 页面自报的拍点），精修和成片导出交给剪映本身。

口径与 ``narration_draft``（走 VectCut 服务）不同：这里**不依赖任何外部服务或第三方
草稿库**，纯本地写文件——桌面包形态（装机 exe / dmg）没有 VectCut 容器，逐镜 MG 又正是
桌面用户的主链路，所以草稿必须能在本机直接生成。

时间单位一律**微秒整数**：剪映草稿里 ``*_timerange`` / ``duration`` 全是微秒，唯一例外
是 ``fps``（整数帧率）。字段集合不是猜的：
- 顶层键、``keyframes`` / ``config`` / ``materials`` 的空骨架全部来自随包的
  ``template_jianying_10_2``（``draft_content.json`` 本体），本模块只往里填素材与轨道，
  缺键这件事由模板兜住；
- 片段（segment）/ 轨道（track）/ 音频素材（materials.audios）的字段形状取自本机
  剪映真实写出的草稿；``user_beats`` 就是剪映时间轴上的「打点」，MG 页面
  ``window.EV`` 的拍点直接落在这里。

不出网、不起浏览器、不调模型：与 ``motion_render`` 的 ``encoder`` / ``page`` 同一口径，
可以纯单测。
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# 一秒 = 1_000_000 微秒：草稿里所有时间都按这个口径整数化。
MICROSECOND = 1_000_000

# 拍点写进剪映的「打点」档位（gear/mode=404 是本机真实草稿里未启用自动节拍时的取值）。
_BEAT_GEAR = 404

# 字幕条的默认样式（剪映口径）：
# - ``size`` 不是像素而是剪映的字号档位，7.0 是竖屏小字幕能看清的下限；
# - ``transform_y`` 的单位是「半个画布高」且向上为正，0.75 高度处即 -0.5，
#   正好落在下三分之一安全区——再往下会被手机底部的进度条压住；
# - ``line_spacing`` 用 0.25 而不是剪映默认的 0.05：两行字幕用默认间距会叠在一起。
SUBTITLE_SIZE = 7.0
SUBTITLE_Y_RATIO = 0.75
_TEXT_LINE_SPACING = 0.25
_TEXT_LETTER_SPACING = 0.0
# 文字轨要压在画面上方：剪映按片段的 render_index 决定前景/背景，文本轨的基线取 15000。
_TEXT_RENDER_INDEX = 15000


def to_microseconds(seconds: float) -> int:
    """秒 → 微秒整数（负数与 NaN 归零，绝不让脏时长流进草稿）。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value) or value <= 0:
        return 0
    return int(round(value * MICROSECOND))


def new_id() -> str:
    """剪映风格的素材/轨道 ID：大写带连字符的 UUID4。"""
    return str(uuid.uuid4()).upper()


def draft_path(path: str) -> str:
    """草稿里的媒体路径一律用正斜杠（本机真实草稿就是 ``C:/Users/...``）。

    Windows 上 ``Path`` 给出来的是反斜杠，而反斜杠在剪映的工程里是转义符——留着会让
    素材显示成「已丢失」。
    """
    return str(path or "").replace("\\", "/")


@dataclass(frozen=True)
class VideoClip:
    """一段要落到主视频轨上的成片（一个镜头 = 一条 MG 渲染产物）。"""

    path: str
    name: str
    duration_us: int
    width: int
    height: int
    timeline_start_us: int
    # MG 页面 window.EV 的拍点（微秒，相对该片段起点）：落成剪映的打点。
    beat_us: tuple[int, ...] = ()
    # 片段音量（1 为原样）：成片里封了程序生成的配乐，旁白或外部 BGM 进来时要把这条让位。
    gain: float = 1.0


@dataclass(frozen=True)
class AudioClip:
    """一段垫在音频轨上的声音（旁白或 BGM）。"""

    path: str
    name: str
    duration_us: int
    timeline_start_us: int
    gain: float = 1.0
    # 该段音频对应的拍点（微秒，相对片段起点）；BGM 用它把小节线带进剪映。
    beat_us: tuple[int, ...] = ()


@dataclass(frozen=True)
class TextClip:
    """一段落在文字轨上的字幕：一行字 + 它在时间轴上占的那扇窗。

    样式只有字号/加粗/竖直位置三个可调项，颜色固定白、居中对齐、带黑描边和阴影——
    MG 成片是画面在动的背景，白字没有描边阴影在剪映预览里根本读不出来。
    """

    text: str
    timeline_start_us: int
    duration_us: int
    size: float = SUBTITLE_SIZE
    bold: bool = True
    transform_y: float = -(2.0 * SUBTITLE_Y_RATIO - 1.0)


@dataclass(frozen=True)
class MotionDraftSpec:
    """一张草稿的完整输入：画幅 + 镜头序列 + 可选旁白/BGM/字幕。"""

    name: str
    width: int
    height: int
    ratio: str
    fps: int
    clips: tuple[VideoClip, ...]
    narration: tuple[AudioClip, ...] = ()
    bgm: AudioClip | None = None
    texts: tuple[TextClip, ...] = ()


def _full_crop() -> dict[str, float]:
    """不裁切：``crop`` 的四角是 0~1 归一化坐标（原点在左上角），取满幅。"""
    return {
        "lower_left_x": 0.0,
        "lower_left_y": 1.0,
        "lower_right_x": 1.0,
        "lower_right_y": 1.0,
        "upper_left_x": 0.0,
        "upper_left_y": 0.0,
        "upper_right_x": 1.0,
        "upper_right_y": 0.0,
    }


def _identity_clip() -> dict[str, Any]:
    """位置/缩放/旋转/翻转/不透明度全默认：逐镜 MG 的画幅就是画布画幅，不做二次变换。"""
    return {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": 1.0, "y": 1.0},
        "transform": {"x": 0.0, "y": 0.0},
    }


def _text_clip(transform_y: float) -> dict[str, Any]:
    """字幕片段的位置：只沿竖直方向挪，缩放/旋转保持默认。"""
    clip = _identity_clip()
    clip["transform"]["y"] = transform_y
    return clip


def _speed_material(speed_id: str) -> dict[str, Any]:
    """片段引用的 speed 素材：字幕轨没有打点/声道/人声分离，只带这一份。"""
    return {
        "curve_speed": None,
        "id": speed_id,
        "mode": 0,
        "speed": 1,
        "type": "speed",
    }


@dataclass(frozen=True)
class _SegmentRefs:
    """一个片段挂在 ``extra_material_refs`` 上的四份附属素材。

    剪映每个片段都要引用一份 speed / 一份 beats（打点容器）/ 一份
    sound_channel_mapping / 一份 vocal_separation，少任何一份，时间轴上那块补丁会显示
    成缺参数。这四份的字段形状逐条对照本机真实草稿。
    """

    speed_id: str
    beat_id: str
    channel_id: str
    vocal_id: str
    beat_us: tuple[int, ...]

    @classmethod
    def new(cls, beat_us: Sequence[int]) -> _SegmentRefs:
        return cls(
            speed_id=new_id(),
            beat_id=new_id(),
            channel_id=new_id(),
            vocal_id=new_id(),
            beat_us=tuple(int(start) for start in beat_us),
        )

    def extras(self) -> dict[str, list[dict[str, Any]]]:
        """返回要追加进 ``materials`` 的四组数组条目。"""
        return {
            "speeds": [_speed_material(self.speed_id)],
            "beats": [
                {
                    "ai_beats": {
                        "beat_speed_infos": [],
                        "beats_path": "",
                        "beats_url": "",
                        "melody_path": "",
                        "melody_percents": [0],
                        "melody_url": "",
                    },
                    "enable_ai_beats": False,
                    "gear": _BEAT_GEAR,
                    "gear_count": len(self.beat_us),
                    "id": self.beat_id,
                    "mode": _BEAT_GEAR,
                    "user_beats": [
                        {"cnt": index, "ts": start}
                        for index, start in enumerate(self.beat_us)
                    ],
                    "user_delete_ai_beats": None,
                    "type": "beats",
                }
            ],
            "sound_channel_mappings": [
                {
                    "audio_channel_mapping": 0,
                    "id": self.channel_id,
                    "is_config_open": False,
                    "type": "",
                }
            ],
            "vocal_separations": [
                {
                    "choice": 0,
                    "id": self.vocal_id,
                    "production_path": "",
                    "time_range": None,
                    "type": "vocal_separation",
                }
            ],
        }


def _segment(
    *,
    segment_id: str,
    material_id: str,
    extra_refs: Sequence[str],
    source_start_us: int,
    duration_us: int,
    timeline_start_us: int,
    volume: float,
    clip: dict[str, Any] | None,
    uniform_scale: dict[str, Any] | None,
    render_index: int = 0,
) -> dict[str, Any]:
    """一条轨道片段：时间与 ``extra_material_refs`` 是必填，其它走真实草稿的默认值。

    ``clip`` / ``uniform_scale`` 只有画面类片段带（本机真实草稿里音频片段这两项就是
    null/缺省），``render_index`` 决定前景背景：字幕要压住 MG 画面才取大值。
    """
    return {
        "caption_info": None,
        "cartoon": False,
        "clip": clip,
        "common_keyframes": [],
        "enable_adjust": False,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": False,
        "enable_smart_color_adjust": False,
        "extra_material_refs": list(extra_refs),
        "group_id": "",
        "hdr_settings": None,
        "id": segment_id,
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": volume if volume else 1.0,
        "material_id": material_id,
        "render_index": render_index,
        "responsive_layout": {
            "enable": False,
            "horizontal_pos_layout": 0,
            "size_layout": 0,
            "target_follow": "",
            "vertical_pos_layout": 0,
        },
        "reverse": False,
        "source_timerange": {"duration": duration_us, "start": source_start_us},
        "speed": 1,
        "target_timerange": {"duration": duration_us, "start": timeline_start_us},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": uniform_scale,
        "visible": True,
        "volume": volume,
    }


def _track(kind: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attribute": 0,
        "flag": 0,
        "id": new_id(),
        "is_default_name": True,
        "name": "",
        "segments": segments,
        "type": kind,
    }


def _video_material(clip: VideoClip, material_id: str) -> dict[str, Any]:
    """视频素材：字段集合取参考实现（VectCut 侧一直在写、剪映能开）的那一份清单。

    本机唯一的真实草稿只有音频、没有视频素材，所以 ``videos[]`` 的形状没有本地样本可对，
    退到参考仓库导出的键集合为准——它是当前唯一被验证过「剪映读得懂」的口径。
    ``crop`` 用 0~1 归一化坐标（不是 ±0.5），四角不裁剪。
    """
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": _full_crop(),
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": clip.duration_us,
        "height": clip.height,
        "id": material_id,
        "local_material_id": "",
        "material_id": material_id,
        "material_name": clip.name,
        "media_path": "",
        "path": draft_path(clip.path),
        "remote_url": None,
        "type": "video",
        "width": clip.width,
    }


def _audio_material(seg: AudioClip, material_id: str) -> dict[str, Any]:
    """音频素材：字段集合照抄本机真实草稿，只有 path/name/duration 是本次值。"""
    # 同一份音频被切成多段时，真实草稿里这些片段共用同一个 local_material_id / music_id
    # （都源于同一个文件），所以按路径派生而不是随机生成。
    source_id = uuid.uuid3(uuid.NAMESPACE_URL, draft_path(seg.path)).hex
    return {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 1,
        "copyright_limit_type": "none",
        "duration": seg.duration_us,
        "effect_id": "",
        "formula_id": "",
        "id": material_id,
        "intensifies_path": "",
        "is_ai_clone_tone": False,
        "is_text_edit_overdub": False,
        "is_ugc": False,
        "local_material_id": source_id,
        "music_id": source_id,
        "name": seg.name,
        "path": draft_path(seg.path),
        "query": "",
        "request_id": "",
        "resource_id": "",
        "search_id": "",
        "source_from": "",
        "source_platform": 0,
        "team_id": "",
        "text_id": "",
        "tone_category_id": "",
        "tone_category_name": "",
        "tone_effect_id": "",
        "tone_effect_name": "",
        "tone_platform": "",
        "tone_second_category_id": "",
        "tone_second_category_name": "",
        "tone_speaker": "",
        "tone_type": "",
        "type": "extract_music",
        "video_id": "",
        "wave_points": [],
    }


def _text_material(clip: TextClip, material_id: str) -> dict[str, Any]:
    """一条字幕素材：正文在 ``content`` 里，而 ``content`` 本身又是一段 JSON 字符串。

    剪映的文字素材不写「一段纯文本」，而是 ``{"styles":[…],"text":…}`` 序列化后再塞进
    ``content``——样式数组按字符区间覆盖全文，这里整条字幕一个样式，所以是一整段
    ``[0, len(text)]``。``check_flag`` 是能力位：基础 7，带描边 +8、带阴影 +32，
    少了位剪映就不渲染对应效果。
    """
    style = {
        "fill": {
            "alpha": 1.0,
            "content": {
                "render_type": "solid",
                "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]},
            },
        },
        "range": [0, max(len(clip.text), 1)],
        "size": clip.size,
        "bold": clip.bold,
        "italic": False,
        "underline": False,
        # 描边宽度用剪映刻度 40 折算到 0~1 的相对宽度（40/100*0.2）。
        "strokes": [
            {
                "content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                "width": 0.08,
            }
        ],
        "shadows": [
            {
                "diffuse": 0.075,
                "angle": -45.0,
                "content": {"solid": {"color": [0.0, 0.0, 0.0]}},
                "distance": 5.0,
                "alpha": 0.9,
            }
        ],
    }
    return {
        "id": material_id,
        "content": json.dumps(
            {"styles": [style], "text": clip.text}, ensure_ascii=False
        ),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": _TEXT_LETTER_SPACING,
        "line_spacing": _TEXT_LINE_SPACING,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7 | 8 | 32,
        "type": "text",
        "fixed_width": -1,
        "fixed_height": -1,
        # 字体整体设置留空 = 用剪映自带默认字体：不引任何外部字体资源，装机版也不缺字。
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_resource_id": "",
        "font_size": 15.0,
        "font_source_platform": 0,
        "font_team_id": "",
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        # 上面 styles 里的 shadows 之外，剪映还读这一组平铺字段来决定阴影开不开。
        "has_shadow": True,
        "shadow_alpha": 0.9,
        "shadow_angle": -45.0,
        "shadow_color": "#000000",
        "shadow_distance": 5.0,
        "shadow_smoothing": 1.35,
    }


def total_duration_us(spec: MotionDraftSpec) -> int:
    """时间轴总长 = 所有轨上片段的最右端点（不是各段时长相加：允许留空隙）。"""
    ends: list[int] = []
    for clip in spec.clips:
        ends.append(clip.timeline_start_us + clip.duration_us)
    for seg in spec.narration:
        ends.append(seg.timeline_start_us + seg.duration_us)
    if spec.bgm is not None:
        ends.append(spec.bgm.timeline_start_us + spec.bgm.duration_us)
    for text in spec.texts:
        ends.append(text.timeline_start_us + text.duration_us)
    return max(ends) if ends else 0


def _volume_of(item: VideoClip | AudioClip) -> float:
    """片段音量：``gain`` 缺省或脏值按原样 1.0，但 0（静音）是有效值要保留。"""
    gain = getattr(item, "gain", 1.0)
    try:
        value = float(gain)
    except (TypeError, ValueError):
        return 1.0
    return value if 0 <= value <= 4 else 1.0


def build_draft_content(
    spec: MotionDraftSpec,
    template: dict[str, Any],
    *,
    platform: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把 ``spec`` 填进模板草稿，返回可直接序列化的草稿字典。

    ``template`` 是随包 ``draft_content.json`` 解析结果：顶层键、``config``、
    ``keyframes`` 与 ``materials`` 的四十来个数组都由它提供，本函数只覆盖画幅/帧率/
    时长/名称，并往 ``materials`` 与 ``tracks`` 里追加本次素材。``platform`` 缺省沿用
    模板里的（10.2 windows），mac 形态调用方要换成自己的平台段。
    """
    doc = copy.deepcopy(template)
    doc["canvas_config"] = {
        "height": int(spec.height),
        "width": int(spec.width),
        "ratio": spec.ratio or "original",
    }
    doc["fps"] = int(spec.fps)
    doc["name"] = spec.name
    # 模板里的 id 是固定占位；每张草稿必须换一个，否则剪映会按 id 把它们认成同一个工程。
    doc["id"] = new_id()
    doc["duration"] = total_duration_us(spec)
    # 时间与 ID 保持模板的零值/占位口径：本机真实草稿里 create_time/update_time 就是 0，
    # 剪映打开时自己补；我们塞当前时间戳反而会让「工程修改时间」显示成未来。
    doc["create_time"] = 0
    doc["update_time"] = 0
    if platform is not None:
        doc["platform"] = dict(platform)
        doc["last_modified_platform"] = dict(platform)

    materials = doc.setdefault("materials", {})
    for key in (
        "audios",
        "beats",
        "sound_channel_mappings",
        "speeds",
        "texts",
        "vocal_separations",
        "videos",
    ):
        materials.setdefault(key, [])

    video_segments: list[dict[str, Any]] = []
    narration_segments: list[dict[str, Any]] = []
    bgm_segments: list[dict[str, Any]] = []
    text_segments: list[dict[str, Any]] = []

    def add(target: list[dict[str, Any]], kind: str, item: VideoClip | AudioClip) -> None:
        is_video = kind == "video"
        segment_id, material_id = new_id(), new_id()
        refs = _SegmentRefs.new(item.beat_us)
        material = (
            _video_material(item, material_id)
            if is_video
            else _audio_material(item, material_id)
        )
        for array_key, items in refs.extras().items():
            materials.setdefault(array_key, []).extend(items)
        materials["videos" if is_video else "audios"].append(material)
        volume = _volume_of(item)
        segment = _segment(
            segment_id=segment_id,
            material_id=material_id,
            extra_refs=[refs.speed_id, refs.beat_id, refs.channel_id, refs.vocal_id],
            source_start_us=0,
            duration_us=item.duration_us,
            timeline_start_us=item.timeline_start_us,
            volume=volume,
            clip=_identity_clip() if is_video else None,
            uniform_scale=None,
        )
        target.append(segment)

    def add_text(item: TextClip) -> None:
        """一条字幕：文字素材进 ``materials.texts``，片段只引用一份 speed。

        打点/声道映射/人声分离那三份是媒体片段才有的附属素材，文字片段硬塞反而会被
        剪映认成坏引用；字号与位置放在片段的 ``clip.transform`` 里，正文样式放在素材
        ``content`` 里——剪映把这两处分开读。
        """
        segment_id, material_id = new_id(), new_id()
        speed_id = new_id()
        materials["speeds"].append(_speed_material(speed_id))
        materials["texts"].append(_text_material(item, material_id))
        text_segments.append(
            _segment(
                segment_id=segment_id,
                material_id=material_id,
                extra_refs=[speed_id],
                source_start_us=0,
                duration_us=item.duration_us,
                timeline_start_us=item.timeline_start_us,
                volume=1.0,
                clip=_text_clip(item.transform_y),
                uniform_scale={"on": True, "value": 1.0},
                render_index=_TEXT_RENDER_INDEX,
            )
        )

    for clip in spec.clips:
        add(video_segments, "video", clip)
    for seg in spec.narration:
        add(narration_segments, "audio", seg)
    if spec.bgm is not None:
        add(bgm_segments, "audio", spec.bgm)
    for text in spec.texts:
        add_text(text)

    # 旁白与 BGM 必须分轨：同一条音频轨上两段都从 0 起会互相重叠，而剪映的轨道
    # 不允许片段交叠——挤在一起会让整条时间轴打不开。
    doc["tracks"] = [_track("video", video_segments)]
    if narration_segments:
        doc["tracks"].append(_track("audio", narration_segments))
    if bgm_segments:
        doc["tracks"].append(_track("audio", bgm_segments))
    # 字幕单独一条文字轨：与画面轨各自独立，重叠与否互不影响。
    if text_segments:
        doc["tracks"].append(_track("text", text_segments))
    return doc
