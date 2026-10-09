# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地图像拼接（不调模型）。

角色一致性参考里有一种纯机械的活：把「三视图造型图」和「五官垫图」并排合成一张
图，方便下游一次引用。这种事没有创造内容，用 PIL 本地拼比再打一次模型快两个数量级
（毫秒 vs 60s），所以放在 utils 里供路由与生成链路共用。
"""

from __future__ import annotations

from pathlib import Path


def stitch_side_by_side(
    left_path: str | Path,
    right_path: str | Path,
    output_path: str | Path,
    *,
    gap_px: int = 16,
    background: tuple[int, int, int] = (245, 245, 245),
) -> Path:
    """左右拼接两张图，统一到较大高度后按比例缩放，中间留一条分隔缝。

    两边都按各自宽高比缩放，只统一高度、不改宽高比，避免把角色拉变形；分隔缝用
    接近五官垫图的浅灰底，拼出来的整图看上去仍是一张三视图设定表的质感。

    Args:
        left_path: 左半图（例如身份三视图造型图）
        right_path: 右半图（例如五官垫图）
        output_path: 输出路径；父目录会自动创建
        gap_px: 两图之间的分隔宽度（像素），0 表示紧贴
        background: 画布底色，用于填充分隔缝

    Returns:
        输出路径（与 ``output_path`` 同一个 Path）
    """
    from PIL import Image

    left = Image.open(left_path).convert("RGB")
    right = Image.open(right_path).convert("RGB")

    target_height = max(left.height, right.height)
    left_resized = left.resize(
        (max(1, int(round(target_height * left.width / left.height))), target_height),
        Image.Resampling.LANCZOS,
    )
    right_resized = right.resize(
        (max(1, int(round(target_height * right.width / right.height))), target_height),
        Image.Resampling.LANCZOS,
    )

    gap = max(0, int(gap_px))
    total_width = left_resized.width + gap + right_resized.width
    canvas = Image.new("RGB", (total_width, target_height), background)
    canvas.paste(left_resized, (0, 0))
    canvas.paste(right_resized, (left_resized.width + gap, 0))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "PNG")
    return out
