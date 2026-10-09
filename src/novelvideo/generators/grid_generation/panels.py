"""角色色彩映射、panel 检测与预计算面板过滤、参考顺序（B3 拆包自单文件 grid_generation.py）。"""

import os
import re
from typing import Any

from novelvideo.generators.prompt_builder import PromptComponents
from novelvideo.models import real_detected_identities


def filter_character_map_for_beats(
    character_map: dict,
    beats: list,
    scene_refs: dict[int, list[Any]] | None = None,
) -> dict:
    """过滤角色映射为当前 beats 中实际出场角色。

    只保留在 beats visual_description 中 {{角色名}} 出现的角色。

    Returns:
        过滤后的 character_map（新 dict，不修改原始数据）
    """
    panel_chars = PromptComponents.extract_panel_characters(beats, character_map)

    # 只保留出场角色
    filtered = {k: dict(v) for k, v in character_map.items() if k in panel_chars}

    non_panel = [c for c in character_map if c not in filtered]
    if non_panel:
        print(f"[filter_character_map] 过滤非出场角色: {non_panel}")

    return filtered


def _has_director_image_ref(scene_refs: dict[int, list[Any]], panel_idx: int = 1) -> bool:
    for ref in scene_refs.get(panel_idx, []) or []:
        if str(getattr(ref, "source_level", "") or "").strip() != "director_image":
            continue
        image_paths = list(getattr(ref, "image_paths", []) or [])
        if image_paths and all(os.path.exists(path) for path in image_paths):
            return True
    return False


def build_color_map_from_character_map(
    character_map: dict,
    sketch_colors: dict[str, str] | None = None,
) -> dict[str, str]:
    """从 character_map + sketch_colors 构建 {key: "#HEX COLOR_NAME"} 颜色映射。

    Returns:
        {identity_key: "#HEX COLOR_NAME"}。
        角色 key 格式为 "char_name_suffix" 或 "char_name"。
    """
    color_map = {}
    for char_name, info in character_map.items():
        identity_colors = info.get("identity_sketch_colors", {})
        if identity_colors:
            for suffix, color_str in identity_colors.items():
                color_map[f"{char_name}_{suffix}"] = color_str
        elif info.get("sketch_color"):
            color_map[char_name] = info["sketch_color"]
    return color_map


def _extract_hex_color(color_str: str, fallback: str) -> str:
    text = str(color_str or "").strip()
    match = re.search(r"#[0-9A-Fa-f]{6}", text)
    return match.group(0) if match else fallback


def _color_for_identity(identity_id: str, character_map: dict, fallback: str) -> str:
    color_map = build_color_map_from_character_map(character_map)
    if identity_id in color_map:
        return _extract_hex_color(color_map[identity_id], fallback)
    for char_name, info in character_map.items():
        if identity_id == char_name or identity_id.startswith(f"{char_name}_"):
            identity_colors = info.get("identity_sketch_colors") or {}
            suffix = (
                identity_id[len(char_name) + 1 :] if identity_id.startswith(f"{char_name}_") else ""
            )
            if suffix and suffix in identity_colors:
                return _extract_hex_color(identity_colors[suffix], fallback)
            return _extract_hex_color(info.get("sketch_color", ""), fallback)
    return fallback


def _draw_blocking_stick_figure(
    draw,
    *,
    x: float,
    y: float,
    scale: float,
    color: str,
    foreground: bool = False,
    seated: bool = False,
) -> None:
    """Draw simple color-coded stick blocking onto a director environment ref."""

    width = max(5, int(scale * 0.08))
    head_r = scale * (0.22 if foreground else 0.16)
    body_len = scale * (0.62 if foreground else (0.34 if seated else 0.48))
    shoulder = scale * (0.22 if foreground else 0.16)
    hip = scale * (0.16 if foreground else 0.12)
    outline = color

    # Head.
    draw.ellipse(
        (x - head_r, y - head_r, x + head_r, y + head_r),
        outline=outline,
        width=width,
    )

    neck_y = y + head_r
    torso_y = neck_y + body_len
    draw.line((x, neck_y, x, torso_y), fill=outline, width=width)
    draw.line(
        (x - shoulder, neck_y + scale * 0.1, x + shoulder, neck_y + scale * 0.1),
        fill=outline,
        width=width,
    )
    draw.line((x - hip, torso_y, x + hip, torso_y), fill=outline, width=width)

    # Arms and rough lower-body cues. Keep it rough: these are placement markers, not anatomy.
    if foreground:
        draw.line(
            (x + shoulder, neck_y + scale * 0.12, x + scale * 0.38, torso_y),
            fill=outline,
            width=width,
        )
        draw.line(
            (x - shoulder, neck_y + scale * 0.12, x - scale * 0.34, torso_y),
            fill=outline,
            width=width,
        )
    elif seated:
        draw.line(
            (x - shoulder, neck_y + scale * 0.1, x - scale * 0.34, torso_y + scale * 0.04),
            fill=outline,
            width=width,
        )
        draw.line(
            (x + shoulder, neck_y + scale * 0.1, x + scale * 0.3, torso_y + scale * 0.02),
            fill=outline,
            width=width,
        )
    else:
        draw.line(
            (x - shoulder, neck_y + scale * 0.12, x - scale * 0.28, torso_y - scale * 0.08),
            fill=outline,
            width=width,
        )
        draw.line(
            (x + shoulder, neck_y + scale * 0.12, x + scale * 0.26, torso_y - scale * 0.1),
            fill=outline,
            width=width,
        )
        draw.line(
            (x - hip, torso_y, x - scale * 0.22, torso_y + scale * 0.24), fill=outline, width=width
        )
        draw.line(
            (x + hip, torso_y, x + scale * 0.24, torso_y + scale * 0.22), fill=outline, width=width
        )


def _prepare_director_blocking_refs(
    *,
    scene_refs: dict[int, list],
    beats: list[dict],
    character_map: dict,
) -> None:
    """Legacy no-op.

    Current 3GS scene sketch submits the PlayCanvas combined.png directly.
    The image already contains the visible actor placeholder and prop/staging
    marker colors; the model edits that single image in place.
    """
    return


def detect_panel_characters(
    character_map: dict,
    sketch_image_path: str,
    rows: int,
    cols: int,
    sketch_colors: dict[str, str] | None = None,
) -> dict[int, set[str]]:
    """Per-panel 颜色检测，返回每个 panel 检测到的角色 key。

    Returns:
        {panel_index(0-based): set of detected color_map keys}
    """
    from novelvideo.generators.sketch_color_detector import detect_sketch_colors_per_panel

    color_map = build_color_map_from_character_map(character_map, sketch_colors)
    if not color_map:
        return {}

    return detect_sketch_colors_per_panel(sketch_image_path, color_map, rows=rows, cols=cols)


def filter_character_map_by_sketch(
    character_map: dict,
    sketch_image_path: str,
    sketch_colors: dict[str, str] | None = None,
) -> dict:
    """检测草图颜色，过滤不存在的角色。

    保留 prompt_only 和无 sketch_color 的角色（无法通过颜色判断）。
    仅过滤有 sketch_color 但颜色在草图中不存在的角色。

    Returns:
        过滤后的 character_map（新 dict）
    """
    from novelvideo.generators.sketch_color_detector import detect_sketch_colors

    color_map = build_color_map_from_character_map(character_map, sketch_colors)
    if not color_map:
        return dict(character_map)

    # 构建 char_to_keys 映射
    char_to_keys = {}
    for char_name, info in character_map.items():
        keys = []
        identity_colors = info.get("identity_sketch_colors", {})
        if identity_colors:
            keys = [f"{char_name}_{suffix}" for suffix in identity_colors]
        elif info.get("sketch_color"):
            keys = [char_name]
        char_to_keys[char_name] = keys

    detected = detect_sketch_colors(sketch_image_path, color_map, verbose=True)

    filtered = {}
    removed = []
    for char_name, info in character_map.items():
        keys = char_to_keys.get(char_name, [])
        if not keys:
            filtered[char_name] = dict(info)
        elif any(k in detected for k in keys):
            filtered[char_name] = dict(info)
        else:
            removed.append(char_name)

    if removed:
        print(f"[filter_by_sketch] 草图中未检测到颜色，移除角色: {removed}")

    return filtered


def load_precomputed_panel_detected(
    beat_numbers: list[int],
    beats_data: list[dict],
) -> dict[int, set[str] | None]:
    """从 beat 数据中读取 detected_identities，转换为 panel_detected 格式。

    Returns:
        {panel_index(0-based): set of identity keys}，未检测的 panel 值为 None
    """
    beat_map = {b.get("beat_number"): b for b in (beats_data or [])}
    result = {}
    for panel_idx, bn in enumerate(beat_numbers):
        beat = beat_map.get(bn, {})
        ids = real_detected_identities(beat.get("detected_identities", []))
        result[panel_idx] = set(ids) if ids else None
    return result


def filter_character_map_by_precomputed(
    character_map: dict,
    panel_detected: dict[int, set[str] | None],
) -> dict:
    """根据预计算结果过滤 character_map：只保留在任一 panel 中出现的角色。"""
    if not panel_detected or all(v is None for v in panel_detected.values()):
        return {}

    all_detected = set()
    for ids in panel_detected.values():
        if ids is not None:
            all_detected.update(ids)

    if not all_detected:
        return {}

    filtered = {}
    removed = []
    for char_name, info in character_map.items():
        identity_colors = info.get("identity_sketch_colors", {})
        keys = []
        if identity_colors:
            keys = [f"{char_name}_{suffix}" for suffix in identity_colors]
        elif info.get("sketch_color"):
            keys = [char_name]

        if not keys or any(k in all_detected for k in keys):
            filtered[char_name] = dict(info)
        else:
            removed.append(char_name)

    if removed:
        print(f"[filter_by_precomputed] 预计算未检测到，移除角色: {removed}")

    return filtered


def resolve_render_reference_order(
    ctx,
    beats: list[dict],
    grid_capacity: int,
    valid_character_map: dict,
) -> list[str]:
    """统一 Render 模式参考图顺序。

    Single source of truth:
    1. 只复用 prompt_builder 在 build() 阶段解析出的顺序
    2. 缺失即报错，禁止运行时重新推导导致所见非所得
    """
    if not valid_character_map:
        return []

    ordered_chars = list(getattr(ctx, "resolved_render_chars", []) or [])
    if ordered_chars:
        return ordered_chars

    raise RuntimeError(
        "Render reference order missing: ctx.resolved_render_chars was not populated "
        "by prompt_builder. Refusing to recompute attachment order at runtime."
    )
