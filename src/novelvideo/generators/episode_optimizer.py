"""Episode-level helpers for 2.0.

Only sketch color assignment remains here.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional


BRIDGMAN_CHARACTER_PALETTE = [
    ("#FF00FF", "FLUORESCENT MAGENTA"),
    ("#00FFFF", "FLUORESCENT CYAN"),
    ("#CCFF00", "FLUORESCENT LIME"),
    ("#FF6B00", "FLUORESCENT ORANGE"),
    ("#7C4DFF", "ELECTRIC VIOLET"),
    ("#00FF66", "NEON MINT"),
    ("#00A2FF", "ELECTRIC AZURE"),
    ("#FFD400", "SIGNAL YELLOW"),
    ("#9D00FF", "NEON PURPLE"),
    ("#00FFCC", "FLUORESCENT AQUA"),
    ("#39FF14", "LASER GREEN"),
    ("#5C6BC0", "INDIGO"),
]

# Prop 专用调色板：跟角色调色板视觉对比
# - 角色调色板是 FLUORESCENT/NEON/ELECTRIC 高饱和荧光色（明度 ≈ 100%）
# - Prop 调色板用 DEEP/SATURATED/PRIMARY 色（明度 ≈ 50-70%，非荧光），形成 value contrast
# - 即使色相意外接近，亮度/饱和度差异让 prop 仍能跟 character 视觉分开
PROP_MARKER_PALETTE = [
    ("#B71C1C", "DEEP CRIMSON"),     # 0°   深红 - 跟橙红角色色对比
    ("#6D4C41", "UMBER BROWN"),      # 16°  棕褐 - 避开角色荧光橙
    ("#827717", "OLIVE BRONZE"),     # 54°  橄榄铜
    ("#1B5E20", "FOREST GREEN"),     # 123° 深森林绿
    ("#006064", "DEEP TEAL"),        # 186° 深青绿
    ("#0D47A1", "ROYAL BLUE"),       # 213° 皇家蓝
    ("#311B92", "DEEP INDIGO"),      # 256° 深靛
    ("#7B1FA2", "DEEP ORCHID"),      # 284° 兰紫 - 和深靛拉开亮度/距离
    ("#880E4F", "WINE BERRY"),       # 332° 酒红梅
    ("#3E2723", "DARK CHOCOLATE"),   # 16°  巧克力棕
]

PRIMARY_MARKER_PALETTE_SIZE = 8


def _hex_to_hue(hex_code: str) -> float:
    r = int(hex_code[1:3], 16) / 255
    g = int(hex_code[3:5], 16) / 255
    b = int(hex_code[5:7], 16) / 255
    import colorsys

    h, _, _ = colorsys.rgb_to_hsv(r, g, b)
    return h * 360


def assign_sketch_colors(
    characters: List[dict],
    min_hue_gap: float = 60.0,
    episode_beats: Optional[List[dict]] = None,
    existing_colors: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Assign stable sketch colors for identities in the episode.

    Existing assignments are immutable: script edits may add or remove
    identities, but already assigned colors must not drift because old sketches
    use those marker colors as part of their content contract.
    """
    episode_keys: set[str] = set()
    if episode_beats is not None:
        from novelvideo.models import extract_char_identities_from_markers

        for beat in episode_beats:
            for _name, identity_id in extract_char_identities_from_markers(
                beat.get("visual_description", ""), strict=False
            ).items():
                if identity_id:
                    episode_keys.add(identity_id)
    else:
        for char in characters:
            for identity in char.get("identities", []):
                iid = identity.get("identity_id", "")
                if iid:
                    episode_keys.add(iid)

    sorted_keys = sorted(episode_keys)

    color_map: dict[str, str] = {
        str(identity_id): str(color)
        for identity_id, color in (existing_colors or {}).items()
        if str(identity_id).strip() and str(color).strip()
    }
    used_hexes = {
        color.strip().split(" ", 1)[0].lower() for color in color_map.values() if color.strip()
    }
    assigned_hues: list[float] = [
        _hex_to_hue(hex_code)
        for hex_code in used_hexes
        if hex_code.startswith("#") and len(hex_code) == 7
    ]
    used_indices: set[int] = {
        index
        for index, (hex_code, _name) in enumerate(BRIDGMAN_CHARACTER_PALETTE)
        if hex_code.lower() in used_hexes
    }

    for marker_id in sorted_keys:
        if marker_id in color_map:
            continue
        available_primary = [
            i
            for i in range(min(PRIMARY_MARKER_PALETTE_SIZE, len(BRIDGMAN_CHARACTER_PALETTE)))
            if i not in used_indices
        ]
        candidate_indices = available_primary or [
            i for i in range(len(BRIDGMAN_CHARACTER_PALETTE)) if i not in used_indices
        ]

        best_idx = None
        best_min_gap = -1.0
        for i in candidate_indices:
            hex_code, _ = BRIDGMAN_CHARACTER_PALETTE[i]
            hue = _hex_to_hue(hex_code)
            if not assigned_hues:
                best_idx = i
                break
            min_gap = min(min(abs(hue - h) % 360, 360 - abs(hue - h) % 360) for h in assigned_hues)
            if min_gap >= min_hue_gap:
                best_idx = i
                break
            if min_gap > best_min_gap:
                best_min_gap = min_gap
                best_idx = i

        if best_idx is not None:
            hex_code, color_name = BRIDGMAN_CHARACTER_PALETTE[best_idx]
            color_map[marker_id] = f"{hex_code} {color_name}"
            assigned_hues.append(_hex_to_hue(hex_code))
            used_indices.add(best_idx)
            print(f"[sketch_color] {marker_id}: {hex_code} {color_name}")
        else:
            logging.warning("[assign_sketch_colors] 调色板用尽，%s 未分配颜色", marker_id)

    return color_map


class EpisodeOptimizer:
    """Thin compatibility wrapper for sketch color assignment only."""

    assign_sketch_colors = staticmethod(assign_sketch_colors)
