"""characters.asset_routes 子模块（自 api/routes/characters.py 拆分）。"""

import shutil

from fastapi import APIRouter
from fastapi import Depends

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import characters as _characters
from novelvideo.api.schemas import CharacterAssetRestoreRequest

router = APIRouter()


@router.get("/projects/{project}/characters/{name}/asset-history")
async def list_character_asset_history(
    project: str,
    name: str,
    kind: str,
    identity_id: str = "",
    user: dict = Depends(get_api_user),
):
    """列出角色资产的历史备份，用于 UI 回看和恢复。"""
    async with _characters._character_project_scope(project, user, required_role="viewer") as (ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}
        try:
            target, _identity = _characters._resolve_character_asset_path(
                project_dir=project_dir,
                character=character,
                kind=kind,
                identity_id=identity_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "data": {
                "kind": kind,
                "identity_id": identity_id,
                "current_url": _characters._asset_url(ctx, project_dir, target),
                "entries": _characters._character_asset_history_entries(
                    ctx=ctx,
                    project_dir=project_dir,
                    target=target,
                ),
            },
        }


@router.post("/projects/{project}/characters/{name}/asset-history/restore")
async def restore_character_asset_history(
    project: str,
    name: str,
    body: CharacterAssetRestoreRequest,
    user: dict = Depends(get_api_user),
):
    """把某个历史备份恢复到角色资产 canonical 槽位。"""
    async with _characters._character_project_scope(project, user) as (ctx, _username, _project_name, project_dir, _output_dir, store):
        character = store.get_character(name)
        if character is None:
            return {"ok": False, "error": f"Character '{name}' not found"}

        kind = str(getattr(body, "kind", "") or "").strip()
        identity_id = str(getattr(body, "identity_id", "") or "").strip()
        history_id = str(getattr(body, "history_id", "") or "").strip()
        try:
            target, identity = _characters._resolve_character_asset_path(
                project_dir=project_dir,
                character=character,
                kind=kind,
                identity_id=identity_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        entries = _characters._character_asset_history_entries(
            ctx=ctx, project_dir=project_dir, target=target
        )
        allowed_ids = {str(entry.get("history_id") or "") for entry in entries}
        if history_id not in allowed_ids:
            return {"ok": False, "error": "History asset not found"}

        try:
            source = _characters._character_asset_history_path(target, history_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not source.exists() or not source.is_file():
            return {"ok": False, "error": "History asset not found"}

        target.parent.mkdir(parents=True, exist_ok=True)
        backup = _characters._backup_character_asset(target)
        shutil.copy2(source, target)
        await _characters._sync_restored_identity_asset(store, name, identity, kind, target)
        if kind == "portrait":
            await store.touch_character_asset(name)

        return {
            "ok": True,
            "data": {
                "kind": kind,
                "identity_id": identity_id,
                "restored": True,
                "url": _characters._asset_url(ctx, project_dir, target),
                "backup_history_id": backup.name if backup else "",
            },
        }
