// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import {
  BOOKMARK_SLOT_COUNT,
  createEmptyBookmarks,
} from "@/features/canvas/domain/viewportBookmarks";

describe("canvasStore viewport bookmarks", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
    useCanvasStore.getState().clearViewportBookmarks();
  });

  it("starts with 10 empty slots", () => {
    const { viewportBookmarks } = useCanvasStore.getState();
    expect(viewportBookmarks).toHaveLength(BOOKMARK_SLOT_COUNT);
    expect(viewportBookmarks.every((s) => s === null)).toBe(true);
  });

  it("sets and clears a single slot", () => {
    useCanvasStore.getState().setViewportBookmark(2, { x: 1, y: 2, zoom: 1.5 });
    expect(useCanvasStore.getState().viewportBookmarks[2]).toEqual({ x: 1, y: 2, zoom: 1.5 });
    useCanvasStore.getState().setViewportBookmark(2, null);
    expect(useCanvasStore.getState().viewportBookmarks[2]).toBeNull();
  });

  it("ignores out-of-range indices", () => {
    useCanvasStore.getState().setViewportBookmark(99, { x: 0, y: 0, zoom: 1 });
    expect(useCanvasStore.getState().viewportBookmarks).toEqual(createEmptyBookmarks());
  });

  it("stores a defensive copy, not the caller's object", () => {
    const bm = { x: 1, y: 2, zoom: 1.5 };
    useCanvasStore.getState().setViewportBookmark(0, bm);
    bm.x = 999;
    expect(useCanvasStore.getState().viewportBookmarks[0]).toEqual({ x: 1, y: 2, zoom: 1.5 });
  });

  it("clears all slots", () => {
    useCanvasStore.getState().setViewportBookmark(0, { x: 0, y: 0, zoom: 1 });
    useCanvasStore.getState().setViewportBookmark(9, { x: 5, y: 5, zoom: 2 });
    useCanvasStore.getState().clearViewportBookmarks();
    expect(useCanvasStore.getState().viewportBookmarks.every((s) => s === null)).toBe(true);
  });

  it("hydrates from dirty persisted data", () => {
    useCanvasStore.getState().hydrateViewportBookmarks([{ x: 1, y: 2, zoom: 1 }, "junk"]);
    const list = useCanvasStore.getState().viewportBookmarks;
    expect(list).toHaveLength(BOOKMARK_SLOT_COUNT);
    expect(list[0]).toEqual({ x: 1, y: 2, zoom: 1 });
    expect(list[1]).toBeNull();
  });

  it("does not pollute undo history", () => {
    const historyBefore = useCanvasStore.getState().history.past.length;
    useCanvasStore.getState().setViewportBookmark(0, { x: 0, y: 0, zoom: 1 });
    useCanvasStore.getState().clearViewportBookmarks();
    useCanvasStore.getState().hydrateViewportBookmarks([{ x: 1, y: 1, zoom: 1 }]);
    expect(useCanvasStore.getState().history.past.length).toBe(historyBefore);
  });

  it("preserves bookmarks across setCanvasData (mid-session content replace)", () => {
    useCanvasStore.getState().setViewportBookmark(0, { x: 0, y: 0, zoom: 1 });
    useCanvasStore.getState().setCanvasData([], []);
    expect(useCanvasStore.getState().viewportBookmarks[0]).toEqual({ x: 0, y: 0, zoom: 1 });
  });

  it("clears bookmarks when hydrating a canvas with no saved bookmarks", () => {
    useCanvasStore.getState().setViewportBookmark(0, { x: 0, y: 0, zoom: 1 });
    useCanvasStore.getState().hydrateViewportBookmarks(undefined);
    expect(useCanvasStore.getState().viewportBookmarks.every((s) => s === null)).toBe(true);
  });
});
