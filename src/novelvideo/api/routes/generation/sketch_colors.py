"""generation.sketch_colors 子模块（自 api/routes/generation.py 拆分）。"""

import re
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation as _generation
from novelvideo.ports import get_usage_meter

router = APIRouter()


# ---------------------------------------------------------------------------
# 草图配色 + AI 颜色检测
# ---------------------------------------------------------------------------


@router.post("/projects/{project}/episodes/{episode_num}/sketches/assign-colors")
async def assign_sketch_colors(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """为本集出场身份和全局道具分配共享颜色。"""
    from novelvideo.generators.episode_optimizer import EpisodeOptimizer
    from novelvideo.generators.grid_generation import _global_prop_marker_colors

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name

    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:

        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        characters = store.get_all_characters()
        char_dicts = [
            {
                "name": c.name,
                "identities": [
                    {"identity_id": id_.identity_id, "identity_name": id_.identity_name}
                    for id_ in (c.identities or [])
                ],
            }
            for c in characters
        ]

        previous_colors = dict(store.get_sketch_colors(episode_num) or {})
        colors = EpisodeOptimizer.assign_sketch_colors(
            char_dicts,
            episode_beats=beats,
            existing_colors=previous_colors,
        )

        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        runtime_prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        previous_prop_marker_colors = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=previous_colors,
        )
        prop_marker_colors = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=colors,
            assign_missing=True,
        )
        if not colors and not prop_marker_colors:
            return {"ok": False, "error": "No identity or global prop markers found in beats"}

        try:
            if colors:
                await store.set_sketch_colors(episode_num, colors)
            if prop_marker_colors and runtime_prop_menu:
                for item in runtime_prop_menu:
                    if not isinstance(item, dict):
                        continue
                    prop_id = str(item.get("prop_id") or item.get("name") or "").strip()
                    if prop_id in prop_marker_colors:
                        item["marker_color"] = prop_marker_colors[prop_id]
                # Marker colours are written back into the menu this request loaded
                # earlier, so a whole-row write here discards whatever scene, prop
                # or identity planning stored meanwhile.
                await store.patch_episode(episode_num, prop_menu=runtime_prop_menu)
        except Exception:
            pass

        previous_marker_colors = {
            **{f"identity:{key}": value for key, value in previous_colors.items()},
            **{f"prop:{key}": value for key, value in previous_prop_marker_colors.items()},
        }
        current_marker_colors = {
            **{f"identity:{key}": value for key, value in colors.items()},
            **{f"prop:{key}": value for key, value in prop_marker_colors.items()},
        }
        should_clean_sketches = _generation._color_assignment_requires_full_sketch_clean(
            previous_marker_colors,
            current_marker_colors,
        )
        if should_clean_sketches:
            from novelvideo.utils.path_resolver import PathResolver

            output_dir = resolved.output_dir
            await run_in_threadpool(PathResolver(output_dir, episode_num).clean_sketches)

        return {
            "ok": True,
            "data": {
                "colors": colors,
                "count": len(colors),
                "prop_colors": prop_marker_colors,
                "prop_count": len(prop_marker_colors),
            },
        }


@router.post("/projects/{project}/episodes/{episode_num}/sketches/detect-identities")
async def detect_sketch_identities(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """AI 视觉识别草图中出现的身份/道具颜色标记。"""
    from novelvideo.agents.global_video_optimizer import detect_identities_by_ai
    from novelvideo.generators.grid_splitter import combine_to_grid
    from novelvideo.generators.grid_generation import _global_prop_marker_colors
    from novelvideo.models import (
        NO_CHARACTER_MARKER,
        NO_PROP_MARKER,
        real_detected_identities,
        real_detected_props,
        split_detected_marker_keys,
    )

    resolved = await _generation._resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name

    async with _generation.generation_store_scope(resolved.ctx, username, project_name) as store:

        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        color_map = dict(store.get_sketch_colors(episode_num) or {})
        script_data_for_fallback = None
        if not color_map:
            try:
                script_data_for_fallback = await store.get_script_as_dict(episode_num)
                color_map = dict((script_data_for_fallback or {}).get("sketch_colors") or {})
            except Exception:
                script_data_for_fallback = None
        if not color_map:
            return {"ok": False, "error": "No sketch colors assigned. Call assign-colors first"}

        episode_obj = _generation._episode_from_store_or_none(store, episode_num)
        runtime_prop_menu = await _generation._runtime_prop_menu_with_global_props(store, episode_obj, beats)
        if not runtime_prop_menu:
            if script_data_for_fallback is None:
                try:
                    script_data_for_fallback = await store.get_script_as_dict(episode_num)
                except Exception:
                    script_data_for_fallback = None
            runtime_prop_menu = list((script_data_for_fallback or {}).get("prop_menu") or [])
        prop_color_map = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=color_map,
        )

        # 反转: "#HEX COLOR_NAME" → marker_id
        color_identity_map = {v: k for k, v in color_map.items()}
        color_identity_map.update({v: k for k, v in prop_color_map.items()})

        # 收集草图文件
        project_dir = resolved.project_dir
        sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
        frame_items: list[tuple[int, str]] = []
        known_beats = {
            int(b.get("beat_number", 0)) for b in beats if int(b.get("beat_number", 0) or 0) > 0
        }
        beat_pattern = re.compile(r"beat_(\d+)\.(png|jpg)$", re.IGNORECASE)
        if sketches_dir.exists():
            for candidate in sorted(sketches_dir.iterdir()):
                if not candidate.is_file():
                    continue
                match = beat_pattern.match(candidate.name)
                if not match:
                    continue
                beat_number = int(match.group(1))
                if known_beats and beat_number not in known_beats:
                    continue
                frame_items.append((beat_number, str(candidate)))

        if not frame_items:
            return {"ok": False, "error": "No sketches found"}

        def _grid_shape(count: int) -> tuple[int, int]:
            if count <= 1:
                return 1, 1
            if count <= 4:
                return 2, 2
            if count <= 9:
                return 3, 3
            if count <= 16:
                return 4, 4
            return 5, 5

        grid_dir = project_dir / "grids" / f"ep{episode_num:03d}" / "sketch"
        grid_dir.mkdir(parents=True, exist_ok=True)

        usage_meter = get_usage_meter()
        ctx = getattr(resolved, "ctx", None)
        project_id = str(getattr(ctx, "project_id", "") or "")
        # [aipdd fork delta] WP-D: 额度预约已拆除，只保留 LLM 用量归因的观测上下文。
        billing_metadata: dict[str, Any] = {"source": "sync_api"}

        detections: dict[int, list[str]] = {}
        try:
            usage_meter.set_llm_usage_context(
                _generation._requester_user_id(resolved, user),
                project_id=project_id,
                resource_kind="sketch",
                billing_metadata=billing_metadata,
            )
            batch_size = 25
            for batch_idx in range(0, len(frame_items), batch_size):
                batch = frame_items[batch_idx : batch_idx + batch_size]
                rows, cols = _grid_shape(len(batch))
                grid_path = (
                    grid_dir / f"_ai_detect_grid_{rows}x{cols}_part{batch_idx // batch_size + 1}.png"
                )
                combine_to_grid([path for _, path in batch], grid_path, rows=rows, cols=cols)
                batch_result = await detect_identities_by_ai(
                    sketch_image_paths=[str(grid_path)],
                    color_identity_map=color_identity_map,
                    total_beats=len(batch),
                )
                ordered_batch = sorted(batch, key=lambda item: item[0])
                for local_idx, marker_ids in (batch_result or {}).items():
                    try:
                        panel_index = int(local_idx)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= panel_index <= len(ordered_batch):
                        beat_number = ordered_batch[panel_index - 1][0]
                        detections[beat_number] = list(marker_ids or [])

            for beat_number, _path in frame_items:
                detections.setdefault(beat_number, [])

            characters = store.get_all_characters()
            identity_detections: dict[int, list[str]] = {}
            prop_detections: dict[int, list[str]] = {}
            allowed_prop_ids = set(prop_color_map)
            for beat_number, keys in detections.items():
                det_ids, det_props = split_detected_marker_keys(
                    keys,
                    beats,
                    characters,
                    allowed_prop_ids=allowed_prop_ids,
                )
                identity_detections[beat_number] = det_ids or [NO_CHARACTER_MARKER]
                prop_detections[beat_number] = det_props or [NO_PROP_MARKER]

            # 持久化
            await store.set_beat_detected_identities(episode_num, identity_detections)
            await store.set_beat_detected_props(episode_num, prop_detections)

        except Exception as e:
            return {"ok": False, "error": f"AI detection failed: {e}"}
        finally:
            usage_meter.clear_llm_usage_context()

        # 转换 key 为字符串（JSON 兼容）
        str_identity_detections = {str(k): v for k, v in identity_detections.items()}
        str_prop_detections = {str(k): v for k, v in prop_detections.items()}
        total_ids = sum(len(real_detected_identities(v)) for v in identity_detections.values())
        total_props = sum(len(real_detected_props(v)) for v in prop_detections.values())

        return {
            "ok": True,
            "data": {
                "detections": str_identity_detections,
                "identity_detections": str_identity_detections,
                "prop_detections": str_prop_detections,
                "total_beats": len(beats),
                "total_identities": total_ids,
                "total_props": total_props,
                "review_message": (
                    "AI 已完成出场身份/道具识别，请核对每个 beat；"
                    "漏识别可在“更多”的出场身份/出场道具中补选。"
                ),
            },
        }
