"""从格式化剧本确定性解析场景列表。

解析规则：
- X-Y 行匹配场景编号，地点信息在 X-Y 行尾或下一行
- 地点行 = X-Y 和 人物：之间的行（恰好 1 行）
- context_lines = 人物：之后到下个场景之间的所有行
- / 和 、 分隔多地点
- 短房间名继承第一个地点的建筑前缀
"""

from dataclasses import dataclass, field

from novelvideo.utils.screenplay_scene_parser import (
    EPISODE_HEADER_RE,
    chinese_to_int,
    is_scene_start_line,
    parse_scene_blocks,
    parse_character_line,
    parse_location_line,
)


@dataclass
class SceneCandidate:
    name: str
    time_of_day: str = "日"
    interior: bool = True
    episodes: list[int] = field(default_factory=list)
    context_lines: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)


def extract_synopsis(text: str) -> str:
    """提取第一集之前的梗概+人物设定部分。"""
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if EPISODE_HEADER_RE.match(stripped) or is_scene_start_line(stripped):
            return "\n".join(text.splitlines()[:i]).strip()
    return ""


def parse_scenes(text: str) -> list[SceneCandidate]:
    """从格式化剧本解析场景列表。

    支持两种格式：
    - 场景编号和地点在同一行：`1-2商场一层入口处 日 内`
    - 场景编号独立一行，地点在下一行：`1-1\n商场一层入口处 日 内`
    """
    blocks = parse_scene_blocks(text)
    scenes_by_name: dict[str, SceneCandidate] = {}
    for block in blocks:
        if not block.location:
            continue
        episode = block.episode or 1
        loc_info = parse_location_line(
            f"{block.location} {block.time_of_day or '日'} {block.interior_exterior or '内'}"
        )
        if not loc_info:
            loc_info = [(block.location, block.time_of_day or "日", block.interior_exterior != "外")]
        for name, tod, interior in loc_info:
            sc = scenes_by_name.get(name)
            if not sc:
                sc = SceneCandidate(name=name, time_of_day=tod, interior=interior)
                scenes_by_name[name] = sc
            if episode not in sc.episodes:
                sc.episodes.append(episode)
            for ch in block.characters:
                if ch not in sc.characters:
                    sc.characters.append(ch)
            sc.context_lines.extend(block.lines)

    return list(scenes_by_name.values())


def _parse_character_line(line: str) -> list[str]:
    """解析 人物：行，提取角色名。

    规则：
    - 按 、 分割
    - 括号中的名字提取为角色名：店员（小李）→ 小李
    - 无括号标注的保留原样
    """
    return parse_character_line(line)


def get_episode_characters(text: str) -> dict[int, list[str]]:
    """从剧本解析每集出场角色。返回 {集数: [角色名]}。"""
    scenes = parse_scenes(text)
    result: dict[int, set[str]] = {}
    for sc in scenes:
        for ep in sc.episodes:
            result.setdefault(ep, set()).update(sc.characters)
    return {ep: sorted(chars) for ep, chars in result.items()}


def _parse_location_line(line: str) -> list[tuple[str, str, bool]]:
    """解析地点行，返回 [(name, time_of_day, interior), ...]"""
    return parse_location_line(line)


def _chinese_to_int(s: str) -> int:
    """中文数字转 int，支持任意数（一~九千九百九十九）。"""
    return chinese_to_int(s)
