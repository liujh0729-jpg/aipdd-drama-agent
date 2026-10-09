// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/** A saved canvas viewport (pan + zoom). */
export interface ViewportBookmark {
  x: number;
  y: number;
  zoom: number;
}

/** Fixed 10-slot array; index 0..9 maps to digit keys 1..9 then 0. */
export type ViewportBookmarks = (ViewportBookmark | null)[];

export const BOOKMARK_SLOT_COUNT = 10;

export function createEmptyBookmarks(): ViewportBookmarks {
  return Array.from({ length: BOOKMARK_SLOT_COUNT }, () => null);
}

/** '1'->0 … '9'->8, '0'->9. Anything else => null. */
export function digitToBookmarkIndex(digit: string): number | null {
  if (!/^[0-9]$/.test(digit)) {
    return null;
  }
  return digit === "0" ? 9 : Number(digit) - 1;
}

/** 0->'1' … 8->'9', 9->'0'. Out of range => null. */
export function bookmarkIndexToDigit(index: number): string | null {
  if (!Number.isInteger(index) || index < 0 || index >= BOOKMARK_SLOT_COUNT) {
    return null;
  }
  return index === 9 ? "0" : String(index + 1);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function isViewportBookmark(value: unknown): value is ViewportBookmark {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    isFiniteNumber(candidate.x) &&
    isFiniteNumber(candidate.y) &&
    isFiniteNumber(candidate.zoom) &&
    (candidate.zoom as number) > 0
  );
}

/** Coerce arbitrary persisted data into a clean length-10 array; never throws. */
export function normalizeBookmarks(input: unknown): ViewportBookmarks {
  const result = createEmptyBookmarks();
  if (!Array.isArray(input)) {
    return result;
  }
  for (let i = 0; i < BOOKMARK_SLOT_COUNT; i += 1) {
    const slot = input[i];
    if (isViewportBookmark(slot)) {
      result[i] = { x: slot.x, y: slot.y, zoom: slot.zoom };
    }
  }
  return result;
}

/** Flow-coordinate point at the center of the bookmarked viewport. */
export function bookmarkCenterInFlow(
  bookmark: ViewportBookmark,
  size: { width: number; height: number },
): { x: number; y: number } {
  return {
    x: (size.width / 2 - bookmark.x) / bookmark.zoom,
    y: (size.height / 2 - bookmark.y) / bookmark.zoom,
  };
}

export interface MinimapViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Map a flow point into minimap pixel space (clamped to the minimap box). */
export function projectToMinimap(
  point: { x: number; y: number },
  viewBox: MinimapViewBox,
  size: { width: number; height: number },
): { x: number; y: number } {
  const safeW = viewBox.width > 0 ? viewBox.width : 1;
  const safeH = viewBox.height > 0 ? viewBox.height : 1;
  const px = ((point.x - viewBox.x) / safeW) * size.width;
  const py = ((point.y - viewBox.y) / safeH) * size.height;
  return {
    x: Math.min(size.width, Math.max(0, px)),
    y: Math.min(size.height, Math.max(0, py)),
  };
}
