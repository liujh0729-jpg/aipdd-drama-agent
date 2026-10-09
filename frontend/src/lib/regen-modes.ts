// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface RegenMode {
  key: string;
  label: string;
  capacity: number;
}

/** Sketch regeneration modes, matching NiceGUI _selected_sketch_regen_mode_keys(). */
export const SKETCH_REGEN_MODES: readonly RegenMode[] = [
  { key: "5x5_2-3_sketch", label: "5×5_2:3 Sketch", capacity: 25 },
  { key: "1x1_2-3_sketch", label: "1×1_2:3 Sketch", capacity: 1 },
  { key: "1x1_1-1_sketch", label: "1×1_1:1 Sketch", capacity: 1 },
  { key: "2x2_2-3_sketch", label: "2×2_2:3 Sketch", capacity: 4 },
  { key: "3x3_2-3_sketch", label: "3×3_2:3 Sketch", capacity: 9 },
  { key: "1x1_1-1", label: "1×1_1:1 1K", capacity: 1 },
  { key: "1x1_9-16_sketch", label: "1×1_9:16 Sketch", capacity: 1 },
  { key: "1x1_16-9_sketch", label: "1×1_16:9 Sketch", capacity: 1 },
  { key: "1x2_4-3_sketch", label: "1×2_4:3 Sketch", capacity: 2 },
  { key: "2x2_1-1", label: "2×2_1:1 2K", capacity: 4 },
  { key: "2x2_16-9_sketch", label: "2×2_16:9 Sketch", capacity: 4 },
  { key: "2x2_9-16_sketch", label: "2×2_9:16 Sketch", capacity: 4 },
  { key: "2x4_4-3_sketch", label: "2×4_4:3 Sketch", capacity: 8 },
  { key: "3x2_2-3", label: "3×2_2:3 2K", capacity: 6 },
  { key: "3x3_1-1_sketch", label: "3×3_1:1 Sketch", capacity: 9 },
  { key: "3x3_9-16_sketch", label: "3×3_9:16 Sketch", capacity: 9 },
  { key: "3x3_3-4_sketch", label: "3×3_3:4 Sketch", capacity: 9 },
  { key: "3x3_16-9_sketch", label: "3×3_16:9 Sketch", capacity: 9 },
  { key: "4x3_3-4_sketch", label: "4×3_3:4 Sketch", capacity: 12 },
  { key: "4x4_1-1_sketch", label: "4×4_1:1 Sketch", capacity: 16 },
  { key: "4x4_16-9_sketch", label: "4×4_16:9 Sketch", capacity: 16 },
  { key: "5x5_1-1_sketch", label: "5×5_1:1 Sketch", capacity: 25 },
  { key: "5x5_16-9_sketch", label: "5×5_16:9 Sketch", capacity: 25 },
  { key: "5x5_9-16_sketch", label: "5×5_9:16 Sketch", capacity: 25 },
  { key: "5x5_1-1", label: "5×5_1:1 4K", capacity: 25 },
];

/** Render regeneration modes (2:3 aspect, publication quality 1K-2K) */
export const RENDER_REGEN_MODES: readonly RegenMode[] = [
  { key: "1x1_2-3", label: "1×1_2:3", capacity: 1 },
  { key: "2x2_2-3", label: "2×2_2:3", capacity: 4 },
  { key: "3x3_2-3", label: "3×3_2:3", capacity: 9 },
];

/** Find the smallest mode that fits the given beat count. */
export function bestFitMode(modes: readonly RegenMode[], count: number): RegenMode {
  const fitting = modes.filter((mode) => mode.capacity >= count);
  if (fitting.length === 0) return modes[modes.length - 1];
  return fitting.reduce((best, mode) =>
    mode.capacity < best.capacity ? mode : best,
  );
}

/** Number of batches the backend will create for overflow. */
export function overflowBatchCount(mode: RegenMode, selectedCount: number): number {
  return Math.ceil(selectedCount / mode.capacity);
}
