# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""动效大片图册清单的加载与兜底。

案例事实(名称、分类、出处、分辨率、时长、原片引用)与重写后的中文规格放在
`motion_templates.json`,随包发布;想换一套图册时把 `MOTION_GALLERY_MANIFEST`
指到外部文件即可,不必改代码重新发版 —— 和画风图册的 `STYLE_GALLERY_MANIFEST`
是同一个思路。

图册是浏览与取用提示词的能力,不该因为一个外部配置文件读坏了就把接口拖死:
外部清单读失败(不在、语法错、字段缺)一律回落内置那份并打 warning。内置那份
读不出来才抛 —— 那是打包坏了,必须吵。

规格文字分两块存:每条案例只存自己的分镜规格(`prompt_cn`),本系统渲染契约
(`render_contract`)整份清单存一次,取用时在这里拼成一条完整提示词。契约只在
一处定义,图册、提示词库、渲染任务拿到的都是同一份文字。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MANIFEST_ENV = "MOTION_GALLERY_MANIFEST"
BUILTIN_MANIFEST_PATH = Path(__file__).with_name("motion_templates.json")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# 案例在提示词库里的登记 key 前缀：`motion.<case_id>`。
PROMPT_KEY_PREFIX = "motion."

# 每条案例必须有的非空字符串字段。resolution/author 允许缺(外部图册常见),
# duration_seconds 允许缺 —— 缺了界面不显示时长,不影响取用提示词。
_REQUIRED_TEXT_FIELDS = ("id", "label", "category", "brief", "prompt_cn", "cover")

_CacheKey = tuple[Any, ...]
_cache: Optional[tuple[_CacheKey, dict]] = None


class MotionManifestError(ValueError):
    """清单结构不合法。"""


def _manifest_path() -> Path:
    override = os.environ.get(MANIFEST_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return BUILTIN_MANIFEST_PATH


def _cache_key(path: Path) -> _CacheKey:
    """带上 mtime/size:原地换清单文件后下一次请求就能生效。"""
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _text(item: dict, field: str, index: int, what: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MotionManifestError(f"第 {index} 条{what}缺少字段 {field}")
    return value


def _parse_case(index: int, item: Any, seen: set[str]) -> dict:
    if not isinstance(item, dict):
        raise MotionManifestError(f"第 {index} 条案例不是对象")
    parsed = {field: _text(item, field, index, "案例") for field in _REQUIRED_TEXT_FIELDS}
    if parsed["id"] in seen:
        raise MotionManifestError(f"案例 id 重复: {parsed['id']}")
    seen.add(parsed["id"])

    tags = item.get("tags", [])
    if not isinstance(tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags
    ):
        raise MotionManifestError(f"案例 {parsed['id']} 的 tags 必须是字符串数组")
    parsed["tags"] = list(tags)

    duration = item.get("duration_seconds")
    if duration is not None:
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            raise MotionManifestError(f"案例 {parsed['id']} 的 duration_seconds 必须是正数")
        parsed["duration_seconds"] = float(duration)

    for field in ("resolution", "author", "doc_section", "doc_title"):
        value = item.get(field)
        parsed[field] = value.strip() if isinstance(value, str) else ""

    media = item.get("media")
    if isinstance(media, dict):
        # sha256 由打包脚本按真实字节算出并写回清单；运行时按需下载原片时校验它。
        sha256_hex = str(media.get("sha256") or "").strip().lower()
        if sha256_hex and not _SHA256_RE.fullmatch(sha256_hex):
            raise MotionManifestError(f"案例 {parsed['id']} 的 media.sha256 不是 64 位十六进制")
        # media.token 是上游文档的素材句柄，只有 scripts/build_motion_pack.py 直接读清单原文件时
        # 需要它；解析结果会整条下发给图册接口，所以这里不收进来。
        parsed["media"] = {
            "file": str(media.get("file") or "").strip(),
            "bytes": int(media.get("bytes") or 0),
            "sha256": sha256_hex,
        }
    else:
        parsed["media"] = {"file": "", "bytes": 0, "sha256": ""}
    return parsed


def _parse_manifest(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise MotionManifestError("清单顶层必须是对象")

    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise MotionManifestError("cases 必须是非空数组")
    seen: set[str] = set()
    parsed_cases = [_parse_case(index, item, seen) for index, item in enumerate(cases)]

    methods = raw.get("methods", [])
    if not isinstance(methods, list):
        raise MotionManifestError("methods 必须是数组")
    parsed_methods = []
    for index, method in enumerate(methods):
        if not isinstance(method, dict):
            raise MotionManifestError(f"第 {index} 条方法不是对象")
        parsed_methods.append(
            {
                "id": _text(method, "id", index, "方法"),
                "label": _text(method, "label", index, "方法"),
                "summary": _text(method, "summary", index, "方法"),
                "where": method.get("where") if isinstance(method.get("where"), str) else "",
            }
        )

    categories = raw.get("categories")
    if not isinstance(categories, list) or not categories or not all(
        isinstance(name, str) and name.strip() for name in categories
    ):
        raise MotionManifestError("categories 必须是非空字符串数组")
    # 分类顺序是展示口径,清单里出现未登记的分类必须吵:图册分组会静默多出未知项。
    undeclared = sorted({case["category"] for case in parsed_cases} - set(categories))
    if undeclared:
        raise MotionManifestError(f"案例使用了未登记的分类: {', '.join(undeclared)}")

    return {
        "version": str(raw.get("version") or ""),
        "source": str(raw.get("source") or ""),
        "model_hint": str(raw.get("model_hint") or ""),
        "render_contract": _text(raw, "render_contract", 0, "清单"),
        "categories": list(categories),
        "methods": parsed_methods,
        "cases": parsed_cases,
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
            raise RuntimeError(f"内置动效大片清单不可用: {path}") from exc
        logger.warning(
            "[freezone] 动效大片清单 %s 不可用(%s),回落到内置清单", path, exc
        )
        manifest = _read_manifest(BUILTIN_MANIFEST_PATH)

    _cache = (key, manifest)
    return manifest


def reset_motion_template_cache() -> None:
    """改环境变量或原地换文件后强制重读;测试用。"""
    global _cache
    _cache = None


def load_motion_manifest() -> dict:
    """整份图册(案例、方法、分类顺序、渲染契约),顶层键与清单文件一致。"""
    return _load()


def load_motion_cases() -> list[dict]:
    # 内层 tags/media 也要复制:只 dict(case) 挡不住 `item["tags"].append(...)`,
    # 那会顺着缓存污染后面每一次请求。图册就 37 条,复制不值一提。
    return [
        {**case, "tags": list(case["tags"]), "media": dict(case["media"])}
        for case in _load()["cases"]
    ]


def get_motion_manifest_version() -> str:
    return _load()["version"]


def compose_motion_prompt(case_id: str) -> str:
    """案例自身的分镜规格 + 本系统渲染契约,拼成可直接交给模型的一条提示词。

    图册的「复制提示词」、提示词库的「动效大片」分组和渲染任务的默认 prompt 都
    走这里,保证三处文字完全一致 —— 契约改一次就行,不用挨个地方同步。
    """
    manifest = _load()
    for case in manifest["cases"]:
        if case["id"] == case_id:
            return case["prompt_cn"].rstrip() + "\n\n" + manifest["render_contract"].strip() + "\n"
    raise KeyError(case_id)


def motion_prompt_key(case_id: str) -> str:
    """案例在提示词库里的登记 key。

    登记方(prompt_library_defaults)与读取方(图册路由)都走这个换算,免得两边
    各拼一次前缀、拼错一边后覆盖值静默失效。
    """
    return f"{PROMPT_KEY_PREFIX}{case_id}"
