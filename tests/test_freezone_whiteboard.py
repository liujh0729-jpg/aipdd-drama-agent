"""[aipdd fork delta] 白板逐笔揭示渲染核心单测。

钉住 ``novelvideo.freezone.whiteboard`` 的可单测纯函数：前景掩码、连通域分区
与阅读顺序、时长分配守恒、帧数换算、画布 contain 适配，以及渲染主体在桩掉
ffmpeg 编码后按帧数产出。全程用合成图，不依赖真实模型、不触发 ffmpeg、不触网。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from novelvideo.freezone import whiteboard as module

_BEIGE = np.array(module.WARM_BEIGE_BGR, dtype=np.uint8)


def _canvas_with_ink(h: int, w: int, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    """暖米黄底画布上按 ``boxes``（y0,x0,y1,x1）涂深色墨迹。"""
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    canvas[:] = _BEIGE
    for y0, x0, y1, x1 in boxes:
        canvas[y0:y1, x0:x1] = (20, 20, 20)
    return canvas


# ============================================================
# 前景掩码
# ============================================================


def test_foreground_mask_covers_ink_not_bg() -> None:
    image = _canvas_with_ink(100, 100, [(20, 20, 40, 60)])
    mask = module.foreground_mask(image)
    assert mask.shape == (100, 100)
    assert mask.dtype == np.uint8
    # 墨迹中心命中，暖米黄底不命中。
    assert mask[30, 40] == 255
    assert mask[5, 5] == 0


def test_foreground_mask_blank_beige_has_no_ink() -> None:
    image = np.zeros((50, 50, 3), dtype=np.uint8)
    image[:] = _BEIGE
    mask = module.foreground_mask(image)
    assert int(mask.max()) == 0


# ============================================================
# 分区与阅读顺序
# ============================================================


def test_segment_regions_reading_order_top_left_first() -> None:
    # 右上、左下、左上三块：阅读顺序应为 左上 → 右上 → 左下。
    image = _canvas_with_ink(
        120,
        120,
        [
            (10, 70, 30, 110),  # 右上
            (80, 10, 100, 50),  # 左下
            (10, 10, 30, 50),  # 左上
        ],
    )
    regions = module.segment_regions(module.foreground_mask(image))
    assert len(regions) == 3
    # 同行带内先左后右，再下一行。
    assert [ (r.x0, r.y0) for r in regions ][0] == (10, 10)


def test_segment_regions_falls_back_to_single_region_when_empty() -> None:
    mask = np.zeros((40, 60), dtype=np.uint8)
    regions = module.segment_regions(mask)
    assert len(regions) == 1
    assert regions[0].x0 == 0 and regions[0].y0 == 0
    assert regions[0].x1 == 60 and regions[0].y1 == 40


# ============================================================
# 时长分配（帧数守恒）
# ============================================================


def test_plan_reveal_frames_conserves_total() -> None:
    image = _canvas_with_ink(100, 100, [(10, 10, 30, 90), (60, 10, 80, 40)])
    regions = module.segment_regions(module.foreground_mask(image))
    total = 100
    bounds = module.plan_reveal_frames(regions, total)
    assert len(bounds) == len(regions)
    # 单调不回退，首帧从 0、末帧到 total：时长守恒。
    assert bounds[0][0] == 0
    assert bounds[-1][1] == total
    for (s0, e0), (s1, _e1) in zip(bounds, bounds[1:]):
        assert s1 == e0 and e0 >= s0


def test_plan_reveal_frames_larger_area_gets_more_frames() -> None:
    image = _canvas_with_ink(
        100,
        100,
        [(10, 10, 40, 90), (60, 10, 75, 25)],  # 第一块面积明显更大
    )
    regions = module.segment_regions(module.foreground_mask(image))
    assert regions[0].area >= regions[-1].area or regions[-1].area <= regions[0].area
    bounds = module.plan_reveal_frames(regions, 90)
    # 面积最大的区域帧数不少于面积最小的区域。
    areas = [r.area for r in regions]
    frames = [e - s for s, e in bounds]
    big = areas.index(max(areas))
    small = areas.index(min(areas))
    assert frames[big] >= frames[small]


# ============================================================
# 帧数 / 参数夹取
# ============================================================


@pytest.mark.parametrize(
    ("duration_ms", "fps", "expected"),
    [
        (3000, 30, 90),
        (1000, 24, 24),
        (0, 30, 6),  # 非法时长夹到最小 200ms → 6 帧
        (4000, 30, 120),
    ],
)
def test_frame_count(duration_ms: int, fps: int, expected: int) -> None:
    assert module.frame_count(duration_ms, fps) == expected


def test_clamp_helpers_bound_extremes() -> None:
    assert module.clamp_duration_ms("abc") == 4000
    assert module.clamp_duration_ms(-100) == module._MIN_SCENE_MS
    assert module.clamp_fps(0) == 1
    assert module.clamp_fps(999) == 60


# ============================================================
# 画布 contain 适配
# ============================================================


def test_fit_onto_canvas_centers_and_pads_beige() -> None:
    small = _canvas_with_ink(20, 20, [(5, 5, 15, 15)])
    canvas = module._fit_onto_canvas(small, 100, 60)
    assert canvas.shape == (60, 100, 3)
    # 角落留边为暖米黄底，中央区域含墨迹。
    assert tuple(int(v) for v in canvas[0, 0]) == tuple(_BEIGE.tolist())
    assert module.foreground_mask(canvas).sum() > 0


# ============================================================
# 渲染主体：桩掉 ffmpeg 编码，只校验帧数与产出路径
# ============================================================


def test_render_whiteboard_scene_produces_frame_count_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "scene.png"
    # 写一张带两块墨迹的合成线稿。
    import cv2

    image = _canvas_with_ink(160, 160, [(20, 20, 60, 140), (100, 30, 130, 90)])
    assert cv2.imwrite(str(source), image)

    captured: dict[str, object] = {}

    def _fake_encode(frames, width, height, fps, out_path: Path) -> None:
        captured["count"] = len(frames)
        captured["shape"] = frames[0].shape
        Path(out_path).write_bytes(b"mp4")  # 占位，避免依赖真实编码

    monkeypatch.setattr(module, "_encode_frames_to_mp4", _fake_encode)

    out = tmp_path / "clip.mp4"
    result = module.render_whiteboard_scene(
        image_path=source,
        out_path=out,
        duration_ms=2000,
        width=160,
        height=120,
        fps=24,
        pen_cursor=False,
    )
    assert result == out
    assert captured["count"] == module.frame_count(2000, 24)
    # RGB 帧，宽 160 高 120。
    assert captured["shape"] == (120, 160, 3)


async def test_render_whiteboard_scene_async_wraps_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "scene.png"
    import cv2

    image = _canvas_with_ink(80, 80, [(10, 10, 40, 70)])
    assert cv2.imwrite(str(source), image)

    def _fake_encode(frames, width, height, fps, out_path: Path) -> None:
        Path(out_path).write_bytes(b"mp4")

    monkeypatch.setattr(module, "_encode_frames_to_mp4", _fake_encode)
    out = tmp_path / "clip.mp4"
    result = await module.render_whiteboard_scene_async(
        image_path=source,
        out_path=out,
        duration_ms=1000,
        width=80,
        height=60,
        fps=10,
        pen_cursor=False,
    )
    assert result == out and out.exists()


def test_render_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        module.render_whiteboard_scene(
            image_path=tmp_path / "nope.png",
            out_path=tmp_path / "clip.mp4",
            duration_ms=1000,
            width=64,
            height=64,
            fps=10,
            pen_cursor=False,
        )


# ============================================================
# 分镜规划：JSON 解析容忍 / 时长分配守恒 / 出图 prompt
# ============================================================


def test_parse_scenes_tolerates_prose_and_fence() -> None:
    raw = (
        "好的，以下是分镜：\n```json\n"
        '[{"scenePrompt":"一只猫","keyText":"宠物","weight":2},'
        '{"scenePrompt":"一棵树","keyText":"自然","weight":1}]\n```\n希望有帮助'
    )
    scenes = module.parse_whiteboard_scenes(raw)
    assert len(scenes) == 2
    assert scenes[0]["scenePrompt"] == "一只猫"
    assert scenes[0]["keyText"] == "宠物"
    assert scenes[0]["weight"] == 2.0


def test_parse_scenes_wraps_dict_and_truncates_keytext() -> None:
    raw = '{"scenes": [{"scenePrompt":"a","keyText":"一二三四五六七八九十一二三","weight":"x"}]}'
    scenes = module.parse_whiteboard_scenes(raw)
    assert len(scenes) == 1
    assert len(scenes[0]["keyText"]) <= module._KEY_TEXT_MAX_CHARS
    # 非法 weight 回退 1.0。
    assert scenes[0]["weight"] == 1.0


def test_parse_scenes_empty_on_garbage() -> None:
    assert module.parse_whiteboard_scenes("完全没有 JSON") == []
    assert module.parse_whiteboard_scenes("") == []


def test_allocate_scene_durations_conserves_total() -> None:
    scenes = [{"weight": 2}, {"weight": 1}, {"weight": 1}]
    durations = module.allocate_scene_durations_ms(scenes, 4000)
    assert len(durations) == 3
    assert sum(durations) == 4000
    assert all(d >= module._MIN_SCENE_MS for d in durations)


def test_allocate_scene_durations_single_and_empty() -> None:
    assert module.allocate_scene_durations_ms([], 4000) == []
    assert module.allocate_scene_durations_ms([{"weight": 1}], 2500) == [2500]


def test_compose_scene_prompt_forbids_text() -> None:
    prompt = module.compose_scene_image_prompt("一只戴帽子的猫")
    assert "一只戴帽子的猫" in prompt
    assert "不要出现任何文字" in prompt
    assert module.WHITEBOARD_IMAGE_STYLE_GUIDE in prompt


def test_overlay_key_text_writes_png(tmp_path) -> None:
    import cv2

    src = tmp_path / "scene.png"
    image = _canvas_with_ink(120, 160, [(30, 30, 90, 130)])
    assert cv2.imwrite(str(src), image)
    out = tmp_path / "over.png"
    result = module.overlay_key_text(
        image_path=src, out_path=out, text="关键词", width=160, height=120
    )
    assert result == out and out.exists()
    written = cv2.imread(str(out))
    assert written is not None and written.shape[:2] == (120, 160)


def test_overlay_key_text_empty_text_still_writes(tmp_path) -> None:
    import cv2

    src = tmp_path / "scene.png"
    assert cv2.imwrite(str(src), _canvas_with_ink(80, 80, [(20, 20, 60, 60)]))
    out = tmp_path / "over.png"
    result = module.overlay_key_text(
        image_path=src, out_path=out, text="", width=80, height=80
    )
    assert result == out and out.exists()
