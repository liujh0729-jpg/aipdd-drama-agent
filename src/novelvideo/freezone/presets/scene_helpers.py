"""场景提示内容与项目风格辅助（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.generators.scene_reference_images import build_scene_reference_prompt
from novelvideo.models import NovelScene

from .roles import SCENE_PLY_INPUT_ROLES


def _scene_prompt_content(scene_id: str, meta: dict[str, Any]) -> str:
    env_prompt = str(meta.get("environment_prompt") or "").strip()
    description = str(meta.get("description") or "").strip()
    scene_type = str(meta.get("scene_type") or "").strip()
    lines = [f"Scene: {scene_id}"]
    if scene_type:
        lines.append(f"Type: {scene_type}")
    if env_prompt:
        lines.extend(["", "[Environment Prompt]", env_prompt])
    if description and description != env_prompt:
        lines.extend(["", "[Description]", description])
    if not env_prompt and not description:
        lines.extend(["", "No scene prompt found; use the scene id as fallback intent."])
    style_block = _scene_style_block_from_meta(meta)
    if style_block:
        lines.extend(["", style_block])
    return "\n".join(lines)


def _scene_style_block_from_meta(meta: dict[str, Any]) -> str:
    style_name = str(meta.get("style_name") or "").strip()
    style_prompt = str(meta.get("style_prompt") or "").strip()
    avoid_instructions = str(meta.get("avoid_instructions") or "").strip()
    if not style_name and not style_prompt and not avoid_instructions:
        return ""
    parts = ["PROJECT STYLE PRESET:"]
    if style_name:
        parts.append(f"- Style id/name: {style_name}")
    if style_prompt:
        parts.append("- Positive style directives:")
        parts.append(style_prompt)
    if avoid_instructions:
        parts.append("- Negative / avoid directives:")
        parts.append(avoid_instructions)
    parts.append("- Apply this style preset consistently to the scene asset.")
    return "\n".join(parts)


def _project_style_meta(username: str, project: str, project_dir: Path) -> dict[str, str]:
    try:
        from novelvideo.config import IMAGE_DEFAULT_STYLE, get_style_preset
        from novelvideo.project_config import load_project_config

        project_config = load_project_config(username, project)
        style_id = str(project_config.get("visual_style") or IMAGE_DEFAULT_STYLE).strip()
        preset = get_style_preset(
            style_id, username=username, project=project, project_dir=str(project_dir)
        )
        label = str(preset.get("label") or style_id).strip()
        style_name = f"{label} ({style_id})" if label and label != style_id else style_id
        return {
            "style_name": style_name,
            "style_prompt": str(preset.get("style_instructions") or "").strip(),
            "avoid_instructions": str(preset.get("avoid_instructions") or "").strip(),
        }
    except Exception:
        return {}


def _scene_from_prompt_meta(scene_id: str, meta: dict[str, Any]) -> NovelScene:
    # Prefer the raw environment prompt: the composed effective prompt would be
    # re-composed (and partially dropped) by build_scene_effective_prompt.
    environment_prompt = str(
        meta.get("raw_environment_prompt") or meta.get("environment_prompt") or ""
    ).strip()
    return NovelScene(
        name=scene_id,
        scene_type=str(meta.get("scene_type") or "interior").strip() or "interior",
        base_scene_id=str(meta.get("base_scene_id") or "").strip(),
        variant_id=str(meta.get("variant_id") or "").strip(),
        time_of_day=str(meta.get("time_of_day") or "").strip(),
        description=str(meta.get("description") or "").strip(),
        environment_prompt=environment_prompt,
        variant_prompt=str(meta.get("variant_prompt") or "").strip(),
    )


def _base_scene_from_prompt_meta(meta: dict[str, Any]) -> NovelScene | None:
    base_scene_id = str(meta.get("base_scene_id") or "").strip()
    base_environment_prompt = str(meta.get("base_environment_prompt") or "").strip()
    base_description = str(meta.get("base_description") or "").strip()
    if not base_scene_id or not (base_environment_prompt or base_description):
        return None
    return NovelScene(
        name=base_scene_id,
        scene_type=str(meta.get("base_scene_type") or "interior").strip() or "interior",
        environment_prompt=base_environment_prompt,
        description=base_description,
    )


def _scene_reference_prompt_content(
    scene_id: str,
    meta: dict[str, Any],
    *,
    kind: str,
    has_master_reference: bool = False,
) -> str:
    scene = _scene_from_prompt_meta(scene_id, meta)
    return build_scene_reference_prompt(
        kind,  # type: ignore[arg-type]
        scene,
        style_name=str(meta.get("style_name") or "").strip(),
        style_prompt=str(meta.get("style_prompt") or "").strip(),
        avoid_instructions=str(meta.get("avoid_instructions") or "").strip(),
        has_master_reference=has_master_reference,
        base_scene=_base_scene_from_prompt_meta(meta),
    )


def _scene_ply_input_roles(role: str, ref: dict[str, Any]) -> tuple[str, ...] | None:
    if role != "scene_3gs_active_ply":
        return SCENE_PLY_INPUT_ROLES.get(role)

    rel_path = str(ref.get("rel_path") or "").lower()
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    ply_kind = str(meta.get("ply_kind") or "").strip().lower()
    source_hint = f"{ply_kind} {rel_path}"
    if "master_sharp" in source_hint or ply_kind == "master":
        return ("scene_master",)
    if "reverse_sharp" in source_hint or ply_kind == "reverse":
        return ("scene_reverse_master",)
    if "pano_sharp" in source_hint or "pano_depth" in source_hint or ply_kind == "pano":
        return ("scene_director_pano_360", "scene_360")
    if "custom" in source_hint or ply_kind == "custom":
        return ("scene_master", "scene_director_pano_360", "scene_360")
    return ("scene_master", "scene_reverse_master", "scene_director_pano_360", "scene_360")
