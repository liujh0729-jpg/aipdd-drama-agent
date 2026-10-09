# ruff: noqa: F401
"""Preset canvas builders for project-scoped Freezone.

This module is intentionally file-system based. It builds an initial canvas
from canonical project artifacts, while the canvas JSON remains the editable
workspace state.

B2 拆包：单文件 presets.py（5,317 行）→ 本包；导入路径与符号面零变化。
子模块：constants / vision_egress / refs / aspect / ref_collect / beat_context /
episode_context / nodes / roles / scene_helpers / workflows / prompts /
asset_context / payload。
"""

from novelvideo.config import IMAGE_DEFAULT_STYLE as PROP_REF_DEFAULT_STYLE
from novelvideo.freezone.skill_registry import SKILL_SCHEMA_VERSION
from novelvideo.generators.prop_generation import build_prop_reference_prompt
from novelvideo.generators.scene_reference_images import build_scene_reference_prompt
from novelvideo.models import (
    NO_CHARACTER_MARKER,
    NO_PROP_MARKER,
    NovelScene,
    build_prop_menu,
    build_scene_effective_prompt,
)
from novelvideo.utils.path_resolver import (
    PathResolver,
    canonical_beat_director_env_only_path,
    canonical_identity_costume_path,
    canonical_identity_path,
    canonical_identity_portrait_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
    compute_identity_costume_path,
    compute_identity_portrait_path,
)
from novelvideo.utils.static_urls import project_static_url

from .constants import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    AUDIO_EXTS,
    TEXT_EXTS,
    FREEZONE_PRESET_DEFAULT_IMAGE_MODEL,
    FREEZONE_PRESET_IMAGE_ASPECT_RATIOS,
)
from .vision_egress import (
    FreezoneVisionEgress,
    prepare_freezone_vision_egress,
    complete_freezone_vision_egress,
    abandon_freezone_vision_egress,
)
from .refs import (
    PresetRef,
    _compact_context,
    _mainline_context_for_ref,
    _safe_id,
    preset_key_for_request,
    canvas_id_for_preset,
    _make_url,
    _path_rel_if_inside,
    _rel,
)
from .aspect import (
    _greatest_common_divisor,
    _image_aspect_ratio,
    _parse_aspect_ratio_value,
    _nearest_supported_image_aspect_ratio,
    _normalize_supported_image_aspect_ratio,
    _project_sketch_aspect_ratio,
    _context_sketch_aspect_ratio,
    _media_type_for_path,
    _aspect_ratio_for_ref,
)
from .ref_collect import (
    _add_file_ref,
    _latest_matching,
    _normalize_scene_name,
    _jsonable,
    _MARKER_RE,
    _visual_markers,
    _identity_character,
    _identity_name,
    _as_list,
    _prop_id_from_item,
    _identity_id_from_item,
    _real_identity_ids,
    _real_prop_ids,
    _add_character_refs,
    _add_character_identity_ref,
    _add_mainline_identity_ref,
    _add_prop_refs,
    _add_selected_background_ref,
    _add_scene_refs,
    _beat_scene_source_urls,
)
from .beat_context import collect_beat_preset_refs, build_beat_preset_context
from .episode_context import build_episode_preset_context
from .nodes import (
    _node_source_from_ref,
    _node_mainline_context_from_ref,
    _beat_target_placeholder_ref,
    _text_node,
    _skill_node,
    _beat_context_node,
    _beat_context_display_name,
    _upload_node,
    _pano_360_viewer_node,
    _three_d_world_node,
    _director_world_source_from_ref,
    _director_world_node,
    _asset_node_source_from_ref,
    _image_gen_node,
    _asset_image_node,
    _ref_image_node,
    _edge,
    _candidate_binding_edge,
    _skill_role_edge,
    _reference_path_lookup_keys,
    _is_asset_library_reference,
)
from .roles import (
    SCENE_IMAGE_ROLES,
    SCENE_PLY_INPUT_ROLES,
    SCENE_PLY_WORKFLOW_ROLES,
    DIRECTOR_CAPTURE_IMAGE_ROLES,
    BEAT_CONTEXT_BINDING_ROLES,
    DIRECTOR_CAPTURE_LAYOUT_POSITIONS,
    SKETCH_DIRECTOR_CAPTURE_LAYOUT_POSITIONS,
)
from .scene_helpers import (
    _scene_prompt_content,
    _scene_style_block_from_meta,
    _project_style_meta,
    _scene_from_prompt_meta,
    _base_scene_from_prompt_meta,
    _scene_reference_prompt_content,
    _scene_ply_input_roles,
)
from .workflows import _append_scene_asset_workflow, _append_director_capture_workflow
from .prompts import (
    _prompt_text_node,
    _display_prompt_text,
    _character_profile_content,
    _prop_profile_content,
    _gender_label,
    _split_marker_color,
    _identity_marker_context,
    _prop_marker_context,
    _replace_beat_markers,
    _beat_sketch_marker_context,
    _beat_sketch_marker_content,
    _beat_sketch_prompt,
    _beat_render_prompt,
    _content_for_context,
    _episode_overview_content,
    _episode_beat_content,
    _episode_beat_context,
    _beat_context_mainline_context,
    _beat_context_edit_fields,
)
from .asset_context import build_asset_preset_context
from .payload import build_canvas_payload_from_context
