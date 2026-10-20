# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效剪映草稿导出（motion_draft 包）纯逻辑测试。
"""三段都守：``schema`` 拼出的草稿字典形状、``project`` 的输入解析口径、
``writer`` 落盘的目录结构。

草稿这层的价值全在「剪映打开是不是那条时间线」，而键集合与镜像文件是最容易悄悄坏掉的
部分：所以断言的是**剪映真实写出过的那份字段清单**（本机草稿实测 + 随包模板骨架），
不是我们自己拟的字段。不起剪映、不出网。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from novelvideo.freezone.motion_draft import (
    SCORE_GAIN_UNDER_BGM,
    SCORE_GAIN_UNDER_NARRATION,
    AudioClip,
    ClipInput,
    MotionDraftInputError,
    MotionDraftSpec,
    TextClip,
    VideoClip,
    beats_to_microseconds,
    build_draft_content,
    build_spec,
    jianying_projects_dir,
    load_sidecar,
    read_clips,
    resolve_media_path,
    scan_motion_renders,
    sidecar_has_score,
    sidecar_path_for,
    template_dir,
    to_microseconds,
    total_duration_us,
    write_motion_draft,
)
from novelvideo.freezone.motion_draft.schema import (
    MICROSECOND,
    SUBTITLE_SIZE,
    SUBTITLE_Y_RATIO,
)

# 剪映给文字轨片的默认前景层级（比画面/音频都高，字幕压住画面才读得到）。
_TEXT_RENDER_INDEX = 15000

# 随包 10.2 骨架的顶层键：模板兜住「键齐不齐」，草稿必须一个不多一个不少。
_TEMPLATE_TOP_KEYS = {
    "canvas_config",
    "color_space",
    "config",
    "cover",
    "create_time",
    "duration",
    "extra_info",
    "fps",
    "free_render_index_mode_on",
    "group_container",
    "id",
    "keyframe_graph_list",
    "keyframes",
    "last_modified_platform",
    "materials",
    "mutable_config",
    "name",
    "new_version",
    "platform",
    "relationships",
    "render_index_track_mode_on",
    "retouch_cover",
    "source",
    "static_cover_image_path",
    "time_marks",
    "tracks",
    "update_time",
    "version",
}
# 剪映真实写出的片段字段清单（本机草稿里那条音频片段实测抠出来的 33 个键）。
_SEGMENT_KEYS = {
    "caption_info",
    "cartoon",
    "clip",
    "common_keyframes",
    "enable_adjust",
    "enable_color_correct_adjust",
    "enable_color_curves",
    "enable_color_match_adjust",
    "enable_color_wheels",
    "enable_lut",
    "enable_smart_color_adjust",
    "extra_material_refs",
    "group_id",
    "hdr_settings",
    "id",
    "intensifies_audio",
    "is_placeholder",
    "is_tone_modify",
    "keyframe_refs",
    "last_nonzero_volume",
    "material_id",
    "render_index",
    "responsive_layout",
    "reverse",
    "source_timerange",
    "speed",
    "target_timerange",
    "template_id",
    "template_scene",
    "track_attribute",
    "track_render_index",
    "uniform_scale",
    "visible",
    "volume",
}


def _template() -> dict[str, Any]:
    root = template_dir()
    if root is None:  # pragma: no cover - 随包资源缺失属于安装问题
        pytest.skip("随包剪映 10.2 模板骨架不存在")
    return json.loads((root / "draft_content.json").read_text(encoding="utf-8"))


def _spec(*, clips: int = 2, with_audio: bool = True) -> MotionDraftSpec:
    video_clips = tuple(
        VideoClip(
            path=f"E:/out/shot{i}.mp4",
            name=f"shot{i}.mp4",
            duration_us=to_microseconds(2.0),
            width=1920,
            height=1080,
            timeline_start_us=to_microseconds(2.0 * i),
            beat_us=(to_microseconds(0.5),) if i == 0 else (),
        )
        for i in range(clips)
    )
    narration = (
        (
            AudioClip(
                path="E:/out/vo.mp3",
                name="vo.mp3",
                duration_us=to_microseconds(4.0),
                timeline_start_us=0,
            ),
        )
        if with_audio
        else ()
    )
    bgm = (
        AudioClip(
            path="E:/out/bgm.mp3",
            name="bgm.mp3",
            duration_us=to_microseconds(30.0),
            timeline_start_us=0,
            gain=0.2,
        )
        if with_audio
        else None
    )
    return MotionDraftSpec(
        name="动效草稿",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=video_clips,
        narration=narration,
        bgm=bgm,
    )


# --------------------------------------------------------------- schema
def test_to_microseconds_rejects_dirty_durations() -> None:
    assert to_microseconds(2.5) == 2_500_000
    assert to_microseconds(0) == 0
    assert to_microseconds(-3) == 0
    assert to_microseconds(float("nan")) == 0
    assert to_microseconds(float("inf")) == 0
    assert to_microseconds(None) == 0
    assert to_microseconds("1.5") == 1_500_000


def test_draft_content_keeps_template_top_level_keys() -> None:
    doc = build_draft_content(_spec(), _template())
    assert set(doc) == _TEMPLATE_TOP_KEYS
    assert doc["canvas_config"] == {"height": 1080, "width": 1920, "ratio": "16:9"}
    assert doc["fps"] == 30
    assert doc["name"] == "动效草稿"
    assert doc["duration"] == total_duration_us(_spec()) == to_microseconds(30.0)


def test_draft_content_assigns_unique_ids_per_draft() -> None:
    first = build_draft_content(_spec(), _template())
    second = build_draft_content(_spec(), _template())
    # 模板里那个占位 id 不能原样带出去，否则两张草稿会被剪映认成同一个工程。
    assert first["id"] != second["id"]
    assert first["id"] != "00000000-0000-4000-8000-000000000000"


def test_segments_carry_the_measured_jianying_field_set() -> None:
    doc = build_draft_content(_spec(), _template())
    tracks = doc["tracks"]
    # 旁白与 BGM 各占一条音频轨：两段都从 0 起，挤在同一条轨上就是重叠片段。
    assert [track["type"] for track in tracks] == ["video", "audio", "audio"]
    assert [len(track["segments"]) for track in tracks] == [2, 1, 1]
    for track in tracks:
        for segment in track["segments"]:
            assert set(segment) == _SEGMENT_KEYS
            assert len(segment["extra_material_refs"]) == 4


def test_no_track_holds_overlapping_segments() -> None:
    """剪映的轨道不允许片段交叠——同轨两段时间范围相交就打不开，这条必须守住。"""
    doc = build_draft_content(_spec(), _template())
    for track in doc["tracks"]:
        ranges = sorted(
            (
                segment["target_timerange"]["start"],
                segment["target_timerange"]["start"] + segment["target_timerange"]["duration"],
            )
            for segment in track["segments"]
        )
        for (_, previous_end), (next_start, _) in zip(ranges, ranges[1:]):
            assert next_start >= previous_end


def test_video_track_segments_are_seamless_and_timed_in_microseconds() -> None:
    doc = build_draft_content(_spec(), _template())
    segments = doc["tracks"][0]["segments"]
    assert segments[0]["target_timerange"] == {"duration": 2_000_000, "start": 0}
    assert segments[1]["target_timerange"] == {"duration": 2_000_000, "start": 2_000_000}
    assert segments[0]["source_timerange"]["start"] == 0
    assert isinstance(segments[1]["target_timerange"]["start"], int)


def test_every_segment_reference_resolves_in_materials() -> None:
    doc = build_draft_content(_spec(), _template())
    materials = doc["materials"]
    known = {item["id"] for item in materials["videos"]} | {
        item["id"] for item in materials["audios"]
    }
    for group in (
        materials["speeds"],
        materials["beats"],
        materials["sound_channel_mappings"],
        materials["vocal_separations"],
    ):
        known |= {item["id"] for item in group}
    known |= {item["id"] for item in materials["videos"]} | {
        item["id"] for item in materials["audios"]
    }
    for track in doc["tracks"]:
        for segment in track["segments"]:
            for ref in segment["extra_material_refs"]:
                assert ref in known
            assert segment["material_id"] in known


def test_video_material_uses_forward_slash_path_and_reference_id() -> None:
    spec = MotionDraftSpec(
        name="反斜杠",
        width=1080,
        height=1920,
        ratio="9:16",
        fps=30,
        clips=(
            VideoClip(
                path=r"E:\out\shot 1.mp4",
                name="shot 1.mp4",
                duration_us=to_microseconds(1.0),
                width=1080,
                height=1920,
                timeline_start_us=0,
            ),
        ),
    )
    doc = build_draft_content(spec, _template())
    material = doc["materials"]["videos"][0]
    assert material["path"] == "E:/out/shot 1.mp4"
    assert material["id"] == doc["tracks"][0]["segments"][0]["material_id"]
    assert material["type"] == "video"
    # 不裁切时 crop 四角取满幅（0~1 归一化，原点在左上角）。
    assert material["crop"]["upper_left_x"] == 0.0
    assert material["crop"]["lower_right_y"] == 1.0


def test_mg_beats_land_as_jianying_user_beats() -> None:
    doc = build_draft_content(_spec(), _template())
    beat_refs = doc["tracks"][0]["segments"][0]["extra_material_refs"]
    beats = {item["id"]: item for item in doc["materials"]["beats"]}
    first = next(beats[ref] for ref in beat_refs if ref in beats)
    assert first["user_beats"] == [{"cnt": 0, "ts": 500_000}]
    assert first["gear_count"] == 1
    # 没有拍点的第二镜仍要有打点容器，否则片段参数不齐。
    second_refs = doc["tracks"][0]["segments"][1]["extra_material_refs"]
    second = next(beats[ref] for ref in second_refs if ref in beats)
    assert second["user_beats"] == []
    assert second["gear_count"] == 0


def test_audio_gain_becomes_segment_volume() -> None:
    doc = build_draft_content(_spec(), _template())
    volumes = [
        segment["volume"] for track in doc["tracks"][1:] for segment in track["segments"]
    ]
    assert volumes == [1.0, 0.2]


def test_no_audio_keeps_a_single_video_track() -> None:
    doc = build_draft_content(_spec(with_audio=False), _template())
    assert [track["type"] for track in doc["tracks"]] == ["video"]
    assert doc["materials"]["audios"] == []


# --------------------------------------------------------------- 文字轨
def _spec_with_captions() -> MotionDraftSpec:
    return replace(
        _spec(with_audio=False),
        texts=(
            TextClip(text="开场一句话", timeline_start_us=0, duration_us=2_000_000),
            TextClip(text="收尾一句话", timeline_start_us=2_000_000, duration_us=2_000_000),
        ),
    )


def test_text_track_sits_on_its_own_layer_with_the_measured_segment_shape() -> None:
    doc = build_draft_content(_spec_with_captions(), _template())
    assert [track["type"] for track in doc["tracks"]] == ["video", "text"]
    picture = doc["tracks"][0]["segments"][0]
    segments = doc["tracks"][1]["segments"]
    assert len(segments) == 2
    # 前景层级：文字轨片段要大于画面片段，否则字幕会被 MG 画面盖掉。
    assert picture["render_index"] == 0
    assert [segment["render_index"] for segment in segments] == [
        _TEXT_RENDER_INDEX,
        _TEXT_RENDER_INDEX,
    ]
    for segment in segments:
        assert set(segment) == _SEGMENT_KEYS
        # 文字片段只带一份 speed：打点/声道映射/人声分离是媒体片段才有的附属素材。
        assert len(segment["extra_material_refs"]) == 1
        assert segment["extra_material_refs"][0] in {
            item["id"] for item in doc["materials"]["speeds"]
        }
        assert segment["material_id"] in {item["id"] for item in doc["materials"]["texts"]}
        assert segment["uniform_scale"] == {"on": True, "value": 1.0}


def test_a_subtitle_defaults_to_the_lower_third() -> None:
    doc = build_draft_content(_spec_with_captions(), _template())
    # transform.y 以半个画布高为单位、向上为正：0.75 高度处换算出来是 -0.5。
    assert [segment["clip"]["transform"]["y"] for segment in doc["tracks"][1]["segments"]] == [
        -(2.0 * SUBTITLE_Y_RATIO - 1.0),
        -(2.0 * SUBTITLE_Y_RATIO - 1.0),
    ]


def test_subtitle_text_survives_the_double_json_nesting() -> None:
    """剪映的正文不在素材字段上，而是 ``content`` 里再套一层 JSON 字符串。"""
    doc = build_draft_content(_spec_with_captions(), _template())
    material = doc["materials"]["texts"][0]
    content = json.loads(material["content"])
    assert content["text"] == "开场一句话"
    style = content["styles"][0]
    assert style["range"] == [0, len("开场一句话")]
    assert style["size"] == SUBTITLE_SIZE
    assert style["bold"] is True
    assert style["fill"]["content"]["solid"]["color"] == [1.0, 1.0, 1.0]
    # 白字要能盖在动画上：描边与阴影都常驻，check_flag 的 +8/+32 两位决定剪映渲不渲它们。
    assert style["strokes"] and style["shadows"]
    assert material["check_flag"] == 7 | 8 | 32
    assert material["has_shadow"] is True
    assert material["line_spacing"] == 0.25
    assert material["alignment"] == 1
    assert material["font_title"] == "none" and material["fonts"] == []


def test_a_subtitle_outlasting_the_picture_extends_the_draft_duration() -> None:
    spec = replace(
        _spec(with_audio=False),
        texts=(TextClip(text="尾句", timeline_start_us=4_000_000, duration_us=1_500_000),),
    )
    doc = build_draft_content(spec, _template())
    assert total_duration_us(spec) == 5_500_000
    assert doc["duration"] == 5_500_000


def test_no_subtitle_keeps_the_text_track_out_of_the_draft() -> None:
    doc = build_draft_content(_spec(), _template())
    assert "text" not in [track["type"] for track in doc["tracks"]]
    assert doc["materials"]["texts"] == []


# --------------------------------------------------------------- project
def test_sidecar_is_read_and_wins_over_payload(tmp_path: Path) -> None:
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"fake")
    sidecar_path_for(video).write_text(
        json.dumps(
            {
                "duration_seconds": 3.5,
                "width": 720,
                "height": 1280,
                "beats": [{"t": 1.25, "type": "hit"}, {"t": 2.0, "type": "hit"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 9.9, "width": 1920, "beats": [0.1]}],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    assert clips[0].duration_seconds == 3.5
    assert clips[0].width == 720
    assert clips[0].height == 1280
    assert clips[0].beat_us == (1_250_000, 2_000_000)


def test_payload_fills_in_when_sidecar_missing(tmp_path: Path) -> None:
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 2.0, "width": 1080, "height": 1920, "beats": [0.5, 1.0]}],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    assert clips[0].duration_seconds == 2.0
    assert clips[0].width == 1080
    assert clips[0].beat_us == (500_000, 1_000_000)


def test_missing_duration_fails_with_an_actionable_message(tmp_path: Path) -> None:
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"fake")
    with pytest.raises(MotionDraftInputError) as exc:
        read_clips(
            [{"source": str(video)}],
            project_dir=tmp_path,
            fallback_width=1920,
            fallback_height=1080,
        )
    assert "取不到时长" in str(exc.value)


def test_absolute_outside_file_and_relative_inside_both_resolve(tmp_path: Path) -> None:
    inside = tmp_path / "freezone" / "shot.mp4"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"fake")
    assert resolve_media_path(str(inside), tmp_path, what="第 1 镜") == inside
    # 同源静态 URL 的形态是 ``/static/<用户>/<项目>/<相对路径>``。
    assert (
        resolve_media_path("/static/user-1/project/freezone/shot.mp4", tmp_path, what="第 1 镜")
        == inside
    )
    assert resolve_media_path("freezone/shot.mp4", tmp_path, what="第 1 镜") == inside


def test_remote_url_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionDraftInputError, match="外链"):
        resolve_media_path("https://evil.example/x.mp4", tmp_path, what="第 1 镜")


def test_beats_accept_both_spellings_and_drop_junk() -> None:
    assert beats_to_microseconds([0.5, {"t": 1.0}, {"time": 2.0}, "bad", {"t": -1}]) == (
        500_000,
        1_000_000,
        2_000_000,
    )
    assert beats_to_microseconds([]) == ()
    assert beats_to_microseconds(None) == ()


def test_load_sidecar_never_raises_on_dirty_json(tmp_path: Path) -> None:
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"fake")
    assert load_sidecar(video) == {}
    sidecar_path_for(video).write_text("{not json", encoding="utf-8")
    assert load_sidecar(video) == {}


def test_build_spec_places_clips_seamlessly_and_pads_audio(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    vo = tmp_path / "vo.mp3"
    for path in (first, second, vo):
        path.write_bytes(b"fake")
    clips = read_clips(
        [
            {"source": str(first), "duration_seconds": 2.0},
            {"source": str(second), "duration_seconds": 3.0},
        ],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    spec = build_spec(
        name="整片",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        narration={"source": str(vo)},
        bgm=None,
        project_dir=tmp_path,
    )
    assert [clip.timeline_start_us for clip in spec.clips] == [0, 2_000_000]
    assert total_duration_us(spec) == 5_000_000
    # 旁白没给时长 → 按整片垫满。
    assert spec.narration[0].duration_us == 5_000_000
    assert spec.narration[0].gain == 1.0


def _scored_clip(tmp_path: Path, name: str, *, duration: float) -> Path:
    """一条「渲染时带上了程序配乐」的成片：sidecar 里 ``audio.engine`` 是 code-arranged。"""
    video = tmp_path / name
    video.write_bytes(b"fake")
    sidecar_path_for(video).write_text(
        json.dumps(
            {
                "duration_seconds": duration,
                "audio": {"engine": "code-arranged", "bpm": 120.0, "style": "pulse"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return video


def test_embedded_score_ducks_under_narration_and_bgm(tmp_path: Path) -> None:
    """成片自带的配乐分轨要让位：有人声压到 0.2，只有外部 BGM 压到 0.35。"""
    first = _scored_clip(tmp_path, "a.mp4", duration=2.0)
    second = _scored_clip(tmp_path, "b.mp4", duration=3.0)
    vo = tmp_path / "vo.mp3"
    music = tmp_path / "bgm.mp3"
    for path in (vo, music):
        path.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(first)}, {"source": str(second)}],
        project_dir=tmp_path,
        fallback_width=1080,
        fallback_height=1920,
    )
    assert [clip.has_audio for clip in clips] == [True, True]

    def spec_of(**kwargs) -> MotionDraftSpec:
        return build_spec(
            name="整片",
            width=1080,
            height=1920,
            ratio="9:16",
            fps=30,
            clips=clips,
            project_dir=tmp_path,
            **kwargs,
        )

    assert [clip.gain for clip in spec_of().clips] == [1.0, 1.0]
    # 旁白优先于外部 BGM：两者同时存在时按人声那一档走。
    assert [
        clip.gain for clip in spec_of(narration={"source": str(vo)}).clips
    ] == [SCORE_GAIN_UNDER_NARRATION, SCORE_GAIN_UNDER_NARRATION]
    assert [
        clip.gain
        for clip in spec_of(narration={"source": str(vo)}, bgm={"source": str(music)}).clips
    ] == [SCORE_GAIN_UNDER_NARRATION, SCORE_GAIN_UNDER_NARRATION]
    assert [
        clip.gain for clip in spec_of(bgm={"source": str(music)}).clips
    ] == [SCORE_GAIN_UNDER_BGM, SCORE_GAIN_UNDER_BGM]
    # 让位要真的落到剪映认的那个字段上（片段级 volume，不是素材级）。
    doc = build_draft_content(
        spec_of(narration={"source": str(vo)}), _template()
    )
    video_track = doc["tracks"][0]
    assert [segment["volume"] for segment in video_track["segments"]] == [0.2, 0.2]
    assert [segment["last_nonzero_volume"] for segment in video_track["segments"]] == [0.2, 0.2]


def test_a_silent_render_is_never_ducked(tmp_path: Path) -> None:
    """渲染降级成无声片时不许压音量：0.2 的无声片只会让用户以为草稿坏了。"""
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    sidecar_path_for(video).write_text(
        json.dumps(
            {"duration_seconds": 2.0, "audio": {"engine": "silent", "error": "本机没有 ffmpeg"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert sidecar_has_score(load_sidecar(video)) is False
    vo = tmp_path / "vo.mp3"
    vo.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video)}],
        project_dir=tmp_path,
        fallback_width=1080,
        fallback_height=1920,
    )
    spec = build_spec(
        name="整片",
        width=1080,
        height=1920,
        ratio="9:16",
        fps=30,
        clips=clips,
        narration={"source": str(vo)},
        project_dir=tmp_path,
    )
    assert clips[0].has_audio is False
    assert spec.clips[0].gain == 1.0


def test_per_beat_narration_lands_on_its_own_clip_window(tmp_path: Path) -> None:
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    vo_a = tmp_path / "vo_a.mp3"
    vo_b = tmp_path / "vo_b.mp3"
    for path in (first, second, vo_a, vo_b):
        path.write_bytes(b"fake")
    clips = read_clips(
        [
            {"source": str(first), "duration_seconds": 2.0, "narration": {"source": str(vo_a)}},
            {
                "source": str(second),
                "duration_seconds": 3.0,
                # 逐镜旁白给了实测时长就按它走，不被片段时长盖掉。
                "narration": {"source": str(vo_b), "duration_seconds": 1.5, "gain": 0.8},
            },
        ],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    spec = build_spec(
        name="逐镜配音",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        narration=None,
        bgm=None,
        project_dir=tmp_path,
    )
    assert [seg.timeline_start_us for seg in spec.narration] == [0, 2_000_000]
    # 第一段没给时长 → 对齐该镜 2s；第二段用 payload 的 1.5s。
    assert [seg.duration_us for seg in spec.narration] == [2_000_000, 1_500_000]
    assert [seg.gain for seg in spec.narration] == [1.0, 0.8]
    assert total_duration_us(spec) == 5_000_000


def test_a_longer_per_beat_narration_is_cut_to_its_clip_window(tmp_path: Path) -> None:
    """逐镜旁白给了比该镜还长的时长：截到镜窗，否则同轨交叠，整盘草稿打不开。"""
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    vo_a = tmp_path / "vo_a.mp3"
    vo_b = tmp_path / "vo_b.mp3"
    for path in (first, second, vo_a, vo_b):
        path.write_bytes(b"fake")
    clips = read_clips(
        [
            {"source": str(first), "duration_seconds": 2.0, "narration": {"source": str(vo_a), "duration_seconds": 9.0}},
            {"source": str(second), "duration_seconds": 3.0, "narration": {"source": str(vo_b), "duration_seconds": 4.0}},
        ],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    spec = build_spec(
        name="超长旁白",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        project_dir=tmp_path,
    )
    assert [seg.duration_us for seg in spec.narration] == [2_000_000, 3_000_000]
    ends = [seg.timeline_start_us + seg.duration_us for seg in spec.narration]
    starts = [seg.timeline_start_us for seg in spec.narration]
    assert ends == [2_000_000, 5_000_000]
    # 同一条音频轨上不许有任何交叠。
    assert all(starts[i] >= ends[i - 1] for i in range(1, len(starts)))
    assert total_duration_us(spec) == 5_000_000


def test_a_whole_film_narration_may_outlast_the_picture(tmp_path: Path) -> None:
    """整片旁白独占一条轨，给了更长的时长不会被镜窗截短。"""
    video = tmp_path / "a.mp4"
    vo = tmp_path / "vo.mp3"
    for path in (video, vo):
        path.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 2.0}],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    spec = build_spec(
        name="旁白比画面长",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        narration={"source": str(vo), "duration_seconds": 7.5},
        project_dir=tmp_path,
    )
    assert spec.narration[0].duration_us == 7_500_000
    assert total_duration_us(spec) == 7_500_000


def test_zero_gain_stays_muted_in_the_draft(tmp_path: Path) -> None:
    """接口写了「0 静音」就得真的静音：不能把 0 当缺省悄悄放回原音量。"""
    video = tmp_path / "a.mp4"
    vo = tmp_path / "vo.mp3"
    music = tmp_path / "bgm.mp3"
    for path in (video, vo, music):
        path.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 2.0}],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )
    spec = build_spec(
        name="静音旁白",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        narration={"source": str(vo), "gain": 0},
        bgm={"source": str(music), "gain": 0.2},
        project_dir=tmp_path,
    )
    assert spec.narration[0].gain == 0
    assert spec.bgm is not None and spec.bgm.gain == 0.2
    doc = build_draft_content(spec, _template())
    # 旁白与 BGM 各占一条音频轨：轨顺序是 [video, narration, bgm]。
    assert [segment["volume"] for track in doc["tracks"][1:] for segment in track["segments"]] == [0.0, 0.2]
    # 静音片段仍要留一个可解开的非零档位，否则剪映里取消静音还是 0。
    assert doc["tracks"][1]["segments"][0]["last_nonzero_volume"] == 1.0


def test_film_and_per_beat_narration_are_mutually_exclusive(tmp_path: Path) -> None:
    video = tmp_path / "a.mp4"
    vo = tmp_path / "vo.mp3"
    whole = tmp_path / "whole.mp3"
    for path in (video, vo, whole):
        path.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 2.0, "narration": {"source": str(vo)}}],
        project_dir=tmp_path,
        fallback_width=1280,
        fallback_height=720,
    )
    with pytest.raises(MotionDraftInputError, match="旁白只能选一种"):
        build_spec(
            name="混给",
            width=1280,
            height=720,
            ratio="16:9",
            fps=30,
            clips=clips,
            narration={"source": str(whole)},
            bgm=None,
            project_dir=tmp_path,
        )


def test_clip_name_falls_back_to_the_sidecar_title(tmp_path: Path) -> None:
    video = tmp_path / "job9.mp4"
    video.write_bytes(b"fake")
    sidecar_path_for(video).write_text(
        json.dumps({"duration_seconds": 2.0, "title": "开场标题动画"}, ensure_ascii=False),
        encoding="utf-8",
    )
    clips = read_clips(
        [{"source": str(video)}],
        project_dir=tmp_path,
        fallback_width=1280,
        fallback_height=720,
    )
    # payload 没点名 → 用 sidecar 里的片名，而不是裸 job_id 文件名。
    assert clips[0].name == "开场标题动画"


def test_build_spec_without_audio_objects(tmp_path: Path) -> None:
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    clips = read_clips(
        [{"source": str(video), "duration_seconds": 1.0}],
        project_dir=tmp_path,
        fallback_width=1080,
        fallback_height=1920,
    )
    spec = build_spec(
        name="单镜",
        width=1080,
        height=1920,
        ratio="9:16",
        fps=24,
        clips=clips,
        narration={"source": ""},
        bgm=None,
        project_dir=tmp_path,
    )
    assert spec.narration == ()
    assert spec.bgm is None


# --------------------------------------------------------------- 字幕给法
def _two_shot_clips(
    tmp_path: Path, *, captions: tuple[str, str] = ("", "")
) -> tuple[ClipInput, ...]:
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    for path in (first, second):
        path.write_bytes(b"fake")
    return read_clips(
        [
            {
                "source": str(first),
                "duration_seconds": 2.0,
                "caption": captions[0],
            },
            {
                "source": str(second),
                "duration_seconds": 3.0,
                "caption": captions[1],
            },
        ],
        project_dir=tmp_path,
        fallback_width=1920,
        fallback_height=1080,
    )


def test_per_shot_captions_follow_their_own_shot_window(tmp_path: Path) -> None:
    clips = _two_shot_clips(tmp_path, captions=("第一镜的字", ""))
    spec = build_spec(
        name="逐镜字幕",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=clips,
        project_dir=tmp_path,
    )
    assert [item.text for item in spec.texts] == ["第一镜的字"]
    assert [item.timeline_start_us for item in spec.texts] == [0]
    assert [item.duration_us for item in spec.texts] == [2_000_000]


def test_a_blank_caption_lands_no_text_clip(tmp_path: Path) -> None:
    spec = build_spec(
        name="没有字幕",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=_two_shot_clips(tmp_path),
        project_dir=tmp_path,
    )
    assert spec.texts == ()


def test_written_subtitle_rows_pad_until_the_next_line(tmp_path: Path) -> None:
    """手写字幕只想写文字和起点：时长自动垫到下一条起点，最后一条垫到全片结束。"""
    spec = build_spec(
        name="整片字幕",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=_two_shot_clips(tmp_path),
        subtitles=[
            {"text": "第三条先写", "start_seconds": 4.0},
            {"text": "第一条", "start_seconds": 0.0},
            # 给了比这段间隔还长的时长：按间隔截短，交叠会让整盘草稿打不开。
            {"text": "第二条", "start_seconds": 2.5, "duration_seconds": 9.0},
        ],
        project_dir=tmp_path,
    )
    assert [item.text for item in spec.texts] == ["第一条", "第二条", "第三条先写"]
    assert [item.timeline_start_us for item in spec.texts] == [0, 2_500_000, 4_000_000]
    assert [item.duration_us for item in spec.texts] == [2_500_000, 1_500_000, 1_000_000]
    assert total_duration_us(spec) == 5_000_000


def test_caption_and_written_subtitle_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(MotionDraftInputError, match="字幕只能选一种给法"):
        build_spec(
            name="两种字幕",
            width=1920,
            height=1080,
            ratio="16:9",
            fps=30,
            clips=_two_shot_clips(tmp_path, captions=("这一镜有字", "")),
            subtitles=[{"text": "还要手写", "start_seconds": 1.0}],
            project_dir=tmp_path,
        )


def test_a_written_subtitle_starting_past_the_film_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionDraftInputError, match="已经超出全片"):
        build_spec(
            name="越界字幕",
            width=1920,
            height=1080,
            ratio="16:9",
            fps=30,
            clips=_two_shot_clips(tmp_path),
            subtitles=[{"text": "全片只有 5 秒", "start_seconds": 8.0}],
            project_dir=tmp_path,
        )


def test_two_written_subtitles_on_the_same_second_are_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionDraftInputError, match="撞在同一秒"):
        build_spec(
            name="撞车字幕",
            width=1920,
            height=1080,
            ratio="16:9",
            fps=30,
            clips=_two_shot_clips(tmp_path),
            subtitles=[
                {"text": "同一秒第一条", "start_seconds": 1.0},
                {"text": "同一秒第二条", "start_seconds": 1.0},
            ],
            project_dir=tmp_path,
        )


def test_a_row_without_text_is_skipped_instead_of_written_empty(tmp_path: Path) -> None:
    spec = build_spec(
        name="空文本",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=_two_shot_clips(tmp_path),
        subtitles=[{"text": "  ", "start_seconds": 0.0}, {"text": "有字", "start_seconds": 1.0}],
        project_dir=tmp_path,
    )
    assert [item.text for item in spec.texts] == ["有字"]


def test_subtitle_style_falls_back_to_the_mobile_default_on_dirty_values(tmp_path: Path) -> None:
    spec = build_spec(
        name="脏样式",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=_two_shot_clips(tmp_path),
        subtitles=[{"text": "一行字", "start_seconds": 0.0}],
        text_style={"size": 0, "bold": "yes", "y_ratio": 5},
        project_dir=tmp_path,
    )
    assert spec.texts[0].size == SUBTITLE_SIZE
    assert spec.texts[0].bold is True
    assert spec.texts[0].transform_y == -(2.0 * SUBTITLE_Y_RATIO - 1.0)


def test_subtitle_style_honours_the_given_size_and_position(tmp_path: Path) -> None:
    spec = build_spec(
        name="标题档",
        width=1920,
        height=1080,
        ratio="16:9",
        fps=30,
        clips=_two_shot_clips(tmp_path),
        subtitles=[{"text": "上三分之一档", "start_seconds": 0.0}],
        text_style={"size": 12, "bold": False, "y_ratio": 0.2},
        project_dir=tmp_path,
    )
    assert spec.texts[0].size == 12
    assert spec.texts[0].bold is False
    assert spec.texts[0].transform_y == pytest.approx(0.6)


# --------------------------------------------------------------- renders
def test_scan_lists_renders_and_survives_a_missing_sidecar(tmp_path: Path) -> None:
    older = tmp_path / "job_old.mp4"
    newer = tmp_path / "job_new.mp4"
    for path in (older, newer):
        path.write_bytes(b"fake")
    sidecar_path_for(older).write_text(
        json.dumps(
            {
                "case_id": "M43",
                "title": "开场",
                "episode": 3,
                "beat_number": 7,
                "duration_seconds": 2.5,
                "width": 1280,
                "height": 720,
                "fps": 30,
                "aspect_ratio": "16:9",
                "beats": [{"t": 1.0, "type": "swap"}],
            },
        ),
        encoding="utf-8",
    )
    # 按渲染先后排：older 先落盘，就该排在前面。
    stat = older.stat()
    os.utime(older, (stat.st_atime - 10, stat.st_mtime - 10))
    clips = scan_motion_renders(tmp_path)
    assert [clip.job_id for clip in clips] == ["job_old", "job_new"]
    assert clips[0].name == "开场"
    assert (clips[0].episode, clips[0].beat_number) == (3, 7)
    assert clips[0].duration_seconds == 2.5
    assert clips[0].beats == ({"t": 1.0, "type": "swap"},)
    assert clips[0].has_sidecar is True
    # 没 sidecar 的成片仍然要列出来（用户可能就想带上它，时长由 payload 兜）。
    assert clips[1].has_sidecar is False
    assert clips[1].duration_seconds is None
    assert clips[1].name == "job_new"
    assert clips[1].episode == 0


def test_scan_result_carries_no_local_path(tmp_path: Path) -> None:
    video = tmp_path / "job1.mp4"
    video.write_bytes(b"fake")
    item = scan_motion_renders(tmp_path)[0].as_dict()
    # 路由给前端的只有同源 URL：本机绝对路径不能泄漏进接口。
    assert "video_path" not in item
    assert item["job_id"] == "job1"


def test_scan_tolerates_a_dirty_sidecar(tmp_path: Path) -> None:
    video = tmp_path / "job2.mp4"
    video.write_bytes(b"fake")
    sidecar_path_for(video).write_text(
        json.dumps({"episode": "abc", "beats": "nope", "duration_seconds": "x", "width": None}),
        encoding="utf-8",
    )
    clip = scan_motion_renders(tmp_path)[0]
    assert clip.episode == 0
    assert clip.beats == ()
    assert clip.duration_seconds is None
    assert clip.width == 0


def test_scan_on_a_missing_directory_returns_nothing(tmp_path: Path) -> None:
    assert scan_motion_renders(tmp_path / "nope") == ()


# --------------------------------------------------------------- writer
def test_writer_lays_out_the_whole_jianying_project_dir(tmp_path: Path) -> None:
    out = tmp_path / "动效草稿"
    result = write_motion_draft(_spec(), out_dir=out)
    assert result.ok is True, result.error
    assert (out / "draft_content.json").is_file()
    assert (out / "draft_content.json.bak").is_file()
    assert (out / "template-2.tmp").is_file()
    assert (out / "draft_meta_info.json").is_file()
    assert (out / "draft_settings").is_file()
    # 剪映 9.x/10.x 真正读的是 Timelines/<ID>/draft_content.json，目录名必须等于草稿 id。
    timeline_dir = out / "Timelines" / result.timeline_id
    assert timeline_dir.is_dir()
    for name in ("draft_content.json", "draft_content.json.bak", "template-2.tmp", "template.tmp"):
        assert (timeline_dir / name).is_file()
    root = json.loads((out / "draft_content.json").read_text(encoding="utf-8"))
    mirror = json.loads((timeline_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert root == mirror
    assert root["id"] == result.timeline_id
    # 骨架的侧文件也要跟着铺齐（attachment / 配置 / 时间线索引）。
    assert (out / "attachment_pc_common.json").is_file()
    assert (out / "Timelines" / "project.json").is_file()


def test_writer_aligns_timeline_indexes(tmp_path: Path) -> None:
    out = tmp_path / "draft"
    result = write_motion_draft(_spec(), out_dir=out)
    project = json.loads((out / "Timelines" / "project.json").read_text(encoding="utf-8"))
    assert project["id"] == result.timeline_id
    assert project["main_timeline_id"] == result.timeline_id
    assert [item["id"] for item in project["timelines"]] == [result.timeline_id]
    layout = json.loads((out / "timeline_layout.json").read_text(encoding="utf-8"))
    assert layout["dockItems"][0]["timelineIds"] == [result.timeline_id]


def test_writer_fills_meta_with_name_and_forward_slash_paths(tmp_path: Path) -> None:
    out = tmp_path / "draft"
    result = write_motion_draft(_spec(), out_dir=out)
    meta = json.loads((out / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_name"] == "动效草稿"
    assert meta["draft_id"] == result.timeline_id
    assert "\\" not in meta["draft_fold_path"]
    assert meta["draft_fold_path"].endswith("draft")
    assert meta["draft_root_path"] == meta["draft_fold_path"].rsplit("/", 1)[0]


def test_writer_clears_stale_lock_files(tmp_path: Path) -> None:
    out = tmp_path / "draft"
    out.mkdir()
    (out / ".locked").write_text("", encoding="utf-8")
    assert write_motion_draft(_spec(), out_dir=out).ok is True
    assert not (out / ".locked").exists()


def test_writer_reuses_the_same_directory_on_re_export(tmp_path: Path) -> None:
    out = tmp_path / "draft"
    first = write_motion_draft(_spec(), out_dir=out)
    again = write_motion_draft(_spec(clips=1), out_dir=out)
    assert again.ok is True, again.error
    doc = json.loads((out / "draft_content.json").read_text(encoding="utf-8"))
    assert len(doc["tracks"][0]["segments"]) == 1
    assert doc["id"] != first.timeline_id


def test_write_result_exposes_a_dict_shape(tmp_path: Path) -> None:
    payload = write_motion_draft(_spec(), out_dir=tmp_path / "draft").as_dict()
    assert payload["ok"] is True
    assert payload["timeline_id"]
    assert payload["deployed_dir"] == ""
    assert isinstance(payload["files"], list)


def test_projects_dir_probe_returns_none_off_install_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 指向一个空的 LOCALAPPDATA：没装剪映时必须是 None，而不是抛栈。
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert jianying_projects_dir() is None


def test_microsecond_constant_matches_schema_unit() -> None:
    assert MICROSECOND == 1_000_000 == to_microseconds(1.0)
