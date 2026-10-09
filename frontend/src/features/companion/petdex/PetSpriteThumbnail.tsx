// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CSSProperties } from "react";
import {
  PETDEX_GRID_COLS,
  PETDEX_GRID_ROWS,
} from "@/features/companion/petdex/petdex-pets";

type PetSpriteThumbnailProps = {
  url: string;
  /** 网格列/行数（默认 petdex 标准 8×9）。 */
  cols?: number;
  rows?: number;
  className?: string;
};

/**
 * 静态精灵图缩略图：取左上角 idle 首帧，不做动画。画廊行 / 导入预览共用，避免三处
 * 各写一遍背景网格定位（动画版见 SpritePetCompanion）。
 */
export function PetSpriteThumbnail({
  url,
  cols = PETDEX_GRID_COLS,
  rows = PETDEX_GRID_ROWS,
  className,
}: PetSpriteThumbnailProps) {
  const style: CSSProperties = {
    backgroundImage: `url("${url}")`,
    backgroundSize: `${cols * 100}% ${rows * 100}%`,
    backgroundPosition: "0% 0%",
    backgroundRepeat: "no-repeat",
    imageRendering: "pixelated",
  };
  return <div className={className} style={style} />;
}
