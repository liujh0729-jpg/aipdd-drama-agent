"""Sketch 流程辅助：模式选取、按地点分组、sketch 查找（B3 拆包自单文件 grid_generation.py）。"""

from pathlib import Path
from typing import List, Optional, Tuple

from novelvideo.models import beat_scene_id

from .regen_modes import DEFAULT_SKETCH_ASPECT_RATIO, get_sketch_nxn_modes


def _pick_nxn_mode(n: int, aspect_ratio: str = DEFAULT_SKETCH_ASPECT_RATIO):
    """选最小能装下 n beats 的 NxN 模式。"""
    modes = get_sketch_nxn_modes(aspect_ratio)
    for cap, mk, r, c in modes:
        if n <= cap:
            return mk, r, c, cap
    # fallback 到最大模式
    cap, mk, r, c = modes[-1]
    return mk, r, c, cap


def _group_beats_by_location(beats: List[dict]):
    """按 scene_id 字段分组 beats，保持出现顺序。返回 (scene_id, beats_list) 的列表。"""
    from collections import OrderedDict

    groups: OrderedDict = OrderedDict()
    for b in beats:
        loc = beat_scene_id(b) or "未知"
        groups.setdefault(loc, []).append(b)
    return list(groups.items())


def _is_space_map_beat(beat: dict) -> bool:
    visual = str((beat or {}).get("visual_description") or "").strip().lower()
    return (
        visual.startswith("[space_map")
        or visual.startswith("[space_anchor_map]")
        or visual.startswith("[absolute_layout_map]")
    )


def sketch_scene_grid_split(
    all_beats: List[dict],
    aspect_ratio: str = DEFAULT_SKETCH_ASPECT_RATIO,
) -> List[dict]:
    """草图：严格按 scene 切分，再为每个 scene 选择最小可容纳网格。"""
    modes = get_sketch_nxn_modes(aspect_ratio)
    max_cap = modes[-1][0]
    all_beats = [beat for beat in (all_beats or []) if not _is_space_map_beat(beat)]
    if not all_beats:
        cap, mk, r, c = modes[-1]
        return [
            {
                "scene_id": "",
                "rows": r,
                "cols": c,
                "mode_key": mk,
                "beats": [],
                "beat_numbers": [],
                "padding_count": cap,
            }
        ]

    result = []
    for scene_id, scene_beats in _group_beats_by_location(all_beats):
        offset = 0
        total = len(scene_beats)
        while offset < total:
            remaining = min(total - offset, max_cap)
            mk, r, c, cap = _pick_nxn_mode(remaining, aspect_ratio=aspect_ratio)
            chunk = scene_beats[offset : offset + cap]
            beat_numbers = [b.get("beat_number", 0) for b in chunk]
            result.append(
                {
                    "scene_id": scene_id,
                    "rows": r,
                    "cols": c,
                    "mode_key": mk,
                    "beats": list(chunk),
                    "beat_numbers": beat_numbers,
                    "padding_count": cap - len(chunk),
                }
            )
            offset += cap
    return result


def find_sketch_for_beat_range(
    sketch_dir: str, beat_start: int, beat_end: int
) -> Optional[Tuple[str, int, int]]:
    """在草图目录中查找覆盖指定 beat 范围的草图文件。

    Args:
        sketch_dir: 草图目录路径
        beat_start: 起始 beat 编号（1-based）
        beat_end: 结束 beat 编号（1-based，含）

    Returns:
        (文件路径, rows, cols) 或 None。
        文件命名约定: sketch_b{start}-{end}_{rows}x{cols}.jpg
    """
    candidates = []
    for f in Path(sketch_dir).glob("sketch_b*_*x*.jpg"):
        name = f.stem  # e.g., "sketch_b1-25_5x5"
        try:
            parts = name.split("_b")[1].split("_")
            s, e = parts[0].split("-")
            r, c = parts[1].split("x")
            file_start, file_end = int(s), int(e)
            if file_start <= beat_start and beat_end <= file_end:
                span = file_end - file_start
                candidates.append((span, str(f), int(r), int(c)))
        except (IndexError, ValueError):
            continue
    if not candidates:
        return None
    # 选择覆盖范围最小的（最精确匹配）
    candidates.sort(key=lambda x: x[0])
    _, path, rows, cols = candidates[0]
    return path, rows, cols
