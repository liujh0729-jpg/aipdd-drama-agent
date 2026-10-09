"""Character reference-map helpers shared by API, UI, and generation pipelines."""

from __future__ import annotations

from pathlib import Path


def build_character_map_for_grid(
    grid_beats: list[dict],
    characters: list[dict],
    user_output_dir: Path,
    project: str,
    *,
    sketch_colors: dict[str, str] | None = None,
    use_detected_identities: bool = False,
) -> dict[str, dict]:
    """Build the character reference map used by sketch/render grid generation.

    Args:
        use_detected_identities: When true, collect identities from detected sketch colors
            for render; otherwise collect identities from {{identity}} markers for sketch.
    """
    from novelvideo.generators.prompt_builder import PromptComponents
    from novelvideo.models import real_detected_identities

    character_map: dict[str, dict] = {}
    if use_detected_identities:
        seen = []
        for beat in grid_beats:
            for detected_id in real_detected_identities(beat.get("detected_identities") or []):
                name = detected_id.split("_", 1)[0] if "_" in detected_id else detected_id
                if name not in seen and any(c.get("name") == name for c in characters):
                    seen.append(name)
        collected_chars = seen
    else:
        char_dict = {c.get("name"): None for c in characters if c.get("name")}
        collected_chars = PromptComponents.extract_panel_characters(grid_beats, char_dict)

    for char_name in collected_chars:
        for char in characters:
            if char.get("name") != char_name:
                continue

            char_assets_dir = user_output_dir / project / "assets" / "characters" / char_name
            ref_path = ""

            identity_ids: list[str] = []
            if use_detected_identities:
                for beat in grid_beats:
                    for detected_id in real_detected_identities(
                        beat.get("detected_identities") or []
                    ):
                        if detected_id.startswith(char_name + "_") and detected_id not in identity_ids:
                            identity_ids.append(detected_id)
            else:
                from novelvideo.models import extract_char_identities_from_markers

                for beat in grid_beats:
                    visual_description = beat.get("visual_description", "")
                    for name, identity_id in extract_char_identities_from_markers(
                        visual_description,
                        strict=False,
                    ).items():
                        if name == char_name and identity_id and identity_id not in identity_ids:
                            identity_ids.append(identity_id)

            identity_id = identity_ids[0] if identity_ids else None

            identity_appearances: dict[str, str] = {}
            identity_sketch_colors: dict[str, str] = {}
            for current_identity_id in identity_ids:
                for identity in char.get("identities", []):
                    if identity.get("identity_id") == current_identity_id:
                        suffix = (
                            current_identity_id.split("_", 1)[1]
                            if "_" in current_identity_id
                            else current_identity_id
                        )
                        identity_appearances[suffix] = identity.get("appearance_details", "")
                        sketch_color = ""
                        if sketch_colors:
                            sketch_color = sketch_colors.get(identity.get("identity_id", ""), "")
                        if sketch_color:
                            identity_sketch_colors[suffix] = sketch_color
                        break

            appearance_details = (
                identity_appearances.get(
                    identity_id.split("_", 1)[1] if identity_id and "_" in identity_id else "",
                    "",
                )
                if identity_id
                else ""
            )
            if not appearance_details:
                appearance_details = char.get("appearance_details", "")

            identity_face_prompt = ""
            identity_portrait_path = ""
            primary_body_type = ""
            if identity_id:
                for identity in char.get("identities", []):
                    if identity.get("identity_id") == identity_id:
                        identity_face_prompt = identity.get("face_prompt", "")
                        identity_portrait_path = identity.get("portrait_image", "")
                        primary_body_type = identity.get("body_type", "")
                        break

            is_identity = False
            if identity_id:
                identity_name = identity_id.split("_", 1)[1] if "_" in identity_id else identity_id
                identity_path = char_assets_dir / "identities" / f"{identity_name}.png"
                if identity_path.exists():
                    ref_path = str(identity_path)
                    is_identity = True

            if not ref_path:
                if identity_face_prompt and identity_id:
                    identity_name = identity_id.split("_", 1)[1] if "_" in identity_id else identity_id
                    from novelvideo.utils.path_resolver import compute_identity_portrait_path

                    project_dir = user_output_dir / project
                    id_portrait = compute_identity_portrait_path(project_dir, char_name, identity_name)
                    if id_portrait:
                        ref_path = id_portrait
                    elif identity_portrait_path and Path(identity_portrait_path).exists():
                        ref_path = identity_portrait_path

                if not ref_path and not identity_face_prompt:
                    from novelvideo.utils.path_resolver import compute_portrait_path

                    project_dir = user_output_dir / project
                    portrait_path = compute_portrait_path(project_dir, char_name)
                    standard_portrait = char_assets_dir / "portrait.png"
                    if portrait_path and Path(portrait_path).exists():
                        ref_path = portrait_path
                    elif standard_portrait.exists():
                        ref_path = str(standard_portrait)

            if not ref_path and not identity_face_prompt:
                state_portrait = char.get("portrait_path", "")
                if state_portrait and Path(state_portrait).exists():
                    ref_path = state_portrait

            reference_mode = (
                "composite" if is_identity else ("portrait_only" if ref_path else "prompt_only")
            )
            effective_face_prompt = identity_face_prompt or char.get("face_prompt", "") or char_name

            identity_ref_images: dict[str, str] = {}
            identity_face_prompts_map: dict[str, str] = {}
            identity_body_types_map: dict[str, str] = {}
            if identity_ids:
                from novelvideo.utils.path_resolver import (
                    compute_identity_portrait_path as _compute_id_portrait,
                )

                project_dir = user_output_dir / project
                for current_identity_id in identity_ids:
                    if current_identity_id == identity_id:
                        continue
                    for identity in char.get("identities", []):
                        if identity.get("identity_id") == current_identity_id:
                            face_prompt_override = identity.get("face_prompt", "")
                            if not face_prompt_override:
                                break
                            suffix = (
                                current_identity_id.split("_", 1)[1]
                                if "_" in current_identity_id
                                else current_identity_id
                            )
                            identity_portrait = identity.get("portrait_image", "")
                            if not identity_portrait or not Path(identity_portrait).exists():
                                identity_portrait = _compute_id_portrait(
                                    project_dir,
                                    char_name,
                                    suffix,
                                )
                            if identity_portrait and Path(identity_portrait).exists():
                                identity_ref_images[suffix] = identity_portrait
                            elif face_prompt_override:
                                identity_face_prompts_map[suffix] = face_prompt_override
                            body_type_override = identity.get("body_type", "")
                            if body_type_override:
                                identity_body_types_map[suffix] = body_type_override
                            break

            primary_suffix = (
                identity_id.split("_", 1)[1] if identity_id and "_" in identity_id else ""
            )
            if primary_body_type and primary_suffix:
                identity_body_types_map[primary_suffix] = primary_body_type
            character_map[char_name] = {
                "face_prompt": effective_face_prompt,
                "portrait_path": ref_path,
                "ref_path": ref_path,
                "base_prompt": effective_face_prompt,
                "reference_mode": reference_mode,
                "gender": char.get("gender", ""),
                "body_type": char.get("body_type", ""),
                "appearance_details": appearance_details,
                "identity_appearances": identity_appearances,
                "identity_sketch_colors": identity_sketch_colors,
                "sketch_color": identity_sketch_colors.get(primary_suffix, ""),
                "identity_ref_images": identity_ref_images,
                "identity_face_prompts": identity_face_prompts_map,
                "identity_body_types": identity_body_types_map,
            }
            break

    return character_map


__all__ = ["build_character_map_for_grid"]
