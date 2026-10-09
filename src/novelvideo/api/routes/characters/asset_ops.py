"""characters.asset_ops 子模块（自 api/routes/characters.py 拆分）。"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlencode

from fastapi import UploadFile

from novelvideo.api.routes import characters as _characters
from novelvideo.project_context import ProjectContext
from novelvideo.utils.path_resolver import canonical_identity_costume_path
from novelvideo.utils.path_resolver import canonical_identity_path
from novelvideo.utils.path_resolver import canonical_identity_portrait_path
from novelvideo.utils.path_resolver import canonical_portrait_path
from novelvideo.utils.upload_safety import create_staged_upload_file

from ._common import CHARACTER_ASSET_KINDS



def _safe_asset_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", str(name or "").strip()) or "untitled"


def _identity_by_id(character, identity_id: str):
    for identity in character.identities or []:
        if identity.identity_id == identity_id:
            return identity
    return None


def _asset_url(ctx: ProjectContext, project_dir: Path, abs_path: str | Path) -> str:
    path = Path(abs_path)
    if not path.exists():
        return ""
    try:
        rel_path = path.relative_to(project_dir).as_posix()
    except ValueError:
        return ""
    return _characters.make_static_url_for_context(ctx, rel_path, local_path=path)


def _backup_character_asset(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup = path.with_name(f"{path.stem}_{ts}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def _persist_uploaded_character_image(file: UploadFile, target: Path) -> None:
    """Decode and atomically publish one uploaded character image."""

    from PIL import Image

    tmp_path: Path | None = None
    converted = None
    try:
        try:
            file.file.seek(0)
        except (AttributeError, OSError):
            pass
        with Image.open(file.file) as source:
            converted = source.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = create_staged_upload_file(
            target.parent,
            prefix=f".{target.stem}_",
            suffix=target.suffix,
            destination=target,
        )
        converted.save(tmp_path, format="PNG")
        _backup_character_asset(target)
        tmp_path.replace(target)
        tmp_path = None
    finally:
        if converted is not None:
            converted.close()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _persist_uploaded_character_voice(
    file: UploadFile,
    *,
    project_dir: Path,
    character_name: str,
    slot: str,
    filename: str,
) -> tuple[str, str, str]:
    try:
        file.file.seek(0)
    except (AttributeError, OSError):
        pass
    content = file.file.read()
    return _characters.persist_character_voice_file(
        project_dir=project_dir,
        character_name=character_name,
        slot=slot,
        filename=filename,
        content=content,
    )


def _persist_recorded_character_voice(
    data_url: str,
    *,
    project_dir: Path,
    character_name: str,
    slot: str,
) -> tuple[str, str, str]:
    content, extension = _characters.decode_recorded_audio_data_url(data_url)
    return _characters.persist_character_voice_file(
        project_dir=project_dir,
        character_name=character_name,
        slot=slot,
        filename=f"recorded{extension}",
        content=content,
    )


def _resolve_character_asset_path(
    *,
    project_dir: Path,
    character,
    kind: str,
    identity_id: str = "",
) -> tuple[Path, object | None]:
    if kind not in CHARACTER_ASSET_KINDS:
        raise ValueError(f"Unsupported character asset kind: {kind}")
    if kind == "portrait":
        return canonical_portrait_path(project_dir, character.name), None

    identity = _identity_by_id(character, identity_id)
    if identity is None:
        raise ValueError(f"Identity '{identity_id}' not found")
    identity_name = getattr(identity, "identity_name", "") or identity_id
    if kind == "identity":
        return (
            canonical_identity_path(project_dir, character.name, identity_name),
            identity,
        )
    if kind == "identity_costume":
        return (
            canonical_identity_costume_path(project_dir, character.name, identity_name),
            identity,
        )
    return (
        canonical_identity_portrait_path(project_dir, character.name, identity_name),
        identity,
    )


def _history_id_for_path(target: Path, path: Path) -> str:
    history_dir = target.parent / "_history"
    try:
        rel = path.relative_to(history_dir)
    except ValueError:
        return path.name
    return f"_history/{rel.as_posix()}"


def _character_asset_history_entries(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    target: Path,
) -> list[dict]:
    entries: list[dict] = []
    if target.parent.exists():
        timestamped = re.compile(
            rf"^{re.escape(target.stem)}_(?P<stamp>\d{{14,20}}){re.escape(target.suffix)}$"
        )
        for path in target.parent.glob(f"{target.stem}_*{target.suffix}"):
            if path.is_file() and timestamped.match(path.name):
                stat = path.stat()
                entries.append(
                    {
                        "history_id": _history_id_for_path(target, path),
                        "filename": path.name,
                        "url": _characters._asset_url(ctx, project_dir, path),
                        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "bytes": stat.st_size,
                    }
                )

    history_dir = target.parent / "_history"
    if history_dir.exists():
        for path in history_dir.glob(f"{target.name}.*.bak"):
            if not path.is_file():
                continue
            stat = path.stat()
            entries.append(
                {
                    "history_id": _history_id_for_path(target, path),
                    "filename": path.name,
                    "url": _characters._asset_url(ctx, project_dir, path),
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "bytes": stat.st_size,
                }
            )

    entries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return entries


def _character_asset_history_path(target: Path, history_id: str) -> Path:
    raw = str(history_id or "").strip()
    if not raw:
        raise ValueError("history_id is required")
    if raw.startswith("_history/"):
        name = raw.removeprefix("_history/")
        if "/" in name or "\\" in name:
            raise ValueError("invalid history_id")
        return target.parent / "_history" / name
    if "/" in raw or "\\" in raw:
        raise ValueError("invalid history_id")
    return target.parent / raw


async def _sync_restored_identity_asset(
    store, character_name: str, identity, kind: str, target: Path
):
    if identity is None:
        return
    identity_id = getattr(identity, "identity_id", "")
    if kind == "identity_costume":
        await store.update_character_identity(
            character_name, identity_id, costume_image=str(target)
        )
    elif kind == "identity_portrait":
        await store.update_character_identity(
            character_name, identity_id, portrait_image=str(target)
        )


def _character_asset_links(
    *,
    project: str,
    character_name: str,
    kind: str,
    identity_id: str = "",
) -> dict[str, str]:
    query = {"kind": kind}
    if identity_id:
        query["identity_id"] = identity_id
    base = f"/api/v1/projects/{quote(project, safe='')}/characters/{quote(character_name, safe='')}"
    return {
        "history_url": f"{base}/asset-history?{urlencode(query)}",
        "restore_url": f"{base}/asset-history/restore",
    }
