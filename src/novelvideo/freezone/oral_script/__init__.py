# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 短视频口播稿创作（工具栏首项）的方法论层。

方法论蒸馏自开源技能包「太上老君·AI炼丹炉」
（https://github.com/liuhaisi2026212-maker/taishang-laojun，创作者：柳爷「柳爷不迷
糊」，本体许可 CC BY-NC 4.0）。这里搬运的是**判断方式**而不是句式：炉心七条底层判
断、开炉（第一句 + 勾证衔接 + 中段推进 + 断头台收尾）、回炉（完整成稿）、收丹（标
题）、改稿六刀与去 AI 味规则库。原仓库的公开逐字稿、作者固定句式与个人素材都没有
带进来；对外交付物里也不出现「柳爷」「炉子」「模块」这些内部名字（原技能明确要求
「对用户说大白话，不报模块名」）。

本包**不触网、不调模型**：``prompts`` 只组装给网关文本模型的写作指令，``deai`` 是
纯本地的去 AI 味机械预检（蒸馏自原仓库 ``ai_tell_linter.py``），``script`` 负责把模
型返回的分节 Markdown 拆成结构化产物。真正出网只有一处——runner 经
``generate_freezone_text`` 走既有文本网关分发，与公众号文章任务同一口径。
"""

from .deai import AiTellHit, lint_script
from .prompts import (
    DEFAULT_TITLE_COUNT,
    DURATION_BANDS,
    FORMULAS,
    build_oral_script_prompt,
    build_rewrite_prompt,
    clamp_duration,
    clamp_title_count,
    estimate_speech_seconds,
    normalize_formula,
    resolve_word_budget,
)
from .script import (
    SECTION_IDS,
    SECTION_TITLES,
    OralScriptParsed,
    delivered_text,
    parse_oral_script,
    section_records,
)

__all__ = [
    "AiTellHit",
    "DEFAULT_TITLE_COUNT",
    "DURATION_BANDS",
    "FORMULAS",
    "OralScriptParsed",
    "SECTION_IDS",
    "SECTION_TITLES",
    "build_oral_script_prompt",
    "build_rewrite_prompt",
    "clamp_duration",
    "clamp_title_count",
    "delivered_text",
    "estimate_speech_seconds",
    "lint_script",
    "normalize_formula",
    "parse_oral_script",
    "resolve_word_budget",
    "section_records",
]
