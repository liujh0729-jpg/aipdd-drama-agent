# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""画风图册（风格名录）的加载与提示词拼装。

案例图册回答「别人这片子是怎么做的」，这份图册回答「想要某种观感，该按什么口径
让画布去画」：66 条风格（51 支画风 + 15 支 MG 动态设计），每条自带风格特征、色板、
代码技法路线、运动性格、排版与禁则。名录参考上游两个风格仓库，**规格文字与色板全部
按本画布的渲染契约重写**，不收录上游任何素材、样片与文案。

与案例图册同一个思路：风格事实放在随包的 ``motion_styles.json``，想换一套名录时把
``MOTION_STYLE_MANIFEST`` 指到外部文件即可；外部清单读坏了回落内置并打 warning，
内置读不出来才抛 —— 那是打包坏了。

渲染契约**不在这份清单里重复一遍**：它只有 ``motion_templates.json`` 一个出处，
:func:`compose_motion_style_prompt` 在拼装时才把它接上。于是图册、「存入提示词库」
和渲染任务拿到的契约文字永远与案例那侧一致，改一次就够。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MANIFEST_ENV = "MOTION_STYLE_MANIFEST"
BUILTIN_MANIFEST_PATH = Path(__file__).with_name("motion_styles.json")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# 风格在提示词库里的登记 key 前缀：`motion-style.<style_id>`。
# 与案例的 `motion.` 分开：两条名录会各自增删条目，共用前缀就没法按前缀还原出处。
PROMPT_KEY_PREFIX = "motion-style."

_REQUIRED_TEXT_FIELDS = (
    "id",
    "label",
    "label_en",
    "category",
    "brief",
    "prompt_cn",
    "source",
)
_MIN_PALETTE = 2
_MAX_PALETTE = 8

_CacheKey = tuple[Any, ...]
_cache: Optional[tuple[_CacheKey, dict]] = None


class MotionStyleManifestError(ValueError):
    """风格清单结构不合法。"""


def _manifest_path() -> Path:
    override = os.environ.get(MANIFEST_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return BUILTIN_MANIFEST_PATH


def _cache_key(path: Path) -> _CacheKey:
    """带上 mtime/size：原地换清单文件后下一次请求就能生效。"""
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _text(item: dict, field: str, index: int, what: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MotionStyleManifestError(f"第 {index} 条{what}缺少字段 {field}")
    return value


def _parse_style(index: int, item: Any, seen: set[str]) -> dict:
    if not isinstance(item, dict):
        raise MotionStyleManifestError(f"第 {index} 条风格不是对象")
    parsed = {field: _text(item, field, index, "风格") for field in _REQUIRED_TEXT_FIELDS}
    if parsed["id"] in seen:
        raise MotionStyleManifestError(f"风格 id 重复: {parsed['id']}")
    seen.add(parsed["id"])

    tags = item.get("tags", [])
    if not isinstance(tags, list) or not tags or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags
    ):
        raise MotionStyleManifestError(f"风格 {parsed['id']} 的 tags 必须是非空字符串数组")
    parsed["tags"] = [tag.strip() for tag in tags]

    # 色板是卡片上那块代码画出来的样张：没有它，风格墙就是一排灰块。
    palette = item.get("palette")
    if not isinstance(palette, list) or not (_MIN_PALETTE <= len(palette) <= _MAX_PALETTE):
        raise MotionStyleManifestError(
            f"风格 {parsed['id']} 的 palette 必须是 {_MIN_PALETTE}～{_MAX_PALETTE} 项数组"
        )
    for color in palette:
        if not isinstance(color, str) or not _HEX_COLOR_RE.match(color.strip()):
            raise MotionStyleManifestError(
                f"风格 {parsed['id']} 的色板项不是 #rrggbb: {color!r}"
            )
    parsed["palette"] = [color.strip().lower() for color in palette]
    return parsed


def _parse_manifest(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise MotionStyleManifestError("清单顶层必须是对象")

    styles = raw.get("styles")
    if not isinstance(styles, list) or not styles:
        raise MotionStyleManifestError("styles 必须是非空数组")
    seen: set[str] = set()
    parsed_styles = [_parse_style(index, item, seen) for index, item in enumerate(styles)]

    categories = raw.get("categories")
    if not isinstance(categories, list) or not categories or not all(
        isinstance(name, str) and name.strip() for name in categories
    ):
        raise MotionStyleManifestError("categories 必须是非空字符串数组")
    # 分类顺序是展示口径；清单里出现未登记的分类必须吵，否则筛选条会静默多出未知项。
    undeclared = sorted({style["category"] for style in parsed_styles} - set(categories))
    if undeclared:
        raise MotionStyleManifestError(f"风格使用了未登记的分类: {', '.join(undeclared)}")

    return {
        "version": str(raw.get("version") or ""),
        "source": str(raw.get("source") or ""),
        "categories": list(categories),
        "styles": parsed_styles,
    }


def _read_manifest(path: Path) -> dict:
    return _parse_manifest(json.loads(path.read_text(encoding="utf-8")))


def _load() -> dict:
    global _cache

    path = _manifest_path()
    key = _cache_key(path)
    if _cache is not None and _cache[0] == key:
        return _cache[1]

    try:
        manifest = _read_manifest(path)
    except (OSError, ValueError) as exc:
        if path == BUILTIN_MANIFEST_PATH:
            raise RuntimeError(f"内置画风图册清单不可用: {path}") from exc
        logger.warning(
            "[freezone] 画风图册清单 %s 不可用(%s)，回落到内置清单", path, exc
        )
        manifest = _read_manifest(BUILTIN_MANIFEST_PATH)

    _cache = (key, manifest)
    return manifest


def reset_motion_style_cache() -> None:
    """改环境变量或原地换文件后强制重读；测试用。"""
    global _cache
    _cache = None


def load_motion_style_manifest() -> dict:
    """整份风格图册（风格、分类顺序、出处），顶层键与清单文件一致。"""
    return _load()


def load_motion_styles() -> list[dict]:
    # 内层 tags/palette 也要复制：只 dict(style) 挡不住 `item["palette"].append(...)`，
    # 那会顺着缓存污染后面每一次请求。
    return [
        {**style, "tags": list(style["tags"]), "palette": list(style["palette"])}
        for style in _load()["styles"]
    ]


def get_motion_style(style_id: str) -> Optional[dict]:
    for style in _load()["styles"]:
        if style["id"] == style_id:
            return style
    return None


def compose_motion_style_prompt(style_id: str) -> str:
    """风格规格 + 本系统渲染契约，拼成可直接交给模型的一条提示词。

    契约取自案例图册那份清单（唯一出处），所以「按风格出片」与「按案例出片」受同
    一套渲染约束；风格图册自己的文件里不存契约，免得两份文字各改各的。
    """
    from novelvideo.freezone.motion_templates import load_motion_manifest

    for style in _load()["styles"]:
        if style["id"] == style_id:
            return (
                style["prompt_cn"].rstrip()
                + "\n\n"
                + load_motion_manifest()["render_contract"].strip()
                + "\n"
            )
    raise KeyError(style_id)


def motion_style_prompt_key(style_id: str) -> str:
    """风格在提示词库里的登记 key。

    登记方（prompt_library_defaults）与读取方（图册路由、渲染任务）都走这个换算，
    免得两边各拼一次前缀、拼错一边后覆盖值静默失效。
    """
    return f"{PROMPT_KEY_PREFIX}{style_id}"
