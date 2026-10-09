"""统一的 {{}} 标记解析工具。

支持两种格式:
- {{identity_id}} (新格式): 如 {{苏清晏_少女}}
- {{角色名}} (旧格式，向后兼容): 如 {{苏清晏}}

用于:
- prompt_builder.py: 替换为 character_tag
- grid_generation（包）: 替换为 character_tag
- state.py: 替换为角色外貌描述或真实姓名
"""

import hashlib
import re
from typing import Optional

from pypinyin import pinyin, Style


def resolve_marker(
    marker: str,
    character_map: dict[str, dict],
    identity_to_char: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """解析单个 {{}} 标记。

    Args:
        marker: 标记内容（不含括号），如 "苏清晏_少女" 或 "苏清晏"
        character_map: {角色名: {character_tag, ...}} 角色配置
        identity_to_char: {identity_id: 角色名} 反向映射（可选）

    Returns:
        (角色名, character_tag) 或 (None, None) 如果未匹配
    """
    # 1. 直接匹配角色名（旧格式 {{角色名}}）
    if marker in character_map:
        char_cfg = character_map[marker]
        char_tag = _get_char_tag(marker, char_cfg)
        return (marker, char_tag)

    # 2. 通过 identity_to_char 反查角色名（新格式 {{identity_id}}）
    if identity_to_char and marker in identity_to_char:
        char_name = identity_to_char[marker]
        if char_name in character_map:
            char_cfg = character_map[char_name]
            char_tag = _get_char_tag(char_name, char_cfg)
            return (char_name, char_tag)

    # 3. 前缀匹配：identity_id 通常是 "{角色名}_{状态}"
    for char_name in character_map:
        if marker.startswith(char_name + "_") or marker.startswith(char_name + "·"):
            char_cfg = character_map[char_name]
            char_tag = _get_char_tag(char_name, char_cfg)
            return (char_name, char_tag)

    return (None, None)


def resolve_visual_description_markers(
    visual_description: str,
    character_map: dict[str, dict],
    identity_to_char: dict[str, str] | None = None,
    use_names: bool = False,
    keep_identity_suffix: bool = False,
    use_identity_id: bool = False,
    append_char_name: bool = False,
) -> str:
    """替换 visual_description 中的 {{}} 标记为 character_tag 或角色名。

    支持:
    - {{identity_id}} → 解析角色名 → char_tag / 角色名 / identity_id
    - {{角色名}} → char_tag / 角色名（向后兼容）
    - {{未知}} → 去掉括号保留文字

    Args:
        visual_description: 包含 {{}} 标记的画面描述
        character_map: {角色名: {character_tag, ...}} 或 {角色名: CharConfig}
        identity_to_char: {identity_id: 角色名} 反向映射（可选）
        use_names: True 时返回角色名而非 tag（Sketch 模式用）
        keep_identity_suffix: True 时保留身份后缀，如 唐若瑜(婚后时期)（Sketch 模式用）
        use_identity_id: True 时直接返回 identity_id 原文（如 唐若瑜_婚后时期）
        append_char_name: True 时在 identity tag 后附加角色中文名，
            如 [TRY_2ec7·唐若瑜]（仅 use_identity_id=True 时生效，Sketch 模式用）

    Returns:
        替换后的文本
    """
    if not visual_description:
        return visual_description

    def replace_match(m: re.Match) -> str:
        marker = m.group(1)
        char_name, char_tag = resolve_marker(marker, character_map, identity_to_char)
        if char_tag:
            if use_identity_id:
                # 身份级 tag：同一角色不同身份产生不同 hash
                identity_tag = compute_char_tag(char_name, identity_id=marker)
                if append_char_name and char_name:
                    # [TRY_2ec7] → [TRY_2ec7·唐若瑜]
                    identity_tag = identity_tag[:-1] + f"·{char_name}]"
                return f"{identity_tag} "
            if use_names:
                # 保留身份后缀：{{唐若瑜_婚后时期}} → 唐若瑜(婚后时期)
                if keep_identity_suffix and marker != char_name and marker.startswith(char_name + "_"):
                    suffix = marker[len(char_name) + 1:]
                    return f"{char_name}({suffix})"
                return char_name
            return char_tag
        # 未匹配：原样保留（不丢括号）
        return m.group(0)

    return re.sub(r'\{?\{([^}]+)\}\}?', replace_match, visual_description)


def build_identity_to_char_map(
    character_map: dict[str, dict],
    valid_identity_ids: set[str] | None = None,
) -> dict[str, str]:
    """从 character_map 构建 identity_id → 角色名 的反向映射。

    遍历 character_map 中角色的 identities（如果有），
    构建 identity_id → char_name 映射。

    对于简单的 character_map（只有 character_tag），
    使用 "角色名_*" 前缀匹配规则。

    Args:
        character_map: {角色名: {...}} 角色配置
        valid_identity_ids: 合法 identity_id 集合（可选）

    Returns:
        {identity_id: 角色名}
    """
    result = {}

    if valid_identity_ids:
        # 用前缀匹配将 identity_id 映射到角色名
        for identity_id in valid_identity_ids:
            for char_name in character_map:
                if identity_id.startswith(char_name + "_") or identity_id == char_name:
                    result[identity_id] = char_name
                    break

    return result


def _get_char_tag(char_name: str, char_cfg) -> str:
    """从角色配置中获取 character_tag。

    兼容 dict 和对象两种配置格式。
    """
    if isinstance(char_cfg, dict):
        tag = char_cfg.get("character_tag", "")
        if not tag:
            tag = compute_char_tag(char_name)
        return tag
    else:
        # 对象格式 (CharConfig)
        tag = getattr(char_cfg, "character_tag", None) or ""
        if not tag:
            tag = compute_char_tag(char_name)
        return tag


def compute_char_tag(char_name: str, identity_id: str | None = None) -> str:
    """计算角色标签（拼音首字母大写 + hash 后缀）。

    当提供 identity_id 时，hash 基于 identity_id 计算，
    使同一角色的不同身份产生不同 tag。

    Args:
        char_name: 角色名（用于生成拼音首字母前缀）
        identity_id: 身份 ID（可选，用于区分同角色不同身份的 hash）

    Returns:
        如 "[TRY_a1b2]"
    """
    initials = []
    for char in char_name:
        if re.match(r'[a-zA-Z0-9]', char):
            initials.append(char.upper())
        elif re.match(r'[\u4e00-\u9fff]', char):
            py = pinyin(char, style=Style.FIRST_LETTER)
            if py and py[0]:
                initials.append(py[0][0].upper())

    pinyin_initials = ''.join(initials) if initials else 'CHAR'
    hash_source = identity_id if identity_id else char_name
    name_hash = hashlib.md5(hash_source.encode('utf-8')).hexdigest()[:4]
    return f"[{pinyin_initials}_{name_hash}]"
