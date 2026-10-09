"""generation.media_exports 子模块（自 api/routes/generation.py 拆分）。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import GridCutRequest
from novelvideo.api.schemas import GridSketchPreviewRequest
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.task_identity import project_task_state_key

router = APIRouter()


# ── 单 Beat 音频重生 ─────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/audio")
async def regenerate_beat_audio(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """重新生成单个 beat 的 IndexTTS2 语音。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    state_dir = resolved.state_dir
    async with _generation.generation_store_scope(ctx, username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        beat = next((b for b in beats if b.get("beat_number") == beat_num), None)
        if not beat:
            # 按索引回退
            if 1 <= beat_num <= len(beats):
                beat = beats[beat_num - 1]
            else:
                return {"ok": False, "error": f"Beat {beat_num} not found"}

        pending_beat_numbers, missing_voice = await _generation._audio_generation_plan(
            store=store,
            username=username,
            project=project_name,
            episode=episode_num,
            beat_numbers=[beat_num],
            mode="redo_selected",
        )
        if missing_voice:
            return _generation._voice_prereq_error_response(missing_voice)
        if not pending_beat_numbers:
            return {
                "ok": False,
                "code": "audio_generation_not_required",
                "error": "当前 Beat 没有需要生成的音频",
            }

        if ctx is not None:
            queued = await _generation.get_task_backend().enqueue_project_task(
                ctx,
                product_surface="mainline",
                task_type="audio_generation_indextts2",
                queue_kind="default",
                episode=episode_num,
                payload={
                    "episode": episode_num,
                    "mode": "redo_selected",
                    "beat_numbers": pending_beat_numbers,
                    "output_dir": output_dir,
                    "state_dir": state_dir,
                },
            )
            return {
                "ok": True,
                "task_type": "audio_generation_indextts2",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "audio_generation_indextts2", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集 Beat {beat_num} 语音生成已进入队列",
            }

        return {
            "ok": False,
            "error": "音频生成需要 project context",
        }


# ── SRT 字幕导出 ─────────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/export/srt")
async def export_srt(project: str, episode_num: int, user: dict = Depends(get_api_user)):
    """导出 SRT 字幕文件。"""
    from fastapi.responses import PlainTextResponse

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir

    # 从图谱读取 beats
    async with _generation.generation_store_scope(resolved.ctx, resolved.username, resolved.project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": "No beats in script"}

        from novelvideo.export.episode_export import build_srt_content

        srt_content = await build_srt_content(project_dir, episode_num, beats)
        if not srt_content:
            return {"ok": False, "error": "No subtitles to export"}

        return PlainTextResponse(
            content=srt_content,
            media_type="text/srt",
            headers={
                "Content-Disposition": f'attachment; filename="ep{episode_num:03d}.srt"',
            },
        )


@router.get("/projects/{project}/episodes/{episode_num}/export/video")
async def export_final_video(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Download the composed final episode video."""
    from fastapi.responses import FileResponse

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    filename = f"ep{episode_num:03d}_final.mp4"
    final_path = project_dir / "videos" / "episodes" / filename
    if not final_path.exists():
        raise HTTPException(status_code=404, detail="Final video not found")
    return FileResponse(
        path=str(final_path),
        filename=filename,
        media_type="video/mp4",
    )


# ── 网格上传 / Prompt 导出 / 切割 ─────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/upload")
async def upload_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    file: UploadFile = File(...),
    grid_type: str = Form("render"),
    mode_key: str = Form(""),
    beat_numbers: str = Form(""),
    user: dict = Depends(get_api_user),
):
    """上传单张网格整图并更新 pool index 中同 scope 的 grid_path。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    grid_type = grid_type.strip() or "render"
    if grid_type not in {"render", "sketch"}:
        return {"ok": False, "error": "grid_type must be render or sketch"}
    try:
        parsed_beats = _generation._parse_grid_beat_numbers(beat_numbers)
    except Exception as exc:
        return {"ok": False, "error": f"invalid beat_numbers: {exc}"}
    mode_key = mode_key.strip() or "upload"

    content = await file.read()
    if not content:
        return {"ok": False, "error": "uploaded file is empty"}

    suffix = Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in {"png", "jpg", "jpeg", "webp"}:
        suffix = "png"
    if suffix == "jpeg":
        suffix = "jpg"

    from novelvideo.api.upload_workers import (
        asset_resource_lock,
        run_asset_upload_operation,
    )
    from novelvideo.generators.pool_indexer import persist_uploaded_grid

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    filename = _generation._uploaded_grid_filename(grid_type, mode_key, parsed_beats, suffix)
    # All scopes in this episode share the same index. Keep the async lock until
    # the worker has committed or rolled back, including repeated cancellation.
    async with asset_resource_lock(("grid-upload", str(grids_dir))):
        grid_path = await run_asset_upload_operation(
            persist_uploaded_grid,
            grids_dir,
            episode_num,
            grid_index,
            grid_type,
            mode_key,
            parsed_beats,
            filename,
            content,
        )
    grid_rel = grid_path.relative_to(grids_dir).as_posix()

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "grid_type": grid_type,
            "mode_key": mode_key,
            "beat_numbers": parsed_beats,
            "grid_path": grid_rel,
            "grid_url": _generation.make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{grid_rel}",
                local_path=grid_path,
            ),
        },
    }


@router.get("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/prompt")
async def export_grid_prompt(
    project: str,
    episode_num: int,
    grid_index: int,
    grid_type: str = Query("render"),
    mode_key: str = Query(""),
    beat_numbers: str = Query(""),
    user: dict = Depends(get_api_user),
):
    """读取 pool index 中记录的单张网格 prompt 文本。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    grid_type = grid_type.strip() or "render"
    if grid_type not in {"render", "sketch"}:
        return {"ok": False, "error": "grid_type must be render or sketch"}
    try:
        parsed_beats = _generation._parse_grid_beat_numbers(beat_numbers)
    except Exception as exc:
        return {"ok": False, "error": f"invalid beat_numbers: {exc}"}
    mode_key = mode_key.strip()

    from novelvideo.generators.pool_indexer import load_pool_index

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = await run_asset_upload_operation(load_pool_index, grids_dir)
    if not pool:
        return {"ok": False, "error": "No pool index found. Generate grids first."}

    entry = _generation._find_pool_grid_entry(
        pool,
        grid_type=grid_type,
        mode_key=mode_key or None,
        beat_numbers=parsed_beats,
        grid_index=grid_index,
    )
    if entry is None:
        return {"ok": False, "error": "Grid prompt metadata not found"}

    prompt_candidates: list[str] = []
    if entry.prompt_path:
        prompt_candidates.append(entry.prompt_path)
    if parsed_beats and entry.mode_key:
        beats_slug = "-".join(str(beat) for beat in parsed_beats)
        prompt_candidates.append(
            f"{entry.preset}/{grid_type}_{entry.mode_key}_{beats_slug}_prompt.txt"
        )

    for relative in prompt_candidates:
        prompt_path = _generation._safe_grids_file(grids_dir, relative)
        if prompt_path and prompt_path.exists():
            return {
                "ok": True,
                "data": {
                    "grid_index": grid_index,
                    "grid_type": grid_type,
                    "mode_key": entry.mode_key,
                    "beat_numbers": list(entry.beat_nums),
                    "prompt": prompt_path.read_text(encoding="utf-8"),
                    "prompt_path": prompt_path.relative_to(grids_dir).as_posix(),
                },
            }

    return {"ok": False, "error": "Prompt file not found for this grid"}


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/sketch-preview")
async def sketch_grid_preview(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridSketchPreviewRequest,
    user: dict = Depends(get_api_user),
):
    """Return the same sketch-thumbnail preview NiceGUI shows for planned grids.

    This API exposes NiceGUI's `_get_sketch_thumbnail_url` behavior to React:
    it stitches existing beat sketches into a temporary preview image without
    changing the generation pipeline.
    """
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    output_dir = Path(resolved.output_dir)
    ep_grids_dir = output_dir / "grids" / f"ep{episode_num:03d}"

    from novelvideo.generators.grid_generation import crop_sketch_panels
    from novelvideo.generators.pool_indexer import build_beat_sketch_paths, load_pool_index

    beat_numbers = [int(beat) for beat in body.beat_numbers if int(beat) > 0]
    if not beat_numbers:
        return {"ok": False, "error": "beat_numbers is required"}

    paths = build_beat_sketch_paths(ep_grids_dir, beat_numbers)
    pool = await run_asset_upload_operation(load_pool_index, ep_grids_dir)
    if pool:
        latest_pool_paths: dict[int, tuple[float, str]] = {}
        for img in pool.images:
            if img.type != "sketch" or not img.cell_path:
                continue
            beat_num = int(img.original_beat)
            if beat_num not in beat_numbers:
                continue
            cell_path = ep_grids_dir / img.cell_path
            if not cell_path.exists():
                continue
            generated_at = img.generated_at.timestamp() if img.generated_at else 0.0
            current = latest_pool_paths.get(beat_num)
            if current is None or generated_at > current[0]:
                latest_pool_paths[beat_num] = (generated_at, str(cell_path))
        paths = {
            **{beat: path for beat, (_generated_at, path) in latest_pool_paths.items()},
            **paths,
        }
    if not paths:
        return {"ok": False, "error": "No sketch images found for requested beats"}

    beats_slug = "_".join(str(beat) for beat in beat_numbers[:8])
    out_file = (
        ep_grids_dir / f"sketch_thumb_grid{grid_index}_{beats_slug}_{body.rows}x{body.cols}.jpg"
    )
    sketch_out = Path(
        crop_sketch_panels(
            str(ep_grids_dir),
            beat_numbers,
            body.rows,
            body.cols,
            str(out_file),
            beat_sketch_paths=paths,
        )
    )
    try:
        rel = sketch_out.relative_to(ep_grids_dir)
    except ValueError:
        return {"ok": False, "error": "Sketch preview path escaped episode grids directory"}

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "rows": body.rows,
            "cols": body.cols,
            "beat_numbers": beat_numbers,
            "preview_path": str(rel),
            "preview_url": _generation.make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{rel}",
                local_path=sketch_out,
            ),
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/cut")
async def cut_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridCutRequest,
    user: dict = Depends(get_api_user),
):
    """将网格切割为单个 beat 图片入池。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from datetime import datetime
    from novelvideo.generators.pool_indexer import save_grid_and_split

    episode_grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    if not episode_grids_dir.exists():
        return {"ok": False, "error": f"No grids directory for episode {episode_num}"}

    beat_nums = (
        [int(beat) for beat in body.beat_numbers]
        if body.beat_numbers
        else list(range(body.beat_start, body.beat_end + 1))
    )
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    mode_key = body.mode_key or f"{body.rows}x{body.cols}"

    grid_image_path = None
    from novelvideo.generators.pool_indexer import load_pool_index

    pool = await run_asset_upload_operation(load_pool_index, episode_grids_dir)
    entry = _generation._find_pool_grid_entry(
        pool,
        grid_type=body.grid_type,
        mode_key=body.mode_key,
        beat_numbers=beat_nums,
        grid_index=grid_index,
    )
    if entry is not None:
        entry_path = _generation._safe_grids_file(episode_grids_dir, entry.grid_path)
        if entry_path and entry_path.exists():
            grid_image_path = str(entry_path)

    if grid_image_path is None:
        # 兼容旧版根目录 grid_XX.png / jpg 文件。
        grid_files = sorted(episode_grids_dir.glob("*.png")) + sorted(
            episode_grids_dir.glob("*.jpg")
        )
        if grid_index < 0 or grid_index >= len(grid_files):
            return {
                "ok": False,
                "error": f"Grid index {grid_index} out of range (total: {len(grid_files)})",
            }
        grid_image_path = str(grid_files[grid_index])

    if body.grid_type == "render":
        promote_dir = project_dir / "frames" / f"ep{episode_num:03d}"
    else:
        promote_dir = project_dir / "sketches"
    promote_dir.mkdir(parents=True, exist_ok=True)

    result = save_grid_and_split(
        grid_image_path=grid_image_path,
        episode_grids_dir=str(episode_grids_dir),
        grid_type=body.grid_type,
        mode_key=mode_key,
        beat_nums=beat_nums,
        preset="custom",
        rows=body.rows,
        cols=body.cols,
        ts=ts,
        promote_dir=promote_dir,
        force_promote=body.grid_type == "render",
    )

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "added": result.get("added", 0),
            "skipped": result.get("skipped", 0),
        },
    }


# ── ZIP 导出 ─────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/export/zip")
async def export_zip(project: str, episode_num: int, user: dict = Depends(get_api_user)):
    """打包指定集的所有资源为 ZIP 文件下载。"""
    import asyncio
    import zipfile
    import tempfile

    from novelvideo.export.episode_export import build_srt_content
    from novelvideo.utils.async_ops import call_blocking, wait_for_task_completion
    from novelvideo.utils.path_resolver import PathResolver

    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    async with _generation.generation_store_scope(resolved.ctx, resolved.username, project_name) as store:
        beats = await store.get_beats_as_dicts(episode_num)

        ep_tag = f"ep{episode_num:03d}"
        paths = PathResolver(str(project_dir), episode_num)

        files_to_pack: list[tuple[Path, str]] = []
        for beat in beats:
            beat_num = int(beat.get("beat_number", 0) or 0)
            if beat_num <= 0:
                continue
            audio_path = paths.audio(beat_num)
            if audio_path.exists():
                files_to_pack.append((audio_path, f"audio/{audio_path.name}"))
            video_path = paths.video(beat_num)
            if video_path.exists():
                files_to_pack.append((video_path, f"video/{video_path.name}"))

        final_path = paths.final_video()
        if final_path.exists():
            files_to_pack.append((final_path, final_path.name))

        # Keep existing extra project assets in the API ZIP; NiceGUI's core export
        # is beat audio/video + final + SRT, but frames/grids are useful inspection
        # artifacts and were already part of the React API surface.
        extra_dirs = {
            "frames": project_dir / "frames" / ep_tag,
            "grids": project_dir / "grids" / ep_tag,
        }
        for folder_name, folder in extra_dirs.items():
            if folder.exists():
                for file_path in sorted(folder.iterdir()):
                    if file_path.is_file():
                        files_to_pack.append((file_path, f"{folder_name}/{file_path.name}"))

        srt_content = await build_srt_content(project_dir, episode_num, beats)

        def build_zip_file() -> str:
            tmp_path = ""
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
                tmp_path = tmp.name
                tmp.close()
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
                    for file_path, arc_name in files_to_pack:
                        zf.write(file_path, arc_name)
                    if srt_content:
                        zf.writestr(
                            f"{ep_tag}.srt",
                            srt_content,
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
                return tmp_path
            except Exception:
                if tmp_path:
                    Path(tmp_path).unlink(missing_ok=True)
                raise

        build_task = asyncio.create_task(call_blocking(build_zip_file))
        tmp_path, cancellation = await wait_for_task_completion(build_task)
        if cancellation is not None:
            Path(tmp_path).unlink(missing_ok=True)
            raise cancellation
        try:
            return _generation._TemporaryFileResponse(
                path=tmp_path,
                filename=f"{project_name}_{ep_tag}.zip",
                media_type="application/zip",
            )
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
