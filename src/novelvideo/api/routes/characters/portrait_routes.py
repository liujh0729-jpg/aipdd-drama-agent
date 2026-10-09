"""characters.portrait_routes 子模块（自 api/routes/characters.py 拆分）。"""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import IdentityImageGenRequest
from novelvideo.api.schemas import PortraitGenRequest
from novelvideo.api.upload_workers import asset_resource_lock
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.path_resolver import compute_identity_costume_path

from ._common import logger

router = APIRouter()


@router.post("/projects/{project}/characters/{name}/portrait-async")
async def generate_single_portrait_async(
    project: str,
    name: str,
    body: PortraitGenRequest = PortraitGenRequest(),
    user: dict = Depends(get_api_user),
):
    """启动单角色 Portrait 后台任务。"""
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, _store):

        config = _characters.load_project_config(username, project_name)
        scope = f"character:{name}:portrait"
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
                    "mode": "portrait",
                    "task_type": "character_portrait",
                    "character_name": name,
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
                "message": f"肖像生成任务已进入队列: {name}",
            }

        return {"ok": False, "error": "肖像生成需要 project context"}


@router.post("/projects/{project}/characters/{name}/portrait")
async def generate_single_portrait(
    project: str,
    name: str,
    body: PortraitGenRequest,
    user: dict = Depends(get_api_user),
):
    """为单个角色生成肖像（face close-up）。"""
    logger.info(
        "[%s] generate_single_portrait: %s, model=%s", project, name, body.model
    )
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        proj_config = _characters.load_project_config(username, project_name)
        style = body.style or proj_config.get("visual_style", "chinese_period_drama")

        from novelvideo.generators.image_generator import (
            generate_character_reference_unified,
        )

        # 备份旧肖像
        portrait_path = _characters.compute_portrait_path(project_dir, name)
        if portrait_path and Path(portrait_path).exists():
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = Path(portrait_path).with_name(f"portrait_{ts}.png")
            shutil.copy(portrait_path, backup)

        paths = await generate_character_reference_unified(
            character_name=name,
            appearance_prompt=(
                character.face_prompt if hasattr(character, "face_prompt") else ""
            ),
            style=style,
            ethnicity=body.ethnicity,
            model=_characters._resolve_character_image_model(username, project_name, body.model),
            output_dir=output_dir,
            project_dir=str(project_dir),
        )

        if not paths:
            return {"ok": False, "error": "Portrait generation failed"}

        # 复制为标准肖像路径
        char_dir = project_dir / "assets" / "characters" / name
        char_dir.mkdir(parents=True, exist_ok=True)
        final_path = char_dir / "portrait.png"
        shutil.copy(paths[0], final_path)
        # Canonical URLs in the lightweight list are versioned by the SQLite row,
        # so publishing a new file must advance that revision as part of the write.
        await store.touch_character_asset(name)

        portrait_url = _characters._asset_url(ctx, project_dir, final_path)

        return {"ok": True, "data": {"portrait_url": portrait_url}}


@router.post("/projects/{project}/characters/{name}/portrait/upload")
async def upload_portrait(
    project: str,
    name: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """上传角色肖像图片。"""
    logger.info("[%s] upload_portrait: %s", project, name)
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        char_dir = project_dir / "assets" / "characters" / name
        portrait_path = char_dir / "portrait.png"

        async def touch_portrait(_result):
            await store.touch_character_asset(name)

        await run_asset_upload_operation(
            _characters._persist_uploaded_character_image,
            file,
            portrait_path,
            finalize=touch_portrait,
        )

        portrait_url = _characters._asset_url(ctx, project_dir, portrait_path)

        return {"ok": True, "data": {"portrait_url": portrait_url}}


@router.post("/projects/{project}/characters/{name}/identities/{identity_name}/upload")
async def upload_identity_image(
    project: str,
    name: str,
    identity_name: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """上传角色身份图片。"""
    logger.info("[%s] upload_identity_image: %s/%s", project, name, identity_name)
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):

        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        identities_dir = project_dir / "assets" / "characters" / name / "identities"
        img_path = identities_dir / f"{identity_name}.png"
        await run_asset_upload_operation(_characters._persist_uploaded_character_image, file, img_path)

        image_url = _characters._asset_url(ctx, project_dir, img_path)

        return {"ok": True, "data": {"image_url": image_url}}


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/image/delete"
)
async def delete_identity_image(
    project: str,
    name: str,
    identity_id: str,
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user) as (_ctx, _username, _project_name, _project_dir, _output_dir, store):
        deleted = await store.delete_identity_image(name, identity_id)
        return {"ok": True, "data": {"deleted": deleted}}


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/costume/upload"
)
async def upload_identity_costume(
    project: str,
    name: str,
    identity_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        lock_key = (
            "character-costume",
            str(project_dir.resolve()),
            name,
            identity_id,
        )
        async with asset_resource_lock(lock_key):
            character = store.get_character(name)
            if character is None:
                return {"ok": False, "error": f"Character '{name}' not found"}
            identity = _characters._identity_by_id(character, identity_id)
            if identity is None:
                return {"ok": False, "error": f"Identity '{identity_id}' not found"}

            safe_name = _characters._safe_asset_name(identity.identity_name)
            identities_dir = project_dir / "assets" / "characters" / name / "identities"
            target = identities_dir / f"{safe_name}_costume.png"

            async def update_costume_path(_result):
                await store.update_character_identity(
                    name, identity_id, costume_image=str(target)
                )

            await run_asset_upload_operation(
                _characters._persist_uploaded_character_image,
                file,
                target,
                finalize=update_costume_path,
            )
        return {
            "ok": True,
            "data": {"costume_image_url": _characters._asset_url(ctx, project_dir, target)},
        }


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/costume/delete"
)
async def delete_identity_costume(
    project: str,
    name: str,
    identity_id: str,
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        lock_key = (
            "character-costume",
            str(project_dir.resolve()),
            name,
            identity_id,
        )
        async with asset_resource_lock(lock_key):
            character = store.get_character(name)
            if character is None:
                return {"ok": False, "error": f"Character '{name}' not found"}
            identity = _characters._identity_by_id(character, identity_id)
            if identity is None:
                return {"ok": False, "error": f"Identity '{identity_id}' not found"}

            candidate_paths: list[Path] = []
            computed = compute_identity_costume_path(
                project_dir, name, identity.identity_name
            )
            if computed:
                candidate_paths.append(Path(computed))
            saved = str(getattr(identity, "costume_image", "") or "").strip()
            if saved:
                candidate_paths.append(Path(saved))

            deleted = False
            seen: set[Path] = set()
            for path in candidate_paths:
                if path in seen:
                    continue
                seen.add(path)
                if path.exists():
                    path.unlink()
                    deleted = True

            await store.update_character_identity(name, identity_id, costume_image="")
            if hasattr(identity, "costume_image"):
                setattr(identity, "costume_image", "")
        return {"ok": True, "data": {"deleted": deleted}}


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/portrait/upload"
)
async def upload_identity_portrait(
    project: str,
    name: str,
    identity_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}

        safe_name = _characters._safe_asset_name(identity.identity_name)
        identities_dir = project_dir / "assets" / "characters" / name / "identities"
        target = identities_dir / f"{name}_{safe_name}_portrait.png"

        async def update_portrait_path(_result):
            await store.update_character_identity(
                name, identity_id, portrait_image=str(target)
            )

        await run_asset_upload_operation(
            _characters._persist_uploaded_character_image,
            file,
            target,
            finalize=update_portrait_path,
        )
        return {
            "ok": True,
            "data": {"portrait_image_url": _characters._asset_url(ctx, project_dir, target)},
        }


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/portrait/generate-async"
)
async def generate_identity_portrait_async(
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
        scope = f"character:{name}:identity_portrait:{identity.identity_name}"
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
                    "mode": "identity_portrait",
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
                "message": f"身份 Portrait 生成任务已进入队列: {identity.identity_name}",
            }

        return {"ok": False, "error": "身份 Portrait 生成需要 project context"}


@router.post(
    "/projects/{project}/characters/{name}/identities/{identity_id}/portrait/generate"
)
async def generate_identity_portrait(
    project: str,
    name: str,
    identity_id: str,
    body: IdentityImageGenRequest = IdentityImageGenRequest(),
    user: dict = Depends(get_api_user),
):
    """同步生成身份级 portrait，供旧调用保留。新 UI 应优先使用 async。"""
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        identity = _characters._identity_by_id(character, identity_id)
        if identity is None:
            return {"ok": False, "error": f"Identity '{identity_id}' not found"}
        if not getattr(identity, "face_prompt", ""):
            return {"ok": False, "error": "该身份无 face_prompt，无需独立 Portrait"}

        from novelvideo.generators import generate_character_reference_unified

        config = _characters.load_project_config(username, project_name)
        safe_name = _characters._safe_asset_name(identity.identity_name)
        identities_dir = project_dir / "assets" / "characters" / name / "identities"
        identities_dir.mkdir(parents=True, exist_ok=True)
        target = identities_dir / f"{name}_{safe_name}_portrait.png"
        tmp_dir = identities_dir / f".tmp_identity_portrait_{datetime.now():%Y%m%d%H%M%S%f}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            paths = await generate_character_reference_unified(
                character_name=name,
                appearance_prompt=str(identity.face_prompt).strip(),
                output_dir=str(tmp_dir),
                count=1,
                use_mock=False,
                style=body.style or config.get("visual_style", "chinese_period_drama"),
                ethnicity=config.get("ethnicity", "Chinese"),
                model=_characters._resolve_character_image_model(username, project_name, body.model),
                project_dir=str(project_dir),
                usage_task_type="character_portrait",
                usage_scope=f"character:{name}:identity_portrait:{identity.identity_name}",
                identity_name=identity.identity_name,
            )
            if not paths:
                return {"ok": False, "error": "身份 Portrait 生成失败"}
            if target.exists():
                backup = (
                    identities_dir
                    / f"{name}_{safe_name}_portrait_{datetime.now():%Y%m%d%H%M%S}.png"
                )
                shutil.copy(target, backup)
            shutil.copy(paths[0], target)
            await store.update_character_identity(
                name, identity_id, portrait_image=str(target)
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return {
            "ok": True,
            "data": {"portrait_image_url": _characters._asset_url(ctx, project_dir, target)},
        }
