"""generation.seedance2 子模块（自 api/routes/generation.py 拆分）。"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import Seedance2AssetAudioTrimRequest
from novelvideo.api.schemas import Seedance2AssetCropRequest
from novelvideo.api.schemas import Seedance2AssetDeleteRequest
from novelvideo.project_context import ProjectContext
from novelvideo.seedance2_i2v.voice_clone import normalize_seedance2_audio_type
from novelvideo.utils.source_language import detect_episode_asset_language

router = APIRouter()


def _seedance2_asset_status_payload(
    asset: Any,
    *,
    project_ctx: ProjectContext,
    output_dir: Path,
) -> dict[str, Any]:
    try:
        rel_path = str(Path(asset.path).relative_to(output_dir))
    except ValueError:
        rel_path = str(asset.path)
    abs_path = str(asset.path)
    crop_source_path = getattr(asset, "crop_source_path", None)
    crop_source_abs_path = str(crop_source_path) if crop_source_path else ""
    crop_source_rel_path = ""
    if crop_source_path:
        try:
            crop_source_rel_path = str(Path(crop_source_path).relative_to(output_dir))
        except ValueError:
            crop_source_rel_path = crop_source_abs_path
    media_url = ""
    if bool(asset.exists):
        media_url = _generation.make_static_url_for_context(project_ctx, rel_path, local_path=Path(asset.path))
    crop_source_url = ""
    if crop_source_path and Path(crop_source_path).exists():
        crop_source_url = _generation.make_static_url_for_context(
            project_ctx,
            crop_source_rel_path,
            local_path=Path(crop_source_path),
        )
    can_delete = (
        str(asset.key).startswith(("user_image:", "user_audio:"))
        or "seedance2_uploads" in Path(abs_path).parts
        or "seedance2_crops" in Path(abs_path).parts
    )
    return {
        "key": str(asset.key),
        "label": str(asset.label),
        "media_type": str(asset.media_type),
        "selected": bool(asset.selected),
        "exists": bool(asset.exists),
        "reference_label": str(asset.reference_label),
        "note": str(asset.note or asset.validation_error or ""),
        "identity_id": str(getattr(asset, "identity_id", "") or ""),
        "prop_id": str(getattr(asset, "prop_id", "") or ""),
        "prop_scope": str(getattr(asset, "prop_scope", "") or ""),
        "path": rel_path,
        "url": media_url,
        "abs_path": abs_path,
        "crop_source_path": crop_source_rel_path,
        "crop_source_abs_path": crop_source_abs_path,
        "crop_source_url": crop_source_url,
        "validation_error": str(asset.validation_error or ""),
        "fallback_text": str(asset.fallback_text or ""),
        "can_crop": bool(asset.exists and asset.media_type == "image"),
        "can_trim": bool(asset.exists and asset.media_type == "audio"),
        "can_delete": can_delete,
    }


def _seedance2_returned_last_frame_status_payload(
    *,
    project_ctx: ProjectContext,
    output_dir: Path,
    episode: int,
    beat_num: int,
    enabled: bool,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    base_path = (
        Path(output_dir)
        / "videos"
        / "beats"
        / f"ep{int(episode):03d}"
        / "returned_last_frames"
        / f"beat_{int(beat_num):02d}"
    )
    path = next(
        (
            base_path.with_suffix(suffix)
            for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif")
            if base_path.with_suffix(suffix).exists()
        ),
        base_path.with_suffix(".png"),
    )
    if not path.exists():
        return None
    try:
        rel_path = path.relative_to(output_dir).as_posix()
    except ValueError:
        rel_path = str(path)
    return {
        "key": "returned_last_frame",
        "label": f"返回尾帧 · Beat {int(beat_num)}",
        "media_type": "image",
        "selected": False,
        "exists": True,
        "reference_label": "尾帧",
        "note": "Seedance2 返回尾帧",
        "identity_id": "",
        "prop_id": "",
        "prop_scope": "",
        "path": rel_path,
        "url": _generation.make_static_url_for_context(project_ctx, rel_path, local_path=path),
        "abs_path": str(path),
        "validation_error": "",
        "fallback_text": "",
        "can_crop": False,
        "can_delete": False,
    }


def _seedance2_voice_status_payload(
    *,
    beat: dict[str, Any],
    characters: list[Any],
    username: str,
    project: str,
    store: Any,
    output_dir: Path,
) -> dict[str, Any]:
    audio_type = normalize_seedance2_audio_type(beat)
    if audio_type == "silence":
        return {
            "required": False,
            "ready": True,
            "label": "无音频",
            "detail": "静音 Beat 不生成音频",
            "speaker": "",
        }
    if audio_type == "dialogue":
        from novelvideo.seedance2_i2v.voice_reference_service import (
            dialogue_voice_reference_rows,
        )

        rows = dialogue_voice_reference_rows(
            beat,
            characters=characters,
            project_dir=output_dir,
        )
        ready_rows = [row for row in rows if row.status.active_reference_path]
        names = [row.display_name or row.speaker for row in rows]
        ready = bool(rows) and len(ready_rows) == len(rows)
        return {
            "required": True,
            "ready": ready,
            "label": "声线就绪" if ready else "声线缺失",
            "detail": "、".join(names) if names else "未指定 speaker",
            "speaker": str(beat.get("speaker") or ""),
        }

    from novelvideo.seedance2_i2v.voice_reference_service import (
        resolve_narrator_reference_status,
    )

    status = resolve_narrator_reference_status(
        store=store,
        username=username,
        project=project,
    )
    return {
        "required": True,
        "ready": bool(status.active_reference_path),
        "label": "声线就绪" if status.active_reference_path else "声线缺失",
        "detail": str(status.detail or status.error or "第三人称项目解说声线未配置"),
        "speaker": "NARRATOR",
    }


async def _seedance2_panel_context(
    *,
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    output_dir = Path(resolved.output_dir)
    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)
        beat = next((item for item in beats if int(item.get("beat_number") or 0) == beat_num), None)
        if not beat:
            raise HTTPException(status_code=404, detail=f"Beat {beat_num} not found")

        next_beat = next(
            (item for item in beats if int(item.get("beat_number") or 0) == beat_num + 1),
            None,
        )
        characters = store.get_all_characters()
        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        language = await detect_episode_asset_language(
            store,
            episode_num,
            fallback_text="\n".join(
                str(item.get(field) or "")
                for item in beats
                for field in ("narration_segment", "dialogue", "visual_description")
            ),
        )
        return {
            "project_ctx": resolved.ctx,
            "username": username,
            "project_name": project_name,
            "output_dir": output_dir,
            "store": store,
            "beats": beats,
            "beat": beat,
            "next_beat": next_beat,
            "characters": characters,
            "prop_menu": prop_menu,
            "language": language,
        }


def _seedance2_status_response(
    *,
    project: str,
    episode_num: int,
    beat_num: int,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    from novelvideo.seedance2_i2v.panel_service import (
        build_seedance2_video_panel_state,
    )
    from novelvideo.utils.path_resolver import PathResolver

    output_dir = Path(ctx["output_dir"])
    beat = ctx["beat"]
    state = build_seedance2_video_panel_state(
        project_dir=output_dir,
        episode=episode_num,
        beat=beat,
        next_beat=ctx["next_beat"],
        characters=ctx["characters"],
        prop_menu=ctx["prop_menu"],
        state_dir=Path(ctx["store"].state_dir),
        language=ctx.get("language"),
    )
    assets = state.assets
    selected_assets = [asset for asset in assets if asset.selected]
    missing_assets = [
        asset
        for asset in assets
        if asset.required and (not asset.exists or bool(asset.validation_error))
    ]
    fallback_assets = [
        asset for asset in assets if str(asset.fallback_text or "").strip() and not asset.selected
    ]
    paths = PathResolver(output_dir, episode_num)
    project_ctx = ctx["project_ctx"]
    asset_items = [
        _seedance2_asset_status_payload(asset, project_ctx=project_ctx, output_dir=output_dir)
        for asset in assets
    ]
    try:
        from novelvideo.seedance2_i2v.models import parse_seedance2_config

        config = parse_seedance2_config(beat.get("seedance2_config_json") or "{}")
        returned_last_frame = _seedance2_returned_last_frame_status_payload(
            project_ctx=project_ctx,
            output_dir=output_dir,
            episode=episode_num,
            beat_num=beat_num,
            enabled=bool(config.return_last_frame),
        )
    except Exception:
        returned_last_frame = None
    if returned_last_frame is not None:
        asset_items.append(returned_last_frame)

    return {
        "ok": True,
        "data": {
            "beat_number": beat_num,
            "audio_type": normalize_seedance2_audio_type(beat),
            "seedance2_config_json": str(beat.get("seedance2_config_json") or ""),
            "media": {
                "render_ready": paths.frame(beat_num).exists(),
                "audio_ready": paths.audio(beat_num).exists(),
                "video_ready": paths.video(beat_num).exists(),
            },
            "voice": _seedance2_voice_status_payload(
                beat=beat,
                characters=ctx["characters"],
                username=ctx["username"],
                project=project,
                store=ctx["store"],
                output_dir=output_dir,
            ),
            "prompt": {
                "ready": bool(str(state.final_prompt or "").strip()),
                "source": str(state.prompt_source or ""),
                "status": str(state.prompt_status or ""),
                "has_guidance": bool(str(state.prompt_guidance or "").strip()),
                "text_overlay_enabled": bool((state.text_overlay or {}).get("enabled")),
                "text_overlay": state.text_overlay or {},
                "inputs_stale": bool(
                    state.prompt_inputs_hash
                    and state.prompt_inputs_hash != state.current_prompt_inputs_hash
                ),
            },
            "assets": {
                "total": len(assets),
                "selected": len(selected_assets),
                "missing": len(missing_assets),
                "images": len([asset for asset in selected_assets if asset.media_type == "image"]),
                "audios": len([asset for asset in selected_assets if asset.media_type == "audio"]),
                "fallbacks": len(fallback_assets),
                "items": asset_items,
            },
        },
    }


@router.get("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2-status")
async def get_seedance2_beat_status(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-aligned read-only Seedance 2.0 status for one Beat."""
    ctx = await _generation._seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    return _generation._seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/upload")
async def upload_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a manual Seedance 2.0 reference asset."""
    ctx = await _generation._seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        save_seedance2_uploaded_file,
    )

    target = await save_seedance2_uploaded_file(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        project_dir=ctx["output_dir"],
        filename=file.filename or "seedance2_asset",
        upload_stream=file.file,
        content_type=file.content_type or "",
    )
    if target is None:
        return {"ok": False, "error": "unsupported or empty Seedance2 reference asset"}
    return _generation._seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/delete")
async def delete_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetDeleteRequest,
    user: dict = Depends(get_api_user),
):
    """Remove a manually attached Seedance 2.0 reference asset."""
    ctx = await _generation._seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        remove_seedance2_uploaded_asset,
    )

    removed = await remove_seedance2_uploaded_asset(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        project_dir=ctx["output_dir"],
        media_kind=body.media_kind,
        path=body.path,
    )
    if not removed:
        return {"ok": False, "error": "Seedance2 reference asset was not removed"}
    return _generation._seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/crop")
async def crop_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetCropRequest,
    user: dict = Depends(get_api_user),
):
    """Crop an existing Seedance 2.0 image reference into a manual reference."""
    ctx = await _generation._seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        crop_seedance2_asset_to_reference,
    )

    target = await crop_seedance2_asset_to_reference(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        project_dir=ctx["output_dir"],
        asset_key=body.asset_key,
        source_path=body.source_path,
        crop_data=body.model_dump(),
    )
    if target is None:
        return {"ok": False, "error": "Seedance2 reference crop failed"}
    return _generation._seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/audio-trim")
async def trim_seedance2_audio_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetAudioTrimRequest,
    user: dict = Depends(get_api_user),
):
    """Trim an existing Seedance 2.0 audio reference into a 3-5 second clip."""
    ctx = await _generation._seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        trim_seedance2_audio_to_reference,
    )

    try:
        target = await trim_seedance2_audio_to_reference(
            store=ctx["store"],
            episode=episode_num,
            beat=ctx["beat"],
            project_dir=ctx["output_dir"],
            asset_key=body.asset_key,
            source_path=body.source_path,
            start_seconds=body.start_seconds,
            duration_seconds=body.duration_seconds,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if target is None:
        return {"ok": False, "error": "Seedance2 audio reference trim failed"}
    return _generation._seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )
