# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号排版内置主题库（设计变量，非第三方原文件）。

每个主题 = 一组**设计 token**（配色 / 字号 / 行高 / 字距 / 正文下划线 / 强调色配额 /
是否深底），由 ``layout.py`` 的组件模板消费。这里只有数据，没有 HTML；HTML 片段全
部在 ``layout.py`` 里按 token 现拼，确保「不凭记忆手写第三方组件、不 vendor 原文件」。

主题色值为公开的常用配色，命名沿用社区习惯（中文名对用户展示、英文标识用于产物
命名）。默认主题 ``DEFAULT_THEME``。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GzhTheme:
    id: str
    name_cn: str
    desc: str
    dark: bool  # 深底主题（如终端风），正文容器需改白字
    # 配色
    primary: str  # 主色（下划线 / 章节装饰 / 强调）
    title: str  # 标题色
    body: str  # 正文色
    muted: str  # 辅助文字（署名 / 小标签）
    line: str  # 细线 / 分隔色
    chip_bg: str  # 标签底色
    page_bg: str  # 全局容器底
    block_bg: str  # 引用 / 卡片底
    accent: str  # 点睛强调色（全篇 ≤3 处）
    # 排版参数
    body_size: int = 15
    line_height: float = 1.8
    letter_spacing: float = 0.3
    max_width: int = 677
    underline_css: str = ""  # 正文关键词下划线（权威值）

    def tokens(self) -> dict[str, str]:
        """给 layout 消费的字符串化 token 表。"""
        return {
            "primary": self.primary,
            "title": self.title,
            "body": self.body,
            "muted": self.muted,
            "line": self.line,
            "chip_bg": self.chip_bg,
            "page_bg": self.page_bg,
            "block_bg": self.block_bg,
            "accent": self.accent,
            "body_size": f"{self.body_size}px",
            "line_height": f"{self.line_height}",
            "letter_spacing": f"{self.letter_spacing}px",
            "max_width": f"{self.max_width}px",
            "underline_css": self.underline_css,
        }


_FONT_LIGHT = "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif"
_FONT_MONO = "'SF Mono',Menlo,Consolas,'Courier New',monospace"


THEMES: dict[str, GzhTheme] = {
    "graphite-minimal": GzhTheme(
        id="graphite-minimal",
        name_cn="石墨极简",
        desc="设计、科技评论、专业观点、高端品牌（灰阶克制、大留白）",
        dark=False,
        primary="#52525B",
        title="#27272A",
        body="#52525B",
        muted="#A1A1AA",
        line="#E4E4E7",
        chip_bg="#F4F4F5",
        page_bg="#FFFFFF",
        block_bg="#FAFAFA",
        accent="#F97316",
        underline_css="border-bottom:2px solid #52525B;font-weight:600;",
    ),
    "moyu-green": GzhTheme(
        id="moyu-green",
        name_cn="摸鱼绿",
        desc="教程、测评、清单、工具盘点（卡片丰富、信息密度高，默认推荐）",
        dark=False,
        primary="#059669",
        title="#064E3B",
        body="#374151",
        muted="#6B7280",
        line="#A7F3D0",
        chip_bg="#ECFDF5",
        page_bg="#FFFFFF",
        block_bg="#F0FDF4",
        accent="#059669",
        underline_css="border-bottom:2px solid #A7F3D0;font-weight:600;",
    ),
    "red-white": GzhTheme(
        id="red-white",
        name_cn="红白深度",
        desc="深度分析、观点、力量感话题（编号章节、红色克制点睛）",
        dark=False,
        primary="#DC2626",
        title="#1F2937",
        body="#374151",
        muted="#9CA3AF",
        line="#FECACA",
        chip_bg="#FEF2F2",
        page_bg="#FFFFFF",
        block_bg="#FFF5F5",
        accent="#DC2626",
        underline_css="border-bottom:2px solid #FECACA;font-weight:600;",
    ),
    "amber-orange": GzhTheme(
        id="amber-orange",
        name_cn="琥珀暖阳",
        desc="创意话题、品牌故事、生活随笔（温暖明快）",
        dark=False,
        primary="#EA580C",
        title="#7C2D12",
        body="#44403C",
        muted="#A8A29E",
        line="#FED7AA",
        chip_bg="#FFEDD5",
        page_bg="#FFFDFB",
        block_bg="#FFF7ED",
        accent="#EA580C",
        underline_css="border-bottom:2px solid #FDBA74;font-weight:600;",
    ),
    "terminal-geek": GzhTheme(
        id="terminal-geek",
        name_cn="终端极客",
        desc="技术、编程、极客向（深色终端窗口风、霓虹绿）",
        dark=True,
        primary="#3FB950",
        title="#E6EDF3",
        body="#C9D1D9",
        muted="#8B949E",
        line="#30363D",
        chip_bg="#1F2428",
        page_bg="#0D1117",
        block_bg="#161B22",
        accent="#39C5CF",
        underline_css="border-bottom:2px solid #238636;font-weight:600;",
    ),
    "zen-whitespace": GzhTheme(
        id="zen-whitespace",
        name_cn="留白禅意",
        desc="随笔、冥想、深度生活流（墨绿细线、呼吸感最强）",
        dark=False,
        primary="#4A5D52",
        title="#2F3E36",
        body="#4B5563",
        muted="#9AA5A0",
        line="#B5C8BC",
        chip_bg="#EEF3F0",
        page_bg="#FCFCFA",
        block_bg="#F3F6F4",
        accent="#4A5D52",
        body_size=16,
        line_height=2.0,
        letter_spacing=0.5,
        underline_css="border-bottom:1.5px solid #B5C8BC;font-weight:500;",
    ),
}

DEFAULT_THEME = "moyu-green"


def normalize_theme(value: object) -> str:
    """把任意入参归一到已注册主题 id；未知回落默认。"""
    text = str(value or "").strip().lower()
    if text in THEMES:
        return text
    short = text.replace("_", "-")
    if short in THEMES:
        return short
    return DEFAULT_THEME


def theme_or_default(value: object) -> GzhTheme:
    return THEMES[normalize_theme(value)]


__all__ = [
    "GzhTheme",
    "THEMES",
    "DEFAULT_THEME",
    "normalize_theme",
    "theme_or_default",
    "_FONT_LIGHT",
    "_FONT_MONO",
]
