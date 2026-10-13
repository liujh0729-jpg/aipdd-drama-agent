# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号写作风格库（方法论要点，供 prompt 组装）。

内置若干套常用写作风格的**要点提示**（自研归纳，不复制第三方原文件），连同「去 AI
味」通用约束一起，由 ``write.build_write_prompt`` 拼进网关文本模型的写作指令。风格
只影响文字表达，产物仍是结构化 Markdown，交给 ``layout`` 排版。
"""

from __future__ import annotations

from dataclasses import dataclass

# 所有风格共享的「去 AI 味」硬约束 + 输出契约。
COMMON_RULES = (
    "只用 Markdown 组织结构，不要输出 HTML、不要代码围栏包裹全文。",
    "第一行用 `# 文章标题`；分节用 `## 章节标题`（3-6 节）；小节用 `### `。",
    "正文关键词用 `**加粗**`，核心概念用 `==高亮==`，需下划线强调用 `++文字++`。",
    "需要金句时单独一行以 `> ` 引用；步骤用 `1. 2. 3.`；要点用 `- `。",
    "中文标点一律全角（，。！？：；），不要夹杂半角标点与英文引号。",
    "少用「首先/其次/综上所述/值得注意的是」等模板过渡词，句子长短交错，口语自然。",
    "不编造事实与数据；素材没给的不要臆测，可写观点但要标明是判断。",
    "结尾另起一段给出可执行的收束或互动，不要空洞总结。",
)


@dataclass(frozen=True, slots=True)
class WriterStyle:
    id: str
    name_cn: str
    desc: str
    hints: tuple[str, ...]


STYLES: dict[str, WriterStyle] = {
    "viewpoint": WriterStyle(
        id="viewpoint",
        name_cn="观点深度",
        desc="深度分析、观点输出（判断先行、逻辑递进、金句点睛）",
        hints=(
            "开篇抛出一个鲜明判断，而非背景铺垫。",
            "每个章节围绕一个分论点展开，论点—论据—推论层层递进。",
            "多用短句制造节奏，关键结论单独成段加重。",
            "在恰当处给出反常识视角或代价权衡。",
        ),
    ),
    "tutorial": WriterStyle(
        id="tutorial",
        name_cn="教程清单",
        desc="教程、操作指南、工具盘点（步骤清晰、信息密度高）",
        hints=(
            "先讲清「能解决什么问题」，再进入步骤。",
            "操作步骤用有序列表，一步一个动作，可直接照做。",
            "工具/条目用要点列表，每项给一句用途与一句注意点。",
            "关键参数、易错点用 ==高亮== 或 **加粗** 标出。",
        ),
    ),
    "emotional": WriterStyle(
        id="emotional",
        name_cn="情绪共鸣",
        desc="情感、生活、观点随笔（强共鸣、口语、短句、金句密度高）",
        hints=(
            "从具体场景或一句扎心的话切入。",
            "句子尽量短，一段不超过三行，制造呼吸与情绪起伏。",
            "把抽象道理落到可感的细节和人物处境上。",
            "每节留一句可被截图转发的金句。",
        ),
    ),
    "data": WriterStyle(
        id="data",
        name_cn="数据复盘",
        desc="复盘、报告、增长分析（结论前置、数据说话、克制表达）",
        hints=(
            "开头用一句话给出最重要的结论。",
            "关键指标用表格或有序列表呈现，先数字后解读。",
            "区分事实（数据）与判断（解读），不混写。",
            "避免形容词堆砌，用可核对的表述。",
        ),
    ),
    "story": WriterStyle(
        id="story",
        name_cn="叙事特稿",
        desc="人物、案例、经历叙事（场景化、时间线、细节驱动）",
        hints=(
            "用一个有画面感的场景开场。",
            "按时间或因果推进，用 `### ` 切分关键节点。",
            "多用具体动作与对话，少用抽象概括。",
            "在转折处用 `> ` 引用原话或金句。",
        ),
    ),
    "rational": WriterStyle(
        id="rational",
        name_cn="理性干货",
        desc="科技评论、专业科普（克制冷静、结构严谨、去情绪化）",
        hints=(
            "先给定义与边界，再展开机制。",
            "论证遵循「是什么—为什么—怎么用」的顺序。",
            "术语首次出现给一句白话解释。",
            "观点与证据对齐，不过度推论。",
        ),
    ),
}

DEFAULT_STYLE = "viewpoint"


def normalize_style(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in STYLES:
        return text
    short = text.replace("_", "-")
    if short in STYLES:
        return short
    return DEFAULT_STYLE


def style_or_default(value: object) -> WriterStyle:
    return STYLES[normalize_style(value)]


__all__ = [
    "WriterStyle",
    "STYLES",
    "DEFAULT_STYLE",
    "COMMON_RULES",
    "normalize_style",
    "style_or_default",
]
