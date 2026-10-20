# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：MG 页面产出侧（page.py）纯函数单测。

runner 侧只测到「一条好回复能过、一条坏回复在起浏览器之前就被挡下」，这两个用例都
经过整条任务链路；本文件守的是收口规则本身——围栏取最长、文档首尾切片、契约预检的
四种拒绝理由。这三段逻辑决定了给用户的错误文案，不能只靠端到端用例兜着。
"""

from __future__ import annotations

from novelvideo.freezone.motion_render.page import (
    build_motion_page_prompt,
    extract_motion_page,
    motion_page_error,
)

_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
    "<canvas id=\"c\" width=\"1280\" height=\"720\"></canvas><script>"
    "window.DUR=2.0;"
    "window.render=function(t){const g=document.getElementById('c').getContext('2d');"
    "g.fillStyle=t<1?'#c0392b':'#16a085';g.fillRect(0,0,1280,720);};"
    "</script></body></html>"
)


def test_prompt_states_the_delivery_shape_the_renderer_needs() -> None:
    prompt = build_motion_page_prompt(
        "案例规格第一行",
        subject="一只手表在暗场里缓慢旋转",
        width=1280,
        height=720,
        fps=30,
        duration_seconds=8.4,
    )
    assert "案例规格第一行" in prompt
    assert "【本镜头内容】一只手表在暗场里缓慢旋转" in prompt
    assert "画布 1280x720 像素、30 帧/秒" in prompt
    assert "总时长 8.4 秒（window.DUR 就写这个值）" in prompt
    assert "不要放在代码围栏里" in prompt
    # 渲染环境不联网：外链必须写进提示词里挡掉，否则页面必然画白。
    assert "不要引用任何外部" in prompt


def test_prompt_leaves_the_rhythm_to_the_model_without_a_duration() -> None:
    prompt = build_motion_page_prompt("规格", width=720, height=1280, fps=24)
    assert "window.DUR 由你按拍点节奏决定" in prompt
    assert "【本镜头内容】" not in prompt


def test_extract_prefers_the_longest_fenced_block() -> None:
    raw = (
        "先给你一个片段：\n\n```css\nbody{margin:0}\n```\n\n"
        "完整页面在这里：\n\n```html\n好的，下面是完整页面\n" + _PAGE + "\n```\n\n以上。"
    )
    page = extract_motion_page(raw)
    assert page == _PAGE
    assert "先给你一个片段" not in page


def test_extract_drops_the_prose_around_a_bare_document() -> None:
    raw = "好的，下面是完整页面：\n\n" + _PAGE + "\n\n如需调整节奏可以改 DUR。"
    assert extract_motion_page(raw) == _PAGE


def test_extract_takes_a_bare_fence_and_an_uppercase_doctype() -> None:
    upper = _PAGE.replace("<!doctype html>", "<!DOCTYPE HTML>").replace("<html>", "<HTML>")
    assert extract_motion_page("``` \n" + upper + "\n```").lower().startswith("<!doctype html>")


def test_extract_returns_empty_when_there_is_no_document() -> None:
    assert extract_motion_page("") == ""
    assert extract_motion_page("我只给了分镜大纲：第一幕 0-1 秒…") == ""
    # 有开头没结尾：截到一半的页面渲染出来必然是坏的，交给上层报「没按契约产出」。
    assert extract_motion_page(_PAGE[:200]) == ""


def test_extract_falls_back_to_the_whole_reply_when_the_fence_holds_no_html() -> None:
    raw = "```python\nprint('跑题了')\n```\n\n" + _PAGE
    assert extract_motion_page(raw) == _PAGE


def test_preflight_accepts_both_window_property_spellings() -> None:
    assert motion_page_error(_PAGE) == ""
    bracket = _PAGE.replace("window.DUR=2.0;", 'window["DUR"] = 2.0;').replace(
        "window.render=function(t)", 'window["render"] = function (t)'
    )
    assert motion_page_error(bracket) == ""


def test_preflight_names_the_missing_contract_piece() -> None:
    assert "没有产出 HTML 文档" in motion_page_error("")
    assert "页面没有定义 window.render(t)" in motion_page_error(
        _PAGE.replace("window.render=function(t)", "window.paint=function(t)")
    )
    assert "页面没有定义 window.DUR" in motion_page_error(_PAGE.replace("window.DUR=2.0;", ""))
    short = "<!doctype html><html><body><script>window.DUR=1;window.render=t=>{};</script></body></html>"
    assert "页面过短" in motion_page_error(short)
