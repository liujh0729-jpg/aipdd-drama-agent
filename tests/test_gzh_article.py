# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号「文章编写与排版」程序化 pipeline 单测（Phase 2A）。

钉住 ``novelvideo.gzh`` 的四条纯本地契约，全程不触网、不调模型、不起渲染服务：
- ``layout.render_article``：Markdown → 公众号合规 HTML，每个中文文本节点都进
  ``<span leaf>``、所有内置主题都过 ``validate`` 的 ERROR 门禁；
- ``validate``：平台红线（``<style>``/``<div>``/``class``/``id``/…）命中即 ERROR、
  ``assert_compliant`` 抛 ``GzhHtmlInvalid``；未包裹 / 半角标点只 WARN、不致命；
- ``write``：写作 prompt 的风格 / 篇幅 / 素材契约，Markdown 规整与结构判定；
- ``publish``：凭证缺失降级为 ``needs_config``（不抛错、附配置指引）、``dry_run`` 不外发。
"""

from __future__ import annotations

import pytest

from novelvideo.gzh import layout, publish, validate, write
from novelvideo.gzh.themes import THEMES


MD = """# 检索增强生成的三个关键点

RAG 把**外部知识**接进大模型，回答可追溯到出处。

## 为什么要用 RAG
模型记不住全部知识，++实时性++要求答案能更新。

- 切块入库
- 检索最相关段落
- 拼进上下文再生成

## 常见误区
> 把 RAG 当成万能记忆体是最大误区。

===重点===：检索质量决定上限。

## 落地建议
1. 先做数据清洗
2. 再调检索召回

```python
print("hello")
```
"""


def test_render_article_is_compliant_across_all_themes():
    for theme in THEMES:
        html = layout.render_article(MD, theme=theme)
        result = validate.validate_html(html)
        assert result.errors == [], f"theme {theme} errors: {result.errors}"
        assert result.ok
        # 每个中文文本节点都进 span leaf：整篇必然有大量包裹。
        assert result.span_leaf_count > 20, theme


def test_render_article_wraps_every_cjk_and_keeps_forbidden_out():
    html = layout.render_article(MD, theme="moyu-green")
    # 公众号红线：不得出现 <style>/<div>/class=/id=/position:absolute 等。
    for token in ("<style", "<div", "class=", " id=", "position: absolute", "@keyframes"):
        assert token not in html
    assert "<span leaf=\"" in html


def test_infer_title_takes_first_h1():
    assert layout.infer_title(MD, fallback="x") == "检索增强生成的三个关键点"
    # 无标题时回退。
    assert layout.infer_title("纯正文没有标题", fallback="兜底标题") == "兜底标题"


def test_validate_flags_platform_red_lines():
    bad = '<div class="wrap" id="a"><style>p{}</style><p>中文内容</p></div>'
    result = validate.validate_html(bad)
    assert not result.ok
    joined = "；".join(result.errors)
    assert "<div>" in joined
    assert "class" in joined
    assert "<style>" in joined
    with pytest.raises(validate.GzhHtmlInvalid):
        validate.assert_compliant(bad, name="bad.html")


def test_validate_fully_unwrapped_cjk_is_error():
    # 有中文但没有任何 span leaf → 判 ERROR（样式会大面积丢失）。
    result = validate.validate_html("<section>这是一段没有包裹的中文正文</section>")
    assert not result.ok
    assert any("span leaf" in e for e in result.errors)


def test_validate_unwrapped_and_half_punct_are_warnings_only():
    html = '<section><span leaf="">已包裹的中文</span>漏包裹中文, 半角逗号</section>'
    result = validate.validate_html(html)
    # 至少有一处 leaf → 不误判全文缺包裹；漏包裹与半角标点只 WARN。
    assert result.ok
    assert result.span_leaf_count >= 1
    assert any("未被" in w for w in result.warnings) or any("半角" in w for w in result.warnings)


def test_build_write_prompt_contract_style_words_material():
    prompt = write.build_write_prompt(
        topic="RAG 检索增强生成",
        material="这是一段参考素材，里面有事实。",
        style="viewpoint",
        target_words=2000,
        audience="一线工程师",
        tone="克制、专业",
    )
    assert "【写作风格】" in prompt
    assert "【通用规则】" in prompt
    assert "【主题】RAG 检索增强生成" in prompt
    assert "【目标读者】一线工程师" in prompt
    assert "【语气】克制、专业" in prompt
    assert "约 2000 字" in prompt
    assert "这是一段参考素材" in prompt
    assert "只能在这份资料范围内取材" in prompt
    assert prompt.rstrip().endswith("不要额外解释。")


def test_build_write_prompt_clamps_words_and_handles_no_material():
    prompt = write.build_write_prompt(topic="主题", target_words=99999)
    assert "约 6000 字" in prompt
    assert "未提供参考素材" in prompt
    low = write.build_write_prompt(topic="主题", target_words=1)
    assert "约 300 字" in low


def test_normalize_markdown_strips_fence_and_collapses_blanks():
    raw = "```markdown\n# 标题\n\n\n\n正文段落\n```"
    assert write.normalize_markdown(raw) == "# 标题\n\n正文段落"
    assert write.has_markdown_structure("# 标题") is True
    assert write.has_markdown_structure("没有标题的正文") is False


async def test_publish_without_credentials_degrades_to_needs_config(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_APPID", raising=False)
    monkeypatch.delenv("WECHAT_SECRET", raising=False)
    # 指向一个不存在的家目录配置文件，且 tmp 项目目录里没有 config.json。
    monkeypatch.setattr(publish, "HOME_CONFIG", str(tmp_path / "absent.json"))
    outcome = await publish.publish_article("<section>正文</section>", project_dir=tmp_path)
    assert outcome.status == "needs_config"
    assert outcome.draft_media_id == ""
    # 不抛错且带回配置指引。
    assert any(publish.SETUP_GUIDE in g for g in outcome.guidance)


async def test_publish_dry_run_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "HOME_CONFIG", str(tmp_path / "absent.json"))
    outcome = await publish.publish_article(
        "<section>正文</section>",
        appid="wx-test",
        secret="secret-test",
        dry_run=True,
    )
    assert outcome.status == "dry_run"


async def test_publish_missing_cover_is_error_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "HOME_CONFIG", str(tmp_path / "absent.json"))
    # 有凭证、非 dry-run、但没封面图 → error（图文草稿封面必填），且不发起网络请求。
    outcome = await publish.publish_article(
        "<section>正文</section>",
        appid="wx-test",
        secret="secret-test",
        project_dir=tmp_path,
    )
    assert outcome.status == "error"
    assert "封面" in outcome.message
