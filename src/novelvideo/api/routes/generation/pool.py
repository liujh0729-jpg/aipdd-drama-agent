"""generation.pool 子模块（自 api/routes/generation.py 拆分）。"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import PoolSelectRequest
from novelvideo.api.schemas import VideoPoolSelectRequest
from novelvideo.api.upload_workers import run_asset_upload_operation

router = APIRouter()


# ── 视频池查看 & 选择 ─────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/video-pool")
async def list_video_pool(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir

    from novelvideo.generators.video_pool_indexer import load_video_pool_index

    videos_ep_dir = project_dir / "videos" / "beats" / f"ep{episode_num:03d}"
    pool = load_video_pool_index(videos_ep_dir)
    if not pool:
        return {"ok": True, "data": None}

    videos = []
    for entry in pool.videos:
        item = entry.model_dump()
        if item.get("generated_at"):
            item["generated_at"] = entry.generated_at.isoformat()
        rel_path = f"videos/beats/ep{episode_num:03d}/pool/{entry.video_path}"
        item["video_url"] = _generation.make_static_url_for_context(
            resolved.ctx,
            rel_path,
            local_path=project_dir / rel_path,
        )
        videos.append(item)

    return {
        "ok": True,
        "data": {
            "episode": pool.episode,
            "videos": videos,
            "beat_assignments": pool.beat_assignments,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/video-pool-select")
async def select_video_pool(
    project: str,
    episode_num: int,
    beat_num: int,
    body: VideoPoolSelectRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from novelvideo.generators.video_pool_indexer import assign_video_to_beat

    videos_ep_dir = project_dir / "videos" / "beats" / f"ep{episode_num:03d}"
    ok = assign_video_to_beat(videos_ep_dir, beat_num, body.pool_id)
    if not ok:
        return {
            "ok": False,
            "error": f"Pool entry '{body.pool_id}' not found or file missing",
        }

    rel_path = f"videos/beats/ep{episode_num:03d}/beat_{beat_num:02d}.mp4"
    video_url = _generation.make_static_url_for_context(
        resolved.ctx,
        rel_path,
        local_path=project_dir / rel_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": body.pool_id,
            "video_url": video_url,
        },
    }


# ── 图片池查看 & 选择 ─────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/grids")
async def list_grids(project: str, episode_num: int, user: dict = Depends(get_api_user)):
    """查看网格预览和图片池。"""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        is_pool_image_stale,
        load_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = await run_asset_upload_operation(load_pool_index, grids_dir)
    if not pool:
        return {"ok": True, "data": None}

    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:
        script_data = await store.get_script_as_dict(episode_num) or {}
        sketch_colors = script_data.get("sketch_colors", {}) or {}
        script_mt = None
        beat_hashes: dict[int, str] = {}
        for beat in script_data.get("beats", []):
            beat_num = beat.get("beat_number")
            if beat_num is not None:
                beat_hashes[beat_num] = compute_beat_content_hash(beat, sketch_colors=sketch_colors)

        images = []
        for img in pool.images:
            entry = img.model_dump()
            # datetime → ISO string
            if entry.get("generated_at"):
                entry["generated_at"] = entry["generated_at"].isoformat()
            # cell URL
            if img.cell_path:
                cell_path = grids_dir / img.cell_path
                entry["cell_url"] = _generation.make_static_url_for_context(
                    resolved.ctx,
                    f"grids/ep{episode_num:03d}/{img.cell_path}",
                    local_path=cell_path,
                )
            else:
                entry["cell_url"] = ""
            # grid URL
            if img.grid_path:
                grid_path = grids_dir / img.grid_path
                entry["grid_url"] = _generation.make_static_url_for_context(
                    resolved.ctx,
                    f"grids/ep{episode_num:03d}/{img.grid_path}",
                    local_path=grid_path,
                )
            else:
                entry["grid_url"] = ""
            entry["stale"] = is_pool_image_stale(img, beat_hashes, script_mt)
            images.append(entry)

        return {
            "ok": True,
            "data": {
                "episode": pool.episode,
                "modes": pool.modes,
                "images": images,
                "beat_assignments": pool.beat_assignments,
            },
        }


@router.post("/projects/{project}/episodes/{episode_num}/grids/rebuild-pool")
async def rebuild_grids_pool_index(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Rebuild the episode image pool index using the same helper as NiceGUI."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import rebuild_pool_index

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    grids_dir.mkdir(parents=True, exist_ok=True)
    pool = rebuild_pool_index(
        episode_grids_dir=grids_dir,
        episode=episode_num,
        split_cells=True,
    )
    return {
        "ok": True,
        "data": {
            "episode": pool.episode,
            "image_count": len(pool.images),
            "mode_count": len(pool.modes),
        },
    }


@router.get("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch-candidates")
async def get_beat_sketch_candidates(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return sketch pool candidates for a beat without treating them as the current sketch."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        is_pool_image_stale,
        load_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    current_path = project_dir / "sketches" / f"ep{episode_num:03d}" / f"beat_{beat_num:02d}.png"
    current_sketch_url = ""
    if current_path.exists():
        current_sketch_url = _generation.make_static_url_for_context(
            resolved.ctx,
            f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png",
            local_path=current_path,
        )

    pool = await run_asset_upload_operation(load_pool_index, grids_dir)
    if not pool:
        return {
            "ok": True,
            "data": {
                "episode": episode_num,
                "beat": beat_num,
                "current_sketch_url": current_sketch_url,
                "candidate_count": 0,
                "candidates": [],
            },
        }

    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:
        script_data = await store.get_script_as_dict(episode_num) or {}
        sketch_colors = script_data.get("sketch_colors", {}) or {}
        beat_hashes: dict[int, str] = {}
        for beat in script_data.get("beats", []) or []:
            raw_beat_num = beat.get("beat_number")
            try:
                parsed_beat_num = int(raw_beat_num)
            except (TypeError, ValueError):
                continue
            beat_hashes[parsed_beat_num] = compute_beat_content_hash(
                beat,
                sketch_colors=sketch_colors,
            )

        candidates = []
        for img in pool.images:
            if img.type != "sketch" or int(img.original_beat or 0) != int(beat_num):
                continue
            if not img.cell_path:
                continue
            cell_path = grids_dir / img.cell_path
            if not cell_path.exists():
                continue
            generated_at = img.generated_at.isoformat() if img.generated_at else ""
            candidates.append(
                {
                    "id": img.id,
                    "type": "sketch",
                    "mode": img.mode,
                    "cell_path": img.cell_path,
                    "url": _generation.make_static_url_for_context(
                        resolved.ctx,
                        f"grids/ep{episode_num:03d}/{img.cell_path}",
                        local_path=cell_path,
                    ),
                    "grid_path": img.grid_path,
                    "grid_index": img.grid_index,
                    "cell_index": img.cell_index,
                    "row": img.row,
                    "col": img.col,
                    "original_beat": img.original_beat,
                    "generated_at": generated_at,
                    "stale": is_pool_image_stale(img, beat_hashes, None),
                }
            )
        candidates.sort(
            key=lambda item: (str(item.get("generated_at") or ""), str(item.get("id") or "")),
            reverse=True,
        )

        return {
            "ok": True,
            "data": {
                "episode": episode_num,
                "beat": beat_num,
                "current_sketch_url": current_sketch_url,
                "candidate_count": len(candidates),
                "candidates": candidates,
            },
        }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/pool-select")
async def select_pool_image(
    project: str,
    episode_num: int,
    beat_num: int,
    body: PoolSelectRequest,
    user: dict = Depends(get_api_user),
):
    """选择 pool 图片，按类型设为 beat 首帧或草图。"""
    import shutil

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        is_pool_image_stale,
        load_pool_index,
        assign_beat_image,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = await run_asset_upload_operation(load_pool_index, grids_dir)
    if not pool:
        return {"ok": False, "error": "No pool index found. Generate grids first."}

    pool_img = pool.get_image(body.pool_id)
    if not pool_img:
        return {"ok": False, "error": f"Pool ID '{body.pool_id}' not found in pool index"}

    if pool_img and pool_img.type == "sketch":
        async with _generation.generation_store_scope(
            resolved.ctx, username, project_name
        ) as store:
            script_data = await store.get_script_as_dict(episode_num) or {}
            sketch_colors = script_data.get("sketch_colors", {}) or {}
            beats = script_data.get("beats", [])
            script_mt = None
            beat_hashes: dict[int, str] = {}
            beat_index = pool_img.original_beat - 1
            if 0 <= beat_index < len(beats):
                beat_hashes[pool_img.original_beat] = compute_beat_content_hash(
                    beats[beat_index], sketch_colors=sketch_colors
                )
            if is_pool_image_stale(pool_img, beat_hashes, script_mt) and not body.force:
                return {
                    "ok": False,
                    "stale": True,
                    "error": "该草图已过期，请先重新生成。如确认仍要使用，请传 force=true。",
                }

    cell_path = pool_img.cell_path
    if not cell_path:
        return {"ok": False, "error": f"Pool ID '{body.pool_id}' not found in pool index"}

    # 完整路径
    cell_full = grids_dir / cell_path
    if not cell_full.exists():
        return {"ok": False, "error": f"Cell image not found at {cell_path}"}

    image_type = pool_img.type or "render"
    data = {
        "beat_num": beat_num,
        "pool_id": body.pool_id,
        "image_type": image_type,
    }

    if image_type == "sketch":
        sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
        sketches_dir.mkdir(parents=True, exist_ok=True)
        dest = sketches_dir / f"beat_{beat_num:02d}.png"
        shutil.copy2(str(cell_full), str(dest))
        rel = f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png"
        data["sketch_url"] = _generation.make_static_url_for_context(
            resolved.ctx,
            rel,
            local_path=dest,
        )
    else:
        frames_dir = project_dir / "frames" / f"ep{episode_num:03d}"
        dest = frames_dir / f"beat_{beat_num:02d}.png"
        await run_asset_upload_operation(
            assign_beat_image, grids_dir, beat_num, cell_path, image_type
        )
        rel = f"frames/ep{episode_num:03d}/beat_{beat_num:02d}.png"
        data["frame_url"] = _generation.make_static_url_for_context(
            resolved.ctx,
            rel,
            local_path=dest,
        )

    return {
        "ok": True,
        "data": data,
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/upload")
async def upload_beat_sketch(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a beat sketch, store the canonical sketch file, and add it to the pool."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    try:
        image = await _generation._read_uploaded_rgb_image(file)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    sketch_path = sketches_dir / f"beat_{beat_num:02d}.png"

    pool_id = await run_asset_upload_operation(
        _generation._register_uploaded_pool_image,
        project_dir=project_dir,
        episode_num=episode_num,
        beat_num=beat_num,
        image=image,
        image_type="sketch",
    )
    rel = f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png"
    sketch_url = _generation.make_static_url_for_context(
        resolved.ctx,
        rel,
        local_path=sketch_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": pool_id,
            "sketch_url": sketch_url,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/render/upload")
async def upload_beat_render(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a beat render first frame, promote it, and add it to the pool."""
    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    try:
        image = await _generation._read_uploaded_rgb_image(file)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    frames_dir = project_dir / "frames" / f"ep{episode_num:03d}"
    frame_path = frames_dir / f"beat_{beat_num:02d}.png"

    pool_id = await run_asset_upload_operation(
        _generation._register_uploaded_pool_image,
        project_dir=project_dir,
        episode_num=episode_num,
        beat_num=beat_num,
        image=image,
        image_type="render",
    )
    rel = f"frames/ep{episode_num:03d}/beat_{beat_num:02d}.png"
    frame_url = _generation.make_static_url_for_context(
        resolved.ctx,
        rel,
        local_path=frame_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": pool_id,
            "frame_url": frame_url,
        },
    }
