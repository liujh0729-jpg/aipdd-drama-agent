"""characters.face_mat_routes 子模块（自 api/routes/characters.py 拆分）。"""

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import IdentityImageGenRequest
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.path_resolver import canonical_identity_composite_path
from novelvideo.utils.path_resolver import compute_identity_costume_path
from novelvideo.utils.path_resolver import compute_identity_face_mat_path
from novelvideo.utils.path_resolver import compute_identity_path
from novelvideo.utils.path_resolver import compute_identity_portrait_path

from ._common import logger

router = APIRouter()


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/face-mat/generate-async"
)
async def generate_identity_face_mat_async(
    project: str,
    name: str,
    identity_id: str,
    body: IdentityImageGenRequest = IdentityImageGenRequest(),
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 五官垫图：单张 1:1 画布 4×4 网格设定表（异步任务）。

    严格复刻参考图人物的 8 个头部角度（半身头像构图），用于角色一致性锁脸参考。
    """
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}

        config = _characters.load_project_config(username, project_name)
        scope = f"character:{name}:identity_face_mat:{identity.identity_name}"
        style = body.style or config.get("visual_style", "chinese_period_drama")
        model = _characters._resolve_character_image_model(username, project_name, body.model)
        if ctx is not None:
            queued = await _characters.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="character_portrait",
                queue_kind="default",
                episode=0,
                scope=scope,
                payload={
                    "mode": "identity_face_mat",
                    "task_type": "character_portrait",
                    "character_name": name,
                    "identity_id": identity_id,
                    "identity_name": identity.identity_name,
                    "style": style,
                    "model": model,
                    "scope": scope,
                    "output_dir": str(project_dir),
                },
            )
            return {
                "ok": True,
                "task_type": "character_portrait",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "character_portrait", ctx.project_id, 0, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"五官垫图生成任务已进入队列: {identity.identity_name}",
            }

        return {"ok": False, "error": "五官垫图生成需要 project context"}


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/composite/stitch"
)
async def stitch_identity_composite(
    project: str,
    name: str,
    identity_id: str,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 把身份三视图造型图与五官垫图左右拼成一张图。

    纯尺幅合成（本地 PIL）：不创造新内容，因此不重走模型、不进任务队列，毫秒级回图，拼完可直接当作一张一致性参考图使用。
    """
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}

        from novelvideo.utils.image_stitch import stitch_side_by_side

        left = compute_identity_path(project_dir, name, identity.identity_name)
        right = compute_identity_face_mat_path(project_dir, name, identity.identity_name)
        if not left or not right:
            return {"ok": False, "error": "请先生成身份三视图造型图与五官垫图"}

        target = canonical_identity_composite_path(project_dir, name, identity.identity_name)
        # PIL 是同步 CPU/IO 活，抱到线程里，别占住事件循环
        await asyncio.to_thread(stitch_side_by_side, left, right, target)
        await store.update_character_identity(name, identity_id, composite_image=str(target))

        return {
            "ok": True,
            "data": {
                "composite_image_path": str(target),
                "composite_image_url": _characters._asset_url(ctx, project_dir, target),
            },
        }


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/generate-async"
)
async def generate_identity_image_async(
    project: str,
    name: str,
    identity_id: str,
    body: IdentityImageGenRequest = IdentityImageGenRequest(),
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}

        config = _characters.load_project_config(username, project_name)
        scope = f"character:{name}:identity:{identity.identity_name}"
        style = body.style or config.get("visual_style", "chinese_period_drama")
        model = _characters._resolve_character_image_model(username, project_name, body.model)
        if ctx is not None:
            queued = await _characters.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="identity_image",
                queue_kind="default",
                episode=0,
                scope=scope,
                payload={
                    "mode": "identity_image",
                    "task_type": "identity_image",
                    "character_name": name,
                    "identity_id": identity_id,
                    "identity_name": identity.identity_name,
                    "style": style,
                    "model": model,
                    "scope": scope,
                    "output_dir": str(project_dir),
                },
            )
            return {
                "ok": True,
                "task_type": "identity_image",
                "scope": scope,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "identity_image", ctx.project_id, 0, scope=scope
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"身份图生成任务已进入队列: {identity.identity_name}",
            }

        return {"ok": False, "error": "身份图生成需要 project context"}


@router.post("/projects/{project}/characters/{name}/identities/{identity_id}/generate")
async def generate_identity_image(
    project: str,
    name: str,
    identity_id: str,
    body: IdentityImageGenRequest = IdentityImageGenRequest(),
    user: dict = Depends(get_api_user),
):
    """基于角色肖像生成身份参考图（Identity Locking）。"""
    from novelvideo.generators.image_generator import generate_identity_image_unified

    logger.info(
        "[%s] generate_identity_image: %s/%s, model=%s",
        project,
        name,
        identity_id,
        body.model,
    )
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        # 查找身份
        identity = None
        for id_ in character.identities or []:
            if id_.identity_id == identity_id:
                identity = id_
                break
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}

        costume_image = compute_identity_costume_path(
            project_dir, name, identity.identity_name
        ) or (getattr(identity, "costume_image", "") or "")
        identity_portrait = compute_identity_portrait_path(
            project_dir, name, identity.identity_name
        ) or (getattr(identity, "portrait_image", "") or "")
        identity_age = getattr(identity, "age_group", "") or ""
        char_age = getattr(character, "age_group", "youth") or "youth"
        is_age_variant = bool(identity_age and identity_age != char_age)
        has_costume_image = bool(costume_image and Path(costume_image).exists())
        has_identity_portrait = bool(identity_portrait and Path(identity_portrait).exists())
        if (
            not identity.appearance_details
            and not getattr(identity, "face_prompt", "")
            and not has_costume_image
        ):
            return {
                "ok": False,
                "error": "Identity has no appearance_details, face_prompt, or costume_image",
            }

        # 输出路径
        identities_dir = project_dir / "assets" / "characters" / name / "identities"
        identities_dir.mkdir(parents=True, exist_ok=True)
        safe_identity_name = re.sub(r'[/\\:*?"<>|]', "_", identity.identity_name)
        output_path = identities_dir / f"{safe_identity_name}.png"

        # 备份旧文件
        if output_path.exists():
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = identities_dir / f"{safe_identity_name}_{ts}.png"
            shutil.copy(output_path, backup)

        # 读取项目配置获取默认 style/ethnicity
        proj_config = _characters.load_project_config(username, project_name)

        face_override = getattr(identity, "face_prompt", "") or ""
        identity_scope = f"character:{name}:identity:{identity.identity_name}"
        if is_age_variant:
            combined_prompt = (
                ""
                if has_identity_portrait and has_costume_image
                else (
                    identity.appearance_details
                    if has_identity_portrait
                    else (
                        face_override
                        if has_costume_image
                        else (
                            f"{face_override}\n{identity.appearance_details}"
                            if identity.appearance_details
                            else face_override
                        )
                    )
                )
            )
            result = await generate_identity_image_unified(
                character_name=name,
                identity_prompt=combined_prompt,
                reference_image_path=identity_portrait if has_identity_portrait else "",
                output_path=str(output_path),
                character_tag=getattr(identity, "character_tag", ""),
                ethnicity=proj_config.get("ethnicity", "Chinese"),
                style=body.style or proj_config.get("visual_style"),
                model=_characters._resolve_character_image_model(username, project_name, body.model),
                project_dir=str(project_dir),
                costume_image_path=costume_image if has_costume_image else "",
                usage_task_type="identity_image",
                usage_scope=identity_scope,
                identity_name=identity.identity_name,
            )
        else:
            portrait_path = _characters.compute_portrait_path(project_dir, name)
            if not portrait_path or not Path(portrait_path).exists():
                return {
                    "ok": False,
                    "error": f"Character '{name}' has no portrait. Generate portrait first",
                }

            result = await generate_identity_image_unified(
                character_name=name,
                identity_prompt="" if has_costume_image else identity.appearance_details,
                reference_image_path=str(portrait_path),
                output_path=str(output_path),
                character_tag=getattr(identity, "character_tag", ""),
                ethnicity=proj_config.get("ethnicity", "Chinese"),
                style=body.style or proj_config.get("visual_style"),
                model=_characters._resolve_character_image_model(username, project_name, body.model),
                project_dir=str(project_dir),
                costume_image_path=costume_image if has_costume_image else "",
                usage_task_type="identity_image",
                usage_scope=identity_scope,
                identity_name=identity.identity_name,
            )

        if isinstance(result, bool):
            success = result
            error_msg = "Identity image generation failed"
        else:
            success = result.get("success", False)
            error_msg = result.get("error", "Identity image generation failed")
        if not success:
            return {"ok": False, "error": error_msg}

        image_url = _characters._asset_url(
            ctx,
            project_dir,
            project_dir
            / "assets"
            / "characters"
            / name
            / "identities"
            / f"{safe_identity_name}.png",
        )

        return {"ok": True, "data": {"image_url": image_url}}
