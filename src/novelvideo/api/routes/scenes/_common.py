"""scenes._common 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from novelvideo.api.routes import scenes as _scenes
from novelvideo.project_context import ProjectContext
from novelvideo.sqlite_store import SQLiteStore



logger = logging.getLogger("novelvideo.api.scenes")


class _InvalidSceneImageError(ValueError):
    pass


class _InvalidScenePanoRatioError(ValueError):
    pass


class _EmptyScenePackageError(ValueError):
    pass


_SCENE_TIME_TOKENS = {
    "清晨",
    "晨",
    "上午",
    "正午",
    "午",
    "午后",
    "下午",
    "黄昏",
    "傍晚",
    "夜晚",
    "夜",
    "白天",
    "日",
}


def _project_style(username: str, project: str) -> str:
    config = _scenes.load_project_config_file(username, project)
    return str(config.get("visual_style") or config.get("project_style") or "")


def _asset_url(ctx: ProjectContext, project_dir: Path, abs_path: str | Path) -> str:
    path = Path(abs_path)
    if not path.exists():
        return ""
    try:
        rel_path = path.relative_to(project_dir).as_posix()
    except ValueError:
        return ""
    return _scenes.make_static_url_for_context(ctx, rel_path, local_path=path)


async def _resolve_scene_project(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
) -> tuple[ProjectContext, str, str, Path, str, SQLiteStore]:
    ctx = await _scenes.resolve_project_context(
        user=user,
        project_id=project,
        required_role=required_role,
    )
    # Scene routes use SQLite's direct async methods. Hydrating the legacy
    # character/episode/prop caches here adds three unrelated full-table reads
    # to every scene request and was especially visible on OSS-backed homes.
    store = await _scenes.make_sqlite_store_for_context(ctx, load_graph_state=False)
    return (
        ctx,
        ctx.owner_username,
        ctx.project_name,
        Path(ctx.output_dir),
        str(ctx.output_dir),
        store,
    )


@asynccontextmanager
async def _scene_project_scope(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
) -> "AsyncIterator[tuple[ProjectContext, str, str, Path, str, SQLiteStore]]":
    """``_resolve_scene_project`` 的作用域版：出了 ``async with`` 一定关连接。

    裸版本把 store 直接返回给路由，而路由从头到尾没有一处 ``close()``——正常返回、
    提前返回、以及中途抛错三条路都不关，每次调用泄漏一条 aiosqlite 连接 + 后台线程。
    沿用裸版本的 ``load_graph_state=False``（场景路由只用 SQLite 直接异步方法，不必
    水合 character/episode/prop 缓存）。工厂按 ``_scenes`` 模块全局解析以保留测试接缝。
    元组形状与裸版本一致，改造路由只需把赋值换成 ``async with``。
    """
    ctx = await _scenes.resolve_project_context(
        user=user,
        project_id=project,
        required_role=required_role,
    )
    store = await _scenes.make_sqlite_store_for_context(ctx, load_graph_state=False)
    try:
        yield (
            ctx,
            ctx.owner_username,
            ctx.project_name,
            Path(ctx.output_dir),
            str(ctx.output_dir),
            store,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
