"""Sketch/regen 模式配置表（REGEN_MODE_CONFIGS 等）与查询函数（B3 拆包自单文件 grid_generation.py）。"""

from typing import Dict


# =============================================================================
# Sketch 模式配置
# =============================================================================
# Sketch 默认比例（向后兼容）
DEFAULT_SKETCH_ASPECT_RATIO = "2:3"

# =============================================================================
# 再生模式配置表（Regen Mode Configs）
# =============================================================================
# mode_key 格式: "{rows}x{cols}_{aspect_ratio_normalized}"
# 唯一全局配置表，所有 pool 引用此表
# 后续需要新比例时（如 1x1_16-9 横屏），直接在此表新增即可

REGEN_MODE_CONFIGS: Dict[str, dict] = {
    "1x1_1-1": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "1x1_1:1 1K",
        "capacity": 1,
    },
    "1x1_9-16": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "1x1_9:16 1K",
        "capacity": 1,
    },
    "1x1_2-3": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "1x1_2:3 1K",
        "capacity": 1,
    },
    "1x1_16-9": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "1x1_16:9 1K",
        "capacity": 1,
    },
    "1x2_4-3": {
        "rows": 1,
        "cols": 2,
        "aspect_ratio": "4:3",
        "image_size": "1K",
        "label": "1x2_4:3 1K",
        "capacity": 2,
    },
    "1x2_16-9": {
        "rows": 1,
        "cols": 2,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "1x2_16:9 1K",
        "capacity": 2,
    },
    "1x3_16-9": {
        "rows": 1,
        "cols": 3,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "1x3_16:9 1K",
        "capacity": 3,
    },
    "1x3_21-9": {
        "rows": 1,
        "cols": 3,
        "aspect_ratio": "21:9",
        "image_size": "1K",
        "label": "1x3_21:9 1K",
        "capacity": 3,
    },
    "1x4_21-9": {
        "rows": 1,
        "cols": 4,
        "aspect_ratio": "21:9",
        "image_size": "1K",
        "label": "1x4_21:9 1K",
        "capacity": 4,
    },
    "1x6_4-1": {
        "rows": 1,
        "cols": 6,
        "aspect_ratio": "4:1",
        "image_size": "1K",
        "label": "1x6_4:1 1K",
        "capacity": 6,
    },
    "2x2_1-1": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "label": "2x2_1:1 2K",
        "capacity": 4,
    },
    "2x2_9-16": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "9:16",
        "image_size": "2K",
        "label": "2x2_9:16 2K",
        "capacity": 4,
    },
    "2x3_1-1": {
        "rows": 2,
        "cols": 3,
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "label": "2x3_1:1 2K",
        "capacity": 6,
    },
    "2x4_4-3": {
        "rows": 2,
        "cols": 4,
        "aspect_ratio": "4:3",
        "image_size": "2K",
        "label": "2x4_4:3 2K",
        "capacity": 8,
    },
    "2x2_2-3": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "2:3",
        "image_size": "2K",
        "label": "2x2_2:3 2K",
        "capacity": 4,
    },
    "2x2_16-9": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "16:9",
        "image_size": "2K",
        "label": "2x2_16:9 2K",
        "capacity": 4,
    },
    "3x2_9-16": {
        "rows": 3,
        "cols": 2,
        "aspect_ratio": "9:16",
        "image_size": "2K",
        "label": "3x2_9:16 2K",
        "capacity": 6,
    },
    "3x2_2-3": {
        "rows": 3,
        "cols": 2,
        "aspect_ratio": "2:3",
        "image_size": "2K",
        "label": "3x2_2:3 2K",
        "capacity": 6,
    },
    "3x3_1-1": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "1:1",
        "image_size": "4K",
        "label": "3x3_1:1 4K",
        "capacity": 9,
    },
    "3x3_9-16": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "9:16",
        "image_size": "4K",
        "label": "3x3_9:16 4K",
        "capacity": 9,
    },
    "3x3_2-3": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "2:3",
        "image_size": "2K",
        "label": "3x3_2:3 2K",
        "capacity": 9,
    },
    "3x3_16-9": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "16:9",
        "image_size": "4K",
        "label": "3x3_16:9 4K",
        "capacity": 9,
    },
    "4x3_9-16": {
        "rows": 4,
        "cols": 3,
        "aspect_ratio": "9:16",
        "image_size": "4K",
        "label": "4x3_9:16 4K",
        "capacity": 12,
    },
    "4x3_3-4": {
        "rows": 4,
        "cols": 3,
        "aspect_ratio": "3:4",
        "image_size": "4K",
        "label": "4x3_3:4 4K",
        "capacity": 12,
    },
    "4x4_1-1": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "1:1",
        "image_size": "4K",
        "label": "4x4_1:1 4K",
        "capacity": 16,
    },
    "4x4_16-9": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "16:9",
        "image_size": "4K",
        "label": "4x4_16:9 4K",
        "capacity": 16,
    },
    "5x4_9-16": {
        "rows": 5,
        "cols": 4,
        "aspect_ratio": "9:16",
        "image_size": "4K",
        "label": "5x4_9:16 4K",
        "capacity": 20,
    },
    "5x5_1-1": {
        "rows": 5,
        "cols": 5,
        "aspect_ratio": "1:1",
        "image_size": "4K",
        "label": "5x5_1:1 4K",
        "capacity": 25,
    },
    # Sketch 专用
    "1x1_1-1_sketch": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "1x1_1:1 Sketch",
        "capacity": 1,
    },
    "1x1_9-16_sketch": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "1x1_9:16 Sketch",
        "capacity": 1,
    },
    "1x1_2-3_sketch": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "1x1_2:3 Sketch",
        "capacity": 1,
    },
    "1x1_16-9_sketch": {
        "rows": 1,
        "cols": 1,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "1x1_16:9 Sketch",
        "capacity": 1,
    },
    "1x2_4-3_sketch": {
        "rows": 1,
        "cols": 2,
        "aspect_ratio": "4:3",
        "image_size": "1K",
        "label": "1x2_4:3 Sketch",
        "capacity": 2,
    },
    "2x2_2-3_sketch": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "2x2_2:3 Sketch",
        "capacity": 4,
    },
    "2x2_16-9_sketch": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "2x2_16:9 Sketch",
        "capacity": 4,
    },
    "2x2_9-16_sketch": {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "2x2_9:16 Sketch",
        "capacity": 4,
    },
    "3x3_1-1_sketch": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "3x3_1:1 Sketch",
        "capacity": 9,
    },
    "3x3_9-16_sketch": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "3x3_9:16 Sketch",
        "capacity": 9,
    },
    "3x3_3-4_sketch": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "3:4",
        "image_size": "1K",
        "label": "3x3_3:4 Sketch",
        "capacity": 9,
    },
    "3x3_2-3_sketch": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "3x3_2:3 Sketch",
        "capacity": 9,
    },
    "3x3_16-9_sketch": {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "3x3_16:9 Sketch",
        "capacity": 9,
    },
    "2x3_1-1_sketch": {
        "rows": 2,
        "cols": 3,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "2x3_1:1 Sketch",
        "capacity": 6,
    },
    "2x4_4-3_sketch": {
        "rows": 2,
        "cols": 4,
        "aspect_ratio": "4:3",
        "image_size": "1K",
        "label": "2x4_4:3 Sketch",
        "capacity": 8,
    },
    "4x3_3-4_sketch": {
        "rows": 4,
        "cols": 3,
        "aspect_ratio": "3:4",
        "image_size": "1K",
        "label": "4x3_3:4 Sketch",
        "capacity": 12,
    },
    "4x4_1-1_sketch": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "4x4_1:1 Sketch",
        "capacity": 16,
    },
    "4x4_9-16_sketch": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "4x4_9:16 Sketch",
        "capacity": 16,
    },
    "4x4_2-3_sketch": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "4x4_2:3 Sketch",
        "capacity": 16,
    },
    "4x4_16-9_sketch": {
        "rows": 4,
        "cols": 4,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "4x4_16:9 Sketch",
        "capacity": 16,
    },
    "5x5_1-1_sketch": {
        "rows": 5,
        "cols": 5,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "label": "5x5_1:1 Sketch",
        "capacity": 25,
    },
    "5x5_2-3_sketch": {
        "rows": 5,
        "cols": 5,
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "label": "5x5_2:3 Sketch",
        "capacity": 25,
    },
    "5x5_16-9_sketch": {
        "rows": 5,
        "cols": 5,
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "label": "5x5_16:9 Sketch",
        "capacity": 25,
    },
    "5x5_9-16_sketch": {
        "rows": 5,
        "cols": 5,
        "aspect_ratio": "9:16",
        "image_size": "1K",
        "label": "5x5_9:16 Sketch",
        "capacity": 25,
    },
}


def get_sketch_default_mode_key(aspect_ratio: str = DEFAULT_SKETCH_ASPECT_RATIO) -> str:
    target = aspect_ratio.replace(":", "-")
    candidate = f"5x5_{target}_sketch"
    if candidate in REGEN_MODE_CONFIGS:
        return candidate
    return "5x5_2-3_sketch"


def get_sketch_nxn_modes(
    aspect_ratio: str = DEFAULT_SKETCH_ASPECT_RATIO,
) -> list[tuple[int, str, int, int]]:
    result: list[tuple[int, str, int, int]] = []
    for cap, rows, cols in [(1, 1, 1), (4, 2, 2), (9, 3, 3), (16, 4, 4), (25, 5, 5)]:
        matches = [
            mode_key
            for mode_key, cfg in REGEN_MODE_CONFIGS.items()
            if mode_key.endswith("_sketch")
            and cfg.get("rows") == rows
            and cfg.get("cols") == cols
            and cfg.get("aspect_ratio") == aspect_ratio
        ]
        if matches:
            result.append((cap, matches[0], rows, cols))
            continue
        same_size_fallbacks = [
            mode_key
            for mode_key, cfg in REGEN_MODE_CONFIGS.items()
            if mode_key.endswith("_sketch") and cfg.get("rows") == rows and cfg.get("cols") == cols
        ]
        if same_size_fallbacks:
            preferred_same_size = next(
                (
                    mode_key
                    for mode_key in same_size_fallbacks
                    if REGEN_MODE_CONFIGS[mode_key].get("aspect_ratio")
                    == DEFAULT_SKETCH_ASPECT_RATIO
                ),
                same_size_fallbacks[0],
            )
            result.append((cap, preferred_same_size, rows, cols))
            continue
        raise ValueError(f"No sketch mode available for grid size {rows}x{cols}")
    return result


# Sketch 默认 mode_key，其余信息从 REGEN_MODE_CONFIGS 查表
SKETCH_DEFAULT_MODE_KEY = get_sketch_default_mode_key()
PLANNER_VERSION = "2026-04-18-v1"
