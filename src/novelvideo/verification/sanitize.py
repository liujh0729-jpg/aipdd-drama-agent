"""Prompt 输入消毒工具 — 移除可能的注入指令。"""

import re


# 常见注入模式
_INJECTION_PATTERNS = [
    # 系统/角色指令覆盖（英文）
    re.compile(r"\[?\s*(SYSTEM|ASSISTANT|USER|HUMAN)\s*[:\]]\s*", re.IGNORECASE),
    # 常见注入指令（英文）
    re.compile(
        r"(?:ignore|disregard|forget|override|bypass)\s+"
        r"(?:(?:all|previous|above|prior|earlier)\s+)+"
        r"(?:instructions?|rules?|prompts?|constraints?)",
        re.IGNORECASE,
    ),
    # XML-style 标签注入
    re.compile(r"<\s*/?\s*(?:system|instruction|prompt|role)\s*>", re.IGNORECASE),
    # 中文注入指令：忽略/无视/跳过 + 指令/规则/审核/检查
    re.compile(
        r"(?:请|你应该|你必须|立即|直接)?"
        r"(?:忽略|无视|跳过|绕过|覆盖|放弃)"
        r"(?:之前|以上|上面|前面|所有|全部|一切)?"
        r"(?:的)?"
        r"(?:指令|规则|提示|约束|审核|检查|要求|限制)",
    ),
    # 中文注入：直接输出/返回 passed/true/通过
    re.compile(
        r"(?:请|你应该|你必须|立即)?"
        r"(?:直接|强制|无条件)"
        r"(?:输出|返回|回复|报告)"
        r".*?"
        r"(?:passed|true|通过|合格|满分|10分)",
    ),
]


def sanitize_prompt_input(text: str | None) -> str:
    """移除用户可控文本中的潜在注入指令。

    对 visual_description、scene_id、time_of_day、visual_style、
    appearance_details 等拼入 LLM prompt 的字段进行基本消毒。

    注意：这是一道防线而非银弹。核心防护依赖于 LLM 的 system prompt
    中的结构化输出约束和 output_type 强制解析。

    Args:
        text: 原始文本（可能为 None）

    Returns:
        消毒后的文本
    """
    if not text:
        return ""

    result = text
    for pattern in _INJECTION_PATTERNS:
        result = pattern.sub("", result)

    return result.strip()
