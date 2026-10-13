# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号文章写作层（纯 prompt 组装 + Markdown 规整）。

本模块**不触网、不调模型**——只负责：把「风格要点 + 去 AI 味约束 + 素材」拼成给网关
文本模型的写作指令（``build_write_prompt``），并对模型返回的 Markdown 做轻量规整
（``normalize_markdown``，剥掉误加的整篇代码围栏、收敛多余空行）。真正调用
``generate_freezone_text`` 由 runner 经既有出网分发完成，保持与其它文本任务一致的
egress 记账。
"""

from __future__ import annotations

import re

from .writer_styles import COMMON_RULES, normalize_style, style_or_default

MAX_MATERIAL_CHARS = 12000


def _clamp_material(material: str) -> str:
    text = str(material or "").strip()
    if len(text) <= MAX_MATERIAL_CHARS:
        return text
    return text[:MAX_MATERIAL_CHARS]


def build_write_prompt(
    *,
    topic: str = "",
    material: str = "",
    style: object = None,
    target_words: int = 1500,
    audience: str = "",
    tone: str = "",
) -> str:
    """组装写作指令：风格 + 通用约束 + 素材 + 具体化参数。"""
    style_id = normalize_style(style)
    resolved = style_or_default(style_id)

    topic_clean = str(topic or "").strip()
    material_clean = _clamp_material(material)
    audience_clean = str(audience or "").strip()
    tone_clean = str(tone or "").strip()

    try:
        words = int(target_words)
    except (TypeError, ValueError):
        words = 1500
    words = max(300, min(6000, words))

    lines: list[str] = [
        "你是一名资深公众号主笔。请按要求写一篇文章，只输出 Markdown 正文。",
        "",
        f"【写作风格】{resolved.name_cn}：{resolved.desc}",
    ]
    lines += [f"- {h}" for h in resolved.hints]

    lines += ["", "【通用规则】"]
    lines += [f"- {r}" for r in COMMON_RULES]

    if topic_clean:
        lines += ["", f"【主题】{topic_clean}"]
    if audience_clean:
        lines += [f"【目标读者】{audience_clean}"]
    if tone_clean:
        lines += [f"【语气】{tone_clean}"]
    lines += [f"【篇幅】正文约 {words} 字。"]

    if material_clean:
        lines += [
            "",
            "【素材】（只能在这份资料范围内取材改写，不得编造其中没有的事实）",
            material_clean,
        ]
    elif topic_clean:
        lines += [
            "",
            "【素材】未提供参考素材，可基于常识与公开通识展开，但观点要标明是判断、"
            "不要虚构具体数据或来源。",
        ]

    lines += ["", "现在直接输出 Markdown 文章，第一行为 `# 标题`，不要额外解释。"]
    return "\n".join(lines)


_FENCE_RX = re.compile(r"^\s*```(?:markdown|md|text)?\s*\n(.*)\n```\s*$", re.S | re.I)


def normalize_markdown(raw: str) -> str:
    """规整模型返回：剥整篇代码围栏、去首尾空白、收敛 3+ 连续空行为 2 行。"""
    text = str(raw or "").strip()
    # 若整篇被一个 markdown 围栏包裹，取围栏内内容。
    m = _FENCE_RX.match(text)
    if m:
        text = m.group(1).strip()
    # 收敛多余空行。
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def has_markdown_structure(md: str) -> bool:
    """粗校验：产物是否含至少一个标题（作为可排版的下界）。"""
    text = str(md or "")
    return bool(re.search(r"^#{1,6}\s+\S", text, re.M))


__all__ = [
    "build_write_prompt",
    "normalize_markdown",
    "has_markdown_structure",
    "MAX_MATERIAL_CHARS",
]
