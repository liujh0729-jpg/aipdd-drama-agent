"""批量网格合并与拆解：build_regen_plan / character_grid_split / sketch_grid_split（B3 拆包自单文件 grid_generation.py）。"""

import re
from typing import Dict, List, Tuple

from novelvideo.models import beat_scene_id, real_detected_identities

from .packing import (
    _count_batch_composite_chars,
    MANY_CHARS_REF_THRESHOLD,
    _smart_repack_beats,
    pack_beats,
    CHARACTER_POOL_2,
    CHARACTER_POOL_3,
    _cap_pool_for_many_chars,
)
from .regen_modes import REGEN_MODE_CONFIGS
from .plan import PlanEntry, SKETCH_GRID_PLAN
from .newapi_protocol import image_generation_selection_forces_single_cell
from .constants import HUIMENG_IMAGE2_SINGLE_CELL_REASON
from .modes import _single_cell_render_mode_key, _render_pool_template_for_aspect

from novelvideo.generators import grid_generation as _gg


def _merge_small_grids(
    entries: List[dict],
    pool_template: List[str],
    character_map: Dict[str, dict] | None = None,
) -> List[dict]:
    """合并连续的小网格（1-2 beat）为更大的网格，减少 1x1 碎片。

    策略：扫描连续的小网格（capacity <= 2），累积 beats 后用 pack_beats
    重新分配到更大的网格（优先 2x3、2x2、1x2）。
    合并后仍遵守铁律：>= 3 composite 角色时 capacity <= 2。
    """
    merged = []
    small_buffer = []  # 暂存同 scene_id 的连续小网格

    def flush_buffer():
        """将累积的小网格 beats 重新 pack 成更大的网格。"""
        if not small_buffer:
            return
        all_beats = []
        for entry in small_buffer:
            all_beats.extend(entry["beats"])

        combined_loc = small_buffer[0].get("scene_id", "")

        # 铁律检查：合并后的 beats 如果 composite 角色 >= 阈值，用 smart_repack
        if character_map:
            n_comp = _count_batch_composite_chars(all_beats, character_map)
            if n_comp >= MANY_CHARS_REF_THRESHOLD:
                merged.extend(
                    _smart_repack_beats(
                        all_beats,
                        character_map,
                        list(pool_template),
                        overrides={},
                        loc=combined_loc,
                    )
                )
                return

        # 正常合并：用 pack_beats 重新分配
        pool = pool_template * 100
        mode_keys = pack_beats(len(all_beats), pool)

        offset = 0
        for mk in mode_keys:
            cfg = REGEN_MODE_CONFIGS[mk]
            rows, cols = cfg["rows"], cfg["cols"]
            capacity = cfg["capacity"]
            batch = all_beats[offset : offset + capacity]
            beat_numbers = [b.get("beat_number", i + 1) for i, b in enumerate(batch)]
            merged.append(
                {
                    "scene_id": combined_loc,
                    "rows": rows,
                    "cols": cols,
                    "mode_key": mk,
                    "beats": batch,
                    "beat_numbers": beat_numbers,
                    "padding_count": capacity - len(batch),
                }
            )
            offset += capacity

    for entry in entries:
        n_beats = len(entry["beats"])
        entry_scene = entry.get("scene_id", "")
        buffer_scene = small_buffer[0].get("scene_id", "") if small_buffer else None
        if n_beats <= 2:
            if small_buffer and entry_scene != buffer_scene:
                flush_buffer()
                small_buffer = []
            small_buffer.append(entry)
        else:
            # 遇到大网格，先 flush 之前的小网格
            flush_buffer()
            small_buffer = []
            merged.append(entry)

    # flush 尾部
    flush_buffer()

    return merged


def _pack_location_beats(
    beats: List[dict],
    loc: str,
    pool_template: List[str],
    overrides: dict,
) -> List[dict]:
    """将一个场景子组的 beats pack 成网格 entries。"""
    pool = pool_template * 100
    mode_keys = pack_beats(len(beats), pool)
    entries = []
    offset = 0
    for mk in mode_keys:
        cfg = REGEN_MODE_CONFIGS[mk]
        rows, cols = cfg["rows"], cfg["cols"]
        capacity = cfg["capacity"]
        batch = beats[offset : offset + capacity]
        beat_numbers = [b.get("beat_number", i + 1) for i, b in enumerate(batch)]
        entry = {
            "scene_id": loc,
            "rows": rows,
            "cols": cols,
            "mode_key": mk,
            "beats": batch,
            "beat_numbers": beat_numbers,
            "padding_count": capacity - len(batch),
        }
        grid_key = (rows, cols)
        if grid_key in overrides:
            ar, isz = overrides[grid_key]
            entry["mode_key"] = f"{rows}x{cols}_{ar.replace(':', '-')}"
        entries.append(entry)
        offset += capacity
    return entries


def build_regen_plan(
    selected_beats: list[dict],
    strategy: str,
    aspect_mode: str,
    character_map: dict | None = None,
    force_one_by_one: bool = False,
    image_generation_selection: str | None = None,
) -> list[PlanEntry]:
    """Build the canonical render plan for a selected beat set."""
    single_cell_reason = ""
    if force_one_by_one:
        single_cell_reason = "force-1x1"
    elif image_generation_selection_forces_single_cell(image_generation_selection):
        single_cell_reason = HUIMENG_IMAGE2_SINGLE_CELL_REASON

    if single_cell_reason:
        mode_key = _single_cell_render_mode_key(aspect_mode)
        return [
            PlanEntry(
                mode_key=mode_key,
                rows=1,
                cols=1,
                beat_numbers=(int(beat["beat_number"]),),
                location=str(beat_scene_id(beat) or beat.get("location") or ""),
                reasons=(single_cell_reason,),
            )
            for beat in selected_beats
        ]

    pool_template = _render_pool_template_for_aspect(aspect_mode)

    if strategy == "location":
        raw_plan = _gg.scene_grid_split(
            list(selected_beats),
            aspect_mode=aspect_mode,
            pool_template=pool_template,
            character_map=character_map if character_map else None,
        )
        return [
            PlanEntry(
                mode_key=entry["mode_key"],
                rows=int(entry["rows"]),
                cols=int(entry["cols"]),
                beat_numbers=tuple(int(n) for n in entry["beat_numbers"]),
                location=str(entry.get("scene_id") or entry.get("location") or ""),
                padding_count=int(entry.get("padding_count") or 0),
                reasons=tuple(entry.get("reasons") or ()),
                warnings=tuple(entry.get("warnings") or ()),
            )
            for entry in raw_plan
        ]

    if strategy == "naive":
        mode_keys = pack_beats(len(selected_beats), list(pool_template))
        entries: list[PlanEntry] = []
        offset = 0
        for mode_key in mode_keys:
            cfg = REGEN_MODE_CONFIGS[mode_key]
            capacity = cfg["capacity"]
            batch = selected_beats[offset : offset + capacity]
            if not batch:
                break
            entries.append(
                PlanEntry(
                    mode_key=mode_key,
                    rows=int(cfg["rows"]),
                    cols=int(cfg["cols"]),
                    beat_numbers=tuple(int(beat["beat_number"]) for beat in batch),
                    padding_count=capacity - len(batch),
                )
            )
            offset += capacity
        return entries

    raise ValueError(f"unknown strategy: {strategy!r}")


def _get_beat_visual_composite_chars(beat: dict, character_map: dict) -> frozenset:
    """从 visual_description 的 {{}} 标记提取实际绘制的有参考图角色。

    Render 场景优先使用 detected_identities，确保铁律拆分与上色/导出使用同一套角色来源。
    若无 detected_identities，再回退到 visual_description 的 {{}} 标记。
    包括 composite 和 portrait_only 模式（都有参考图占 image slot）。
    """
    ref_names = sorted(
        [
            name
            for name, info in character_map.items()
            if info.get("reference_mode") != "prompt_only"
            and (info.get("reference_path") or info.get("ref_path") or info.get("portrait_path"))
        ],
        key=len,
        reverse=True,
    )
    detected = real_detected_identities(beat.get("detected_identities") or [])
    if detected:
        markers = list(detected)
    else:
        visual = beat.get("visual_description", "")
        markers = re.findall(r"\{\{([^}]+)\}\}", visual)
    result = set()
    for marker in markers:
        for char_name in ref_names:
            if marker == char_name or marker.startswith(char_name + "_"):
                result.add(char_name)
                break
    return frozenset(result)


def _flush_to_grids(
    beats: List[dict],
    composite_count: int,
) -> List[dict]:
    """将一组 beats 打包为一个或多个网格 entry。"""
    if not beats:
        return []
    if composite_count <= 2:
        template = CHARACTER_POOL_2  # 最大 2x2
    else:
        template = CHARACTER_POOL_3  # 最大 2x2
    # 铁律
    template = _cap_pool_for_many_chars(template, composite_count)
    pool = list(template) * 100
    mode_keys = pack_beats(len(beats), pool)

    entries = []
    offset = 0
    for mk in mode_keys:
        cfg = REGEN_MODE_CONFIGS[mk]
        capacity = cfg["capacity"]
        batch = beats[offset : offset + capacity]
        beat_numbers = [b.get("beat_number", i + 1) for i, b in enumerate(batch)]
        entries.append(
            {
                "rows": cfg["rows"],
                "cols": cfg["cols"],
                "mode_key": mk,
                "beats": batch,
                "beat_numbers": beat_numbers,
                "padding_count": capacity - len(batch),
                "composite_count": composite_count,
            }
        )
        offset += capacity
    return entries


def character_grid_split(
    all_beats: List[dict],
    character_map: Dict[str, dict],
) -> List[dict]:
    """按 composite 角色全局分组 beats，自动选择网格尺寸。

    三阶段算法：
    1. 分类：按 visual_description 的 {{}} 标记提取 composite 角色数
       - >2 chars → overflow（≤2x2 pool）
       - 0 chars  → empty（就近吸收）
       - 1-2 chars → normal（配对分组）
    2. 贪心 set-cover 配对：全局聚合同角色对的所有 beats
    3. pack 到网格：≤2 chars → CHARACTER_POOL_2（最大2x2），>2 chars → CHARACTER_POOL_3（最大2x2）

    Args:
        all_beats: 所有 beat 数据（含 visual_description / beat_number 字段）
        character_map: 角色映射 {角色名: {reference_mode, ...}}

    Returns:
        网格计划列表，格式与 scene_grid_split 对齐
    """
    if not all_beats:
        return []

    # ── 阶段 1：分类 ──
    overflow_beats: List[dict] = []  # >2 composite chars
    empty_beats: List[dict] = []  # 0 composite chars
    normal_beats: List[Tuple[int, dict, frozenset]] = []  # (index, beat, chars)

    for idx, beat in enumerate(all_beats):
        chars = _gg._get_beat_visual_composite_chars(beat, character_map)
        if len(chars) > 2:
            overflow_beats.append(beat)
        elif len(chars) == 0:
            empty_beats.append(beat)
        else:
            normal_beats.append((idx, beat, chars))

    # ── 阶段 2：贪心 set-cover 配对 ──
    # 收集所有出现过的 1-char 和 2-char 候选对
    all_char_sets: set = set()
    for _, _, chars in normal_beats:
        all_char_sets.add(chars)

    # 枚举所有可能的 pair（1 或 2 个角色的组合）
    all_single_chars = set()
    for cs in all_char_sets:
        all_single_chars |= cs
    candidate_pairs: List[frozenset] = []
    single_list = sorted(all_single_chars)
    for i, c1 in enumerate(single_list):
        candidate_pairs.append(frozenset({c1}))
        for c2 in single_list[i + 1 :]:
            candidate_pairs.append(frozenset({c1, c2}))

    assigned = [False] * len(normal_beats)
    pair_groups: List[Tuple[frozenset, List[dict]]] = []  # (pair, beats)

    while True:
        # 找覆盖最多未分配 beats 的 pair
        best_pair = None
        best_indices = []
        for pair in candidate_pairs:
            indices = [
                i
                for i, (_, _, chars) in enumerate(normal_beats)
                if not assigned[i] and chars <= pair
            ]
            if len(indices) > len(best_indices):
                best_pair = pair
                best_indices = indices
        if not best_indices:
            break
        for i in best_indices:
            assigned[i] = True
        group_beats = [normal_beats[i][1] for i in best_indices]
        pair_groups.append((best_pair, group_beats))

    # ── empty beats 就近吸收到最近的 pair group ──
    if empty_beats and pair_groups:
        for eb in empty_beats:
            eb_num = eb.get("beat_number", 0)
            best_group_idx = 0
            best_dist = float("inf")
            for gi, (_, group_beats) in enumerate(pair_groups):
                for gb in group_beats:
                    dist = abs(gb.get("beat_number", 0) - eb_num)
                    if dist < best_dist:
                        best_dist = dist
                        best_group_idx = gi
            pair_groups[best_group_idx][1].append(eb)
    elif empty_beats:
        # 没有 pair group，empty beats 自成一组
        pair_groups.append((frozenset(), empty_beats))

    # ── 阶段 3：pack 到网格 ──
    result: List[dict] = []

    # pair groups → 4x4
    for pair, beats in pair_groups:
        # 按 beat_number 排序保持顺序
        beats.sort(key=lambda b: b.get("beat_number", 0))
        result.extend(_flush_to_grids(beats, len(pair)))

    # overflow → 批量 flush，让 pack_beats 自动打包
    if overflow_beats:
        overflow_beats.sort(key=lambda b: b.get("beat_number", 0))
        max_cc = max(
            len(_gg._get_beat_visual_composite_chars(ob, character_map)) for ob in overflow_beats
        )
        result.extend(_flush_to_grids(overflow_beats, max_cc))

    # 按首个 beat_number 排序
    result.sort(key=lambda g: g["beat_numbers"][0] if g["beat_numbers"] else 0)

    return result


def sketch_grid_split(total_beats: int) -> list[tuple[int, int]]:
    """Sketch 模式专用分割：返回 1x 5x5 单张网格（共 25 panel 位）。

    按 SKETCH_GRID_PLAN 顺序分配，beats 不足时只使用需要的网格数量。
    """
    plan = []
    remaining = total_beats
    for grid_cfg in SKETCH_GRID_PLAN:
        if remaining <= 0:
            break
        r, c = grid_cfg["rows"], grid_cfg["cols"]
        plan.append((r, c))
        remaining -= r * c
    return plan if plan else [(1, 1)]


def _coalesce_locations(location_groups):
    """按 · 前缀合并细粒度场景组为粗粒度。

    "家·客厅", "家·餐厅" → "家"
    无 · 的场景保持原样。保持首次出现顺序。
    """
    from collections import OrderedDict

    coarse: OrderedDict[str, list] = OrderedDict()
    for fine_loc, beats in location_groups.items():
        prefix = fine_loc.split("·")[0].split("・")[0].strip()
        if not prefix:
            prefix = fine_loc
        if prefix not in coarse:
            coarse[prefix] = []
        coarse[prefix].extend(beats)
    return coarse
