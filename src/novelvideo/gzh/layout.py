# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Markdown → 公众号合规 HTML 的程序化排版器。

输入是 ``write.py`` 产出的结构化 Markdown，输出是可直接粘贴进公众号编辑器、且能过
``validate.assert_compliant``（ERROR=0）的内联样式 HTML。所有组件片段在此**按主题 token
现拼**，不手写具体色值、不 vendor 第三方原文件；每个中文文本节点一律包 ``<span leaf="">``
（公众号粘贴保样式的关键），只用 ``section/p/span/strong/h3/table/img/br`` 等被允许的标签，
绝不出现 ``div``/``class``/``id``/``<style>``/``position:fixed``/``display:grid``/CSS 变量。

设计：本模块**纯确定性**、不触网、不调模型，便于单测钉住产出契约。
"""

from __future__ import annotations

import html as _html
import re
from typing import Iterable

from .themes import DEFAULT_THEME, GzhTheme, theme_or_default

# 内联标记解析：代码 / 高亮 / 下划线 / 加粗 / 删除线 / 链接。
_CODE_RX = re.compile(r"`([^`]+)`")
_HL_RX = re.compile(r"==(.+?)==")
_UL_RX = re.compile(r"\+\+(.+?)\+\+|<u>(.+?)</u>")
_BOLD_RX = re.compile(r"\*\*(.+?)\*\*")
_STRIKE_RX = re.compile(r"~~(.+?)~~")
_LINK_RX = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def esc(text: str) -> str:
    return _html.escape(str(text or ""), quote=False)


def _leaf(text: str) -> str:
    """把一段文字包成 ``<span leaf="">``（先转义，避免误触红线正则）。"""
    escaped = esc(text)
    if not escaped:
        return ""
    return f'<span leaf="">{escaped}</span>'


# ------------------------------------------------------------------
# Markdown → blocks
# ------------------------------------------------------------------

def _is_hr(line: str) -> bool:
    s = line.strip()
    return bool(re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s))


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    return "|" in s and bool(re.fullmatch(r"\|?[\s:|\-]+\|?", s)) and "-" in s


def _parse_blocks(md: str) -> list[dict]:
    text = str(md or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks: list[dict] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        fence = re.match(r"^```(\w*)\s*$", stripped)
        if fence:
            lang = fence.group(1)
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            blocks.append({"type": "code", "lang": lang, "text": "\n".join(buf)})
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append({"type": f"h{level}", "text": heading.group(2).strip()})
            i += 1
            continue

        if _is_hr(line):
            blocks.append({"type": "hr"})
            i += 1
            continue

        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(x for x in buf if x)})
            continue

        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = _split_row(line)
            i += 2  # 跳过分隔行
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            items: list[str] = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]).strip())
                i += 1
            blocks.append({"type": "ul", "items": items})
            continue

        if re.match(r"^\s*\d+[.)]\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+[.)]\s+", "", lines[i]).strip())
                i += 1
            blocks.append({"type": "ol", "items": items})
            continue

        # 独立成行的图片
        img = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", stripped)
        if img:
            blocks.append({"type": "image", "alt": img.group(1), "url": img.group(2)})
            i += 1
            continue

        # 普通段落：合并软换行的连续行
        buf = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (
                not nxt
                or nxt.startswith("#")
                or nxt.startswith(">")
                or nxt.startswith("```")
                or re.match(r"^\s*[-*+]\s+", lines[i])
                or re.match(r"^\s*\d+[.)]\s+", lines[i])
                or _is_hr(lines[i])
            ):
                break
            buf.append(nxt)
            i += 1
        blocks.append({"type": "para", "text": " ".join(buf)})

    return blocks


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


# ------------------------------------------------------------------
# inline
# ------------------------------------------------------------------

def _render_inline(text: str, tk: dict) -> str:
    """把内联标记转成 HTML，保证每个文字段都被 ``<span leaf>`` 包裹。

    策略：先把「受保护」的片段（代码 / 高亮 / 下划线 / 加粗 / 删除线 / 链接文字）
    统一替换成占位符并记录其渲染结果，剩余普通文字按 leaf 输出，最后回填。占位符
    用不含中日韩且不会被红线正则命中的安全标记。
    """
    recorded: list[str] = []

    def _stash(rendered: str) -> str:
        recorded.append(rendered)
        return f"\x00{len(recorded) - 1}\x00"

    def _repl(rx, fn):
        nonlocal text
        text = rx.sub(lambda m: _stash(fn(m)), text)

    _repl(_CODE_RX, lambda m: _code_span(m.group(1), tk))
    _repl(_HL_RX, lambda m: _highlight_span(m.group(1), tk))
    _repl(_UL_RX, lambda m: _underline_span(m.group(2) or m.group(1) or "", tk))
    _repl(_STRIKE_RX, lambda m: _strike_span(m.group(1), tk))
    _repl(_BOLD_RX, lambda m: _bold_span(m.group(1), tk))
    _repl(_LINK_RX, lambda m: _underline_span(m.group(1) or "", tk))

    # 剩余普通文本按 leaf 输出（占位符原样保留在文本里，稍后回填）。
    parts: list[str] = []
    for chunk in re.split(r"\x00(\d+)\x00", text):
        if chunk == "":
            continue
        if chunk.isdigit():
            parts.append(recorded[int(chunk)])
        else:
            parts.append(_leaf(chunk))
    return "".join(parts)


def _bold_span(t: str, tk: dict) -> str:
    return (
        f'<strong style="color:{tk["title"]};font-weight:700;">'
        f"{_leaf(t)}</strong>"
    )


def _highlight_span(t: str, tk: dict) -> str:
    return (
        f'<span style="background:{tk["chip_bg"]};color:{tk["title"]};'
        f'padding:2px 7px;border-radius:3px;font-weight:700;font-size:14px;">'
        f"{_leaf(t)}</span>"
    )


def _underline_span(t: str, tk: dict) -> str:
    return f'<span style="{tk["underline_css"]}">{_leaf(t)}</span>'


def _strike_span(t: str, tk: dict) -> str:
    return (
        f'<span style="text-decoration:line-through;color:{tk["muted"]};">'
        f"{_leaf(t)}</span>"
    )


def _code_span(t: str, tk: dict) -> str:
    return (
        f'<span style="background:{tk["chip_bg"]};color:{tk["title"]};'
        f"padding:2px 6px;border-radius:4px;font-family:'SF Mono',Consolas,Monaco,monospace;"
        f'font-size:14px;">{_leaf(t)}</span>'
    )


# ------------------------------------------------------------------
# block renderers
# ------------------------------------------------------------------

def _para(t: str, tk: dict, *, margin: str = "0 10px 22px") -> str:
    return (
        f'<p style="margin:{margin};font-size:{tk["body_size"]};'
        f'line-height:{tk["line_height"]};text-align:justify;color:{tk["body"]};'
        f'letter-spacing:{tk["letter_spacing"]};">{_render_inline(t, tk)}</p>'
    )


def _subheading(t: str, tk: dict) -> str:
    return (
        f'<p style="font-size:16px;font-weight:800;color:{tk["title"]};'
        f'margin:28px 10px 14px;padding-left:12px;'
        f'border-left:3px solid {tk["primary"]};line-height:1.4;">{_leaf(t)}</p>'
    )


def _section_heading(num: int, t: str, tk: dict, *, first: bool) -> str:
    mt = "8px" if first else "56px"
    return (
        f'<section style="margin-top:{mt};margin-bottom:28px;padding:0 10px;">'
        f'<section style="padding-bottom:16px;border-bottom:1px solid {tk["line"]};">'
        f'<p style="font-size:44px;font-weight:900;color:{tk["line"]};margin:0;'
        f'line-height:1;letter-spacing:-2px;">{_leaf(f"{num:02d}")}</p>'
        f'<h3 style="font-size:20px;font-weight:800;color:{tk["title"]};margin:8px 0 0;'
        f'line-height:1.4;">{_leaf(t)}</h3>'
        f"</section></section>"
    )


def _divider(tk: dict) -> str:
    return (
        f'<section style="padding:0 10px;">'
        f'<section style="height:1px;background:{tk["line"]};margin:8px 0;">'
        f'<span leaf=""><br></span></section></section>'
    )


def _quote(t: str, tk: dict) -> str:
    return (
        f'<section style="border-left:3px solid {tk["primary"]};'
        f'background:{tk["block_bg"]};padding:14px 18px;margin:0 10px 24px;">'
        f'<p style="font-size:{tk["body_size"]};color:{tk["title"]};margin:0;'
        f'line-height:{tk["line_height"]};font-weight:600;">{_render_inline(t, tk)}</p>'
        f"</section>"
    )


def _code_block(t: str, tk: dict) -> str:
    body = esc(t)
    return (
        f'<section style="background:{tk["block_bg"]};'
        f'border-left:3px solid {tk["primary"]};padding:14px 16px;'
        f'margin:0 10px 24px;border-radius:4px;">'
        f'<p style="margin:0;font-family:\'SF Mono\',Menlo,Consolas,monospace;'
        f"font-size:13px;line-height:1.7;color:{tk['body']};white-space:pre;"
        f'word-break:break-all;overflow-x:auto;"><span leaf="">{body}</span></p>'
        f"</section>"
    )


def _ul(items: Iterable[str], tk: dict) -> str:
    rows = []
    for it in items:
        rows.append(
            '<section style="display:flex;align-items:flex-start;margin-bottom:10px;">'
            f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
            f'background:{tk["primary"]};margin:10px 12px 0 0;flex-shrink:0;">'
            '<span leaf=""><br></span></span>'
            f'<p style="font-size:{tk["body_size"]};color:{tk["body"]};margin:0;'
            f'line-height:{tk["line_height"]};flex:1;text-align:justify;">'
            f"{_render_inline(it, tk)}</p></section>"
        )
    return (
        f'<section style="padding:0 10px;margin-bottom:24px;">{"".join(rows)}</section>'
    )


def _ol(items: Iterable[str], tk: dict) -> str:
    rows = []
    for idx, it in enumerate(items, start=1):
        rows.append(
            '<section style="display:flex;align-items:flex-start;margin-bottom:12px;">'
            f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f'width:22px;height:22px;background:{tk["primary"]};color:#fff;font-size:12px;'
            f'font-weight:700;border-radius:50%;flex-shrink:0;margin:3px 12px 0 0;">'
            f'{_leaf(str(idx))}</span>'
            f'<p style="font-size:{tk["body_size"]};color:{tk["body"]};margin:0;'
            f'line-height:{tk["line_height"]};flex:1;text-align:justify;">'
            f"{_render_inline(it, tk)}</p></section>"
        )
    return (
        f'<section style="padding:0 10px;margin-bottom:24px;">{"".join(rows)}</section>'
    )


def _table(header: list[str], rows: list[list[str]], tk: dict) -> str:
    ths = "".join(
        f'<th style="background:{tk["primary"]};color:#fff;font-weight:700;'
        f'padding:8px 12px;text-align:left;border:1px solid {tk["line"]};">'
        f"{_leaf(c)}</th>"
        for c in header
    )
    body_rows = []
    for ri, row in enumerate(rows):
        bg = tk["block_bg"] if ri % 2 else "transparent"
        tds = "".join(
            f'<td style="padding:8px 12px;border:1px solid {tk["line"]};'
            f'color:{tk["body"]};background:{bg};">{_leaf(c)}</td>'
            for c in row
        )
        body_rows.append(f"<tr>{tds}</tr>")
    return (
        '<section style="margin:0 10px 24px;overflow-x:auto;">'
        f'<table style="width:100%;border-collapse:collapse;font-size:14px;">'
        f"<thead><tr>{ths}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        "</section>"
    )


def _image(url: str, alt: str, tk: dict) -> str:
    safe_url = esc(url).replace('"', "&quot;")
    cap = _leaf(f"— {alt}") if alt else ""
    cap_html = (
        f'<p style="font-size:12px;color:{tk["muted"]};text-align:center;'
        f'margin:8px 10px 24px;">{cap}</p>'
        if cap
        else ""
    )
    return (
        f'<section style="border:1px solid {tk["line"]};padding:4px;margin:0 10px 12px;">'
        '<section style="margin:0;overflow:hidden;"><span leaf="">'
        f'<img src="{safe_url}" style="max-width:100%;height:auto;display:block;'
        'margin:0 auto;"></span></section></section>' + cap_html
    )


def _intro_card(quote: str, author: str, tk: dict) -> str:
    author_line = (
        f'<p style="text-align:right;font-size:12px;color:{tk["muted"]};margin:16px 0 0;">'
        f"{_leaf('—— ' + author)}</p>"
        if author
        else ""
    )
    return (
        f'<section style="margin:10px 10px 40px;padding:28px 22px;'
        f'border-top:1px solid {tk["line"]};border-bottom:1px solid {tk["line"]};">'
        f'<p style="font-size:11px;color:{tk["muted"]};margin:0 0 14px;letter-spacing:2px;">'
        f'{_leaf("QUOTE")}</p>'
        f'<p style="font-size:18px;font-weight:700;color:{tk["title"]};margin:0;'
        f'line-height:1.7;">{_render_inline(quote, tk)}</p>' + author_line + "</section>"
    )


def _guide(points: list[str], tk: dict) -> str:
    cards = []
    for idx, pt in enumerate(points[:3], start=1):
        mr = "8px" if idx < min(3, len(points)) else "0"
        cards.append(
            f'<section style="flex:1;background:{tk["block_bg"]};'
            f'border-top:1px solid {tk["line"]};padding:16px 12px;margin-right:{mr};">'
            f'<p style="font-size:11px;color:{tk["muted"]};margin:0 0 8px;">'
            f'{_leaf(f"{idx:02d}")}</p>'
            f'<p style="font-size:13px;font-weight:700;color:{tk["title"]};margin:0;'
            f'line-height:1.5;">{_leaf(pt)}</p></section>'
        )
    return (
        '<section style="padding:0 10px 36px;">'
        f'<p style="font-size:11px;color:{tk["muted"]};margin:0 0 14px;letter-spacing:2px;">'
        f'{_leaf("本文看点")}</p>'
        '<section style="display:flex;justify-content:space-between;">'
        + "".join(cards)
        + "</section></section>"
    )


def _end_and_signature(signature: str, tk: dict) -> str:
    sig_html = ""
    if signature:
        sig_html = (
            f'<p style="margin-bottom:0;font-size:{tk["body_size"]};'
            f'line-height:{tk["line_height"]};color:{tk["body"]};text-align:justify;">'
            f"{_render_inline(signature, tk)}</p>"
        )
    return (
        '<section style="padding:0 10px;"><section style="text-align:center;margin:8px 0 24px;">'
        '<section style="display:flex;align-items:center;justify-content:center;">'
        f'<span style="height:1px;width:48px;background:{tk["line"]};margin-right:16px;">'
        '<span leaf=""><br></span></span>'
        f'<span style="font-size:10px;color:{tk["muted"]};letter-spacing:4px;">'
        f'{_leaf("END")}</span>'
        f'<span style="height:1px;width:48px;background:{tk["line"]};margin-left:16px;">'
        '<span leaf=""><br></span></span></section></section>'
        f'<section style="border-top:1px solid {tk["line"]};padding-top:24px;">'
        f"{sig_html}</section></section>"
    )


# ------------------------------------------------------------------
# public API
# ------------------------------------------------------------------

def render_article(
    markdown: str,
    *,
    theme: object = DEFAULT_THEME,
    intro_quote: str = "",
    author: str = "",
    signature: str = "",
    guide_points: list[str] | None = None,
) -> str:
    """把结构化 Markdown 渲染成公众号合规 HTML（返回完整内联样式片段）。"""
    resolved: GzhTheme = theme_or_default(theme)
    tk = resolved.tokens()
    blocks = _parse_blocks(markdown)

    h2_texts = [b["text"] for b in blocks if b["type"] == "h2"]
    use_guide = guide_points if guide_points is not None else h2_texts
    guide_ready = bool(use_guide) and len(use_guide) >= 3

    out: list[str] = []
    if intro_quote:
        out.append(_intro_card(intro_quote, author, tk))

    chapter = 0
    guide_emitted = False
    for b in blocks:
        t = b["type"]
        if t == "h1":
            continue  # 文章标题在公众号平台侧设置，正文内不重复
        if t == "h2":
            chapter += 1
            if not guide_emitted and guide_ready:
                out.append(_guide(list(use_guide), tk))
                guide_emitted = True
            if chapter > 1:
                out.append(_divider(tk))
            out.append(_section_heading(chapter, b["text"], tk, first=chapter == 1))
            continue
        if chapter == 0 and not guide_emitted and guide_ready and t not in ("hr",):
            # 第一章出现前先补看点导引（若前言段落后仍有大量章节）。
            pass
        if t == "h3":
            out.append(_subheading(b["text"], tk))
        elif t == "para":
            out.append(_para(b["text"], tk))
        elif t == "quote":
            out.append(_quote(b["text"], tk))
        elif t == "code":
            out.append(_code_block(b["text"], tk))
        elif t == "ul":
            out.append(_ul(b["items"], tk))
        elif t == "ol":
            out.append(_ol(b["items"], tk))
        elif t == "table":
            out.append(_table(b["header"], b["rows"], tk))
        elif t == "image":
            out.append(_image(b["url"], b["alt"], tk))
        elif t == "hr":
            out.append(_divider(tk))

    # 若正文有章节但导引还没落到第一章前（如开头就是 h2），补在开头。
    if guide_ready and not guide_emitted:
        out.insert(1 if intro_quote else 0, _guide(list(use_guide), tk))

    out.append(_end_and_signature(signature, tk))

    body = "".join(out)
    container = (
        f'<section style="max-width:{tk["max_width"]};margin:0 auto;'
        f'background:{tk["page_bg"]};color:{tk["body"]};'
        f"line-height:{tk['line_height']};letter-spacing:{tk['letter_spacing']};"
        'overflow-x:hidden;">' + body + "</section>"
    )
    return container


def infer_title(markdown: str, *, fallback: str = "") -> str:
    """取第一个 ``# `` 一级标题作为文章标题（公众号侧用于标题栏）。"""
    for b in _parse_blocks(markdown):
        if b["type"] == "h1":
            return b["text"]
    for line in str(markdown or "").splitlines():
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return fallback


__all__ = [
    "render_article",
    "infer_title",
    "esc",
]
