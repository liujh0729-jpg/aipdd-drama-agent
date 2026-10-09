"""场景 / 导演采集角色与布局常量（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations


SCENE_IMAGE_ROLES = {
    "scene_master",
    "scene_reverse_master",
    "scene_360",
    "scene_director_pano_360",
}

SCENE_PLY_INPUT_ROLES = {
    "scene_3gs_master_ply": ("scene_master",),
    "scene_3gs_reverse_ply": ("scene_reverse_master",),
    "scene_3gs_pano_ply": ("scene_director_pano_360", "scene_360"),
    "scene_3gs_custom_scene": ("scene_master", "scene_director_pano_360", "scene_360"),
}

SCENE_PLY_WORKFLOW_ROLES = {
    "scene_3gs_master_ply": ("master", "scene_master", "3D 世界（正面）"),
    "scene_3gs_reverse_ply": ("reverse", "scene_reverse_master", "3D 世界（背面）"),
    "scene_3gs_pano_ply": ("pano", "scene_director_pano_360", "3D 世界（360）"),
}

DIRECTOR_CAPTURE_IMAGE_ROLES = (
    "selected_background",
    "director_combined",
    "director_env",
    "director_env_actor",
    "actor_overlay",
    "actor_mask",
    "prop_staging_overlay",
    "prop_staging_mask",
    "director_repaired_or_alt",
)

BEAT_CONTEXT_BINDING_ROLES = {
    "current_sketch",
    "current_frame",
    "current_video",
    "current_audio",
    "selected_background",
    "director_combined",
}

DIRECTOR_CAPTURE_LAYOUT_POSITIONS = {
    "selected_background": (-1080, -1040),
    "director_env": (-1500, 1560),
    "director_combined": (-1080, -360),
    "actor_overlay": (-1060, 1560),
    "director_env_actor": (-620, 1560),
    "prop_staging_overlay": (-1500, 1940),
    "actor_mask": (-1060, 1940),
    "prop_staging_mask": (-620, 1940),
    "director_repaired_or_alt": (-1500, 2320),
}

SKETCH_DIRECTOR_CAPTURE_LAYOUT_POSITIONS = {
    "selected_background": (-1080, -1040),
    "director_combined": (-1080, -360),
    "director_env": (-1500, 1560),
    "actor_overlay": (-1060, 1560),
    "director_env_actor": (-620, 1560),
    "prop_staging_overlay": (-1500, 1940),
    "actor_mask": (-1060, 1940),
    "prop_staging_mask": (-620, 1940),
    "director_repaired_or_alt": (-1500, 2320),
}
