"""characters._common 子模块（自 api/routes/characters.py 拆分）。"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from novelvideo.api.deps import make_sqlite_store
from novelvideo.api.deps import sqlite_store_for_context_scope
from novelvideo.api.deps import sqlite_store_scope
from novelvideo.api.routes import characters as _characters
from novelvideo.config import image_generation_selection_options
from novelvideo.config import normalize_character_image_selection
from novelvideo.config import normalize_image_generation_selection
from novelvideo.project_context import ProjectContext
from novelvideo.seedance2_i2v.character_voice_storage import DEFAULT_SLOT as VOICE_DEFAULT_SLOT
from novelvideo.sqlite_store import SQLiteStore



logger = logging.getLogger("novelvideo.api.characters")



CHARACTER_IMAGE_SELECTION_CONFIG_KEY = "character_image_selection"
ASSET_IMAGE_SELECTION_CONFIG_KEYS = {
    "character": CHARACTER_IMAGE_SELECTION_CONFIG_KEY,
    "scene": "scene_image_selection",
    "prop": "prop_image_selection",
}
CHARACTER_IMAGE_USAGE_TASK_TYPES = ("character_portrait", "identity_image")
CHARACTER_ASSET_KINDS = {
    "portrait",
    "identity",
    "identity_costume",
    "identity_portrait",
}

VOICE_SLOT_LABELS = {
    VOICE_DEFAULT_SLOT: "默认（兜底）",
    "child": "幼年",
    "youth": "青年",
    "middle": "中年",
    "elder": "老年",
}


async def _resolve_character_project(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
) -> tuple[ProjectContext | None, str, str, Path, str, SQLiteStore]:
    resolved = await _characters.resolve_project_scope(project, user, required_role=required_role)
    store = (
        await _characters.make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(resolved.username, resolved.project_name)
    )
    return (
        resolved.ctx,
        resolved.username,
        resolved.project_name,
        resolved.project_dir,
        resolved.output_dir,
        store,
    )


@asynccontextmanager
async def _character_project_scope(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
    load_graph_state: bool = True,
) -> "AsyncIterator[tuple[ProjectContext | None, str, str, Path, str, SQLiteStore]]":
    """``_resolve_character_project`` 的作用域版：出了 ``async with`` 一定关连接。

    裸版本把 store 直接返回给路由，而路由从头到尾没有一处 ``close()``——正常返回、
    "角色不存在" 这类提前返回、以及中途抛错，三条路都不关。每个 SQLiteStore 背后是
    一条 aiosqlite 连接加一个后台线程，指望 GC 回收既不及时也不保证。角色页一进去
    就打列表、选中角色再打 identities，这两条正是资产页的常规加载路径，泄漏是按请
    求数累积的。

    元组形状与裸版本一致，改造路由只需要把赋值换成 ``async with``。
    """
    resolved = await _characters.resolve_project_scope(project, user, required_role=required_role)
    store_scope = (
        sqlite_store_for_context_scope(
            resolved.ctx,
            load_graph_state=load_graph_state,
        )
        if resolved.ctx
        else sqlite_store_scope(
            resolved.username,
            resolved.project_name,
            load_graph_state=load_graph_state,
        )
    )
    async with store_scope as store:
        yield (
            resolved.ctx,
            resolved.username,
            resolved.project_name,
            resolved.project_dir,
            resolved.output_dir,
            store,
        )


def _character_image_selection_payload(username: str, project: str) -> dict:
    options = _characters.character_image_selection_options()
    config = _characters.load_project_config_file(username, project)
    saved_selection = str(
        config.get(CHARACTER_IMAGE_SELECTION_CONFIG_KEY) or ""
    ).strip()
    if saved_selection in options:
        selection = saved_selection
    else:
        selection = normalize_character_image_selection(saved_selection)
        if selection not in options:
            selection = _characters.get_character_image_selection()
    return {"character_image_selection": selection, "options": options}


def _asset_image_source_selection_payload(username: str, project: str, asset_kind: str) -> dict:
    options = image_generation_selection_options()
    config_key = ASSET_IMAGE_SELECTION_CONFIG_KEYS[asset_kind]
    if asset_kind == "character":
        selection = _character_image_selection_payload(username, project)["character_image_selection"]
    else:
        saved_selection = str(_characters.load_project_config_file(username, project).get(config_key) or "")
        selection = normalize_image_generation_selection(saved_selection)
    return {
        "asset_kind": asset_kind,
        "image_source_selection": selection,
        "options": options,
    }


def _validate_asset_image_source_kind(asset_kind: str) -> str | None:
    normalized = str(asset_kind or "").strip().lower()
    if normalized in ASSET_IMAGE_SELECTION_CONFIG_KEYS:
        return normalized
    return None


def _resolve_character_image_model(
    username: str, project: str, requested_model: str | None
) -> str:
    model = str(requested_model or "").strip()
    if model:
        return model
    return _character_image_selection_payload(username, project)[
        "character_image_selection"
    ]
