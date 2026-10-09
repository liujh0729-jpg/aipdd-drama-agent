// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Pure layout math for "分镜组" (storyboard groups) created via 合并分镜组.
// Members are packed into a uniform 宫格 grid of equal cells, in reading order
// (row by row). Kept free of React / store imports so it can be unit-tested and
// reused by both the store action and the toolbar re-layout.
//
// Cells are sized to FULLY CONTAIN the largest member while honouring the chosen
// aspect ratio. Canvas nodes carry large minimum sizes (an image node won't
// render below ~520×420), so cells must never be smaller than the content —
// otherwise the node clamps to its min and spills out of the cell / group.

export interface StoryboardAspectOption {
  /** Stable key persisted on the group node, e.g. "16:9". */
  key: string;
  label: string;
  /** width / height. */
  ratio: number;
}

export const STORYBOARD_ASPECTS: StoryboardAspectOption[] = [
  { key: '16:9', label: '16:9', ratio: 16 / 9 },
  { key: '4:3', label: '4:3', ratio: 4 / 3 },
  { key: '1:1', label: '1:1', ratio: 1 },
  { key: '3:4', label: '3:4', ratio: 3 / 4 },
  { key: '9:16', label: '9:16', ratio: 9 / 16 },
];

export const DEFAULT_STORYBOARD_ASPECT = '16:9';
// Thin gap + even padding, like the libtv reference.
export const STORYBOARD_CELL_GAP = 8;
export const STORYBOARD_PADDING = 12;
// Thumbnail cell width — the storyboard board renders members as large previews
// (libtv style: small scattered nodes become a big grid), independent of the
// interactive node sizes.
export const STORYBOARD_THUMB_WIDTH = 560;
// Floating header (`-top-7`) sits above the first row; reserve room for it.
export const STORYBOARD_HEADER_PADDING = 34;
const MIN_GROUP_WIDTH = 220;
const MIN_GROUP_HEIGHT = 140;

export function resolveStoryboardAspectRatio(aspectKey: string | undefined): number {
  const match = STORYBOARD_ASPECTS.find((option) => option.key === aspectKey);
  return match ? match.ratio : 16 / 9;
}

/**
 * Default column count for `count` members: a near-square grid (ceil(√n)), so 5
 * members → 3 columns / 2 rows like the reference. A caller-supplied positive
 * `requested` overrides it (clamped to [1, count]).
 */
export function resolveStoryboardCols(count: number, requested?: number): number {
  if (count <= 0) {
    return 1;
  }
  if (typeof requested === 'number' && Number.isFinite(requested) && requested >= 1) {
    return Math.min(Math.max(1, Math.round(requested)), count);
  }
  return Math.max(1, Math.ceil(Math.sqrt(count)));
}

/**
 * Smallest cell of the given aspect ratio that fully contains a `baseWidth` ×
 * `baseHeight` member box. The member is letterboxed inside; the cell is never
 * smaller than the content, so nodes never clamp past the cell.
 */
export function computeStoryboardCell(
  baseWidth: number,
  baseHeight: number,
  aspectKey: string | undefined
): { cellWidth: number; cellHeight: number } {
  const width = Math.max(1, Math.round(baseWidth));
  const height = Math.max(1, Math.round(baseHeight));
  const ratio = resolveStoryboardAspectRatio(aspectKey);
  if (width / height > ratio) {
    // Content is wider than the cell aspect → width-bound.
    return { cellWidth: width, cellHeight: Math.round(width / ratio) };
  }
  // Content is taller → height-bound.
  return { cellWidth: Math.round(height * ratio), cellHeight: height };
}

export interface StoryboardCellRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface StoryboardGridLayout {
  cols: number;
  rows: number;
  cellWidth: number;
  cellHeight: number;
  groupWidth: number;
  groupHeight: number;
  /** One rect per member, in reading order (index = member index). */
  cells: StoryboardCellRect[];
}

export interface StoryboardGridInput {
  count: number;
  cellWidth: number;
  cellHeight: number;
  cols?: number;
}

/**
 * Lay `count` equal cells into a reading-order grid. Coordinates are relative to
 * the group node's own origin; the group box wraps the grid with even padding
 * and extra room at the top for the floating header.
 */
/** Rect of a single grid slot (relative to the group origin), filled or empty. */
export function storyboardSlotRect(
  index: number,
  cols: number,
  cellWidth: number,
  cellHeight: number
): StoryboardCellRect {
  const col = index % cols;
  const row = Math.floor(index / cols);
  return {
    x: STORYBOARD_PADDING + col * (cellWidth + STORYBOARD_CELL_GAP),
    y: STORYBOARD_HEADER_PADDING + row * (cellHeight + STORYBOARD_CELL_GAP),
    width: cellWidth,
    height: cellHeight,
  };
}

export function computeStoryboardGridLayout(input: StoryboardGridInput): StoryboardGridLayout {
  const count = Math.max(0, Math.round(input.count));
  const cellWidth = Math.max(1, Math.round(input.cellWidth));
  const cellHeight = Math.max(1, Math.round(input.cellHeight));
  const cols = resolveStoryboardCols(count, input.cols);
  const rows = count > 0 ? Math.ceil(count / cols) : 1;

  const cells: StoryboardCellRect[] = [];
  for (let index = 0; index < count; index += 1) {
    cells.push(storyboardSlotRect(index, cols, cellWidth, cellHeight));
  }

  const groupWidth = Math.max(
    MIN_GROUP_WIDTH,
    STORYBOARD_PADDING * 2 + cols * cellWidth + (cols - 1) * STORYBOARD_CELL_GAP
  );
  const groupHeight = Math.max(
    MIN_GROUP_HEIGHT,
    STORYBOARD_HEADER_PADDING + STORYBOARD_PADDING + rows * cellHeight + (rows - 1) * STORYBOARD_CELL_GAP
  );

  return { cols, rows, cellWidth, cellHeight, groupWidth, groupHeight, cells };
}

/**
 * Layout of the COMPACT thumbnail board the storyboard group renders (libtv
 * style). Uses a fixed small thumbnail width and the chosen aspect — decoupled
 * from the members' real (large) node sizes. Shared by the GroupNode renderer
 * and the store so the box and the grid always match.
 */
export function computeStoryboardBoardLayout(input: {
  count: number;
  cols?: number;
  aspectKey?: string;
}): StoryboardGridLayout {
  const cellWidth = STORYBOARD_THUMB_WIDTH;
  const cellHeight = Math.max(1, Math.round(cellWidth / resolveStoryboardAspectRatio(input.aspectKey)));
  return computeStoryboardGridLayout({
    count: input.count,
    cols: input.cols,
    cellWidth,
    cellHeight,
  });
}
