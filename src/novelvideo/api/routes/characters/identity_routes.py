"""characters.identity_routes 子模块（自 api/routes/characters.py 拆分）。"""

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.asset_metadata import newest_updated_at
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import IdentityCreate
from novelvideo.api.schemas import IdentityUpdate
from novelvideo.utils.path_resolver import compute_identity_composite_path
from novelvideo.utils.path_resolver import compute_identity_costume_path
from novelvideo.utils.path_resolver import compute_identity_face_mat_path
from novelvideo.utils.path_resolver import compute_identity_path
from novelvideo.utils.path_resolver import compute_identity_portrait_path

router = APIRouter()


@router.get("/projects/{project}/characters/{name}/identities")
async def get_character_identities(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    """获取角色全部身份及图片。"""
    # store 只用来取角色，取完就关：后面拼载荷读的是已经在内存里的模型对象和
    # 文件系统，不再需要连接。"角色不存在" 的提前返回因此也发生在关闭之后。
    async with _characters._character_project_scope(
        project,
        user,
        required_role="viewer",
        load_graph_state=False,
    ) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        characters = await store.list_characters()

    target = None
    for c in characters:
        if c.name == name:
            target = c
            break

    if target is None:
        return {"ok": False, "error": f"Character '{name}' not found"}

    identities = []
    asset_project = getattr(ctx, "project_id", "") or project
    if hasattr(target, "identities"):
        for ident in target.identities:
            identity_name = (
                ident.identity_name if hasattr(ident, "identity_name") else ""
            )
            abs_image = (
                compute_identity_path(project_dir, target.name, identity_name)
                if identity_name
                else ""
            )
            abs_costume = (
                compute_identity_costume_path(project_dir, target.name, identity_name)
                if identity_name
                else ""
            )
            abs_portrait = (
                compute_identity_portrait_path(project_dir, target.name, identity_name)
                if identity_name
                else ""
            )
            # [aipdd fork delta] 五官垫图（1:1 画布 4×4 网格设定表）
            abs_face_mat = (
                compute_identity_face_mat_path(project_dir, target.name, identity_name)
                if identity_name
                else ""
            )
            # [aipdd fork delta] 「三视图 + 五官垫图」本地拼接图
            abs_composite = (
                compute_identity_composite_path(project_dir, target.name, identity_name)
                if identity_name
                else ""
            )
            item = {
                "identity_id": (
                    ident.identity_id if hasattr(ident, "identity_id") else ""
                ),
                "identity_name": identity_name,
                "appearance_details": getattr(ident, "appearance_details", ""),
                "face_prompt": getattr(ident, "face_prompt", ""),
                "age_group": getattr(ident, "age_group", ""),
                "body_type": getattr(ident, "body_type", ""),
                "image_path": abs_image,
                "image_url": (
                    _characters._asset_url(ctx, project_dir, abs_image) if abs_image else ""
                ),
                "costume_image_path": abs_costume,
                "costume_image_url": (
                    _characters._asset_url(ctx, project_dir, abs_costume) if abs_costume else ""
                ),
                "portrait_image_path": abs_portrait,
                "portrait_image_url": (
                    _characters._asset_url(ctx, project_dir, abs_portrait) if abs_portrait else ""
                ),
                "face_mat_image_path": abs_face_mat,
                "face_mat_image_url": (
                    _characters._asset_url(ctx, project_dir, abs_face_mat) if abs_face_mat else ""
                ),
                "composite_image_path": abs_composite,
                "composite_image_url": (
                    _characters._asset_url(ctx, project_dir, abs_composite) if abs_composite else ""
                ),
                "updated_at": newest_updated_at(
                    getattr(ident, "updated_at", ""),
                    getattr(target, "updated_at", ""),
                    _characters.tree_updated_at(abs_image),
                    _characters.tree_updated_at(abs_costume),
                    _characters.tree_updated_at(abs_portrait),
                    _characters.tree_updated_at(abs_face_mat),
                    _characters.tree_updated_at(abs_composite),
                ),
            }
            item.update(
                _characters._character_asset_links(
                    project=asset_project,
                    character_name=target.name,
                    kind="identity",
                    identity_id=getattr(ident, "identity_id", ""),
                )
            )
            item["costume_history_url"] = _characters._character_asset_links(
                project=asset_project,
                character_name=target.name,
                kind="identity_costume",
                identity_id=getattr(ident, "identity_id", ""),
            )["history_url"]
            item["portrait_history_url"] = _characters._character_asset_links(
                project=asset_project,
                character_name=target.name,
                kind="identity_portrait",
                identity_id=getattr(ident, "identity_id", ""),
            )["history_url"]
            item.update(_characters._identity_voice_fields(ctx, project_dir, ident))
            identities.append(item)

    return {"ok": True, "data": identities}


@router.post("/projects/{project}/characters/{name}/identities")
async def add_identity(
    project: str,
    name: str,
    body: IdentityCreate,
    user: dict = Depends(get_api_user),
):
    """为角色新增一个身份。"""
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        from novelvideo.models import CharacterIdentity

        identity_id = f"{name}_{body.identity_name}"
        identity = CharacterIdentity(
            identity_id=identity_id,
            character_name=name,
            identity_name=body.identity_name,
            age_group=body.age_group,
            appearance_details=body.appearance_details,
            source="api",
        )

        await store.add_character_identity(name, identity)

        return {
            "ok": True,
            "data": {
                "identity_id": identity_id,
                "identity_name": body.identity_name,
                "age_group": body.age_group,
                "appearance_details": body.appearance_details,
            },
        }


@router.patch("/projects/{project}/characters/{name}/identities/{identity_id}")
async def update_identity(
    project: str,
    name: str,
    identity_id: str,
    body: IdentityUpdate,
    user: dict = Depends(get_api_user),
):
    """编辑角色身份属性。"""
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return {"ok": True, "data": {"message": "No fields to update"}}

        await store.update_character_identity(name, identity_id, **updates)

        return {
            "ok": True,
            "data": {"identity_id": identity_id, "updated_fields": list(updates.keys())},
        }


@router.delete("/projects/{project}/characters/{name}/identities/{identity_id}")
async def delete_identity(
    project: str,
    name: str,
    identity_id: str,
    user: dict = Depends(get_api_user),
):
    """删除角色身份。"""
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        await store.delete_character_identity(name, identity_id)

        return {"ok": True, "data": {"identity_id": identity_id, "message": "身份已删除"}}


@router.get("/projects/{project}/characters/{name}/identities/{identity_id}/attempts")
async def get_identity_attempts(
    project: str,
    name: str,
    identity_id: str,
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user, required_role="viewer") as (_ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}
        safe_name = _characters._safe_asset_name(identity.identity_name)
        identities_dir = project_dir / "assets" / "characters" / name / "identities"
        image_attempts = len(
            [
                p
                for p in identities_dir.glob(f"{safe_name}*.png")
                if not p.name.endswith("_costume.png") and "_portrait" not in p.stem
            ]
        )
        portrait_attempts = sum(
            1
            for path in identities_dir.glob(f"*{safe_name}_portrait*.png")
            if path.is_file() and not path.name.startswith(".")
        )
        return {
            "ok": True,
            "data": {
                "image_attempts": image_attempts,
                "portrait_attempts": portrait_attempts,
            },
        }
