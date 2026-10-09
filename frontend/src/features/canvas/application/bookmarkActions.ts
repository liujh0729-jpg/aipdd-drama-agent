// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ReactFlowInstance } from "@xyflow/react";

import type { ViewportBookmark } from "@/features/canvas/domain/viewportBookmarks";

/** Snapshot the live camera into a bookmark. */
export function captureCurrentViewport(reactFlow: ReactFlowInstance): ViewportBookmark {
  const { x, y, zoom } = reactFlow.getViewport();
  return { x, y, zoom };
}

/** Ease-in-out cubic: slow start, quick middle, gentle settle — reads as a smooth glide. */
const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/** Animate the camera to a bookmarked viewport with a smooth, eased glide. */
export function jumpToBookmark(reactFlow: ReactFlowInstance, bookmark: ViewportBookmark): void {
  void reactFlow.setViewport(
    { x: bookmark.x, y: bookmark.y, zoom: bookmark.zoom },
    { duration: 550, ease: easeInOutCubic, interpolate: "smooth" },
  );
}
