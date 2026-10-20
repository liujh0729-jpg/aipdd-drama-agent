"""[aipdd fork delta] 提示词库：注册表不变量、覆盖 / 恢复默认、渲染容错与调用点接线。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# [aipdd fork delta] 新增 character.face_mat 后为 20 条；新增 director 组 3 条后为 23 条；
# 新增 director novel（整本故事→多集剧本）3 条后为 26 条。
# [aipdd fork delta] 新增 freezone.story_version.system（故事版基础提示词）后为 27 条。
# [aipdd fork delta] 动效大片图册 37 条案例按清单登记（motion.<case_id>）后为 64 条。
# [aipdd fork delta] 画风图册 66 条风格按清单登记（motion-style.<style_id>）后为 130 条。
KEY_TOTAL = 130


@pytest.fixture()
def library_db(monkeypatch, tmp_path):
    """把覆盖值落到临时 settings.db，避免污染本机状态。"""
    from novelvideo import config
    from novelvideo.catalog import prompt_library

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    prompt_library._invalidate_override_cache()
    yield prompt_library
    prompt_library._invalidate_override_cache()


@pytest.fixture()
def prompt_client(library_db):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import prompt_library as route

    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    # 写操作已抬到 ``require_owner``：夹具需以浏览器会话里的 owner 身份过闸。
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "u-alice",
        "username": "alice",
        "role": "owner",
        "credential_kind": "browser_session",
    }
    return TestClient(app)


def _client_as(library_db, user: dict) -> TestClient:
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import prompt_library as route

    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: user
    return TestClient(app)


def test_registry_shape_is_stable(library_db) -> None:
    entries = library_db.prompt_entries()
    keys = [entry.key for entry in entries]
    assert len(keys) == len(set(keys)) == KEY_TOTAL
    assert library_db.GROUP_ORDER == (
        "character",
        "video",
        "director",
        "freezone",
        "grid",
        "motion",
        # 画风图册单独一组：与案例共用一组会挤成 100 多条，没法浏览。
        "motion_style",
    )
    for entry in entries:
        assert entry.default.strip() == entry.default
        assert entry.label.strip() and entry.description.strip()
        for name in entry.variables:
            assert f"{{{name}}}" in entry.default
            assert name.isidentifier()


def test_story_version_prompt_entry_registered(library_db) -> None:
    # [aipdd fork delta] 故事版：分镜脚本页生成多格分镜板的基础提示词必须已
    # 入提示词库（group=freezone，变量 user_requirement），且能被 render_prompt 取用。
    key = "freezone.story_version.system"
    entry = library_db.get_prompt_entry(key)
    assert entry.group == "freezone"
    assert entry.variables == ("user_requirement",)
    assert "创作要求：{user_requirement}" in entry.default
    rendered = library_db.render_prompt(key, user_requirement="开场三个镜头")
    assert "开场三个镜头" in rendered


def test_motion_gallery_cases_are_registered_from_the_manifest(library_db) -> None:
    """动效图册每条案例都进库，且默认文字与清单现拼的那条一致（同一出处）。"""
    from novelvideo.freezone.motion_templates import (
        compose_motion_prompt,
        load_motion_cases,
        motion_prompt_key,
    )

    cases = load_motion_cases()
    entries = [e for e in library_db.prompt_entries() if e.group == "motion"]
    assert len(entries) == len(cases)
    assert [e.key for e in entries] == [motion_prompt_key(c["id"]) for c in cases]
    for entry, case in zip(entries, cases):
        assert entry.label == case["label"]
        assert entry.description == case["brief"]
        assert entry.variables == ()
        assert entry.default == compose_motion_prompt(case["id"]).rstrip()


def test_motion_prompt_override_reaches_the_gallery(library_db) -> None:
    """改库里的动效条目后，图册取用的那条提示词要跟着变（不是只改展示）。"""
    # route_helpers 顶层 import novelvideo.api.schemas，而 api 包又会回头 import
    # route_helpers —— 先让 api 包初始化完再取辅助函数，否则撞上这个既有环。
    import novelvideo.api  # noqa: F401
    from novelvideo.freezone import route_helpers
    from novelvideo.freezone.motion_templates import motion_prompt_key

    key = motion_prompt_key("M43")
    library_db.set_prompt_override(key, "只保留主体与节奏，其余自由发挥")
    try:
        built = route_helpers.build_freezone_motion_prompt("M43")
        assert built == "只保留主体与节奏，其余自由发挥\n"
    finally:
        library_db.reset_prompt_override(key)
    assert built != route_helpers.build_freezone_motion_prompt("M43")


def test_motion_style_prompt_override_reaches_the_gallery(library_db) -> None:
    """画风图册与案例同一口径：改库里的风格条目，图册复制的和按风格出片用的都跟着变。"""
    import novelvideo.api  # noqa: F401
    from novelvideo.freezone import motion_styles, route_helpers

    key = motion_styles.motion_style_prompt_key("ink-wash")
    library_db.set_prompt_override(key, "只留墨色浓淡与呼吸感，其余自由发挥")
    try:
        assert (
            route_helpers.build_freezone_motion_style_prompt("ink-wash")
            == "只留墨色浓淡与呼吸感，其余自由发挥\n"
        )
    finally:
        library_db.reset_prompt_override(key)
    # 复位后回到「风格规格 + 渲染契约」那份拼装文字。
    assert route_helpers.build_freezone_motion_style_prompt("ink-wash") == (
        motion_styles.compose_motion_style_prompt("ink-wash").rstrip() + "\n"
    )


def test_unknown_key_is_a_programming_error(library_db) -> None:
    with pytest.raises(KeyError):
        library_db.get_prompt_entry("nope.nope")
    with pytest.raises(KeyError):
        library_db.set_prompt_override("nope.nope", "x")
    with pytest.raises(KeyError):
        library_db.reset_prompt_override("nope.nope")


def test_render_only_replaces_known_placeholders(library_db) -> None:
    assert (
        library_db.render_template("a {known} b {unknown} c", {"known": "X"})
        == "a X b {unknown} c"
    )
    assert library_db.render_template("no vars here", {}) == "no vars here"
    assert library_db.render_template("no vars here", {"spare": "X"}) == "no vars here"


def test_override_roundtrip_and_revision(library_db) -> None:
    key = "video.keyframe_transition.system"
    entry = library_db.get_prompt_entry(key)
    assert library_db.get_prompt_text(key) == entry.default
    assert library_db.revision() == "0"

    library_db.set_prompt_override(key, "CUSTOM TRANSITION")
    assert library_db.get_prompt_text(key) == "CUSTOM TRANSITION"
    assert library_db.get_prompt_item(key)["is_overridden"] is True
    assert library_db.revision() != "0"

    library_db.reset_prompt_override(key)
    assert library_db.get_prompt_text(key) == entry.default
    assert library_db.get_prompt_item(key)["is_overridden"] is False
    assert library_db.revision() == "0"


def test_saving_the_builtin_text_clears_the_override(library_db) -> None:
    key = "character.costume_reference"
    entry = library_db.get_prompt_entry(key)
    library_db.set_prompt_override(key, "TEMP")
    library_db.set_prompt_override(key, f"  {entry.default}\n")
    assert library_db.get_prompt_item(key)["is_overridden"] is False
    assert library_db.revision() == "0"


def test_blank_and_oversized_values_are_rejected(library_db) -> None:
    key = "character.costume_reference"
    with pytest.raises(ValueError):
        library_db.set_prompt_override(key, "   ")
    with pytest.raises(ValueError):
        library_db.set_prompt_override(
            key, "x" * (library_db.MAX_OVERRIDE_CHARS + 1)
        )
    assert library_db.get_prompt_item(key)["is_overridden"] is False


def test_render_prompt_uses_the_override(library_db) -> None:
    key = "grid.ethnicity.fallback"
    library_db.set_prompt_override(key, "ETHNICITY={value}")
    try:
        assert (
            library_db.render_prompt(key, value="Han") == "ETHNICITY=Han"
        )
    finally:
        library_db.reset_prompt_override(key)


def test_generation_entrypoints_read_the_library(library_db) -> None:
    """改库后生成链路的入口函数立即读到新值（不再有写死常量）。"""
    from novelvideo.freezone import text_node
    from novelvideo.generators import character_generation, prompt_builder

    character_key = "character.ethnicity.fallback"
    grid_key = "grid.ethnicity.fallback"
    translator_key = "freezone.translator.system"
    library_db.set_prompt_override(character_key, "PORTRAIT ETH={ethnicity}")
    library_db.set_prompt_override(grid_key, "GRID ETH={value}")
    library_db.set_prompt_override(translator_key, "TRANSLATOR OVERRIDE")
    try:
        assert (
            character_generation._default_ethnicity_instruction("Han")
            == "PORTRAIT ETH=Han"
        )
        assert prompt_builder.default_ethnicity_instruction("Han") == "GRID ETH=Han"
        assert (
            text_node.freezone_translation_system_prompt().strip()
            == "TRANSLATOR OVERRIDE"
        )
    finally:
        library_db.reset_prompt_override(character_key)
        library_db.reset_prompt_override(grid_key)
        library_db.reset_prompt_override(translator_key)


def test_api_lists_updates_and_resets(prompt_client: TestClient) -> None:
    listing = prompt_client.get("/api/v1/prompt-library")
    assert listing.status_code == 200, listing.text
    payload = listing.json()["data"]
    assert payload["revision"] == "0"
    assert payload["groups"] == [
        "character",
        "video",
        "director",
        "freezone",
        "grid",
        "motion",
        "motion_style",
    ]
    assert len(payload["items"]) == KEY_TOTAL
    first = payload["items"][0]
    assert first["key"] == "character.portrait.animation"
    assert first["is_overridden"] is False
    assert first["value"] == first["default"]

    saved = prompt_client.put(
        "/api/v1/prompt-library/character.portrait.animation",
        json={"value": "MY PORTRAIT {character_tag}"},
    )
    assert saved.status_code == 200, saved.text
    saved_items = {
        item["key"]: item for item in saved.json()["data"]["items"]
    }
    assert saved_items["character.portrait.animation"]["is_overridden"] is True
    assert (
        saved_items["character.portrait.animation"]["value"]
        == "MY PORTRAIT {character_tag}"
    )
    assert saved.json()["data"]["revision"] != "0"

    reset = prompt_client.delete(
        "/api/v1/prompt-library/character.portrait.animation"
    )
    assert reset.status_code == 200, reset.text
    reset_items = {
        item["key"]: item for item in reset.json()["data"]["items"]
    }
    assert reset_items["character.portrait.animation"]["is_overridden"] is False
    assert reset.json()["data"]["revision"] == "0"


def test_api_rejects_unknown_key_and_blank_value(prompt_client: TestClient) -> None:
    missing = prompt_client.put(
        "/api/v1/prompt-library/nope.nope", json={"value": "x"}
    )
    assert missing.status_code == 404
    blank = prompt_client.put(
        "/api/v1/prompt-library/character.costume_reference",
        json={"value": "   "},
    )
    assert blank.status_code == 400
    missing_reset = prompt_client.delete("/api/v1/prompt-library/nope.nope")
    assert missing_reset.status_code == 404


def test_api_write_endpoints_require_owner(library_db) -> None:
    """全局提示词覆盖影响所有项目/用户，写端点必须只放行 owner。

    C2 回归：此前路由级仅 ``get_api_user``，任意已登录用户（含 member）
    都能改系统级提示词或造成质量 DoS。
    """
    member = _client_as(library_db, {"id": "u-bob", "username": "bob", "role": "member"})
    assert member.get("/api/v1/prompt-library").status_code == 200  # 读仍开放
    denied_put = member.put(
        "/api/v1/prompt-library/character.portrait.animation",
        json={"value": "PWNED {character_tag}"},
    )
    assert denied_put.status_code == 403
    denied_delete = member.delete("/api/v1/prompt-library/character.portrait.animation")
    assert denied_delete.status_code == 403
    # 未写入验证：member 的 PUT 被拒后，生效值仍为内置默认。
    assert (
        member.get("/api/v1/prompt-library").json()["data"]["revision"] == "0"
    )


def test_api_write_endpoints_reject_agent_and_api_key(library_db) -> None:
    """机器凭证不得改全局提示词（与 admin_users/world 边界一致）。"""
    for kind in ("agent_session", "api_key"):
        client = _client_as(
            library_db,
            {"id": "u-svc", "username": "svc", "role": "owner", "credential_kind": kind},
        )
        assert client.put(
            "/api/v1/prompt-library/character.portrait.animation",
            json={"value": "x {character_tag}"},
        ).status_code == 403