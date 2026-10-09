"""characters.character_routes 子模块（自 api/routes/characters.py 拆分）。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from novelvideo.api.asset_metadata import newest_updated_at
from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import may_run_asset_repair
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import CharacterCreate
from novelvideo.api.schemas import CharacterUpdate
from novelvideo.novel_source import has_imported_novel
from novelvideo.novel_source import novel_import_required_response
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.asset_names import path_safe_asset_name
from novelvideo.utils.path_resolver import canonical_portrait_path

from ._common import logger

router = APIRouter()


@router.get("/projects/{project}/characters")
async def list_characters(
    project: str,
    summary: bool = False,
    names: Annotated[list[str] | None, Query()] = None,
    user: dict = Depends(get_api_user),
):
    """获取角色列表；资产工作台可显式使用 summary 跳过文件探测。"""
    async with _characters._character_project_scope(
        project,
        user,
        required_role="viewer",
        load_graph_state=False,
    ) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        if may_run_asset_repair(ctx):
            await _characters._heal_path_unsafe_character_names(store, project_dir)
        characters = await store.list_characters()
        characters = await _characters._repair_duplicate_main_characters(
            store, characters
        )

    requested_names = {
        str(name or "").strip() for name in (names or []) if str(name or "").strip()
    }
    if requested_names:
        characters = [c for c in characters if c.name in requested_names]

    asset_project = getattr(ctx, "project_id", "") or project

    def build_item(c) -> dict:
        canonical_portrait = canonical_portrait_path(project_dir, c.name)
        abs_portrait = str(canonical_portrait) if summary else _characters.compute_portrait_path(
            project_dir, c.name
        )
        item: dict = {
            "name": c.name,
            "aliases": c.aliases if hasattr(c, "aliases") else [],
            "description": c.description if hasattr(c, "description") else "",
            "role": getattr(c, "role", ""),
            "gender": getattr(c, "gender", ""),
            "age_group": getattr(c, "age_group", ""),
            "body_type": getattr(c, "body_type", ""),
            "face_prompt": getattr(c, "face_prompt", ""),
            "is_main": c.is_main if hasattr(c, "is_main") else False,
            "portrait_path": abs_portrait,
            "portrait_url": (
                _characters._convention_asset_url(
                    ctx,
                    project_dir,
                    canonical_portrait,
                    version=getattr(c, "updated_at", "") or "",
                    project_id=project,
                )
                if summary
                else _characters._asset_url(ctx, project_dir, abs_portrait)
                if abs_portrait
                else ""
            ),
            "updated_at": (
                getattr(c, "updated_at", "")
                if summary
                else newest_updated_at(
                    getattr(c, "updated_at", ""),
                    _characters.tree_updated_at(
                        project_dir / "assets" / "characters" / c.name
                    ),
                )
            ),
            # 只出 id，不出身份详情。资产页要把 ``?type=identity&id=`` 深链解析到
            # 拥有它的角色，此前是遍历每个角色各调一次 ``/characters/{name}/identities``
            # ——角色有多少个就发多少个请求，只为建一张 id→角色名 的表。身份已经随
            # ``get_all_characters()`` 在内存里了，这里带出来不多一次查询；而带的是
            # 一串 id，载荷不会随身份的图片/描述增长。
            "identity_ids": [
                str(getattr(ident, "identity_id", "") or "")
                for ident in (getattr(c, "identities", None) or [])
                if str(getattr(ident, "identity_id", "") or "")
            ],
        }
        item.update(
            _characters._character_asset_links(
                project=asset_project,
                character_name=c.name,
                kind="portrait",
            )
        )
        item.update(
            _characters._character_voice_fields(
                ctx, project_dir, c, probe_files=not summary
            )
        )
        return item

    # Full details still perform authoritative filesystem checks. Keep those
    # blocking OSSFS operations away from the API worker's event loop.
    data = await asyncio.to_thread(lambda: [build_item(c) for c in characters])

    return {"ok": True, "data": data}


@router.post("/projects/{project}/characters")
async def add_character(
    project: str,
    body: CharacterCreate,
    user: dict = Depends(get_api_user),
):
    """手动添加单个角色（当自动提取失败时使用）。"""
    logger.info("[%s] add_character: %s (main=%s)", project, body.name, body.is_main)
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):

        from novelvideo.models import NovelCharacter

        # 在查重之前消毒，否则两个只差斜杠的名字会双双通过查重、后写的静默覆盖先写的。
        name = path_safe_asset_name(str(body.name or "").strip(), kind="character")
        if not name:
            return {"ok": False, "error": "Character name is required"}

        # 检查角色是否已存在
        existing = store.get_character(name)
        if existing is not None:
            return {"ok": False, "error": f"Character '{name}' already exists"}

        if body.is_main:
            await _characters._unset_other_main_characters(store, name)

        char = NovelCharacter(
            name=name,
            role=body.role,
            is_main=body.is_main,
            gender=body.gender,
            age_group=body.age_group,
            description=body.description,
            face_prompt=body.face_prompt,
        )
        await store.add_character(char)

        return {
            "ok": True,
            "data": char.model_dump(
                include={
                    "name",
                    "role",
                    "is_main",
                    "gender",
                    "age_group",
                    "description",
                    "face_prompt",
                }
            ),
        }


@router.post("/projects/{project}/characters/build")
async def build_characters(project: str, user: dict = Depends(get_api_user)):
    """从知识图谱补充缺失角色。"""
    logger.info("[%s] build_characters", project)
    resolved = await _characters.resolve_project_scope(project, user, required_role="editor")
    ctx = resolved.ctx
    output_dir = resolved.output_dir
    if ctx is not None:
        if not has_imported_novel(resolved.project_dir):
            return novel_import_required_response()
        queued = await _characters.get_task_backend().enqueue_project_task(
            ctx,
            product_surface="mainline",
            task_type="build_characters",
            queue_kind="default",
            episode=0,
            payload={"output_dir": output_dir},
        )
        return {
            "ok": True,
            "task_type": "build_characters",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("build_characters", ctx.project_id, 0),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": "角色补充任务已进入队列",
        }

    return {"ok": False, "error": "角色补充需要 project context"}


@router.patch("/projects/{project}/characters/{name}")
async def update_character(
    project: str,
    name: str,
    body: CharacterUpdate,
    user: dict = Depends(get_api_user),
):
    """编辑角色基本信息。"""
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        updates = body.model_dump(exclude_none=True)
        requested_name = None
        if "name" in updates:
            # 消毒后再查重、再改名：斜杠会让这个角色的 {name} 接口整排 404。
            requested_name = path_safe_asset_name(
                str(updates.pop("name") or "").strip(), kind="character"
            )
            if not requested_name:
                return {"ok": False, "error": "Character name cannot be empty"}

        updated_fields: list[str] = []
        renamed_from = None
        target_name = name

        if requested_name and requested_name != name:
            if store.get_character(requested_name) is not None:
                return {
                    "ok": False,
                    "error": f"Character '{requested_name}' already exists",
                }
            try:
                await store.rename_character(name, requested_name)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            target_name = requested_name
            renamed_from = name
            updated_fields.append("name")

        if not updates and not updated_fields:
            return {"ok": True, "data": {"message": "No fields to update"}}

        if updates.get("is_main") is True:
            await _characters._unset_other_main_characters(store, target_name)

        if updates:
            await store.update_character(target_name, **updates)
            updated_fields.extend(updates.keys())

        data = {"name": target_name, "updated_fields": updated_fields}
        if renamed_from:
            data["renamed_from"] = renamed_from
        return {"ok": True, "data": data}


@router.post("/projects/{project}/characters/{name}/delete")
async def delete_character(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    """删除角色。POST 保持与 React active UI 的兼容契约。"""
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        await store.delete_character(name)
        return {"ok": True, "data": {"name": name, "deleted": True}}
