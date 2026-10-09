"""beat 打包：积木池模板、_smart_repack_beats、pack_beats、pad_to_aspect_ratio（B3 拆包自单文件 grid_generation.py）。"""

from typing import List

from .regen_modes import REGEN_MODE_CONFIGS

from novelvideo.generators import grid_generation as _gg


# =============================================================================
# 统一积木池：pool 即资源清单，用完即止
# =============================================================================
# 预置池子模板（* N 生成实际资源清单）
LOCATION_POOL_TEMPLATE = ["2x4_4-3", "2x3_1-1", "2x2_2-3", "1x2_4-3", "1x1_2-3"]
LANDSCAPE_RENDER_POOL_TEMPLATE = [
    "4x4_16-9",
    "3x3_16-9",
    "2x2_16-9",
    "1x1_16-9",
]

# 默认模式 pool（与场景分组相同）
DEFAULT_POOL_TEMPLATE = ["2x4_4-3", "2x3_1-1", "2x2_2-3", "1x2_4-3", "1x1_2-3"]
# 角色分组 pool
CHARACTER_POOL_2 = ["2x4_4-3", "2x3_1-1", "2x2_2-3", "1x2_4-3", "1x1_2-3"]
CHARACTER_POOL_3 = ["2x4_4-3", "2x3_1-1", "2x2_2-3", "1x2_4-3", "1x1_2-3"]

# 铁律：>=3 个有参考图的角色引用时，只能用 1x1（不能用网格）
MANY_CHARS_REF_THRESHOLD = 3
MANY_CHARS_MAX_CAPACITY = 1


def _cap_pool_for_many_chars(pool: list, composite_count: int) -> list:
    """当 composite 角色数 >= 阈值时，过滤池子到 capacity <= 上限。"""
    if composite_count >= MANY_CHARS_REF_THRESHOLD:
        capped = [
            mk for mk in pool if REGEN_MODE_CONFIGS[mk]["capacity"] <= MANY_CHARS_MAX_CAPACITY
        ]
        return capped if capped else pool
    return pool


def _count_batch_composite_chars(beats: list, character_map: dict) -> int:
    """统计一批 beats 中出现的唯一 composite 角色数量。"""
    all_chars = set()
    for beat in beats:
        all_chars |= _gg._get_beat_visual_composite_chars(beat, character_map)
    return len(all_chars)


def _smart_repack_beats(
    beats: List[dict],
    character_map: dict,
    pool_template: list,
    overrides: dict = None,
    loc: str = "",
) -> list:
    """智能拆分：将 beats 按 composite 角色数分成连续子组，每组 n_comp < 阈值。

    当一批 beats 合计 n_comp >= 3 时，不是一刀切全部 cap 到 1x2，
    而是贪心扫描：连续 beats 合并 composite 集合，只在真正达到阈值时断开。
    每个子组用完整 pool pack（因为子组内 n_comp < 3），
    只有单个 beat 本身 >= 3 时才用 capped pool。
    """
    if not beats:
        return []

    groups: List[List[dict]] = []
    current_group: List[dict] = []
    current_chars: set = set()

    for beat in beats:
        beat_chars = _gg._get_beat_visual_composite_chars(beat, character_map)
        merged = current_chars | beat_chars

        if len(merged) >= MANY_CHARS_REF_THRESHOLD and current_group:
            # 当前 beat 会让 n_comp 超标，先 flush 已有组
            groups.append(current_group)
            current_group = [beat]
            current_chars = set(beat_chars)
        else:
            current_group.append(beat)
            current_chars = merged

    if current_group:
        groups.append(current_group)

    # 合并角色集相同的子组（如 B1-6{唐若瑜,陆洲} 和 B17-18{唐若瑜,陆洲}）
    merged_groups: List[List[dict]] = []
    merged_charsets: List[frozenset] = []
    for group in groups:
        chars = frozenset()
        for beat in group:
            chars = chars | _gg._get_beat_visual_composite_chars(beat, character_map)
        # 找已有的同角色集组合并
        found = False
        for i, existing_chars in enumerate(merged_charsets):
            if (
                chars == existing_chars
                or (chars | existing_chars) == existing_chars
                or (chars | existing_chars) == chars
            ):
                # 角色集相同或是子集，检查合并后是否仍 < 阈值
                union = chars | existing_chars
                if len(union) < MANY_CHARS_REF_THRESHOLD:
                    merged_groups[i].extend(group)
                    merged_charsets[i] = union
                    found = True
                    break
        if not found:
            merged_groups.append(list(group))
            merged_charsets.append(chars)

    # 合并后按首 beat_number 排序每组内的 beats
    for group in merged_groups:
        group.sort(key=lambda b: b.get("beat_number", 0))

    # 对每个子组用合适的 pool pack
    result = []
    for group in merged_groups:
        n_comp = _count_batch_composite_chars(group, character_map)
        if n_comp >= MANY_CHARS_REF_THRESHOLD:
            # 单个 beat 本身就 >= 3 composite，用 capped pool
            capped = _cap_pool_for_many_chars(list(pool_template), n_comp)
            if loc:
                result.extend(_gg._pack_location_beats(group, loc, capped, overrides or {}))
            else:
                pool = list(capped) * 100
                mode_keys = pack_beats(len(group), pool)
                result.extend(mode_keys)
        else:
            # n_comp < 3，用完整 pool
            if loc:
                result.extend(
                    _gg._pack_location_beats(group, loc, list(pool_template), overrides or {})
                )
            else:
                pool = list(pool_template) * 100
                mode_keys = pack_beats(len(group), pool)
                result.extend(mode_keys)

    return result


def pad_to_aspect_ratio(panel, target_aspect: str, fill_color=(220, 220, 220)):
    """panel 上下/左右补白到目标比例。不裁剪、不拉伸。
    如果 panel 已经比目标更宽/更高，原样返回（不裁剪）。
    白色填充，并向内覆盖几像素盖住 AI 生成的边框线。"""
    from PIL import Image, ImageDraw

    w, h = panel.size
    aw, ah = map(int, target_aspect.replace(":", "x").split("x"))
    target_ratio = aw / ah
    current_ratio = w / h

    if abs(current_ratio - target_ratio) < 0.08:
        return panel  # 已匹配（0.5K 小 panel trim 后比例会有几 % 偏移）

    white = (255, 255, 255)
    # 向内覆盖像素数：盖住原图边缘的边框线
    overlap = max(6, min(w, h) // 60)

    if current_ratio > target_ratio:
        # panel 更宽 → 需要加高（上下补白）
        new_h = int(w / target_ratio)
        result = Image.new("RGB", (w, new_h), white)
        py = (new_h - h) // 2
        result.paste(panel, (0, py))
        # 白色覆盖原图顶部 / 底部 overlap 行
        draw = ImageDraw.Draw(result)
        draw.rectangle([0, py, w - 1, py + overlap - 1], fill=white)
        draw.rectangle([0, py + h - overlap, w - 1, py + h - 1], fill=white)
        return result
    else:
        # panel 更高 → 需要加宽（左右补白）
        new_w = int(h * target_ratio)
        result = Image.new("RGB", (new_w, h), white)
        px = (new_w - w) // 2
        result.paste(panel, (px, 0))
        # 白色覆盖原图左侧 / 右侧 overlap 列
        draw = ImageDraw.Draw(result)
        draw.rectangle([px, 0, px + overlap - 1, h - 1], fill=white)
        draw.rectangle([px + w - overlap, 0, px + w - 1, h - 1], fill=white)
        return result


def pack_beats(total_beats: int, pool: list[str]) -> list[str]:
    """贪心 bin-pack：优先减少网格数量。

    策略：先尝试找一个能装下所有 beats 的最小网格；
    找不到时取最大网格装满，剩余递归。
    """
    if total_beats <= 0:
        return [pool[0]] if pool else []

    # 去重并按容量从小到大排序
    unique_modes = list(dict.fromkeys(pool))  # 保序去重
    by_cap_asc = sorted(unique_modes, key=lambda mk: REGEN_MODE_CONFIGS[mk]["capacity"])

    # 1. 尝试用单个网格装下所有 beats（选最小能装下的）
    for mk in by_cap_asc:
        if REGEN_MODE_CONFIGS[mk]["capacity"] >= total_beats:
            return [mk]

    # 2. 装不下 → 取最大网格，剩余递归
    largest = by_cap_asc[-1]
    largest_cap = REGEN_MODE_CONFIGS[largest]["capacity"]
    remaining = total_beats - largest_cap
    return [largest] + pack_beats(remaining, pool)
