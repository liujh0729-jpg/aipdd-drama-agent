# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 画风图册（风格名录）：清单自检、加载回落、提示词拼装与两个只读端点。
"""图册分两家：案例回答「这片子怎么排镜」，风格回答「想要这种观感，画布该怎么画」。
所以这份清单没有海报与原片 —— 卡片样张由前端按每条的 ``palette`` 用代码画，
上游样片一张都不随包（名录只是规格出处，文字与色板全部按本画布重写）。

守着四件事：
1. 内置清单自己站得住（条数、分类白名单、六段规格、色板格式）；
2. 外部清单坏了回落内置并吵一声，内置坏了直接抛 —— 那是打包坏了，不能静默空图册；
3. 渲染契约不重复存一份，拼风格提示词时从案例清单接上；
4. 案例与风格互斥：两套规格同时给只会让模型在冲突的色板与禁则之间取平均。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import motion as motion_routes
from novelvideo.catalog import prompt_library_defaults
from novelvideo.freezone import motion_styles, motion_templates

_PROMPT_SECTIONS = ("【风格】", "【色板】", "【代码技法】", "【运动】", "【排版】", "【禁则】")
_MG_CATEGORY = "MG 动态设计"


@pytest.fixture(autouse=True)
def _clean_style_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """清单缓存与环境变量都是模块级状态：每条用例前后各清一次，顺序不互相依赖。"""
    motion_styles.reset_motion_style_cache()
    yield
    monkeypatch.delenv("MOTION_STYLE_MANIFEST", raising=False)
    motion_styles.reset_motion_style_cache()


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.include_router(motion_routes.router, prefix="/api/v1")

    async def fake_resolve(*_args, **_kwargs):
        return None

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    return TestClient(app)


def _use_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: Any) -> Path:
    """把一份（可能坏了的）清单指给加载器并强制重读。"""
    path = tmp_path / "styles.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MOTION_STYLE_MANIFEST", str(path))
    motion_styles.reset_motion_style_cache()
    return path


def _parse(raw: Any) -> dict:
    """结构自检直接调解析函数。

    走 ``load_*`` 测不到这些分支：外部清单坏了会**回落内置**（那是有意的降级），
    所以那里看到的是 66 条风格，而不是预期的报错。
    """
    return motion_styles._parse_manifest(raw)


# --------------------------------------------------------------- 1) 内置清单自检


def test_builtin_catalog_registers_the_whole_atlas() -> None:
    manifest = motion_styles.load_motion_style_manifest()
    styles = manifest["styles"]
    assert len(styles) == 66
    assert len({style["id"] for style in styles}) == 66
    # 分类顺序是筛选条的展示口径，必须与清单里实际用到的集合一致。
    assert sorted(manifest["categories"]) == sorted({style["category"] for style in styles})
    assert sum(1 for style in styles if style["category"] == _MG_CATEGORY) == 15
    assert manifest["version"]
    # 出处要写清「名录参考、规格重写」，否则后人会以为这份是从上游搬来的。
    assert "参考" in manifest["source"]


def test_every_style_spec_carries_the_six_sections() -> None:
    for style in motion_styles.load_motion_styles():
        missing = [head for head in _PROMPT_SECTIONS if head not in style["prompt_cn"]]
        assert missing == [], f"{style['id']} 缺段落 {missing}"


def test_palette_is_hex_colors_sized_for_a_card() -> None:
    for style in motion_styles.load_motion_styles():
        assert 2 <= len(style["palette"]) <= 8, style["id"]
        for color in style["palette"]:
            assert color == color.lower()
            assert len(color) == 7 and color.startswith("#"), (style["id"], color)


def test_loaded_styles_are_copies_of_the_cache() -> None:
    """加载函数每次给新对象：调用方改 tags/palette 不许顺着缓存污染下一次请求。"""
    first = motion_styles.load_motion_styles()[0]
    first["tags"].append("injected")
    first["palette"].append("#000000")
    again = motion_styles.get_motion_style(first["id"]) or {}
    assert "injected" not in again["tags"]
    assert "#000000" not in again["palette"]


# ------------------------------------------------------- 2) 结构自检：坏清单要吵


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"styles": []}, "styles 必须是非空数组"),
        ({"styles": "not-a-list"}, "styles 必须是非空数组"),
        ({"categories": "手绘"}, "categories 必须是非空字符串数组"),
        ({"categories": []}, "categories 必须是非空字符串数组"),
        ({"categories": ["手绘与绘画", "  "]}, "categories 必须是非空字符串数组"),
    ],
)
def test_manifest_structure_is_checked(patch: dict, expected: str) -> None:
    style = motion_styles.load_motion_styles()[0]
    raw = {
        "version": "t",
        "source": "s",
        "categories": [style["category"]],
        "styles": [style],
    }
    raw.update(patch)
    with pytest.raises(motion_styles.MotionStyleManifestError, match=expected):
        _parse(raw)


def test_manifest_top_level_must_be_an_object() -> None:
    with pytest.raises(motion_styles.MotionStyleManifestError, match="顶层必须是对象"):
        _parse(["不是对象"])


def test_undeclared_category_is_rejected() -> None:
    """风格写了清单没登记的分类：筛选条会静默多出未知项，必须在加载时就吵。"""
    style = dict(motion_styles.load_motion_styles()[0])
    style["category"] = "没登记过的分类"
    with pytest.raises(motion_styles.MotionStyleManifestError, match="未登记的分类"):
        _parse({"categories": ["手绘与绘画"], "styles": [style]})


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("id", "", "缺少字段 id"),
        ("prompt_cn", "   ", "缺少字段 prompt_cn"),
        ("tags", [], "tags 必须是非空字符串数组"),
        ("tags", ["ok", "  "], "tags 必须是非空字符串数组"),
        ("palette", ["#fff"], "palette 必须是 2～8 项数组"),
        ("palette", ["ffffff", "#111111"], "不是 #rrggbb"),
    ],
)
def test_each_style_is_validated(field: str, value: Any, expected: str) -> None:
    style = dict(motion_styles.load_motion_styles()[0])
    style[field] = value
    with pytest.raises(motion_styles.MotionStyleManifestError, match=expected):
        _parse({"categories": [style["category"]], "styles": [style]})


def test_duplicate_style_id_is_rejected() -> None:
    a, b = motion_styles.load_motion_styles()[:2]
    with pytest.raises(motion_styles.MotionStyleManifestError, match="风格 id 重复"):
        _parse({"categories": [a["category"]], "styles": [a, {**b, "id": a["id"]}]})


def test_palette_is_stored_lowercase_and_trimmed() -> None:
    """色板大小写与空格由解析器收口：前端按它画样张，也按它比对重复。"""
    style = dict(motion_styles.load_motion_styles()[0])
    style["palette"] = [" #FFFFFF ", "#aBcDeF"]
    parsed = _parse({"categories": [style["category"]], "styles": [style]})
    assert parsed["styles"][0]["palette"] == ["#ffffff", "#abcdef"]


def test_short_hex_palette_is_rejected() -> None:
    """只收 #rrggbb：#fff 那种三位缩写前端画出来与规格对不上，不如当场拒。"""
    style = dict(motion_styles.load_motion_styles()[0])
    style["palette"] = ["#fff", "#000"]
    with pytest.raises(motion_styles.MotionStyleManifestError, match="不是 #rrggbb"):
        _parse({"categories": [style["category"]], "styles": [style]})


# ------------------------------------------------------- 3) 外部坏了回落，内置坏了抛


def test_broken_external_manifest_falls_back_to_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """外部清单指错了不许把图册变成空墙：回落内置并留一条 warning 说明出处。"""
    builtin_first = motion_styles.load_motion_styles()[0]["id"]
    bad = tmp_path / "styles.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("MOTION_STYLE_MANIFEST", str(bad))
    motion_styles.reset_motion_style_cache()

    with caplog.at_level("WARNING"):
        manifest = motion_styles.load_motion_style_manifest()

    assert len(manifest["styles"]) == 66
    assert manifest["styles"][0]["id"] == builtin_first
    assert any("画风图册清单" in record.message for record in caplog.records)


def test_missing_builtin_manifest_is_a_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内置清单读不出来是打包坏了：这里必须抛，不能降级成空图册让任务静默跑飞。"""
    monkeypatch.setattr(motion_styles, "BUILTIN_MANIFEST_PATH", Path("no_such_styles.json"))
    motion_styles.reset_motion_style_cache()
    monkeypatch.delenv("MOTION_STYLE_MANIFEST", raising=False)
    with pytest.raises(RuntimeError, match="内置画风图册清单不可用"):
        motion_styles.load_motion_style_manifest()


# ----------------------------------------------------- 4) 提示词：契约只存一份


def test_style_prompt_appends_the_case_side_render_contract() -> None:
    style = motion_styles.load_motion_styles()[0]
    prompt = motion_styles.compose_motion_style_prompt(style["id"])
    contract = motion_templates.load_motion_manifest()["render_contract"]

    assert prompt.startswith(style["prompt_cn"].rstrip())
    assert prompt.endswith(contract.strip() + "\n")
    # 风格清单里不抄契约：两份文字迟早各改各的，图册与任务就会拿到不同口径。
    assert contract not in style["prompt_cn"]
    assert prompt.count(contract.strip()) == 1


def test_unknown_style_prompt_raises_key_error() -> None:
    with pytest.raises(KeyError):
        motion_styles.compose_motion_style_prompt("no_such_style")
    assert motion_styles.get_motion_style("no_such_style") is None


def test_prompt_key_is_separate_from_case_keys() -> None:
    """风格与案例各用一个前缀：共用就没法按前缀还原某条提示词出自哪份名录。"""
    assert motion_styles.motion_style_prompt_key("ink-wash") == "motion-style.ink-wash"
    assert motion_styles.PROMPT_KEY_PREFIX != "motion."
    assert not motion_styles.PROMPT_KEY_PREFIX.startswith("motion..")


# ----------------------------------------------------- 5) 只读端点与提示词库分组


def test_styles_route_returns_a_bare_list_and_display_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)

    payload = client.get("/api/v1/projects/58/freezone/motion/styles").json()

    styles = motion_styles.load_motion_styles()
    assert payload["ok"] is True
    assert isinstance(payload["data"], list)
    assert [item["id"] for item in payload["data"]] == [item["id"] for item in styles]
    assert payload["categories"] == motion_styles.load_motion_style_manifest()["categories"]
    assert payload["version"] == motion_styles.load_motion_style_manifest()["version"]
    assert payload["source"]
    first = payload["data"][0]
    for field in ("label", "label_en", "category", "brief", "tags", "palette", "prompt_cn", "source"):
        assert field in first
    # 风格卡片没有海报与原片：样张由 palette 画，素材口径只属于案例图册。
    assert "poster_ready" not in first
    assert "media" not in first


def test_style_prompt_route_matches_the_loader_and_404s(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    style = motion_styles.load_motion_styles()[0]

    payload = client.get(
        f"/api/v1/projects/58/freezone/motion/styles/{style['id']}/prompt"
    ).json()

    # 图册复制、提示词库与渲染任务必须逐字相同，所以拼装只有一处。
    assert payload["data"]["prompt"] == motion_styles.compose_motion_style_prompt(style["id"])
    assert payload["data"]["style_id"] == style["id"]
    assert (
        client.get("/api/v1/projects/58/freezone/motion/styles/no_such/prompt").status_code == 404
    )


def test_prompt_library_registers_every_style() -> None:
    """每条风格都要在「设置→提示词库」里有对应条目，超管改一条才可能生效到渲染任务。"""
    entries = [
        entry
        for entry in prompt_library_defaults.DEFAULT_PROMPTS
        if entry["group"] == prompt_library_defaults.MOTION_STYLE_GROUP
    ]
    styles = motion_styles.load_motion_styles()
    assert len(entries) == len(styles)
    assert [entry["key"] for entry in entries] == [
        motion_styles.motion_style_prompt_key(style["id"]) for style in styles
    ]
    by_key = {entry["key"]: entry for entry in entries}
    for style in styles[:3]:
        entry = by_key[motion_styles.motion_style_prompt_key(style["id"])]
        # 入库时统一 rstrip 掉尾换行；读取方（build_freezone_*）再补回一个换行，
        # 所以库里存的与图册复制到的只差这一处收口，不是两份文字。
        assert entry["default"] == motion_styles.compose_motion_style_prompt(
            style["id"]
        ).rstrip()
        assert entry["label"]
    assert all(entry["variables"] == () for entry in entries)


# ----------------------------------------------------------- 6) 案例与风格互斥


def test_route_rejects_case_and_style_together_and_an_empty_page() -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes.freezone.motion import _check_page_source

    with pytest.raises(HTTPException) as both:
        _check_page_source("", "M43", "ink-wash")
    assert both.value.status_code == 400
    assert "案例与风格只能选一个" in both.value.detail

    # 贴了源码就不需要 id；三个都空才拦。
    _check_page_source("<html></html>", "", "")
    _check_page_source("", "M43", "")
    _check_page_source("", "", "ink-wash")
    with pytest.raises(HTTPException) as empty:
        _check_page_source("   ", "", "")
    assert "MG 页面内容为空" in empty.value.detail


def test_runner_rejects_case_and_style_together_before_calling_the_model() -> None:
    """排队的 envelope 可能绕过路由直接进任务（重跑、手工投递），任务层要自己再确认。"""
    from novelvideo.task_backend.runners.freezone import _motion_page_source

    with pytest.raises(RuntimeError, match="案例与风格只能选一个"):
        _motion_page_source({"html": "<html></html>", "case_id": "M43", "style_id": "ink-wash"})
    assert _motion_page_source({"html": "<html></html>"}) == ("", "")
    assert _motion_page_source({"style_id": " ink-wash "}) == ("", "ink-wash")
    with pytest.raises(RuntimeError, match="MG 页面内容为空"):
        _motion_page_source({"html": ""})
