"""characters.voice_routes 子模块（自 api/routes/characters.py 拆分）。"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import CharacterVoiceRecordRequest
from novelvideo.api.schemas import CharacterVoiceTrimRequest
from novelvideo.seedance2_i2v.character_voice_storage import ALL_SLOTS as VOICE_SAMPLE_SLOTS
from novelvideo.seedance2_i2v.character_voice_storage import character_voice_resource_key
from novelvideo.seedance2_i2v.character_voice_storage import run_voice_media_operation
from novelvideo.seedance2_i2v.character_voice_storage import voice_resource_lock
from novelvideo.utils.async_ops import metadata_io_limiter

router = APIRouter()


@router.get("/projects/{project}/characters/{name}/voice-samples")
async def list_character_voice_samples(
    project: str,
    name: str,
    user: dict = Depends(get_api_user),
):
    """获取角色 IndexTTS2 声线样本插槽。"""
    async with _characters._character_project_scope(project, user, required_role="viewer") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        return {
            "ok": True,
            "data": _characters._voice_samples_payload(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
            ),
        }


@router.post("/projects/{project}/characters/{name}/voice-samples/{slot}/upload")
async def upload_character_voice_sample(
    project: str,
    name: str,
    slot: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """上传角色 IndexTTS2 声线样本。"""
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        if slot not in VOICE_SAMPLE_SLOTS:
            return {"ok": False, "error": f"Unsupported voice slot: {slot}"}

        filename = file.filename or ""

        async def finalize_voice_update(result):
            rel_path, sha256, updated_at = result
            return await _characters._apply_character_voice_update(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
                store=store,
                slot=slot,
                path=rel_path,
                sha256=sha256,
                updated_at=updated_at,
            )

        try:
            key = character_voice_resource_key(
                project_dir=project_dir, character_name=name, slot=slot
            )
            async with voice_resource_lock(key):
                data = await run_voice_media_operation(
                    _characters._persist_uploaded_character_voice,
                    file,
                    project_dir=project_dir,
                    character_name=name,
                    slot=slot,
                    filename=filename,
                    finalize=finalize_voice_update,
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "data": data}


@router.post("/projects/{project}/characters/{name}/voice-samples/{slot}/record")
async def record_character_voice_sample(
    project: str,
    name: str,
    slot: str,
    body: CharacterVoiceRecordRequest,
    user: dict = Depends(get_api_user),
):
    """保存浏览器录音为角色 IndexTTS2 声线样本。"""
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        if slot not in VOICE_SAMPLE_SLOTS:
            return {"ok": False, "error": f"Unsupported voice slot: {slot}"}

        async def finalize_voice_update(result):
            rel_path, sha256, updated_at = result
            return await _characters._apply_character_voice_update(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
                store=store,
                slot=slot,
                path=rel_path,
                sha256=sha256,
                updated_at=updated_at,
            )

        try:
            key = character_voice_resource_key(
                project_dir=project_dir, character_name=name, slot=slot
            )
            async with voice_resource_lock(key):
                data = await run_voice_media_operation(
                    _characters._persist_recorded_character_voice,
                    body.data_url,
                    project_dir=project_dir,
                    character_name=name,
                    slot=slot,
                    finalize=finalize_voice_update,
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "data": data}


@router.post("/projects/{project}/characters/{name}/voice-samples/{slot}/trim")
async def trim_character_voice_sample(
    project: str,
    name: str,
    slot: str,
    body: CharacterVoiceTrimRequest,
    user: dict = Depends(get_api_user),
):
    """裁剪角色 IndexTTS2 声线样本并写回同一插槽。"""
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        if slot not in VOICE_SAMPLE_SLOTS:
            return {"ok": False, "error": f"Unsupported voice slot: {slot}"}

        async def finalize_voice_update(result):
            rel_path, sha256, updated_at = result
            return await _characters._apply_character_voice_update(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
                store=store,
                slot=slot,
                path=rel_path,
                sha256=sha256,
                updated_at=updated_at,
            )

        try:
            key = character_voice_resource_key(
                project_dir=project_dir, character_name=name, slot=slot
            )
            async with voice_resource_lock(key):
                data = await run_voice_media_operation(
                    _characters.trim_existing_character_voice_file,
                    project_dir=project_dir,
                    character_name=name,
                    slot=slot,
                    source_path=body.source_path,
                    start_seconds=body.start_seconds,
                    duration_seconds=body.duration_seconds,
                    finalize=finalize_voice_update,
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "data": data}


@router.post("/projects/{project}/characters/{name}/voice-samples/{slot}/delete")
async def delete_character_voice_sample(
    project: str,
    name: str,
    slot: str,
    user: dict = Depends(get_api_user),
):
    """清除角色 IndexTTS2 声线样本。"""
    async with _characters._character_project_scope(project, user) as (ctx, username, project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        if slot not in VOICE_SAMPLE_SLOTS:
            return {"ok": False, "error": f"Unsupported voice slot: {slot}"}

        async def finalize_voice_delete(_removed):
            return await _characters._apply_character_voice_update(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
                store=store,
                slot=slot,
                path="",
                sha256="",
                updated_at="",
            )

        key = character_voice_resource_key(
            project_dir=project_dir, character_name=name, slot=slot
        )
        async with voice_resource_lock(key):
            data = await run_voice_media_operation(
                _characters.clear_character_voice_file,
                project_dir=project_dir,
                character_name=name,
                slot=slot,
                finalize=finalize_voice_delete,
                worker_limiter=metadata_io_limiter(),
            )
        return {"ok": True, "data": data}
