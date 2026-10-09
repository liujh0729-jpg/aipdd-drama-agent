"""freezone.mainline 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from novelvideo.api.routes import freezone as _freezone
from novelvideo.freezone.paths import outputs_dir
from novelvideo.freezone.route_helpers import FREEZONE_DEFAULT_IMAGE_MODEL
from novelvideo.freezone.route_helpers import build_scene_360_prompt as _build_scene_360_prompt
from novelvideo.freezone.route_helpers import split_provider_and_model as _split_provider_and_model
from novelvideo.freezone.skill_registry import ResolvedSkillInput
from novelvideo.freezone.skill_registry import SkillRunRequest
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.models import real_detected_identities
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import selection_scope
from novelvideo.task_identity import task_config_scope
from novelvideo.utils.path_resolver import canonical_identity_path
from novelvideo.utils.path_resolver import canonical_portrait_path
from novelvideo.utils.path_resolver import canonical_prop_reference_path
from novelvideo.utils.path_resolver import canonical_scene_master_path
from novelvideo.utils.path_resolver import canonical_scene_reverse_master_path

from ._common import (
    MAINLINE_SCENE_360_IMAGE_SIZE,
    _IMAGE_SIZE_TIERS,
    _IMAGE_SIZE_TIER_BY_CASEFOLD,
)



async def _load_freezone_beat_context(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    episode: int,
    beat: int,
) -> dict:
    async with _freezone.freezone_store_scope(ctx, username, project) as store:
        beats = await store.get_beats_as_dicts(int(episode))
        for row in beats:
            if int(row.get("beat_number") or 0) == int(beat):
                return row
        raise HTTPException(404, f"beat not found: ep={episode} beat={beat}")


def _scene_ref_label(beat: dict) -> str:
    scene_ref = beat.get("scene_ref")
    if isinstance(scene_ref, dict):
        return str(scene_ref.get("name") or scene_ref.get("scene_name") or "").strip()
    if scene_ref:
        return str(scene_ref).strip()
    return ""


async def _collect_mainline_typed_reference_urls(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    beat: dict,
    include_identities: bool = True,
    include_props: bool = True,
    include_scene_master: bool = False,
    include_scene_reverse: bool = False,
) -> list[str]:
    """Auto-inject mainline-authoritative reference images for typed actions.

    Design contract: mainline canvas projection = DB live mirror. Whatever
    references this beat declares (detected_identities / detected_props /
    scene_ref) should be passed to the LLM as visual references, not just
    mentioned in the prompt text. Source of truth is the DB; this helper
    derives URLs from canonical asset paths so the LLM "sees" the same set
    of refs the canvas projects visually (identity nodes / prop nodes /
    scene nodes connected to the mainline skill node).

    Without this, prior behavior was:
      - prompt text included "角色身份: A, B" but no PNG was uploaded,
      - LLM had no visual anchor for identity / prop -> identity drift,
      - users found this surprising ("我标了 detected_identities 为什么没用").
    """
    from novelvideo.freezone.presets import _identity_character, _identity_name

    refs: list[str] = []

    if include_identities:
        known_character_names: list[str] = []
        character_age_by_name: dict[str, str] = {}
        identity_age_by_id: dict[str, str] = {}
        try:
            async with _freezone.freezone_store_for_context_scope(ctx) as store:
                characters = await store.list_characters()  # type: ignore[attr-defined]
            for c in characters or []:
                name = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None)
                if not name:
                    continue
                name = str(name)
                known_character_names.append(name)
                # 记录 character 默认 age_group + 每个 identity 自己的 age_group,
                # 用于判定 age variant(identity.age_group ≠ character.age_group 即变体)。
                char_age = str(
                    getattr(c, "age_group", "")
                    or (c.get("age_group") if isinstance(c, dict) else "")
                    or ""
                ).strip()
                if char_age:
                    character_age_by_name[name] = char_age
                identities_iter = (
                    getattr(c, "identities", None)
                    or (c.get("identities") if isinstance(c, dict) else None)
                    or []
                )
                for ident in identities_iter:
                    ident_id = str(
                        getattr(ident, "identity_id", "")
                        or (ident.get("identity_id") if isinstance(ident, dict) else "")
                        or ""
                    ).strip()
                    ident_age = str(
                        getattr(ident, "age_group", "")
                        or (ident.get("age_group") if isinstance(ident, dict) else "")
                        or ""
                    ).strip()
                    if ident_id and ident_age:
                        identity_age_by_id[ident_id] = ident_age
        except Exception:
            pass

        for identity_id in beat.get("detected_identities") or []:
            identity_id = str(identity_id or "").strip()
            if not identity_id:
                continue
            character = _identity_character(identity_id, known_character_names)
            identity_name = _identity_name(identity_id, character)
            path = canonical_identity_path(project_dir, character, identity_name)
            if not path.exists() or not path.is_file():
                # Age variant identity (identity.age_group ≠ character.age_group)
                # 缺自己的 canonical 时,**不** fallback 到主 character portrait —
                # 主 portrait 通常是 youth 形态,中年/老年变体拿它当 reference 会
                # 触发 identity drift (LLM 看到 youth 脸 → 产出 youth-like)。
                # 跟 presets/payload.py:1260-1265 age-variant fallback 规则保持一致。
                identity_age = identity_age_by_id.get(identity_id, "")
                char_age = character_age_by_name.get(character, "")
                is_age_variant = bool(identity_age and identity_age != char_age)
                if is_age_variant:
                    continue  # 这次 inject 跳过这个 identity ref;LLM 靠 prompt 文字解析
                # fallback to character portrait so LLM at least has a face
                path = canonical_portrait_path(project_dir, character)
            if path.exists() and path.is_file():
                try:
                    rel = path.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                url = _freezone.make_static_url_for_context(ctx, rel, local_path=path)
                if url:
                    refs.append(url)

    if include_props:
        for prop_id in beat.get("detected_props") or []:
            prop_id = str(prop_id or "").strip()
            if not prop_id:
                continue
            path = canonical_prop_reference_path(project_dir, prop_id)
            if path.exists() and path.is_file():
                try:
                    rel = path.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                url = _freezone.make_static_url_for_context(ctx, rel, local_path=path)
                if url:
                    refs.append(url)

    if include_scene_master or include_scene_reverse:
        scene_name = ""
        scene_ref = beat.get("scene_ref")
        if isinstance(scene_ref, dict):
            scene_name = str(
                scene_ref.get("scene_id")
                or scene_ref.get("name")
                or scene_ref.get("scene_name")
                or ""
            ).strip()
        elif scene_ref:
            scene_name = str(scene_ref).strip()
        if scene_name:
            scene_paths: list[Path] = []
            if include_scene_master:
                scene_paths.append(canonical_scene_master_path(project_dir, scene_name))
            if include_scene_reverse:
                scene_paths.append(canonical_scene_reverse_master_path(project_dir, scene_name))
            for p in scene_paths:
                if p.exists() and p.is_file():
                    try:
                        rel = p.relative_to(project_dir).as_posix()
                    except ValueError:
                        continue
                    url = _freezone.make_static_url_for_context(ctx, rel, local_path=p)
                    if url:
                        refs.append(url)

    return refs


def _skill_beat_context_as_prompt_beat(input_item: ResolvedSkillInput | None) -> dict:
    beat_context = (input_item.beat_context if input_item else None) or {}
    scene_id = (
        beat_context.get("scene_id")
        or beat_context.get("sceneId")
        or beat_context.get("scene_name")
        or beat_context.get("sceneName")
        or ""
    )
    scene_ref = {"scene_id": scene_id, "name": scene_id} if scene_id else None
    visual_description = (
        beat_context.get("visual_description")
        or beat_context.get("visualDescription")
        or beat_context.get("content")
        or ""
    )
    detected_identities = (
        beat_context.get("detected_identities") or beat_context.get("detectedIdentities") or []
    )
    if str(beat_context.get("source") or "").strip().lower() == "standalone":
        visual_description = _standalone_beat_context_prompt_visual_description(
            str(visual_description or ""), beat_context
        )
        identity_map = _standalone_beat_context_prompt_identity_map(beat_context)
        detected_identities = [
            identity_map.get(str(item).strip(), str(item).strip())
            for item in detected_identities
            if str(item).strip()
        ]

    return {
        "episode_number": beat_context.get("episode") or beat_context.get("episode_number"),
        "beat_number": beat_context.get("beat") or beat_context.get("beat_number"),
        "scene_ref": scene_ref,
        "visual_description": visual_description,
        "narration_segment": (
            beat_context.get("narration_segment") or beat_context.get("narrationSegment") or ""
        ),
        "detected_identities": detected_identities,
        "detected_props": beat_context.get("detected_props")
        or beat_context.get("detectedProps")
        or [],
    }


def _is_standalone_beat_context_input(input_item: ResolvedSkillInput | None) -> bool:
    beat_context = (input_item.beat_context if input_item else None) or {}
    source = str(beat_context.get("source") or "").strip().lower()
    return source == "standalone"


def _list_text_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _standalone_beat_context_sketch_colors(beat_context: dict) -> dict[str, str]:
    value = beat_context.get("sketch_colors") or beat_context.get("sketchColors") or {}
    return dict(value) if isinstance(value, dict) else {}


def _standalone_beat_context_prop_marker_colors(beat_context: dict) -> dict[str, str]:
    value = beat_context.get("prop_marker_colors") or beat_context.get("propMarkerColors") or {}
    return dict(value) if isinstance(value, dict) else {}


def _standalone_identity_prompt_parts(identity_name: str) -> tuple[str, str, str]:
    identity_name = identity_name.strip()
    if "_" in identity_name:
        char_name, suffix = identity_name.split("_", 1)
        char_name = char_name.strip()
        suffix = suffix.strip()
        if char_name and suffix:
            return char_name, suffix, identity_name
    return identity_name, identity_name, f"{identity_name}_{identity_name}"


def _standalone_beat_context_identity_names(beat_context: dict) -> list[str]:
    # 「无角色出场」哨兵以下划线开头，不能按「角色_身份」拆分，也不是真实角色。
    return real_detected_identities(
        _list_text_values(
            beat_context.get("detected_identities") or beat_context.get("detectedIdentities")
        )
    )


def _standalone_beat_context_prompt_identity_map(beat_context: dict) -> dict[str, str]:
    identity_names = _standalone_beat_context_identity_names(beat_context)
    return {
        identity_name: _standalone_identity_prompt_parts(identity_name)[2]
        for identity_name in identity_names
    }


def _standalone_beat_context_prompt_visual_description(
    visual_description: str, beat_context: dict
) -> str:
    identity_map = _standalone_beat_context_prompt_identity_map(beat_context)
    if not identity_map:
        return visual_description

    def replace_marker(match: re.Match) -> str:
        marker = str(match.group(1) or "").strip()
        return "{{" + identity_map.get(marker, marker) + "}}"

    return re.sub(r"\{\{([^}]+)\}\}", replace_marker, visual_description)


def _standalone_beat_context_character_map(beat_context: dict) -> dict[str, dict]:
    sketch_colors = _standalone_beat_context_sketch_colors(beat_context)
    identity_names = _standalone_beat_context_identity_names(beat_context)
    character_map: dict[str, dict] = {}
    for identity_name in identity_names:
        char_name, suffix, _prompt_identity_id = _standalone_identity_prompt_parts(identity_name)
        entry = character_map.setdefault(
            char_name,
            {
                "base_prompt": char_name,
                "reference_mode": "prompt_only",
                "sketch_color": "",
                "identity_appearances": {},
                "identity_sketch_colors": {},
            },
        )
        color = sketch_colors.get(identity_name) or sketch_colors.get(char_name) or ""
        entry["identity_appearances"][suffix] = identity_name
        if color:
            entry["identity_sketch_colors"][suffix] = color
            entry["sketch_color"] = entry["sketch_color"] or color
    return character_map


def _standalone_beat_context_unified_sketch_prompt(
    *,
    input_item: ResolvedSkillInput | None,
    project_dir: Path,
    reference_path: str,
    reference_role: str,
    aspect_ratio: str,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    from novelvideo.generators.prompt_builder import (
        PromptMode,
        UnifiedPromptBuilder,
        create_prompt_context,
    )
    from novelvideo.utils.asset_resolver import ResolvedAssetRef

    beat_context = (input_item.beat_context if input_item else None) or {}
    beat_payload = dict(_skill_beat_context_as_prompt_beat(input_item))

    is_director_combined = reference_role == "director_combined"
    scene_id = _freezone._first_text_value(
        beat_context, ("scene_id", "sceneId", "scene_name", "sceneName", "title", "name")
    )
    ref = ResolvedAssetRef(
        asset_type="scene",
        base_id=scene_id or "Canvas Beat Context",
        variant_id=reference_role,
        image_paths=[reference_path] if reference_path else [],
        text_description="" if is_director_combined else scene_id,
        source_level="director_image" if is_director_combined else "selected_background_image",
    )
    ctx = create_prompt_context(
        mode=PromptMode.SKETCH,
        beats=[beat_payload],
        rows=1,
        cols=1,
        character_map=_standalone_beat_context_character_map(beat_context),
        aspect_ratio=aspect_ratio,
        scene_refs={1: [ref]},
        sketch_colors=_standalone_beat_context_sketch_colors(beat_context),
        prop_marker_colors=_standalone_beat_context_prop_marker_colors(beat_context),
        project_dir=str(project_dir),
        image_provider=provider or "",
        image_model=model or "",
    )
    return UnifiedPromptBuilder(ctx).build()


def _beat_by_number(beats: list[dict], beat_number: int) -> dict:
    for beat in beats:
        try:
            if int(beat.get("beat_number") or 0) == int(beat_number):
                return beat
        except (TypeError, ValueError):
            continue
    raise HTTPException(404, f"beat not found: {beat_number}")


def _normalize_mainline_skill_aspect_ratio(value: object) -> str:
    raw = str(value or "").strip()
    if raw in {"16:9", "16-9", "landscape"}:
        return "16:9"
    if raw in {"", "2:3", "2-3", "portrait"}:
        return "2:3"
    _freezone._raise_skill_error(
        422,
        code="skill_parameter_aspect_ratio_invalid",
        category="validation",
        message="aspect_ratio must be '2:3' or '16:9'",
        user_action_hint="Choose 2:3 or 16:9 before running the skill.",
    )


def _normalize_mainline_frame_quality(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "medium"
    if raw in {"low", "medium", "high"}:
        return raw
    _freezone._raise_skill_error(
        422,
        code="skill_parameter_quality_invalid",
        category="validation",
        message="quality must be low, medium, or high",
        user_action_hint="Choose low, medium, or high before running the skill.",
    )


def _mainline_mode_key_for_aspect(aspect_ratio: object, *, is_sketch: bool) -> str:
    normalized = _normalize_mainline_skill_aspect_ratio(aspect_ratio)
    if normalized == "16:9":
        return "1x1_16-9_sketch" if is_sketch else "1x1_16-9"
    return "1x1_2-3_sketch" if is_sketch else "1x1_2-3"


def _mainline_skill_aspect_ratio_from_image(path: str | Path) -> str:
    from PIL import Image

    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return "2:3"
    if width <= 0 or height <= 0:
        return "2:3"
    ratio = width / height
    portrait_delta = abs(ratio - (2 / 3))
    landscape_delta = abs(ratio - (16 / 9))
    return "16:9" if landscape_delta < portrait_delta else "2:3"


def _skill_run_parameters(body: SkillRunRequest) -> dict[str, object]:
    return dict(body.parameters if isinstance(body.parameters, dict) else {})


def _skill_background_reference_mode(parameters: dict[str, object]) -> str:
    value = str(parameters.get("background_reference_mode") or "").strip()
    if value in {"material_only", "scene_anchor"}:
        return value
    legacy_repair_value = parameters.get("repair_background_perspective")
    if legacy_repair_value is False:
        return "scene_anchor"
    return "material_only"


async def _mainline_single_beat_config(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    episode: int,
    beat: int,
    mode_key: str,
    aspect_ratio: str,
    is_sketch: bool,
) -> dict:
    from novelvideo.api.routes.generation import (
        _build_character_map,
        _episode_from_store_or_none,
        _resolve_render_bool_setting,
        _resolve_render_image_selection,
        _resolve_sketch_image_selection,
        _runtime_prop_menu_with_global_props,
    )
    from novelvideo.project_config import load_project_config

    async with _freezone.freezone_store_for_context_scope(ctx) as store:
        beats = await store.get_beats_as_dicts(int(episode))
        if not beats:
            raise HTTPException(404, f"No beats found for episode {episode}")
        selected_beat = _beat_by_number(beats, int(beat))
        project_config = load_project_config(username, project_name)
        episode_obj = _episode_from_store_or_none(store, int(episode))
        prop_menu = await _runtime_prop_menu_with_global_props(store, episode_obj, beats)
        sketch_colors = (
            store.get_sketch_colors(int(episode)) or {} if hasattr(store, "get_sketch_colors") else {}
        )
        if is_sketch:
            character_map = (
                await _build_character_map(
                    store,
                    beats,
                    username,
                    project_name,
                    episode_num=int(episode),
                    use_detected_identities=False,
                )
                if hasattr(store, "get_all_characters")
                else {}
            )
            image_selection = _resolve_sketch_image_selection(project_config, None)
            return {
                "beats": beats,
                "character_map": character_map,
                "style": project_config.get("visual_style", "chinese_period_drama"),
                "ethnicity": project_config.get("ethnicity", "Chinese"),
                "model": None,
                "image_generation_selection": image_selection,
                "sketch_colors": sketch_colors,
                "prop_menu": prop_menu,
                "direct_sketch_beats": True,
                "beat_numbers": [int(beat)],
                "mode_key": mode_key,
                "aspect_ratio": aspect_ratio,
            }

        character_map = (
            await _build_character_map(
                store,
                [selected_beat],
                username,
                project_name,
                episode_num=int(episode),
                use_detected_identities=True,
            )
            if hasattr(store, "get_all_characters")
            else {}
        )
        image_selection = _resolve_render_image_selection(project_config, None)
        return {
            "beats": beats,
            "character_map": character_map,
            "style": project_config.get("visual_style", "chinese_period_drama"),
            "ethnicity": project_config.get("ethnicity", "Chinese"),
            "model": None,
            "image_generation_selection": image_selection,
            "selected_beat_numbers": [int(beat)],
            "sketch_colors": sketch_colors,
            "prop_menu": prop_menu,
            "sketch_aspect_padding": _resolve_render_bool_setting(
                project_config,
                "sketch_aspect_padding",
                None,
                True,
            ),
            "mode_key": mode_key,
            "aspect_ratio": aspect_ratio,
        }


def _installed_task_projector():
    """Return the installed projector, or ``None`` when nothing is installed.

    Answering this before any store is opened is what keeps the default inline
    deployment on exactly its old code path.
    """
    from novelvideo.ports import get_task_projection
    from novelvideo.ports.local.projection import NoOpTaskProjection

    projector = get_task_projection()
    if isinstance(projector, NoOpTaskProjection):
        return None
    return projector


async def _build_task_projection(
    projector,
    *,
    store,
    username: str,
    project_name: str,
    episode: int,
    task_type: str,
    extra_config: Mapping[str, Any] | None = None,
) -> dict | None:
    """Resolve one task's project-state inputs into a frozen fragment.

    ``extra_config`` carries the request-shaped inputs a task type needs in
    order to know *which* rows to read -- the caller's own request body, not
    project state.  Every mount point goes through this one function so the
    invocation shape stays in a single place.
    """
    config: dict[str, Any] = {
        "username": username,
        "project_name": project_name,
        "episode": int(episode),
    }
    if extra_config:
        config.update(extra_config)
    return await projector.build(store, config, task_type=task_type)


async def _task_projection_payload(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    episode: int,
    task_type: str,
    extra_config: Mapping[str, Any] | None = None,
) -> dict:
    """Build the ``projection`` fragment to merge into an enqueue payload.

    Returns an empty dict when no projector is installed, so a default inline
    deployment enqueues exactly the payload it enqueued before this existed --
    same keys, same bytes, and no extra store opened either.  A backend that
    does not run the task in this process installs a projector and gets the
    inputs carried along with the task instead.
    """
    projector = _installed_task_projector()
    if projector is None:
        return {}
    # A projected task with an explicitly empty requirement set still needs a
    # projection envelope off the home node: it certifies that the enqueue
    # path audited the task as independent of project state.  Do not open the
    # beat SQLite store merely to produce that empty envelope; standalone
    # canvas tasks intentionally have no database dependency.
    from novelvideo.task_backend.projection import PROJECTION_REQUIREMENTS
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        store = None
        if PROJECTION_REQUIREMENTS.get(task_type) != frozenset():
            store = await stack.enter_async_context(
                _freezone.freezone_store_for_context_scope(ctx)
            )
        projection = await _build_task_projection(
            projector,
            store=store,
            username=username,
            project_name=project_name,
            episode=episode,
            task_type=task_type,
            extra_config=extra_config,
        )
        if projection is None:
            return {}
        return {"projection": projection}


async def _enqueue_mainline_beat_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    task_type: str,
    job_id: str,
    payload: dict,
    episode: int = 0,
    beat_num: int | None = None,
    queue_kind: str = "default",
) -> dict:
    """mainline beat 型任务入队样板（payload 前缀 job_id/episode/beat_num/output_dir）。

    与逐行拷贝相同的行为：`beat_num` 缺省时既不进 enqueue 参数也不进 payload，
    保证 standalone（episode=0、无 beat_num）的键集与键序不变。
    """
    beat_extras = {"beat_num": int(beat_num)} if beat_num is not None else {}
    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind=queue_kind,
        episode=int(episode),
        **beat_extras,
        scope=job_id,
        payload={
            "job_id": job_id,
            "episode": int(episode),
            **beat_extras,
            "output_dir": str(project_dir),
            **payload,
        },
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        beat_num=beat_num,
        scope=job_id,
    )


async def _start_or_enqueue_mainline_sketch_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    beat_payload: dict | None,
    background_url: str,
    aspect_ratio: str = "2:3",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_sketch_from_context"
    mode_key = _mainline_mode_key_for_aspect(aspect_ratio, is_sketch=True)
    base_paths = _freezone._resolve_url_list(project_dir, [background_url])
    if not base_paths:
        raise HTTPException(400, "background_url is required")
    for path_text in base_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"base file not found: {path_text}")
    config = await _freezone._mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio=_normalize_mainline_skill_aspect_ratio(aspect_ratio),
        is_sketch=True,
    )
    effective_beat = dict(beat_payload or {})
    if effective_beat:
        effective_beat["episode_number"] = int(episode)
        effective_beat["beat_number"] = int(beat)
        config["beats"] = [effective_beat]
    config["promote_direct_sketch"] = False
    scene_ref = (effective_beat.get("scene_ref") or {}) if effective_beat else {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    config["canvas_scene_refs"] = [
        {
            "beat_number": int(beat),
            "image_path": base_paths[0],
            "base_id": scene_id or "canvas background",
            "label": str((task_display or {}).get("source_label") or "背景"),
            "source_level": "selected_background_image",
        }
    ]
    job_id = _freezone._new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "生成草图",
        "display_name": "生成草图",
        **(task_display or {}),
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=int(episode),
            task_type=task_type,
        )
        return await _enqueue_mainline_beat_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            episode=int(episode),
            beat_num=int(beat),
            payload={
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
                **projection_payload,
            },
        )

    _freezone._raise_project_context_required(task_type)


def _raise_if_frame_identity_detection_missing(
    beats: list[dict],
    *,
    standalone_beat_context: bool = False,
) -> None:
    """Reject before enqueueing what the render worker would reject anyway."""
    detection_error = render_ai_detection_error(
        beats,
        standalone_beat_context=standalone_beat_context,
    )
    if detection_error:
        _freezone._raise_skill_error(
            422,
            code="render_identity_detection_required",
            category="validation",
            message=detection_error,
        )


async def _start_or_enqueue_mainline_frame_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    beat_payload: dict | None,
    sketch_url: str,
    reference_urls: list[str],
    extra_reference_urls: list[str] | None = None,
    identity_references: list[dict] | None = None,
    prop_references: list[dict] | None = None,
    aspect_ratio: str = "2:3",
    quality: str = "medium",
    background_reference_mode: str = "material_only",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_frame_from_context"
    sketch_paths = _freezone._resolve_url_list(project_dir, [sketch_url])
    if not sketch_paths:
        raise HTTPException(400, "sketch_url is required")
    for path_text in sketch_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"sketch file not found: {path_text}")
    inferred_aspect_ratio = _mainline_skill_aspect_ratio_from_image(sketch_paths[0])
    mode_key = _mainline_mode_key_for_aspect(inferred_aspect_ratio, is_sketch=False)
    reference_paths = _freezone._resolve_url_list(project_dir, reference_urls)
    extra_reference_paths = _freezone._resolve_url_list(project_dir, extra_reference_urls or [])
    resolved_identity_refs: list[dict] = []
    for item in identity_references or []:
        image_paths = _freezone._resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_identity_refs.append({**item, "image_path": image_paths[0]})
    resolved_prop_refs: list[dict] = []
    for item in prop_references or []:
        image_paths = _freezone._resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_prop_refs.append({**item, "image_path": image_paths[0]})
    for path_text in [
        *reference_paths,
        *extra_reference_paths,
        *[str(item["image_path"]) for item in resolved_identity_refs],
        *[str(item["image_path"]) for item in resolved_prop_refs],
    ]:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    config = await _freezone._mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio=inferred_aspect_ratio,
        is_sketch=False,
    )
    effective_beat = dict(beat_payload or {})
    if effective_beat:
        effective_beat["episode_number"] = int(episode)
        effective_beat["beat_number"] = int(beat)
        config["beats"] = [effective_beat]
    _raise_if_frame_identity_detection_missing(
        [_beat_by_number(config.get("beats") or [], int(beat))]
    )
    config["promote_selected_regen"] = False
    config["image_quality"] = _normalize_mainline_frame_quality(quality)
    config["canvas_sketch_paths"] = {str(int(beat)): sketch_paths[0]}
    canvas_refs: list[dict] = []
    scene_ref = (effective_beat.get("scene_ref") or {}) if effective_beat else {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    if reference_paths:
        background_ref = {
            "beat_number": int(beat),
            "image_path": reference_paths[0],
            "base_id": scene_id or "canvas background",
            "label": "背景",
            "source_level": "selected_background_image",
        }
        if background_reference_mode == "material_only":
            background_ref["reference_mode"] = "material_only"
        canvas_refs.append(background_ref)
    generic_ref_index = 1
    for item in resolved_identity_refs:
        identity_id = str(item.get("identity_id") or "").strip()
        if identity_id:
            config.setdefault("canvas_identity_refs", []).append(
                {
                    "beat_number": int(beat),
                    "identity_id": identity_id,
                    "image_path": item["image_path"],
                    "reference_mode": (
                        "portrait_only"
                        if str(item.get("slot_kind") or "") == "portrait"
                        else "composite"
                    ),
                }
            )
            continue
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for item in resolved_prop_refs:
        prop_id = str(item.get("prop_id") or "").strip()
        if prop_id:
            config.setdefault("canvas_prop_refs", []).append(
                {
                    "beat_number": int(beat),
                    "prop_id": prop_id,
                    "image_path": item["image_path"],
                    "source_level": "canvas_prop_reference_image",
                }
            )
            continue
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for path_text in extra_reference_paths:
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": path_text,
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    if canvas_refs:
        config["canvas_scene_refs"] = canvas_refs
    job_id = _freezone._new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "渲染分镜",
        "display_name": "渲染分镜",
        **(task_display or {}),
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=int(episode),
            task_type=task_type,
        )
        return await _enqueue_mainline_beat_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            episode=int(episode),
            beat_num=int(beat),
            payload={
                "mode_key": mode_key,
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
                **projection_payload,
            },
        )

    _freezone._raise_project_context_required(task_type)


def _standalone_beat_context_frame_config(
    *,
    username: str,
    project_name: str,
    beat_payload: dict | None,
    mode_key: str,
    aspect_ratio: str,
    quality: str,
) -> dict:
    from novelvideo.api.routes.generation import (
        _resolve_render_bool_setting,
        _resolve_render_image_selection,
    )
    from novelvideo.project_config import load_project_config

    project_config = load_project_config(username, project_name)
    beat = dict(beat_payload or {})
    beat.pop("_source_beat_context", None)
    if beat:
        beat["episode_number"] = 0
        beat["beat_number"] = 0
        beat["panel_index"] = 0
    return {
        "standalone_beat_context": True,
        "beats": [beat] if beat else [],
        "character_map": _standalone_beat_context_character_map(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "style": project_config.get("visual_style", "chinese_period_drama"),
        "ethnicity": project_config.get("ethnicity", "Chinese"),
        "model": None,
        "image_generation_selection": _resolve_render_image_selection(project_config, None),
        "selected_panel_indices": [0],
        "sketch_colors": _standalone_beat_context_sketch_colors(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "prop_marker_colors": _standalone_beat_context_prop_marker_colors(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "prop_menu": [
            {"prop_id": prop_id, "name": prop_id}
            for prop_id in _list_text_values(
                ((beat_payload or {}).get("_source_beat_context") or {}).get("detected_props")
            )
        ],
        "sketch_aspect_padding": _resolve_render_bool_setting(
            project_config,
            "sketch_aspect_padding",
            None,
            True,
        ),
        "mode_key": mode_key,
        "aspect_ratio": aspect_ratio,
        "promote_selected_regen": False,
        "image_quality": _normalize_mainline_frame_quality(quality),
    }


async def _start_or_enqueue_standalone_frame_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    beat_input: ResolvedSkillInput,
    sketch_url: str,
    reference_urls: list[str],
    extra_reference_urls: list[str] | None = None,
    identity_references: list[dict] | None = None,
    prop_references: list[dict] | None = None,
    quality: str = "medium",
    background_reference_mode: str = "material_only",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_frame_from_context"
    sketch_paths = _freezone._resolve_url_list(project_dir, [sketch_url])
    if not sketch_paths:
        raise HTTPException(400, "sketch_url is required")
    for path_text in sketch_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"sketch file not found: {path_text}")
    inferred_aspect_ratio = _mainline_skill_aspect_ratio_from_image(sketch_paths[0])
    mode_key = _mainline_mode_key_for_aspect(inferred_aspect_ratio, is_sketch=False)
    reference_paths = _freezone._resolve_url_list(project_dir, reference_urls)
    extra_reference_paths = _freezone._resolve_url_list(project_dir, extra_reference_urls or [])
    resolved_identity_refs: list[dict] = []
    for item in identity_references or []:
        image_paths = _freezone._resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_identity_refs.append({**item, "image_path": image_paths[0]})
    resolved_prop_refs: list[dict] = []
    for item in prop_references or []:
        image_paths = _freezone._resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_prop_refs.append({**item, "image_path": image_paths[0]})
    for path_text in [
        *reference_paths,
        *extra_reference_paths,
        *[str(item["image_path"]) for item in resolved_identity_refs],
        *[str(item["image_path"]) for item in resolved_prop_refs],
    ]:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    source_beat_context = dict((beat_input.beat_context if beat_input else None) or {})
    beat_payload = {
        **_skill_beat_context_as_prompt_beat(beat_input),
        "_source_beat_context": source_beat_context,
    }
    config = _standalone_beat_context_frame_config(
        username=username,
        project_name=project_name,
        beat_payload=beat_payload,
        mode_key=mode_key,
        aspect_ratio=inferred_aspect_ratio,
        quality=quality,
    )
    _raise_if_frame_identity_detection_missing(
        config.get("beats") or [],
        standalone_beat_context=True,
    )
    config["canvas_sketch_paths"] = {"0": sketch_paths[0]}
    canvas_refs: list[dict] = []
    scene_ref = beat_payload.get("scene_ref") or {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    if reference_paths:
        background_ref = {
            "panel_index": 0,
            "image_path": reference_paths[0],
            "base_id": scene_id or "canvas background",
            "label": "背景",
            "source_level": "selected_background_image",
        }
        if background_reference_mode == "material_only":
            background_ref["reference_mode"] = "material_only"
        canvas_refs.append(background_ref)
    generic_ref_index = 1
    for item in resolved_identity_refs:
        identity_id = str(item.get("identity_id") or "").strip()
        if identity_id:
            config.setdefault("canvas_identity_refs", []).append(
                {
                    "panel_index": 0,
                    "identity_id": identity_id,
                    "image_path": item["image_path"],
                    "reference_mode": (
                        "portrait_only"
                        if str(item.get("slot_kind") or "") == "portrait"
                        else "composite"
                    ),
                }
            )
            continue
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for item in resolved_prop_refs:
        prop_id = str(item.get("prop_id") or "").strip()
        if prop_id:
            config.setdefault("canvas_prop_refs", []).append(
                {
                    "panel_index": 0,
                    "prop_id": prop_id,
                    "image_path": item["image_path"],
                    "source_level": "canvas_prop_reference_image",
                }
            )
            continue
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for path_text in extra_reference_paths:
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": path_text,
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    if canvas_refs:
        config["canvas_scene_refs"] = canvas_refs
    job_id = _freezone._new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "渲染分镜",
        "display_name": "渲染分镜",
        **(task_display or {}),
    }
    if ctx is not None:
        projection_payload = await _freezone._task_projection_payload(
            ctx=ctx,
            username=username,
            project_name=project_name,
            episode=0,
            task_type=task_type,
        )
        return await _enqueue_mainline_beat_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type=task_type,
            job_id=job_id,
            payload={
                "mode_key": mode_key,
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
                **projection_payload,
            },
        )

    _freezone._raise_project_context_required(task_type)


async def _start_or_enqueue_mainline_direct_sketch_task(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    from novelvideo.api.routes.generation import _director_control_scope

    task_type = "sketch_generation"
    scope = _director_control_scope(int(episode), int(beat))
    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        beat_num=int(beat),
        scope=scope,
        payload={
            "task_kind": "director_control_to_sketch",
            "episode": int(episode),
            "beat_num": int(beat),
            "output_dir": str(project_dir),
            "state_dir": str(ctx.state_dir),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "导演合成图转草图",
            "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
            "source_label": "导演合成图",
            "target_label": "当前草图",
            **(task_display or {}),
        },
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=scope,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        beat_num=int(beat),
        scope=scope,
    )


async def _start_or_enqueue_mainline_director_control_sketch_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode: int,
    beat: int,
    director_combined_url: str,
    aspect_ratio: str = "2:3",
    canvas_id: str | None,
    node_id: str | None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_director_control_sketch"
    source_paths = _freezone._resolve_url_list(project_dir, [director_combined_url])
    if not source_paths:
        raise HTTPException(400, "director_combined_url is required")
    source_path = Path(source_paths[0])
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(404, f"director combined file not found: {source_path}")
    job_id = _freezone._new_job_id()
    projection_payload = await _freezone._task_projection_payload(
        ctx=ctx,
        username=ctx.owner_username,
        project_name=ctx.project_name,
        episode=int(episode),
        task_type=task_type,
    )
    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        beat_num=int(beat),
        scope=job_id,
        payload={
            "job_id": job_id,
            "episode": int(episode),
            "beat_num": int(beat),
            "project_dir": str(project_dir),
            "state_dir": str(ctx.state_dir),
            "control_frame_path": source_path.as_posix(),
            "mode_key": _mainline_mode_key_for_aspect(aspect_ratio, is_sketch=True),
            "aspect_ratio": _normalize_mainline_skill_aspect_ratio(aspect_ratio),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "导演合成图转草图",
            "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
            "source_label": "导演合成图",
            "target_label": "当前草图候选",
            **(task_display or {}),
            **projection_payload,
        },
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        beat_num=int(beat),
        scope=job_id,
    )


async def _start_or_enqueue_mainline_beat_sketch_task(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    canvas_id: str | None,
    node_id: str | None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "sketch_generation"
    mode_key = "1x1_2-3_sketch"
    scope = selection_scope(mode_key, [int(beat)])
    config = await _freezone._mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio="2:3",
        is_sketch=True,
    )
    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        scope=scope,
        payload={
            "episode": int(episode),
            "output_dir": str(project_dir),
            "config": config,
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "生成草图",
            "display_name": f"生成草图 · EP{episode} / Beat {beat}",
            "source_label": "Beat 上下文",
            "target_label": "当前草图",
            **(task_display or {}),
        },
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=scope,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        scope=scope,
    )


async def _start_or_enqueue_mainline_scene_360_candidate_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_id: str,
    description: str | None,
    master_url: str,
    reverse_url: str | None,
    model: str | None,
    image_size: str | None,
    quality: str | None,
    canvas_id: str | None,
    node_id: str | None,
    catalog_id: str | None = None,
    execution_catalog_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    return await _start_or_enqueue_mainline_scene_360_task(
        ctx=ctx,
        project_dir=project_dir,
        scene_id=scene_id,
        description=description,
        master_url=master_url,
        reverse_url=reverse_url,
        model=model,
        image_size=image_size,
        quality=quality,
        canvas_id=canvas_id,
        node_id=node_id,
        catalog_id=catalog_id,
        execution_catalog_id=execution_catalog_id,
        auto_commit=False,
        task_display=task_display,
    )


async def _start_or_enqueue_mainline_scene_360_task(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_id: str,
    description: str | None = None,
    master_url: str,
    reverse_url: str | None,
    model: str | None,
    image_size: str | None,
    quality: str | None,
    canvas_id: str | None,
    node_id: str | None,
    catalog_id: str | None = None,
    execution_catalog_id: str | None = None,
    auto_commit: bool = True,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "stage_asset"
    step = "pano_from_master"
    await _freezone._require_scoped_media_model(
        "image",
        catalog_id or model or FREEZONE_DEFAULT_IMAGE_MODEL,
        requester_user_id=ctx.requester_user_id,
    )
    master_paths = _freezone._resolve_url_list(project_dir, [master_url])
    if not master_paths:
        raise HTTPException(400, "master_url is required")
    for path_text in master_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"master file not found: {path_text}")
    reverse_paths = _freezone._resolve_url_list(project_dir, [reverse_url] if reverse_url else [])
    for path_text in reverse_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reverse master file not found: {path_text}")
    job_id = (
        task_config_scope("stage_asset", {"scene": scene_id, "step": step})
        if auto_commit
        else _freezone._new_job_id()
    )
    artifact_dir = outputs_dir(project_dir, "mainline_scene_360") / job_id
    resolved_provider, resolved_model = _split_provider_and_model(
        "newapi",
        model or FREEZONE_DEFAULT_IMAGE_MODEL,
    )
    if not execution_catalog_id:
        from novelvideo.stage_asset_tasks import (
            Scene360ImageModelSelectionError,
            resolve_scene_360_image_model,
        )

        try:
            resolve_scene_360_image_model(
                resolved_provider or "newapi",
                resolved_model or model or FREEZONE_DEFAULT_IMAGE_MODEL,
            )
        except Scene360ImageModelSelectionError as exc:
            raise HTTPException(400, str(exc)) from exc

    queued = await _freezone.get_task_backend().enqueue_project_task(
        ctx,
        product_surface="freezone",
        task_type=task_type,
        queue_kind="world",
        episode=0,
        scope=job_id,
        payload={
            "scene_name": scene_id,
            "step": step,
            "params": {
                "description": (description or "").strip() or _build_scene_360_prompt(scene_id),
                "provider": resolved_provider or "newapi",
                "model": resolved_model or model or FREEZONE_DEFAULT_IMAGE_MODEL,
                "image_size": image_size or MAINLINE_SCENE_360_IMAGE_SIZE,
                "quality": quality or "medium",
                "master_path": master_paths[0],
                "reverse_master_path": reverse_paths[0] if reverse_paths else "",
                "artifact_dir": str(artifact_dir) if not auto_commit else "",
                "update_manifest": auto_commit,
            },
            "project_dir": str(project_dir),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            **(
                {
                    "scene_360_model_authority": {
                        "kind": "catalog",
                        "catalog_id": execution_catalog_id,
                        "provider": resolved_provider or "newapi",
                        "model": resolved_model
                        or model
                        or FREEZONE_DEFAULT_IMAGE_MODEL,
                    }
                }
                if execution_catalog_id
                else {}
            ),
            "task_family": "mainline_skill",
            "task_label": "生成 360 全景",
            "display_name": f"生成 360 全景 · {scene_id}",
            "source_label": "场景 Master + Reverse",
            "target_label": "360 全景",
            **(task_display or {}),
        },
    )
    return _freezone._project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        scope=job_id,
    )


def _cap_mainline_scene_360_image_size(requested: str | None) -> str:
    """360 全景取 min(前端档位, 2K)。

    上限保留：主线 360 slot 一直按 2K 产出，4K 只是徒增成本。
    但低于上限的档位必须原样透传 —— 画布面板按媒体模型目录里那一档报价，
    目录只给 1K 的模型如果被抬到 2K，报价与执行、乃至与网关能力都对不上。
    """
    value = _IMAGE_SIZE_TIER_BY_CASEFOLD.get((requested or "").strip().casefold())
    if value is None:
        return MAINLINE_SCENE_360_IMAGE_SIZE
    if _IMAGE_SIZE_TIERS.index(value) > _IMAGE_SIZE_TIERS.index(MAINLINE_SCENE_360_IMAGE_SIZE):
        return MAINLINE_SCENE_360_IMAGE_SIZE
    return value
