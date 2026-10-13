# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 讲解视频 HyperFrames 帧生成层单测。

钉住 ``novelvideo.freezone.explainer_frames``：ExplainerTimeline → 项目 spec 的
确定性映射（帧→秒、词汇归一、字幕展平）、产出目录的自包含契约（index.html 的
``data-composition-id``/``data-duration`` + ``assets/gsap.min.js`` 落盘 + 内联 spec
可回解析）、以及设计预设 / 画幅 / 引擎开关的归一化。全程不触网、不调模型、不起
渲染服务。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from novelvideo.freezone import explainer as plan
from novelvideo.freezone import explainer_frames as frames


def _timeline() -> plan.ExplainerTimeline:
    chapters = plan.parse_explainer_narration(
        "# CHAPTER 1 什么是 RAG\n"
        "RAG 先把文档切块入库。检索时取回最相关的几段。\n"
        "# CHAPTER 2 为什么有效\n"
        "模型不必记住全部知识。答案可以追溯到出处。"
    )
    title, shots = plan.parse_explainer_storyboard(
        json.dumps(
            {
                "title": {"big": "RAG", "rest": "检索增强生成"},
                "shots": [
                    {"sentence": 1, "primitive": "flow", "headline": "三步", "items": ["切块", "检索", "生成"], "motion": "softin"},
                    {"sentence": 3, "primitive": "term", "headline": "可追溯", "motion": "lightup"},
                ],
            }
        ),
        chapters=chapters,
    )
    return plan.plan_explainer_timeline(
        chapters=chapters, shots=list(shots), title=title, duration_ms=12000, fps=30
    )


# ============================================================
# 归一化闸门
# ============================================================


def test_normalize_style_preset_falls_back_to_ink() -> None:
    assert frames.normalize_style_preset("OCEAN") == "ocean"
    assert frames.normalize_style_preset("paper") == "paper"
    for junk in (None, "", "neon", 123):
        assert frames.normalize_style_preset(junk) == frames.DEFAULT_STYLE_PRESET


def test_normalize_canvas_format() -> None:
    assert frames.normalize_canvas_format("Portrait") == "portrait"
    assert frames.normalize_canvas_format("square") == "square"
    for junk in (None, "", "4:3", "wide"):
        assert frames.normalize_canvas_format(junk) is None


def test_engine_switch_reads_env(monkeypatch) -> None:
    monkeypatch.delenv(frames.ENGINE_ENV, raising=False)
    assert frames.explainer_engine() == frames.ENGINE_HYPERFRAMES
    monkeypatch.setenv(frames.ENGINE_ENV, "pillow")
    assert frames.explainer_engine() == frames.ENGINE_PILLOW
    monkeypatch.setenv(frames.ENGINE_ENV, "HYPErframes")
    assert frames.explainer_engine() == frames.ENGINE_HYPERFRAMES
    monkeypatch.setenv(frames.ENGINE_ENV, "garbage")
    assert frames.explainer_engine() == frames.DEFAULT_ENGINE


# ============================================================
# spec 映射
# ============================================================


def test_build_spec_maps_frames_to_seconds_and_flattens_cues() -> None:
    tl = _timeline()
    spec = frames.build_spec(
        tl,
        width=1920,
        height=1080,
        style_preset="ink",
        captions=True,
        canvas_format="landscape",
    )
    fps = tl.fps
    assert spec["meta"]["duration"] == round(tl.total_frames / fps, 3)
    assert spec["meta"]["preset"] == "ink"
    assert len(spec["scenes"]) == len(tl.scenes)
    first = spec["scenes"][0]
    assert first["start"] == round(tl.scenes[0].start_frame / fps, 3)
    assert first["duration"] == round(tl.scenes[0].duration_frames / fps, 3)
    # 全局字幕块 = 各镜头 cues 展平，顺序一致。
    flat_cues = [c for scene in tl.scenes for c in scene.cues]
    assert len(spec["captionBlocks"]) == len(flat_cues)
    assert spec["title"]["big"] == "RAG"


def test_build_spec_normalizes_unknown_vocabulary() -> None:
    tl = _timeline()
    # 造一个非法图元/动效的镜头，验证归一到默认词表。
    bad_shot = plan.ExplainerShot(
        chapter_index=0,
        sentence_start=1,
        sentence_end=1,
        sentences=("x",),
        primitive="spaceship",
        motion="explode",
    )
    bad_scene = plan.ExplainerScene(shot=bad_shot, start_frame=0, end_frame=120, cues=())
    spec = frames.build_spec(
        plan.ExplainerTimeline(
            fps=30, total_frames=120, scenes=(bad_scene,), chapter_starts=(0,)
        ),
        width=1920,
        height=1080,
        style_preset="ink",
        captions=True,
        canvas_format=None,
    )
    assert spec["scenes"][0]["primitive"] == "term"
    assert spec["scenes"][0]["motion"] == "softin"


def test_build_spec_captions_off_drops_blocks() -> None:
    tl = _timeline()
    spec = frames.build_spec(
        tl, width=1920, height=1080, style_preset="ink", captions=False, canvas_format="landscape"
    )
    assert spec["captionBlocks"] == []


# ============================================================
# 项目落盘（自包含契约）
# ============================================================


def test_build_explainer_project_writes_self_contained_dir(tmp_path: Path) -> None:
    tl = _timeline()
    proj = frames.build_explainer_project(
        tl,
        project_dir=tmp_path / "hf_project",
        width=1920,
        height=1080,
        style_preset="ocean",
        captions=True,
        canvas_format="landscape",
    )
    root = proj.project_dir
    html = (root / "index.html").read_text(encoding="utf-8")
    # HyperFrames composition 契约要素齐备。
    assert 'data-composition-id="main"' in html
    assert f'data-duration="{proj.duration_seconds}"' in html
    assert 'src="assets/gsap.min.js"' in html
    assert (root / "assets" / "gsap.min.js").exists()
    assert "--ocean" not in html  # ocean 走 CSS 变量，不泄露 preset 名当类
    # 内联 spec 可回解析，并与 build_spec 一致。
    m = re.search(
        r'<script id="explainer-spec" type="application/json">(.*?)</script>', html, re.S
    )
    assert m
    loaded = json.loads(m.group(1).replace("<\\/", "</"))
    assert loaded["meta"]["preset"] == "ocean"
    assert len(loaded["scenes"]) == len(tl.scenes)
    assert proj.style_preset == "ocean"
    assert proj.scene_count == len(tl.scenes)


def test_build_explainer_project_rejects_empty_timeline(tmp_path: Path) -> None:
    empty = plan.ExplainerTimeline(fps=30, total_frames=0, scenes=(), chapter_starts=(0,))
    try:
        frames.build_explainer_project(
            empty, project_dir=tmp_path, width=1920, height=1080
        )
    except ValueError:
        return
    raise AssertionError("empty timeline must be rejected")
