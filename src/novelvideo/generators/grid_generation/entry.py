"""beat 编号助手 / regenerate_selected_beats / create_grid_generator 工厂（B3 拆包自单文件 grid_generation.py）。"""

import math
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import GridGenerationResult
from .modes import parse_regen_mode
from .regen_modes import REGEN_MODE_CONFIGS
from .grid_generator import GridGenerator

from novelvideo.generators import grid_generation as _gg


def _generation_beat_number(beat: dict, fallback_index: int) -> int:
    raw_beat_number = beat.get("beat_number")
    if raw_beat_number is not None:
        try:
            return int(raw_beat_number)
        except (TypeError, ValueError):
            pass
    raw_panel_index = beat.get("panel_index")
    if raw_panel_index is not None:
        try:
            return int(raw_panel_index)
        except (TypeError, ValueError):
            pass
    return fallback_index + 1


async def regenerate_selected_beats(
    selected_beats: List[dict],
    mode_key: str,
    character_map: Dict[str, dict],
    style: str,
    output_dir: str,
    scene_menu: list[dict] | list | None = None,
    prop_menu: list[dict] | list | None = None,
    sketch_colors: dict[str, str] | None = None,
    ethnicity: str = "Chinese",
    is_sketch: bool = False,
    sketch_dir: str = "",
    api_key: Optional[str] = None,
    episode_grids_dir: str = "",
    beat_sketch_paths_override: dict[int, str] | None = None,
    scene_refs_override: dict[int, list[Any]] | None = None,
    prop_refs_override: dict[int, list[Any]] | None = None,
    sketch_aspect_padding: bool = False,
    force_image_size: Optional[str] = None,
    generator_config: Optional[dict] = None,
) -> List[GridGenerationResult]:
    """再生选中的 beats（支持 render 和 sketch 模式）。

    从 REGEN_MODE_CONFIGS[mode_key] 读取 rows, cols, aspect_ratio, image_size，
    使用 perfect_grid_split 分割后逐 grid 调用 generate_grid。

    Args:
        selected_beats: 选中的 beat 数据列表
        mode_key: 再生模式 key，如 "1x1_9-16", "2x2_1-1"
        character_map: 角色映射
        style: 风格
        output_dir: 输出目录
        ethnicity: 种族
        is_sketch: 是否为草图模式
        sketch_dir: 草图目录
        api_key: API key
        sketch_aspect_padding: 草图补白到目标比例

    Returns:
        GridGenerationResult 列表
    """
    rows, cols, aspect_ratio, image_size = parse_regen_mode(mode_key)
    capacity = rows * cols

    # 分割 beats
    num_grids = math.ceil(len(selected_beats) / capacity)
    grid_splits = [mode_key] * num_grids
    print(
        f"[RegenBeats] mode={mode_key}, beats={len(selected_beats)}, "
        f"splits={grid_splits}, aspect_ratio={aspect_ratio}"
    )

    generator = _gg.create_grid_generator(api_key, config=generator_config)
    results = []
    beat_offset = 0
    regen_id = uuid.uuid4().hex

    for grid_idx, split_mk in enumerate(grid_splits, start=1):
        split_cfg = REGEN_MODE_CONFIGS[split_mk]
        g_rows, g_cols = split_cfg["rows"], split_cfg["cols"]
        grid_beat_count = split_cfg["capacity"]
        grid_beats = selected_beats[beat_offset : beat_offset + grid_beat_count]
        beat_offset += grid_beat_count

        # 输出路径
        output_path = str(
            Path(output_dir) / f"regen_{mode_key}_g{grid_idx:02d}_{regen_id}.png"
        )

        # 提取 beat 编号用于 location_beat_numbers
        beat_numbers = [_gg._generation_beat_number(b, i) for i, b in enumerate(grid_beats)]

        # 从图片池构建 per-beat 草图路径
        grid_beat_sketch_paths = None
        if episode_grids_dir and not is_sketch:
            from novelvideo.generators.pool_indexer import build_beat_sketch_paths

            grid_beat_sketch_paths = build_beat_sketch_paths(episode_grids_dir, beat_numbers)
        if beat_sketch_paths_override and not is_sketch:
            grid_beat_sketch_paths = {
                int(beat_num): str(path)
                for beat_num, path in beat_sketch_paths_override.items()
                if int(beat_num) in {int(value) for value in beat_numbers}
            }

        result = await generator.generate_grid(
            beats=grid_beats,
            character_map=character_map,
            scene_menu=scene_menu,
            prop_menu=prop_menu,
            sketch_colors=sketch_colors,
            style=style,
            output_path=output_path,
            ethnicity=ethnicity,
            rows=g_rows,
            cols=g_cols,
            sketch=is_sketch,
            sketch_dir=sketch_dir if not is_sketch else "",
            location_beat_numbers=beat_numbers,
            mode_key=split_mk,
            beat_sketch_paths=grid_beat_sketch_paths,
            scene_refs_override=scene_refs_override,
            prop_refs_override=prop_refs_override,
            sketch_aspect_padding=sketch_aspect_padding,
            force_image_size=force_image_size,
        )
        result.beat_start_index = beat_offset - grid_beat_count
        result.beat_count = len(grid_beats)
        result.grid_rows = g_rows
        result.grid_cols = g_cols
        results.append(result)

        if result.success:
            print(f"[RegenBeats] Grid {grid_idx} 成功: {result.grid_image_path}")
        else:
            print(f"[RegenBeats] Grid {grid_idx} 失败: {result.error}")

    return results


def create_grid_generator(
    api_key: Optional[str] = None,
    config: Optional[dict] = None,
) -> GridGenerator:
    """创建网格生成器。

    Args:
        api_key: Google AI API Key

    Returns:
        GridGenerator 实例
    """
    return _gg.GridGenerator(api_key=api_key, config=config)
