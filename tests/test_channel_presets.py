# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Phase 2B 渠道 → 成片预设（``novelvideo.renderer.presets``）单测。

钉住渠道短视频复用讲解管线前的「纯规格映射」契约，全程不触网、不起渲染服务：
- ``normalize_channel`` / ``get_channel``：未知值回默认渠道（douyin，竖屏 9:16）；
- 画幅档位只取 landscape/portrait/square 三档：竖屏优先渠道（抖音/快手/小红书/
  视频号/TikTok）走 portrait，B站走 landscape；
- ``resolve_duration``：把用户填的时长夹到该渠道 [min, max]，非法/缺省/≤0 回渠道默认档；
- ``copy_suggestions``：标题按渠道 title_max_chars 截断、带话题标签与钩子提示，纯本地组装。
"""

from __future__ import annotations

from novelvideo.renderer import presets


def test_unknown_channel_falls_back_to_default():
    assert presets.normalize_channel("no-such-channel") == presets.DEFAULT_CHANNEL
    assert presets.normalize_channel("") == presets.DEFAULT_CHANNEL
    assert presets.normalize_channel(None) == presets.DEFAULT_CHANNEL
    # 大小写 / 空格归一化命中已知渠道。
    assert presets.normalize_channel("  Douyin ") == "douyin"
    assert presets.get_channel("bilibili").key == "bilibili"


def test_aspect_maps_to_supported_canvas_formats():
    # 画幅只用 explainer_frames 已支持的三档：竖屏渠道 portrait、B站横屏 landscape。
    portrait_channels = {"douyin", "kuaishou", "xiaohongshu", "shipinhao", "tiktok"}
    for key, preset in presets.CHANNELS.items():
        if key == "bilibili":
            assert preset.canvas_format == presets.CANVAS_LANDSCAPE
        else:
            assert key in portrait_channels
            assert preset.canvas_format == presets.CANVAS_PORTRAIT
    assert presets.get_channel("douyin").aspect == "9:16"
    assert presets.get_channel("bilibili").aspect == "16:9"
    # 小红书封面是 3:4（与画幅 9:16 不同口径）。
    assert presets.get_channel("xiaohongshu").cover_ratio == "3:4"


def test_resolve_duration_clamps_to_channel_range():
    douyin = presets.get_channel("douyin")
    # 落在区间内原样返回。
    assert presets.resolve_duration("douyin", 45) == 45
    # 超上限夹到 max、低于下限夹到 min。
    assert presets.resolve_duration("douyin", 9999) == douyin.duration_max_s
    assert presets.resolve_duration("douyin", 1) == douyin.duration_min_s
    # 非法/缺省/≤0 回渠道默认档。
    assert presets.resolve_duration("douyin", 0) == douyin.default_duration_s
    assert presets.resolve_duration("douyin", -5) == douyin.default_duration_s
    assert presets.resolve_duration("douyin", None) == douyin.default_duration_s
    assert presets.resolve_duration("douyin", "abc") == douyin.default_duration_s
    # 浮点字符串也按数值夹取。
    assert presets.resolve_duration("bilibili", "60") == 60


def test_copy_suggestions_truncates_title_and_carries_channel_meta():
    xiaohongshu = presets.get_channel("xiaohongshu")
    long_title = "标题" * 50
    result = presets.copy_suggestions(
        "xiaohongshu",
        title=long_title,
        narration="第一行是引子。\n第二行。",
    )
    assert result["channel"] == "xiaohongshu"
    assert result["aspect"] == xiaohongshu.aspect
    assert result["cover_ratio"] == xiaohongshu.cover_ratio
    # 标题按渠道上限截断（小红书 20 字）。
    assert len(result["title"]) == xiaohongshu.title_max_chars
    assert result["title"] == long_title[: xiaohongshu.title_max_chars]
    # 话题标签与钩子提示来自渠道预设，正文引子取解说词首行。
    assert result["hashtags"] == list(xiaohongshu.hashtags)
    assert xiaohongshu.hook_hint in result["description"]
    assert result["description"].startswith("第一行是引子。")


def test_copy_suggestions_empty_inputs_degrade_gracefully():
    result = presets.copy_suggestions("tiktok", title="", narration="")
    assert result["title"] == ""
    # 空正文时 description 只剩钩子提示，不抛错。
    assert result["description"] == presets.get_channel("tiktok").hook_hint
