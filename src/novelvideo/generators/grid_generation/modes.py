"""场景分组比例覆盖表与 grid mode 解析（parse_regen_mode 等）（B3 拆包自单文件 grid_generation.py）。"""

from typing import Dict

from .regen_modes import REGEN_MODE_CONFIGS
from .constants import SINGLE_CELL_RENDER_MODE_BY_ASPECT, SINGLE_CELL_RENDER_MODE_KEY
from .packing import LANDSCAPE_RENDER_POOL_TEMPLATE, LOCATION_POOL_TEMPLATE


# =============================================================================
# 场景分组比例覆盖表
# =============================================================================
# aspect_mode → {(rows, cols): (aspect_ratio, image_size)}
# 只列出需要覆盖默认查找的 grid size
LOCATION_ASPECT_CONFIGS = {
    "9:16": {
        # 2x2 不覆盖，走 SQUARE 默认 1:1
    },
    "1:1": {
        (2, 2): ("1:1", "2K"),
        (1, 1): ("1:1", "1K"),
    },
}


def parse_regen_mode(mode_key: str) -> tuple:
    """解析再生模式 key，返回 (rows, cols, aspect_ratio, image_size)。

    Args:
        mode_key: 如 '1x1_9-16', '2x2_1-1'

    Returns:
        (rows, cols, aspect_ratio, image_size)
    """
    cfg = REGEN_MODE_CONFIGS[mode_key]
    return cfg["rows"], cfg["cols"], cfg["aspect_ratio"], cfg["image_size"]


def get_default_mode_for_grid(grid_size: str) -> str:
    """Return the default mode key for a grid size like 1x1 / 2x2 / 3x3."""
    for key in REGEN_MODE_CONFIGS:
        if key.startswith(grid_size + "_"):
            return key
    raise ValueError(f"No regen mode for grid size: {grid_size}")


def get_regen_modes_for_grid(grid_size: str) -> list:
    """获取指定 grid_size 的所有模式 key 列表。

    Args:
        grid_size: 如 '1x1', '2x2'

    Returns:
        [mode_key, ...]
    """
    return [k for k in REGEN_MODE_CONFIGS if k.startswith(grid_size + "_")]


def grid_mode_to_mode_key(grid_mode: str) -> str:
    """将简单 grid_mode (如 '3x3') 转换为带比例的 mode_key (如 '3x3_1-1')。

    从 REGEN_MODE_CONFIGS 查找匹配的 mode_key。
    已经是 mode_key 格式的（含 '_'）直接返回。
    """
    # 已经包含比例信息（如 '3x3_1-1'），直接返回
    if "_" in grid_mode:
        return grid_mode
    parts = grid_mode.split("x")
    if len(parts) != 2:
        return grid_mode  # loc 等特殊模式
    try:
        rows, cols = int(parts[0]), int(parts[1])
    except ValueError:
        return grid_mode
    # 从 REGEN_MODE_CONFIGS 查找第一个匹配 (rows, cols) 的 mode_key
    for mk, cfg in REGEN_MODE_CONFIGS.items():
        if cfg["rows"] == rows and cfg["cols"] == cols:
            return mk
    return grid_mode  # fallback


def _single_cell_render_mode_key(aspect_mode: str) -> str:
    return SINGLE_CELL_RENDER_MODE_BY_ASPECT.get(
        str(aspect_mode or "").strip(),
        SINGLE_CELL_RENDER_MODE_KEY,
    )


def _render_pool_template_for_aspect(aspect_mode: str) -> list[str]:
    if str(aspect_mode or "").strip() == "16:9":
        # Square 16:9 grids keep every split cell at 16:9 while still combining beats.
        return list(LANDSCAPE_RENDER_POOL_TEMPLATE)
    return list(LOCATION_POOL_TEMPLATE)


# =============================================================================
# Shot-Level Grid 配置（v2.0 Shot-Centric）
# =============================================================================
# Shot 内 N 个 beats → 1 个 Grid，作为 Seedance 2.0 的 @图片 分镜参考
# 仅使用已验证的格式（Seedance 2.0 理解的 Grid 布局）

SHOT_GRID_CONFIGS: Dict[int, dict] = {
    1: {
        "rows": 1, "cols": 1, "aspect_ratio": "9:16", "image_size": "1K", "order_hint": "",
    },
    2: {
        "rows": 1, "cols": 2, "aspect_ratio": "16:9", "image_size": "2K", "order_hint": "从左到右",
    },
    3: {
        "rows": 1, "cols": 3, "aspect_ratio": "21:9", "image_size": "2K", "order_hint": "从左到右",
    },
    4: {
        "rows": 2,
        "cols": 2,
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "order_hint": "从左到右从上到下",
    },
    5: {
        "rows": 3,
        "cols": 3,
        "aspect_ratio": "1:1",
        "image_size": "4K",
        "order_hint": "从左到右从上到下，前5格",
    },
}


def get_shot_grid_config(beat_count: int) -> dict:
    """获取 Shot 级 Grid 配置。

    Args:
        beat_count: Shot 内 beat 数量（1-5）

    Returns:
        {"rows", "cols", "aspect_ratio", "image_size", "order_hint"}
    """
    beat_count = max(1, min(5, beat_count))
    return SHOT_GRID_CONFIGS[beat_count]
