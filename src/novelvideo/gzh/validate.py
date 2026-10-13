# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号 HTML 合规校验器（排版产物的确定性强门禁）。

把「公众号编辑器会过滤什么」从模型自觉变成代码兜底：排版生成后必跑，检查禁用
标签/属性/样式，并核查中文文本节点是否被 ``<span leaf="">`` 包裹（粘贴进公众号编辑
器后保持内联样式的关键）。ERROR 非空即判定为不可交付——排版链路据此阻断。

本模块为**自研实现**，规则来自微信公众平台公开的能力限制（``<style>``/``<script>``/
``class``/``id``/``position:fixed``/``float``/``@keyframes``/``display:grid``/CSS 变量
等被剥离；正文强调须靠内联 style + ``<span leaf>``），不依赖任何第三方原文件。

对外契约：
- ``validate_html(html) -> GzhValidation``（errors / warnings / span_leaf_count）
- ``assert_compliant(html, *, name)`` —— 有 ERROR 抛 ``GzhHtmlInvalid``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# (正则, 说明) —— 命中即 ERROR：会被公众号编辑器过滤掉或导致粘贴后样式失效。
FORBIDDEN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<style[\s>]", re.I), "<style> 标签会被过滤，样式必须内联"),
    (re.compile(r"<script[\s>]", re.I), "<script> 标签会被过滤"),
    (re.compile(r"</?div[\s>]", re.I), "<div> 会被改写，请用 <section>"),
    (re.compile(r"<link[\s>]", re.I), "外部 <link>（CSS/字体）会被过滤"),
    (re.compile(r"\sclass\s*=", re.I), "class 属性会被剥离，请用内联 style"),
    (re.compile(r"\sid\s*=", re.I), "id 属性会被剥离"),
    (
        re.compile(r"position\s*:\s*(fixed|absolute|sticky)", re.I),
        "position fixed/absolute/sticky 不被支持",
    ),
    (re.compile(r"float\s*:", re.I), "float 不被支持"),
    (re.compile(r"@media", re.I), "@media 媒体查询不被支持"),
    (re.compile(r"@keyframes", re.I), "@keyframes 动画不被支持"),
    (re.compile(r"@import", re.I), "@import 不被支持"),
    (re.compile(r"display\s*:\s*grid", re.I), "display:grid 不被支持，请用 flex"),
    (
        re.compile(r"var\s*\(\s*--", re.I),
        "CSS 变量 var(--x) 不被支持，请写死值",
    ),
    (
        re.compile(
            r"url\s*\(\s*['\"]?https?://[^)]*\.(woff2?|ttf|otf|eot)", re.I
        ),
        "外部字体不被支持",
    ),
]

# 中日韩统一表意文字（含扩展 A）。用 \u 转义避开字面区间，跨 locale 稳定。
CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SKIP_TAGS = {"head", "title", "style", "script"}  # 不进公众号正文粘贴的区域
# 中文字后紧跟半角逗号/分号/叹号/问号（应改全角）；只查「中文在前」避免中英混排误伤。
HALF_PUNCT = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf][,;!?]")
ASCII_QUOTE = re.compile(r"[\"']")
# 代码区特征：等宽字体或 white-space:pre —— 其内半角符号是正常的。
CODE_STYLE = re.compile(
    r"monospace|white-space\s*:\s*pre|courier|consolas|sf mono", re.I
)


class _LeafChecker(HTMLParser):
    """检查每个非空中文文本节点是否处于 ``<span leaf>`` 内、正文标点是否全角。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []
        self.leaf_depth = 0
        self.code_depth = 0
        self.span_leaf_count = 0
        self.unwrapped: list[tuple[str, str]] = []
        self.half_punct: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        ad = dict(attrs)
        is_leaf = tag == "span" and "leaf" in ad
        is_code = bool(CODE_STYLE.search(ad.get("style", "") or ""))
        if is_leaf:
            self.span_leaf_count += 1
            self.leaf_depth += 1
        if is_code:
            self.code_depth += 1
        self.stack.append((tag, is_leaf, is_code))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, was_leaf, was_code in self.stack[i:]:
                    if was_leaf:
                        self.leaf_depth -= 1
                    if was_code:
                        self.code_depth -= 1
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or not CJK.search(text):
            return
        if any(t in SKIP_TAGS for t, _, _ in self.stack):
            return  # <head>/<title>/<style>/<script> 内文字不进公众号正文
        if self.leaf_depth == 0:
            parent = self.stack[-1][0] if self.stack else "(root)"
            snippet = text[:24] + ("…" if len(text) > 24 else "")
            self.unwrapped.append((snippet, parent))
        if self.code_depth == 0 and (
            HALF_PUNCT.search(text) or ASCII_QUOTE.search(text)
        ):
            snippet = text[:24] + ("…" if len(text) > 24 else "")
            self.half_punct.append(snippet)


@dataclass(slots=True)
class GzhValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    span_leaf_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class GzhHtmlInvalid(ValueError):
    """排版产物存在 ERROR 级违规——不可交付。"""

    def __init__(self, errors: list[str], name: str) -> None:
        self.errors = errors
        self.name = name
        detail = "；".join(errors)
        super().__init__(f"{name} 存在 {len(errors)} 处公众号红线违规：{detail}")


def validate_html(html: str) -> GzhValidation:
    result = GzhValidation()

    for rx, msg in FORBIDDEN:
        hits = len(rx.findall(html))
        if hits:
            result.errors.append(f"{msg}（命中 {hits} 处）")

    checker = _LeafChecker()
    try:
        checker.feed(html)
    except Exception as exc:  # 容错：解析失败不致命，只提示
        result.warnings.append(f"HTML 解析中断: {exc}")

    result.span_leaf_count = checker.span_leaf_count
    has_cjk = bool(CJK.search(html))
    if has_cjk and checker.span_leaf_count == 0:
        result.errors.append(
            "全文没有任何 <span leaf=\"\"> 包裹——粘贴到公众号后样式会大面积丢失"
        )
    elif checker.unwrapped:
        sample = "；".join(
            f"「{s}」(在 <{p}> 内)" for s, p in checker.unwrapped[:5]
        )
        result.warnings.append(
            f"{len(checker.unwrapped)} 处中文文本未被 <span leaf> 包裹，"
            f"样式可能丢失。例：{sample}"
        )

    if checker.half_punct:
        sample = "；".join(f"「{s}」" for s in checker.half_punct[:5])
        result.warnings.append(
            f"{len(checker.half_punct)} 处正文疑似半角标点/英文引号，应改中文全角"
            f"（代码块内不计）。例：{sample}"
        )

    return result


def assert_compliant(html: str, *, name: str = "<article>") -> GzhValidation:
    result = validate_html(html)
    if not result.ok:
        raise GzhHtmlInvalid(result.errors, name)
    return result
