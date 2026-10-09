"""characters.voice_ops 子模块（自 api/routes/characters.py 拆分）。"""

from pathlib import Path
from urllib.parse import quote

from novelvideo.api.routes import characters as _characters
from novelvideo.project_context import ProjectContext
from novelvideo.seedance2_i2v.character_voice_storage import AGE_GROUP_SLOTS as VOICE_AGE_GROUP_SLOTS
from novelvideo.seedance2_i2v.character_voice_storage import DEFAULT_SLOT as VOICE_DEFAULT_SLOT
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.static_urls import project_static_url

from ._common import VOICE_SLOT_LABELS



def _voice_slot_metadata(character, slot: str) -> dict[str, str]:
    if slot == VOICE_DEFAULT_SLOT:
        return {
            "path": getattr(character, "reference_audio_path", "") or "",
            "sha256": getattr(character, "reference_audio_sha256", "") or "",
            "updated_at": getattr(character, "reference_audio_updated_at", "") or "",
        }

    samples = getattr(character, "voice_samples_by_age_group", None) or {}
    entry = samples.get(slot) if isinstance(samples, dict) else None
    if not isinstance(entry, dict):
        return {"path": "", "sha256": "", "updated_at": ""}
    return {
        "path": entry.get("path", "") or "",
        "sha256": entry.get("sha256", "") or "",
        "updated_at": entry.get("updated_at", "") or "",
    }


def _voice_slot_update_fields(
    character,
    slot: str,
    *,
    path: str,
    sha256: str,
    updated_at: str,
) -> dict:
    if slot == VOICE_DEFAULT_SLOT:
        return {
            "reference_audio_path": path,
            "reference_audio_sha256": sha256,
            "reference_audio_updated_at": updated_at,
        }

    samples = dict(getattr(character, "voice_samples_by_age_group", None) or {})
    if path:
        samples[slot] = {"path": path, "sha256": sha256, "updated_at": updated_at}
    else:
        samples.pop(slot, None)
    return {"voice_samples_by_age_group": samples}


def _voice_sample_url(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    rel_path: str,
) -> str:
    if not rel_path:
        return ""
    return _characters._asset_url(ctx, project_dir, project_dir / rel_path)


def _convention_asset_url(
    ctx: ProjectContext | None,
    project_dir: Path,
    path: str | Path,
    *,
    version: str = "",
    project_id: str = "",
) -> str:
    """Build a canonical asset URL without probing OSSFS.

    Asset slots are convention-based. List views can let the browser lazily
    request the URL and fall back on image error; only selected-asset detail
    needs authoritative file-existence checks.
    """

    asset_path = Path(path)
    try:
        rel_path = asset_path.relative_to(project_dir).as_posix()
    except ValueError:
        return ""
    asset_project = str(getattr(ctx, "project_id", "") or project_id).strip()
    url = project_static_url(asset_project, rel_path)
    return f"{url}?v={quote(version, safe='')}" if version else url


def _voice_slot_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    character,
    slot: str,
) -> dict:
    meta = _voice_slot_metadata(character, slot)
    default_meta = _voice_slot_metadata(character, VOICE_DEFAULT_SLOT)
    path = meta["path"]
    return {
        "slot": slot,
        "label": VOICE_SLOT_LABELS.get(slot, slot),
        "path": path,
        "url": _voice_sample_url(
            ctx=ctx,
            project_dir=project_dir,
            rel_path=path,
        ),
        "sha256": meta["sha256"],
        "updated_at": meta["updated_at"],
        "inherited_from_default": slot != VOICE_DEFAULT_SLOT
        and not path
        and bool(default_meta["path"]),
        "required": slot == VOICE_DEFAULT_SLOT,
    }


def _voice_samples_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    character,
) -> dict:
    return {
        "character": character.name,
        "slots": [
            _voice_slot_payload(
                ctx=ctx,
                project_dir=project_dir,
                character=character,
                slot=slot,
            )
            for slot in (VOICE_DEFAULT_SLOT, *VOICE_AGE_GROUP_SLOTS)
        ],
    }


def _character_voice_fields(
    ctx: ProjectContext,
    project_dir: Path,
    character,
    *,
    probe_files: bool = True,
) -> dict:
    rel_path = getattr(character, "reference_audio_path", "") or ""
    return {
        "reference_audio_path": rel_path,
        "reference_audio_url": (
            _voice_sample_url(ctx=ctx, project_dir=project_dir, rel_path=rel_path)
            if probe_files
            else _convention_asset_url(ctx, project_dir, project_dir / rel_path)
            if rel_path
            else ""
        ),
        "reference_audio_sha256": getattr(character, "reference_audio_sha256", "")
        or "",
        "reference_audio_updated_at": getattr(
            character, "reference_audio_updated_at", ""
        )
        or "",
        "voice_samples_by_age_group": getattr(
            character, "voice_samples_by_age_group", {}
        )
        or {},
    }


def _identity_voice_fields(ctx: ProjectContext, project_dir: Path, identity) -> dict:
    rel_path = getattr(identity, "reference_audio_path", "") or ""
    return {
        "reference_audio_path": rel_path,
        "reference_audio_url": _voice_sample_url(
            ctx=ctx,
            project_dir=project_dir,
            rel_path=rel_path,
        ),
        "reference_audio_sha256": getattr(identity, "reference_audio_sha256", "") or "",
        "reference_audio_updated_at": getattr(
            identity, "reference_audio_updated_at", ""
        )
        or "",
    }


async def _apply_character_voice_update(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    character,
    store: SQLiteStore,
    slot: str,
    path: str,
    sha256: str,
    updated_at: str,
) -> dict:
    fields = _voice_slot_update_fields(
        character,
        slot,
        path=path,
        sha256=sha256,
        updated_at=updated_at,
    )
    await store.update_character(character.name, **fields)
    for key, value in fields.items():
        setattr(character, key, value)
    return _voice_slot_payload(
        ctx=ctx,
        project_dir=project_dir,
        character=character,
        slot=slot,
    )
