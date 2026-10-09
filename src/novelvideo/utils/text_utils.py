"""文本处理工具函数。"""

import re

# 颜色词列表（单字和多字）
_COLOR_WORDS = [
    # 双字及以上颜色词（先匹配长的）
    "橘红", "橘黄", "橙红", "橙黄", "暗红", "深红", "鲜红", "猩红", "殷红", "嫣红",
    "粉红", "桃红", "玫红", "枣红", "酒红", "暗绿", "深绿", "浅绿", "翠绿", "墨绿",
    "碧绿", "草绿", "嫩绿", "橄榄绿", "暗蓝", "深蓝", "浅蓝", "湛蓝", "蔚蓝", "靛蓝",
    "宝蓝", "天蓝", "冷蓝", "暗黄", "深黄", "浅黄", "金黄", "鹅黄", "暖黄", "土黄",
    "明黄", "暗紫", "深紫", "浅紫", "淡紫", "紫红", "暗灰", "深灰", "浅灰", "银灰",
    "雪白", "惨白", "苍白", "煞白", "发白", "纯白", "乳白", "月白", "漆黑", "乌黑",
    "黝黑", "焦黑", "赤红", "朱红", "橘色", "橙色", "红色", "绿色", "蓝色", "黄色",
    "紫色", "灰色", "白色", "黑色", "粉色", "棕色", "褐色", "青色", "金色", "银色",
    "铜色", "玫瑰色", "琥珀色",
    # 单字颜色词
    "红", "绿", "蓝", "黄", "紫", "灰", "白", "黑", "粉", "棕", "褐", "青",
    "金", "银", "铜", "翠", "碧", "赤", "朱", "丹", "绯", "绛", "橘", "橙",
]

# 按长度降序排列，确保先匹配长词
_COLOR_WORDS.sort(key=len, reverse=True)

# 编译正则：匹配颜色词 + 可选的"色"/"色的"/"的"后缀
_COLOR_PATTERN = re.compile(
    r"(" + "|".join(re.escape(w) for w in _COLOR_WORDS) + r")" + r"(?:色的|色|的)?"
)

# 占位符前缀（用不可能出现在正常文本中的字符序列）
_PLACEHOLDER_PREFIX = "\x00MARK"


def strip_color_words(text: str) -> str:
    """从文本中剥离颜色修饰词，保留 {{}} 标记内的内容不做替换。

    策略：所有含颜色字的词都过滤，草图只需构图和姿态，颜色全靠标记系统。

    Examples:
        >>> strip_color_words("惨白脸色")
        '脸色'
        >>> strip_color_words("红色的长裙")
        '长裙'
        >>> strip_color_words("红盖头")
        '盖头'
        >>> strip_color_words("青石板")
        '石板'
        >>> strip_color_words("金銮殿")
        '銮殿'
        >>> strip_color_words("白玉栏杆")
        '玉栏杆'
        >>> strip_color_words("{{沈知薇_大婚}}鲜红的嫁衣")
        '{{沈知薇_大婚}}嫁衣'
    """
    if not text:
        return text

    # 1. 保护 {{}} 标记：替换为占位符
    markers: list[str] = []
    def _protect_marker(m: re.Match) -> str:
        idx = len(markers)
        markers.append(m.group(0))
        return f"{_PLACEHOLDER_PREFIX}{idx}\x00"

    protected = re.sub(r"\{\{.*?\}\}", _protect_marker, text)

    # 2. 剥离颜色词
    result = _COLOR_PATTERN.sub("", protected)

    # 3. 恢复占位符
    for idx, marker in enumerate(markers):
        result = result.replace(f"{_PLACEHOLDER_PREFIX}{idx}\x00", marker)

    return result
