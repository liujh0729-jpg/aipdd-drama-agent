# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 渠道 → 成片预设（Phase 2B 渠道短视频）。

把「渠道」抽象成一组**成片规格**（画幅 / 时长上下限 / 字幕开关 / 封面比例 /
文案建议口径），供 ``freezone_channel_short_video`` 复用讲解视频的渲染管线时取参。

这里只做「规格映射」——不接任何渠道上传 API（决策基线：短视频渠道定制成片规格、
不自动发布；只有公众号带发布）。出网/渲染都不在本模块职责内。
"""

from __future__ import annotations

from dataclasses import dataclass

# 画幅只取 explainer_frames.CANVAS_FORMATS 已支持的三档（landscape/portrait/square），
# 渠道差异体现在「选哪一档 + 时长档 + 封面比例 + 文案口径」，不新造画幅。
CANVAS_LANDSCAPE = "landscape"
CANVAS_PORTRAIT = "portrait"
CANVAS_SQUARE = "square"


@dataclass(frozen=True, slots=True)
class ChannelPreset:
    """单个渠道的成片预设。"""

    key: str
    label: str
    canvas_format: str  # landscape | portrait | square
    aspect: str  # 结果元数据里的画幅字符串，如 "9:16"
    duration_min_s: int
    duration_max_s: int
    default_duration_s: int
    captions_default: bool
    cover_ratio: str  # 封面比例提示，如 "9:16"
    title_max_chars: int  # 渠道标题长度上限（文案建议里据此截断）
    hashtags: tuple[str, ...]  # 该渠道常用话题标签示例（供文案建议参考）
    hook_hint: str  # 开头钩子风格提示（喂给写作/文案口径）


CHANNELS: dict[str, ChannelPreset] = {
    "douyin": ChannelPreset(
        key="douyin",
        label="抖音",
        canvas_format=CANVAS_PORTRAIT,
        aspect="9:16",
        duration_min_s=15,
        duration_max_s=120,
        default_duration_s=45,
        captions_default=True,
        cover_ratio="9:16",
        title_max_chars=30,
        hashtags=("#涨知识", "#科普", "#干货", "#知识分享"),
        hook_hint="前 3 秒抛出反常识结论或具体痛点，节奏快、句子短",
    ),
    "kuaishou": ChannelPreset(
        key="kuaishou",
        label="快手",
        canvas_format=CANVAS_PORTRAIT,
        aspect="9:16",
        duration_min_s=15,
        duration_max_s=120,
        default_duration_s=45,
        captions_default=True,
        cover_ratio="9:16",
        title_max_chars=30,
        hashtags=("#科普", "#实用技巧", "#知识", "#上热门"),
        hook_hint="口语化、接地气，直接讲「能解决什么问题」",
    ),
    "xiaohongshu": ChannelPreset(
        key="xiaohongshu",
        label="小红书",
        canvas_format=CANVAS_PORTRAIT,
        aspect="3:4",
        duration_min_s=15,
        duration_max_s=180,
        default_duration_s=60,
        captions_default=True,
        cover_ratio="3:4",
        title_max_chars=20,
        hashtags=("#干货分享", "#学习笔记", "#科普", "#自我提升"),
        hook_hint="标题党但真诚，突出「收藏价值」与清单感，正文口语带 emoji",
    ),
    "shipinhao": ChannelPreset(
        key="shipinhao",
        label="视频号",
        canvas_format=CANVAS_PORTRAIT,
        aspect="9:16",
        duration_min_s=15,
        duration_max_s=180,
        default_duration_s=60,
        captions_default=True,
        cover_ratio="9:16",
        title_max_chars=24,
        hashtags=("#科普", "#知识", "#涨知识", "#深度"),
        hook_hint="面向微信生态熟人传播，语气稳重、有信息增量、少浮夸",
    ),
    "bilibili": ChannelPreset(
        key="bilibili",
        label="B站",
        canvas_format=CANVAS_LANDSCAPE,
        aspect="16:9",
        duration_min_s=30,
        duration_max_s=330,
        default_duration_s=120,
        captions_default=True,
        cover_ratio="16:9",
        title_max_chars=40,
        hashtags=("#科普", "#知识分享", "#涨知识", "#硬核"),
        hook_hint="允许铺垫与梗，信息密度高、有深度，标题可带系列感",
    ),
    "tiktok": ChannelPreset(
        key="tiktok",
        label="TikTok",
        canvas_format=CANVAS_PORTRAIT,
        aspect="9:16",
        duration_min_s=15,
        duration_max_s=90,
        default_duration_s=40,
        captions_default=True,
        cover_ratio="9:16",
        title_max_chars=30,
        hashtags=("#learnontiktok", "#facts", "#howto", "#knowledge"),
        hook_hint="极强钩子、前 1 秒抓眼球、句子极短、信息点直给",
    ),
}

DEFAULT_CHANNEL = "douyin"

# 供前端渠道选择器与后端校验共用的有序列表。
CHANNEL_KEYS: tuple[str, ...] = tuple(CHANNELS)


def normalize_channel(value: object) -> str:
    """渠道归一化：未知值回默认渠道（抖音，竖屏短视频最通用）。"""
    key = str(value or "").strip().lower()
    return key if key in CHANNELS else DEFAULT_CHANNEL


def get_channel(value: object) -> ChannelPreset:
    """按（可能不规范的值）取渠道预设。"""
    return CHANNELS[normalize_channel(value)]


def resolve_duration(channel: object, requested: object) -> int:
    """把用户填的时长夹到该渠道的 [min, max]；非法/缺省回渠道默认档。"""
    preset = get_channel(channel)
    try:
        seconds = int(float(requested))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return preset.default_duration_s
    if seconds <= 0:
        return preset.default_duration_s
    return max(preset.duration_min_s, min(preset.duration_max_s, seconds))


def copy_suggestions(
    channel: object,
    *,
    title: str = "",
    narration: str = "",
) -> dict[str, object]:
    """给出该渠道的成片文案建议（标题 / 话题标签 / 正文引子）。

    纯本地字符串组装，不调模型、不出网；上层若想要更聪明的文案可另走 gzh 写作要点。
    """
    preset = get_channel(channel)
    clean_title = str(title or "").strip()
    display_title = (
        clean_title[: preset.title_max_chars] if clean_title else ""
    )
    lines = [part.strip() for part in str(narration or "").splitlines() if part.strip()]
    lead = lines[0][:80] if lines else ""
    return {
        "channel": preset.key,
        "channel_label": preset.label,
        "aspect": preset.aspect,
        "cover_ratio": preset.cover_ratio,
        "title": display_title,
        "title_max_chars": preset.title_max_chars,
        "hashtags": list(preset.hashtags),
        "description": f"{lead}\n\n{preset.hook_hint}".strip(),
        "hook_hint": preset.hook_hint,
    }


__all__ = [
    "CANVAS_LANDSCAPE",
    "CANVAS_PORTRAIT",
    "CANVAS_SQUARE",
    "CHANNELS",
    "CHANNEL_KEYS",
    "DEFAULT_CHANNEL",
    "ChannelPreset",
    "normalize_channel",
    "get_channel",
    "resolve_duration",
    "copy_suggestions",
]
