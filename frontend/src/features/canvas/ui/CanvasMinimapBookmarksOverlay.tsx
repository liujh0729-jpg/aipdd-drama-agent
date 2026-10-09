// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";

import { CanvasViewportBookmarks } from "./CanvasViewportBookmarks";
import { captureCurrentViewport, jumpToBookmark } from "@/features/canvas/application/bookmarkActions";
import {
  type MinimapViewBox,
  bookmarkCenterInFlow,
  bookmarkIndexToDigit,
  projectToMinimap,
} from "@/features/canvas/domain/viewportBookmarks";
import { useCanvasStore } from "@/stores/canvasStore";

interface MinimapMetrics {
  left: number;
  top: number;
  width: number;
  height: number;
  viewBox: MinimapViewBox | null;
}

interface CanvasMinimapBookmarksOverlayProps {
  /**
   * Keep the minimap popover (and this overlay) alive while the cursor is over
   * the bookmark row. The row sits above the minimap with a gap, so without
   * this the popover hover-unmounts before a button click can land.
   */
  onHoverChange?: (hovered: boolean) => void;
}

export function CanvasMinimapBookmarksOverlay({
  onHoverChange,
}: CanvasMinimapBookmarksOverlayProps) {
  const reactFlow = useReactFlow();
  const containerRef = useRef<HTMLDivElement>(null);
  const [metrics, setMetrics] = useState<MinimapMetrics | null>(null);

  const bookmarks = useCanvasStore((state) => state.viewportBookmarks);
  const currentViewport = useCanvasStore((state) => state.currentViewport);
  const canvasViewportSize = useCanvasStore((state) => state.canvasViewportSize);
  const nodeCount = useCanvasStore((state) => state.nodes.length);
  const setViewportBookmark = useCanvasStore((state) => state.setViewportBookmark);
  const clearViewportBookmarks = useCanvasStore((state) => state.clearViewportBookmarks);

  // Measure the live minimap DOM so the row sits above it and pins land on it,
  // independent of which corner react-flow placed the minimap in. Re-measure
  // whenever the camera, node set, or bookmark set changes (those change the
  // minimap svg viewBox), plus once after layout settles via rAF.
  useLayoutEffect(() => {
    const overlay = containerRef.current;
    if (!overlay) return;
    const pane = overlay.closest(".react-flow") as HTMLElement | null;
    if (!pane) return;

    let raf = 0;
    const measure = () => {
      const minimap = pane.querySelector(".react-flow__minimap") as HTMLElement | null;
      if (!minimap) {
        setMetrics(null);
        return;
      }
      const paneRect = pane.getBoundingClientRect();
      const mapRect = minimap.getBoundingClientRect();
      const svg = minimap.querySelector(".react-flow__minimap-svg") as SVGSVGElement | null;
      let viewBox: MinimapViewBox | null = null;
      if (svg && svg.viewBox && svg.viewBox.baseVal) {
        const vb = svg.viewBox.baseVal;
        if (vb.width > 0 && vb.height > 0) {
          viewBox = { x: vb.x, y: vb.y, width: vb.width, height: vb.height };
        }
      }
      setMetrics({
        left: mapRect.left - paneRect.left,
        top: mapRect.top - paneRect.top,
        width: mapRect.width,
        height: mapRect.height,
        viewBox,
      });
    };

    measure();
    raf = window.requestAnimationFrame(measure);
    return () => window.cancelAnimationFrame(raf);
  }, [currentViewport, nodeCount, bookmarks]);

  const handleSetCurrent = (index: number) => {
    setViewportBookmark(index, captureCurrentViewport(reactFlow));
  };
  const handleJump = (index: number) => {
    const bookmark = bookmarks[index];
    if (bookmark) jumpToBookmark(reactFlow, bookmark);
  };
  const handleDelete = (index: number) => setViewportBookmark(index, null);

  // Light up the digit whose bookmark matches where the camera currently sits, so
  // after a jump that number reads as "selected". Self-clears once the user pans
  // away (currentViewport stops matching). Tolerances absorb animation rounding.
  const activeIndex = useMemo(
    () =>
      bookmarks.findIndex(
        (bookmark) =>
          bookmark != null &&
          Math.abs(currentViewport.x - bookmark.x) < 1 &&
          Math.abs(currentViewport.y - bookmark.y) < 1 &&
          Math.abs(currentViewport.zoom - bookmark.zoom) < 0.005,
      ),
    [bookmarks, currentViewport],
  );

  const ROW_GAP = 8;
  const ROW_HEIGHT = 32;

  return (
    <div ref={containerRef} className="pointer-events-none absolute inset-0 z-[10001]">
      {metrics ? (
        <div
          className="pointer-events-auto absolute"
          style={{
            left: metrics.left,
            top: metrics.top - ROW_GAP - ROW_HEIGHT,
            width: metrics.width,
            // Bridge the gap down to the minimap so traversing from the minimap
            // to a row button never leaves a dead zone that hover-unmounts us.
            paddingBottom: ROW_GAP,
          }}
          onMouseEnter={() => onHoverChange?.(true)}
          onMouseLeave={() => onHoverChange?.(false)}
        >
          <CanvasViewportBookmarks
            bookmarks={bookmarks}
            activeIndex={activeIndex}
            onJump={handleJump}
            onSetCurrent={handleSetCurrent}
            onDelete={handleDelete}
            onClearAll={clearViewportBookmarks}
          />
        </div>
      ) : null}

      {metrics && metrics.viewBox && canvasViewportSize.width > 0
        ? bookmarks.map((bookmark, index) => {
            if (!bookmark) return null;
            const center = bookmarkCenterInFlow(bookmark, canvasViewportSize);
            const px = projectToMinimap(center, metrics.viewBox as MinimapViewBox, {
              width: metrics.width,
              height: metrics.height,
            });
            return (
              <div
                key={index}
                className="pointer-events-none absolute flex h-4 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-[10px] font-bold text-black shadow"
                style={{ left: metrics.left + px.x, top: metrics.top + px.y }}
              >
                {bookmarkIndexToDigit(index)}
              </div>
            );
          })
        : null}
    </div>
  );
}
