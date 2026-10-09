"""网格切分：crop_sketch_panels / perfect_grid_split / scene_grid_split（B3 拆包自单文件 grid_generation.py）。"""

import time
from pathlib import Path
from typing import Dict, List

from novelvideo.models import beat_scene_id

from .packing import (
    pad_to_aspect_ratio,
    DEFAULT_POOL_TEMPLATE,
    pack_beats,
    LOCATION_POOL_TEMPLATE,
    MANY_CHARS_REF_THRESHOLD,
    _count_batch_composite_chars,
    MANY_CHARS_MAX_CAPACITY,
    _smart_repack_beats,
)
from .regen_modes import REGEN_MODE_CONFIGS
from .modes import LOCATION_ASPECT_CONFIGS
from .batch_split import _coalesce_locations, _merge_small_grids

from novelvideo.generators import grid_generation as _gg


def crop_sketch_panels(
    sketch_path: str,
    beat_numbers: List[int],
    target_rows: int,
    target_cols: int,
    output_path: str,
    label_beats: bool = False,
    beat_sketch_paths: Dict[int, str] = None,
    target_aspect: str = None,
) -> str:
    """从草图中按 beat 编号提取 panel，拼接成目标布局。

    导出和生成共用此函数，确保所见即所得（WYSIWYG）。

    Args:
        sketch_path: 草图文件路径或草图目录路径
        beat_numbers: 实际 beat 编号列表（1-based），如 [2, 5, 8]
        target_rows: 目标网格行数
        target_cols: 目标网格列数
        output_path: 输出文件路径
        label_beats: 是否在每个 panel 左上角标注 beat 编号
        beat_sketch_paths: {beat_num: full_path} 从图片池取的 per-beat 草图路径
        target_aspect: 目标比例（如 "9:16"），非空时补白到该比例

    Returns:
        保存后的图片路径
    """
    from PIL import Image
    from novelvideo.generators.grid_splitter import _trim_outer_border
    import numpy as np

    def _trim_panel(panel_img):
        """裁掉单个 panel 的白边。"""
        gray_arr = np.array(panel_img.convert("L"))
        trimmed, _ = _trim_outer_border(panel_img, gray_arr)
        return trimmed

    panels = []
    panel_width = None
    panel_height = None
    pool_hit = 0

    for beat_num in beat_numbers:
        # 优先从图片池取单个 beat 草图
        if beat_sketch_paths and beat_num in beat_sketch_paths:
            pool_img = Image.open(beat_sketch_paths[beat_num])
            pool_img = _trim_panel(pool_img)
            if target_aspect:
                pool_img = pad_to_aspect_ratio(pool_img, target_aspect)
            if panel_width is None:
                panel_width = pool_img.width
                panel_height = pool_img.height
            else:
                if pool_img.size != (panel_width, panel_height):
                    pool_img = pool_img.resize((panel_width, panel_height), Image.LANCZOS)
            panels.append(pool_img)
            pool_hit += 1
        else:
            # 无草图 → 灰色占位
            if panel_width:
                panels.append(Image.new("RGB", (panel_width, panel_height), (128, 128, 128)))

    if pool_hit > 0:
        now = time.time()
        if now - getattr(_gg.crop_sketch_panels, "_last_log_t", 0.0) >= 5.0:
            print(f"[crop_sketch_panels] 从图片池取 {pool_hit}/{len(beat_numbers)} 个 beat 草图")
            _gg.crop_sketch_panels._last_log_t = now

    if not panels or panel_width is None:
        raise ValueError(f"无法从草图中提取 beat {beat_numbers}")

    target_width = target_cols * panel_width
    target_height = target_rows * panel_height
    result_img = Image.new("RGB", (target_width, target_height), (255, 255, 255))

    for i, panel in enumerate(panels):
        if i >= target_rows * target_cols:
            break
        r = i // target_cols
        c = i % target_cols
        x = c * panel_width
        y = r * panel_height
        result_img.paste(panel, (x, y))

    # 画面板间分割线
    if target_rows > 1 or target_cols > 1:
        from PIL import ImageDraw as _ImageDraw

        _draw = _ImageDraw.Draw(result_img)
        _line_w = max(1, min(panel_width, panel_height) // 200)
        _line_color = (180, 180, 180)
        for c in range(1, target_cols):
            _x = c * panel_width
            _draw.line([(_x, 0), (_x, target_height)], fill=_line_color, width=_line_w)
        for r in range(1, target_rows):
            _y = r * panel_height
            _draw.line([(0, _y), (target_width, _y)], fill=_line_color, width=_line_w)

    # 在每个 panel 左上角标注 beat 编号
    if label_beats and beat_numbers:
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(result_img)
        font_size = max(16, min(panel_width, panel_height) // 6)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()
        for i, beat_num in enumerate(beat_numbers):
            if i >= target_rows * target_cols:
                break
            r = i // target_cols
            c = i % target_cols
            x = c * panel_width + 4
            y = r * panel_height + 2
            label = f"B{beat_num}"
            # 黑色描边 + 白色文字，确保可读
            for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1), (-2, 0), (2, 0), (0, -2), (0, 2)]:
                draw.text((x + dx, y + dy), label, fill="black", font=font)
            draw.text((x, y), label, fill="white", font=font)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result_img.save(output_path, quality=85)
    return output_path


def get_optimal_grid_size(beat_count: int, max_grid: int = 16) -> tuple[int, int]:
    """根据 beat 数量选择最优网格大小，最小化黑色填充。

    注意：此函数已被 perfect_grid_split() 替代用于批量生成。
    保留此函数用于向后兼容和单网格重新生成。

    Args:
        beat_count: 需要填充的 beat 数量
        max_grid: 最大网格容量

    Returns:
        (rows, cols) 元组
    """
    if beat_count <= 0:
        return (1, 1)  # 最小网格：单张

    # 可用网格（按容量从小到大排列）
    # 包含竖屏 Panel 模式: 1x4=4, 3x2=6, 4x3=12, 5x4=20
    grid_options = [
        (1, 1),  # 1
        (1, 3),  # 3
        (1, 4),  # 4  竖屏 panel (21:9 宽屏)
        (2, 2),  # 4
        (3, 2),  # 6  竖屏 panel
        (3, 3),  # 9
        (4, 3),  # 12 竖屏 panel
        (5, 3),  # 15 竖屏 panel
        (4, 4),  # 16
        (5, 4),  # 20 竖屏 panel
        (5, 5),  # 25
    ]

    for rows, cols in grid_options:
        capacity = rows * cols
        # 跳过超过最大限制的网格
        if capacity > max_grid:
            continue
        if beat_count <= capacity:
            return (rows, cols)

    # 超过所有可用网格，返回最大允许的网格
    for rows, cols in reversed(grid_options):
        if rows * cols <= max_grid:
            return (rows, cols)

    # 默认返回 1x1
    return (1, 1)


def perfect_grid_split(total_beats: int, max_grid: int = 12) -> list[str]:
    """完美分割 beats，使每个网格都正好填满，不需要任何填充 panel。

    内部代理到 pack_beats，使用无限池。

    Args:
        total_beats: 总 beat 数量
        max_grid: 最大网格容量（如 12 表示最大 4x3）

    Returns:
        mode_key 列表，如 ["3x2_9-16", "1x1_9-16"]
    """
    template = DEFAULT_POOL_TEMPLATE
    # 过滤超过 max_grid 的，然后 * 100 生成无限池
    pool = [mk for mk in template if REGEN_MODE_CONFIGS[mk]["capacity"] <= max_grid] * 100
    if not pool:
        smallest = min(template, key=lambda mk: REGEN_MODE_CONFIGS[mk]["capacity"])
        pool = [smallest] * 100
    return pack_beats(total_beats, pool)


def scene_grid_split(
    all_beats: List[dict],
    aspect_mode: str = "9:16",
    pool_template: List[str] | None = None,
    character_map: Dict[str, dict] | None = None,
) -> List[dict]:
    """按 scene_id 分组 beats，每组用最少网格数覆盖。

    将同一 scene_id 的 beat 聚合到同一网格，减少跨网格场景漂移。
    每个 scene_id 组根据 beat 数量自动选择最优网格尺寸。

    积木块序列: 2x2(4), 1x3(3), 1x2(2), 1x1(1)
    2x2 是最大网格，与 2K 1:1 配合效果最佳。

    Args:
        all_beats: 所有 beat 数据（含 scene_id / beat_number 字段）
        aspect_mode: 比例模式（"9:16", "1:1" 等），用于覆盖特定网格尺寸的比例
        pool_template: 自定义积木池模板，优先于 aspect_mode 选择

    Returns:
        网格计划列表，每项：
        {
            "scene_id": "家·餐厅",
            "rows": 2, "cols": 2,
            "mode_key": "2x2_1-1",
            "beats": [beat_dict, ...],
            "beat_numbers": [4, 5, 6, ...],
            "padding_count": 0,
        }
    """
    from collections import OrderedDict

    if pool_template:
        template = pool_template
    else:
        template = LOCATION_POOL_TEMPLATE
    overrides = LOCATION_ASPECT_CONFIGS.get(aspect_mode, {})

    # 1. 按 scene_id 聚合，保持首次出现顺序
    location_groups: OrderedDict[str, List[dict]] = OrderedDict()
    for beat in all_beats:
        loc = beat_scene_id(beat) or "未知场景"
        if loc not in location_groups:
            location_groups[loc] = []
        location_groups[loc].append(beat)

    # 1.5 粗粒度合并（家·客厅 + 家·餐厅 → 家）
    location_groups = _coalesce_locations(location_groups)

    # 2. 对每个 scene_id 组，按 per-beat composite 数细分后分别 pack
    result = []
    for loc, beats in location_groups.items():
        if character_map:
            # 按 per-beat composite 角色数分成轻量组（<= 2）和重量组（>= 3）
            light_beats = []  # <= 2 composite → 先用完整 pool，再 post-process
            heavy_beats = []  # >= 3 composite → cap 到 2x2
            for beat in beats:
                n = len(_gg._get_beat_visual_composite_chars(beat, character_map))
                if n >= MANY_CHARS_REF_THRESHOLD:
                    heavy_beats.append(beat)
                else:
                    light_beats.append(beat)

            # 轻量组：智能 repack（按 composite 连续分组，尽量用大网格）
            if light_beats:
                light_entries = _gg._pack_location_beats(
                    light_beats,
                    loc,
                    list(template),
                    overrides,
                )
                for entry in light_entries:
                    grid_cc = _count_batch_composite_chars(entry["beats"], character_map)
                    if (
                        grid_cc >= MANY_CHARS_REF_THRESHOLD
                        and len(entry["beats"]) > MANY_CHARS_MAX_CAPACITY
                    ):
                        # 智能拆分：按 composite 连续分组，≤2 的子组用完整 pool
                        result.extend(
                            _smart_repack_beats(
                                entry["beats"],
                                character_map,
                                list(template),
                                overrides,
                                loc,
                            )
                        )
                    else:
                        result.append(entry)
            # 重量组：智能 repack
            if heavy_beats:
                result.extend(
                    _smart_repack_beats(
                        heavy_beats,
                        character_map,
                        list(template),
                        overrides,
                        loc,
                    )
                )
        else:
            result.extend(
                _gg._pack_location_beats(
                    beats,
                    loc,
                    list(template),
                    overrides,
                )
            )

    # 3. 合并小网格：连续的 1-beat 网格合并成更大的网格
    result = _merge_small_grids(result, template, character_map)

    return result
