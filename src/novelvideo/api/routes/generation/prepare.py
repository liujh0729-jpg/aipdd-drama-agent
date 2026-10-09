"""generation.prepare 子模块（自 api/routes/generation.py 拆分）。"""

import io
import json
from pathlib import Path
from typing import Any
from typing import Callable

from fastapi import UploadFile

from novelvideo.api.deps import get_user_base_dir
from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import SingleVideoRequest
from novelvideo.seedance2_i2v.pipeline import is_huimeng_seedance2_backend
from novelvideo.utils.path_resolver import compute_identity_path
from novelvideo.utils.path_resolver import compute_portrait_path



async def _read_uploaded_rgb_image(file: UploadFile):
    content = await file.read()
    if not content:
        raise ValueError("empty file")
    try:
        from PIL import Image

        return Image.open(io.BytesIO(content)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"invalid image file: {exc}") from exc


def _register_uploaded_pool_image(
    *,
    project_dir: Path,
    episode_num: int,
    beat_num: int,
    image,
    image_type: str,
) -> str:
    from datetime import datetime
    from novelvideo.generators.pool_indexer import (
        add_cell_with_dedup,
        build_pool_index,
        _state_pool_index_path,
        _load_pool_index_for_update,
        _save_pool_index_unlocked,
        index_file_lock,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    with index_file_lock(_state_pool_index_path(grids_dir)):
        index_path, pool = _load_pool_index_for_update(grids_dir)
        pool = pool or build_pool_index(grids_dir, episode_num)
        upload_dir = grids_dir / image_type
        upload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        cell_path = upload_dir / f"beat_{beat_num:02d}_t{timestamp}.png"
        canonical_dir = project_dir / (
            "sketches" if image_type == "sketch" else "frames"
        ) / f"ep{episode_num:03d}"
        canonical_dir.mkdir(parents=True, exist_ok=True)
        image.save(canonical_dir / f"beat_{beat_num:02d}.png", format="PNG")
        image.save(cell_path, format="PNG")

        pool_image = add_cell_with_dedup(
            pool,
            cell_path,
            grids_dir,
            beat_num,
            timestamp,
            img_type=image_type,
            mode="upload",
            grid_index=0,
            cell_index=0,
            grid_path="",
            row=0,
            col=0,
        )
        if pool_image is None:
            pool_id = f"beat_{beat_num:02d}_t{timestamp}_{image_type}"
            assignment_path = None
        else:
            pool_id = pool_image.id
            assignment_path = pool_image.cell_path
        if image_type != "sketch" and assignment_path:
            pool.beat_assignments[str(beat_num)] = assignment_path
        _save_pool_index_unlocked(pool, index_path)
    return pool_id


async def _runtime_prop_menu_with_global_props(
    store: Any,
    episode_obj: Any,
    beats: list[dict],
) -> list[dict]:
    """Resolve episode prop markers against the global props table without mutating episode JSON."""
    from novelvideo.models import build_prop_menu, collect_prop_marker_ids_from_beat

    prop_menu = [item.model_dump() for item in episode_obj.prop_menu] if episode_obj else []
    marked_prop_ids: list[str] = []
    for beat in beats or []:
        for prop_id in collect_prop_marker_ids_from_beat(beat):
            if prop_id and prop_id not in marked_prop_ids:
                marked_prop_ids.append(prop_id)
    if not marked_prop_ids:
        return prop_menu

    existing = {item.prop_id: item.model_dump() for item in build_prop_menu(prop_menu=prop_menu)}
    changed = False
    for marker_prop_id in marked_prop_ids:
        global_prop = (
            store.get_cached_prop(marker_prop_id) if hasattr(store, "get_cached_prop") else None
        )
        if not global_prop:
            continue
        item = dict(existing.get(marker_prop_id) or {"prop_id": marker_prop_id})
        item["is_global_asset"] = True
        item["prop_type"] = (
            item.get("prop_type") or getattr(global_prop, "prop_type", "") or "object"
        )
        item["description"] = (
            item.get("description")
            or getattr(global_prop, "description", "")
            or getattr(global_prop, "visual_prompt", "")
            or marker_prop_id
        )
        existing[marker_prop_id] = item
        changed = True
    if not changed:
        return prop_menu

    ordered_ids: list[str] = []
    for item in build_prop_menu(prop_menu=prop_menu):
        if item.prop_id not in ordered_ids:
            ordered_ids.append(item.prop_id)
    for prop_id in marked_prop_ids:
        if prop_id in existing and prop_id not in ordered_ids:
            ordered_ids.append(prop_id)
    return [existing[prop_id] for prop_id in ordered_ids if prop_id in existing]


def _prop_marker_colors_from_menu(prop_menu: list[dict] | None) -> dict[str, str]:
    colors: dict[str, str] = {}
    for item in prop_menu or []:
        if not isinstance(item, dict):
            continue
        prop_id = str(item.get("prop_id") or "").strip()
        marker_color = str(item.get("marker_color") or "").strip()
        if prop_id and marker_color:
            colors[prop_id] = marker_color
    return colors


def _episode_from_store_or_none(store: Any, episode_num: int) -> Any | None:
    get_episode = getattr(store, "get_episode", None)
    if get_episode is None:
        return None
    try:
        return get_episode(episode_num)
    except Exception:
        return None


async def _build_character_map(
    store,
    beats,
    username,
    project,
    *,
    episode_num: int | None = None,
    use_detected_identities: bool = False,
):
    """构建角色映射。"""
    from novelvideo.services.character_ref_service import build_character_map_for_grid

    project_dir = get_user_base_dir(username) / project
    characters = store.get_all_characters()
    char_dicts = []
    for c in characters:
        char_dicts.append(
            {
                "name": c.name,
                "gender": c.gender,
                "body_type": getattr(c, "body_type", ""),
                "role": c.role,
                "is_main": getattr(c, "is_main", False),
                "portrait_path": compute_portrait_path(project_dir, c.name),
                "face_prompt": c.face_prompt,
                "appearance_details": c.appearance_details,
                "identities": (
                    [
                        {
                            "identity_id": id_.identity_id,
                            "identity_name": id_.identity_name,
                            "appearance_details": id_.appearance_details,
                            "face_prompt": id_.face_prompt,
                            "body_type": id_.body_type,
                            "fish_voice_id": id_.fish_voice_id,
                            "age_group": id_.age_group,
                            "portrait_image": id_.portrait_image,
                            "costume_image": id_.costume_image,
                            "primary_reference": compute_identity_path(
                                project_dir, c.name, id_.identity_name
                            ),
                            "character_tag": id_.character_tag,
                            "source": id_.source,
                        }
                        for id_ in c.identities
                    ]
                    if c.identities
                    else []
                ),
            }
        )

    # 优先从 SQLite 读取 sketch_colors；若缺失则按当前 beats 重新分配并写回 SQLite。
    _sc = None
    if episode_num:
        _sc = store.get_sketch_colors(episode_num) or None
        if not _sc:
            from novelvideo.generators.episode_optimizer import EpisodeOptimizer

            _sc = (
                EpisodeOptimizer.assign_sketch_colors(
                    char_dicts,
                    episode_beats=beats,
                )
                or None
            )

            if _sc:
                await store.set_sketch_colors(episode_num, _sc)

    return build_character_map_for_grid(
        grid_beats=beats,
        characters=char_dicts,
        user_output_dir=get_user_base_dir(username),
        project=project,
        sketch_colors=_sc,
        use_detected_identities=use_detected_identities,
    )


def _validate_seedance_pro_dialogue_only(beats: list[dict], video_backend: str) -> str | None:
    """Seedance 1.5 有声仅允许 dialogue beat。"""
    if video_backend not in {"seedance_pro", "newapi_seedance-1.5-pro"}:
        return None

    non_dialogue = [
        int(beat.get("beat_number", 0))
        for beat in beats
        if beat.get("audio_type", "narration") != "dialogue"
    ]
    if not non_dialogue:
        return None

    preview = "、".join(str(num) for num in non_dialogue[:8])
    suffix = " 等" if len(non_dialogue) > 8 else ""
    return f"Seedance 1.5 有声只允许用于 dialogue beat；当前包含非 dialogue Beat: {preview}{suffix}"


def _is_seedance2_backend(video_backend: str | None) -> bool:
    return is_huimeng_seedance2_backend(video_backend)


def _is_happyhorse_backend(video_backend: str | None) -> bool:
    return _seedance2_model_from_backend(video_backend) == "happyhorse-1.0"


def _is_grok_video_backend(video_backend: str | None) -> bool:
    return _seedance2_model_from_backend(video_backend) == "grok-video-channel"


def _seedance2_api_resolution(resolution: str | None) -> str:
    text = str(resolution or "").strip()
    if text in {"480p", "720p", "1080p"}:
        return text
    if "480" in text:
        return "480p"
    if "1080" in text:
        return "1080p"
    return "720p"


SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL = {
    "seedance-2.0-fast": ("480p", "720p"),
    "seedance-2.0": ("480p", "720p", "1080p"),
    "seedance-2.0-value": ("720p", "1080p"),
    "seedance-2.0-fast-value": ("720p", "1080p"),
    # Seedance 1.5 Pro（有声）清晰度，来源 huimengi /api/v1/models（480p/720p/1080p）
    "seedance-1.5-pro": ("480p", "720p", "1080p"),
}
SEEDANCE2_DEFAULT_RESOLUTION_OPTIONS = ("480p", "720p")
HAPPYHORSE_RESOLUTION_OPTIONS = ("720p", "1080p")
HAPPYHORSE_RATIO_OPTIONS = ("16:9", "9:16", "1:1", "4:3", "3:4")
HAPPYHORSE_SUPPORTED_MODES = ("first_frame", "multimodal_reference")
GROK_VIDEO_RESOLUTION_OPTIONS = ("720p", "480p")
GROK_VIDEO_RATIO_OPTIONS = ("16:9", "9:16", "1:1", "2:3", "3:2")
GROK_VIDEO_SUPPORTED_MODES = ("first_frame", "multimodal_reference")


def _seedance2_model_from_backend(video_backend: str | None) -> str:
    text = str(video_backend or "").strip().lower()
    for prefix in ("newapi_", "huimeng_", "huimengi_"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _seedance2_resolution_options_for_backend(video_backend: str | None) -> tuple[str, ...]:
    model = _seedance2_model_from_backend(video_backend)
    return SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL.get(
        model,
        SEEDANCE2_DEFAULT_RESOLUTION_OPTIONS,
    )


def _seedance2_resolution_for_backend(
    video_backend: str | None,
    resolution: str | None,
) -> str:
    clean_resolution = _seedance2_api_resolution(resolution)
    options = _seedance2_resolution_options_for_backend(video_backend)
    if clean_resolution in options:
        return clean_resolution
    if "720p" in options:
        return "720p"
    return options[0]


def _happyhorse_resolution_for_backend(resolution: str | None) -> str:
    text = str(resolution or "").strip().lower()
    if "720" in text:
        return "720p"
    return "1080p"


def _happyhorse_ratio_for_backend(ratio: str | None) -> str:
    text = str(ratio or "").strip()
    return text if text in HAPPYHORSE_RATIO_OPTIONS else "16:9"


def _grok_video_resolution_for_backend(resolution: str | None) -> str:
    text = str(resolution or "").strip().lower()
    return text if text in GROK_VIDEO_RESOLUTION_OPTIONS else "720p"


def _grok_video_ratio_for_backend(ratio: str | None) -> str:
    text = str(ratio or "").strip()
    return text if text in GROK_VIDEO_RATIO_OPTIONS else "16:9"


def _seedance2_initial_prompt(beat: dict[str, Any], video_mode: str) -> str:
    if video_mode == "keyframe":
        return str(beat.get("keyframe_prompt") or "").strip()
    return str(beat.get("video_prompt") or beat.get("keyframe_prompt") or "").strip()


def _legacy_video_prompt_for_mode(beat: dict[str, Any], video_mode: str) -> str:
    if video_mode == "keyframe":
        return str(beat.get("keyframe_prompt") or "").strip()
    return str(beat.get("video_prompt") or "").strip()


def _missing_video_prompt_error(beat_num: int) -> str:
    return f"Beat {beat_num} 缺少视频提示词，请先点击“生成本 Beat 提示词”。"


SEEDANCE2_SINGLE_VIDEO_CONFIG_FIELDS = {
    "mode",
    "duration",
    "resolution",
    "ratio",
    "generate_audio",
    "return_last_frame",
    "human_review",
    "scene_optimize",
    "final_prompt",
    "prompt_guidance",
    "text_overlay",
}


def _seedance2_request_config_overrides(body: SingleVideoRequest) -> dict[str, Any]:
    return {
        field: getattr(body, field)
        for field in SEEDANCE2_SINGLE_VIDEO_CONFIG_FIELDS
        if field in body.model_fields_set and getattr(body, field) is not None
    }


def _merge_seedance2_request_config(
    beat: dict[str, Any],
    *,
    seedance2_config_json: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> str | None:
    config_overrides = dict(config_overrides or {})
    if seedance2_config_json is None and not config_overrides:
        return None

    from novelvideo.seedance2_i2v.models import dump_seedance2_config, parse_seedance2_config

    merged = parse_seedance2_config(beat.get("seedance2_config_json")).model_dump(mode="json")
    incoming: dict[str, Any] = {}
    if seedance2_config_json is not None:
        try:
            incoming = json.loads(str(seedance2_config_json or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("seedance2_config_json must be valid JSON") from exc
        if not isinstance(incoming, dict):
            raise ValueError("seedance2_config_json must be a JSON object")
        merged.update(incoming)
    merged.update(config_overrides)

    if "generate_audio" in incoming or "generate_audio" in config_overrides:
        merged["generate_audio_user_set"] = True
    if "human_review" in incoming or "human_review" in config_overrides:
        merged["human_review_user_set"] = True

    saved_json = dump_seedance2_config(merged)
    beat["seedance2_config_json"] = saved_json
    return saved_json


async def _api_audio_duration_seconds(output_dir: str | Path, episode: int, beat_num: int):
    """Duration of one beat's audio, or ``None`` if there isn't one to report.

    ``None`` already means "no duration for this beat" here (the file may simply
    not have been generated yet), so a probe that fails or times out reports the
    same thing rather than failing the whole beat list. On network storage an
    unreadable file is a routine condition, not a request-level error.
    """
    from novelvideo.utils.media_io import get_audio_duration_async
    from novelvideo.utils.path_resolver import PathResolver

    audio_path = PathResolver(output_dir, episode).audio(beat_num)
    if not audio_path.exists():
        return None
    try:
        return await get_audio_duration_async(str(audio_path))
    except Exception:
        return None


async def _prepare_seedance2_api_beat(
    *,
    store: Any,
    output_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    all_beats: list[dict[str, Any]],
    index: int,
    video_backend: str | None,
    resolution: str | None,
    ratio: str | None = None,
    prop_menu: list[Any] | None = None,
) -> Any:
    from novelvideo.manual_shots import resolve_target_video_duration
    from novelvideo.seedance2_i2v.models import parse_seedance2_config

    beat_num = int(beat.get("beat_number") or index + 1)
    video_mode = str(beat.get("video_mode") or "first_frame")
    audio_duration = await _generation._api_audio_duration_seconds(output_dir, episode, beat_num)
    target_duration = resolve_target_video_duration(beat, audio_duration)
    current_config = parse_seedance2_config(beat.get("seedance2_config_json"))
    requested_resolution = (
        _seedance2_api_resolution(resolution)
        if resolution
        else current_config.resolution
    )
    prepared = await _generation.prepare_seedance2_generation_inputs(
        project_output=output_dir,
        state_dir=Path(store.state_dir),
        episode=episode,
        beat=beat,
        next_beat=all_beats[index + 1] if index + 1 < len(all_beats) else None,
        video_mode=video_mode,
        prompt=_seedance2_initial_prompt(beat, video_mode),
        duration=target_duration,
        resolution=_seedance2_resolution_for_backend(
            video_backend,
            requested_resolution,
        ),
        ratio=ratio,
        prop_menu=prop_menu,
    )
    if not str(prepared.prompt or "").strip():
        raise ValueError(
            f"Beat {beat_num} Seedance 2.0 最终提示词为空，"
            "请先填写 Seedance2.0主体提示词或点击“AI 优化”。"
        )

    beat["seedance2_config_json"] = prepared.seedance2_config_json
    if hasattr(store, "update_beat_asset"):
        await store.update_beat_asset(
            episode_number=episode,
            beat_number=beat_num,
            seedance2_config_json=prepared.seedance2_config_json,
        )
    return prepared


async def _prepare_video_api_beat(
    *,
    output_dir: str | Path,
    state_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any] | None,
    frame_path: Path,
    video_mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    ratio: str | None,
    backend_label: str,
    resolution_for_backend: Callable[[str | None], str],
    ratio_for_backend: Callable[[str | None], str],
    max_reference_images: int,
    prop_menu: list[Any] | None = None,
) -> dict[str, Any]:
    """HappyHorse / Grok Video 共用的单 beat 准备实体。

    两条背板差异只余：报错标签、resolution/ratio 归一函数、参考图上限；
    行为与拆分前的两份逐行拷贝相同。
    """
    from novelvideo.seedance2_i2v.assets import (
        append_seedance2_user_reference_assets,
        build_seedance2_project_assets,
        selected_reference_paths,
    )
    from novelvideo.seedance2_i2v.models import (
        Seedance2I2VMode,
        dump_seedance2_config,
        parse_seedance2_config,
    )

    config = parse_seedance2_config(beat.get("seedance2_config_json"))
    mode = config.mode
    if mode == Seedance2I2VMode.FIRST_LAST_FRAME or video_mode == "keyframe":
        raise ValueError(f"{backend_label} 不支持首尾帧模式，请改用首帧模式或多参模式")

    final_prompt = str(config.final_prompt or prompt or "").strip()
    if not final_prompt:
        beat_num = int(beat.get("beat_number") or 0)
        prefix = f"Beat {beat_num} " if beat_num else ""
        raise ValueError(f"{prefix}缺少视频提示词，请先生成或填写视频提示词")

    target_duration = int(config.duration or duration or 0)
    config.duration = target_duration
    config.resolution = resolution_for_backend(resolution or config.resolution)
    config.ratio = ratio_for_backend(ratio or config.ratio)
    config.final_prompt = final_prompt

    image_path: str | None = None
    references: list[dict[str, str]] = []

    if mode == Seedance2I2VMode.FIRST_FRAME:
        image_path = str(frame_path)
    else:
        assets = build_seedance2_project_assets(
            project_output=Path(output_dir),
            episode=episode,
            beat=beat,
            mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
            state_dir=state_dir,
            next_beat=next_beat,
            prop_menu=prop_menu,
        )
        append_seedance2_user_reference_assets(
            assets,
            reference_image_paths=list(config.reference_image_paths),
            reference_audio_paths=[],
        )
        image_paths = selected_reference_paths(assets, "reference_images")
        config.reference_image_paths = list(dict.fromkeys(image_paths))[:max_reference_images]
        config.reference_audio_paths = []
        references = [
            {"type": "image", "path": path, "role": f"图片{index}"}
            for index, path in enumerate(config.reference_image_paths, 1)
        ]

    return {
        "prompt": final_prompt,
        "duration": target_duration,
        "resolution": config.resolution,
        "ratio": config.ratio,
        "image_path": image_path,
        "references": references,
        "config_json": dump_seedance2_config(config),
    }


async def _prepare_happyhorse_api_beat(
    *,
    output_dir: str | Path,
    state_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any] | None,
    frame_path: Path,
    video_mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    ratio: str | None,
    prop_menu: list[Any] | None = None,
) -> dict[str, Any]:
    """HappyHorse 1.0 单 beat 准备（实体 + 背板差异参数）。"""

    return await _prepare_video_api_beat(
        output_dir=output_dir,
        state_dir=state_dir,
        episode=episode,
        beat=beat,
        next_beat=next_beat,
        frame_path=frame_path,
        video_mode=video_mode,
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        prop_menu=prop_menu,
        backend_label="HappyHorse 1.0",
        resolution_for_backend=_happyhorse_resolution_for_backend,
        ratio_for_backend=_happyhorse_ratio_for_backend,
        max_reference_images=9,
    )


async def _prepare_grok_video_api_beat(
    *,
    output_dir: str | Path,
    state_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any] | None,
    frame_path: Path,
    video_mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    ratio: str | None,
    prop_menu: list[Any] | None = None,
) -> dict[str, Any]:
    """Grok Video 单 beat 准备（实体 + 背板差异参数）。"""

    return await _prepare_video_api_beat(
        output_dir=output_dir,
        state_dir=state_dir,
        episode=episode,
        beat=beat,
        next_beat=next_beat,
        frame_path=frame_path,
        video_mode=video_mode,
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        prop_menu=prop_menu,
        backend_label="Grok Video",
        resolution_for_backend=_grok_video_resolution_for_backend,
        ratio_for_backend=_grok_video_ratio_for_backend,
        max_reference_images=7,
    )
