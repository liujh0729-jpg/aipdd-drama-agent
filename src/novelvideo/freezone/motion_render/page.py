# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：MG 页面的产出侧（提示词拼装 + 回复收口 + 契约预检）。

渲染契约写在图册清单的 `render_contract` 里，执行侧是 `capturer.py`；但契约只说
"页面必须暴露 window.render(t)/window.DUR"，模型收到那段规格时并不知道自己交付的
形态要被逐帧截图，于是会顺手加解说、把 HTML 包在 ```html 围栏里、甚至只给一个大纲。
本模块负责把模型回复收成"能直接喂给渲染器的那一个 HTML 文档"，并在启动浏览器之前
先按契约做一次纯文本预检——`capturer` 的报错是给开发者的（页面加载后取不到 DUR），
给用户的那句必须是"模型没按契约产出页面，重试或换模型"。

纯字符串处理，不出网、不起浏览器、不碰 ffmpeg：与 `encoder.py` 一样可以单测。
"""

from __future__ import annotations

import re
from typing import Optional

# 模型爱在文档前后加一句"好的，下面是完整代码"——只认第一个文档起点与最后一个终点。
_DOCTYPE = "<!doctype html"
_HTML_OPEN = "<html"
_HTML_CLOSE = "</html"
# ```html / ```HTML / 裸 ``` 围栏，取最长的一段（模型偶尔把示例拆成两块）。
_FENCE = re.compile(r"```[ \t]*[A-Za-z]*[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)
_WINDOW_RENDER = re.compile(
    r"""window\s*(?:\.\s*render\b|\[\s*["']render["']\s*\])""", re.IGNORECASE
)
_WINDOW_DURATION = re.compile(
    r"""window\s*(?:\.\s*DUR\b|\[\s*["']DUR["']\s*\])""", re.IGNORECASE
)
# 页面里的内联脚本长度下限：挡掉「只写了 window.render = t => {} 的空壳」以外的
# 误判没有意义，但一个能画出东西的 MG 页不可能只有几十字符。
_MIN_PAGE_CHARS = 120
# 上限：契约要求单文件自包含，模型跑飞写出几 MB（或有人贴一整站进来）时早停，
# 别把这么大的正文塞进任务 envelope、再起浏览器。路由入队前与任务里共用这一个数。
MAX_PAGE_CHARS = 400_000


def build_motion_page_prompt(
    case_prompt: str,
    *,
    subject: str = "",
    width: int,
    height: int,
    fps: int,
    duration_seconds: Optional[float] = None,
) -> str:
    """把「案例规格 + 本镜头文案 + 交付形态」拼成一条给文本模型的提示词。

    案例规格由 `motion_templates.compose_motion_prompt` 提供（图册/提示词库共用同一
    份文字），这里只补渲染任务的执行口径：画幅、帧率、时长，以及"只交付一个 HTML
    文档"——那一条决定 `extract_motion_page` 能不能稳定收到源码。
    """
    clean_subject = str(subject or "").strip()
    lines = [
        str(case_prompt or "").strip(),
        "",
        "【本次交付】用中文写注释，界面文字按镜头文案来。"
        f"画布 {width}x{height} 像素、{fps} 帧/秒"
        + (
            f"、总时长 {duration_seconds:g} 秒（window.DUR 就写这个值）"
            if duration_seconds
            else "（window.DUR 由你按拍点节奏决定）"
        )
        + "。",
        "输出且只输出一个完整的单文件 HTML 文档：从 <!doctype html> 开始、到 "
        "</html> 结束，不要解释、不要前后说明文字、不要放在代码围栏里、不要拆成多段。"
        "所有素材用代码绘制或内联数据（data: 开头的 base64），不要引用任何外部 "
        "URL/字体/图片——渲染环境不联网，外链一律画不出来。",
    ]
    if clean_subject:
        lines.insert(1, f"【本镜头内容】{clean_subject}")
    return "\n".join(part for part in lines if part is not None).strip()


def extract_motion_page(raw: str) -> str:
    """从模型回复里取出那一个 HTML 文档源码；取不到时返回空串。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    fenced = _FENCE.findall(text)
    if fenced:
        candidate = max(fenced, key=len).strip()
        if _HTML_OPEN in candidate.lower() or _DOCTYPE in candidate.lower():
            text = candidate
    lowered = text.lower()
    starts = [
        index
        for index in (lowered.find(_DOCTYPE), lowered.find(_HTML_OPEN))
        if index >= 0
    ]
    if not starts:
        return ""
    end = lowered.rfind(_HTML_CLOSE)
    if end < 0:
        return ""
    return text[min(starts):end + len(_HTML_CLOSE) + 1].strip()


def motion_page_error(html: str) -> str:
    """按渲染契约预检一页源码；返回空串表示可以送去渲染，否则给一句可行动的原因。"""
    text = str(html or "").strip()
    if not text:
        return "模型没有产出 HTML 文档：请重试，或换一个能写整页代码的文本模型"
    if len(text) < _MIN_PAGE_CHARS:
        return f"模型产出的页面过短（{len(text)} 字符），不是一页可用的 MG 脚本"
    if len(text) > MAX_PAGE_CHARS:
        return (
            f"页面过大（{len(text)} 字符，上限 {MAX_PAGE_CHARS}）："
            "渲染契约要求单文件自包含，请精简页面、外链改代码绘制"
        )
    if not _WINDOW_RENDER.search(text):
        return "页面没有定义 window.render(t)：不符合本工具渲染契约，请重试"
    if not _WINDOW_DURATION.search(text):
        return "页面没有定义 window.DUR（总时长秒数）：不符合本工具渲染契约，请重试"
    return ""
