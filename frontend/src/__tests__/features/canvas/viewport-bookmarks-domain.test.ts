// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  BOOKMARK_SLOT_COUNT,
  bookmarkCenterInFlow,
  bookmarkIndexToDigit,
  createEmptyBookmarks,
  digitToBookmarkIndex,
  isViewportBookmark,
  normalizeBookmarks,
  projectToMinimap,
} from "@/features/canvas/domain/viewportBookmarks";

describe("viewport bookmarks domain", () => {
  it("maps digits 1-9 to index 0-8 and 0 to index 9", () => {
    expect(digitToBookmarkIndex("1")).toBe(0);
    expect(digitToBookmarkIndex("9")).toBe(8);
    expect(digitToBookmarkIndex("0")).toBe(9);
    expect(digitToBookmarkIndex("a")).toBeNull();
  });

  it("maps index back to digit (9 -> '0')", () => {
    expect(bookmarkIndexToDigit(0)).toBe("1");
    expect(bookmarkIndexToDigit(8)).toBe("9");
    expect(bookmarkIndexToDigit(9)).toBe("0");
    expect(bookmarkIndexToDigit(10)).toBeNull();
    expect(bookmarkIndexToDigit(-1)).toBeNull();
  });

  it("creates a fresh 10-slot empty array (not a shared reference)", () => {
    const a = createEmptyBookmarks();
    const b = createEmptyBookmarks();
    expect(a).toHaveLength(BOOKMARK_SLOT_COUNT);
    expect(a.every((slot) => slot === null)).toBe(true);
    a[0] = { x: 1, y: 2, zoom: 3 };
    expect(b[0]).toBeNull();
  });

  it("normalizes dirty input to length-10 with invalid slots dropped to null", () => {
    const result = normalizeBookmarks([
      { x: 10, y: 20, zoom: 1.5 },
      { x: "bad", y: 0, zoom: 1 },
      null,
      { x: 0, y: 0 },
      42,
      { x: 1, y: 2, zoom: 0 },
    ]);
    expect(result).toHaveLength(BOOKMARK_SLOT_COUNT);
    expect(result[0]).toEqual({ x: 10, y: 20, zoom: 1.5 });
    expect(result[1]).toBeNull();
    expect(result[2]).toBeNull();
    expect(result[3]).toBeNull();
    expect(result[4]).toBeNull();
    expect(result[5]).toBeNull();
  });

  it("normalizes non-array input to all-null", () => {
    expect(normalizeBookmarks(undefined).every((s) => s === null)).toBe(true);
    expect(normalizeBookmarks(null)).toHaveLength(BOOKMARK_SLOT_COUNT);
  });

  it("computes the flow-coordinate center of a bookmarked viewport", () => {
    expect(bookmarkCenterInFlow({ x: 0, y: 0, zoom: 1 }, { width: 800, height: 600 })).toEqual({
      x: 400,
      y: 300,
    });
    expect(bookmarkCenterInFlow({ x: -200, y: -100, zoom: 2 }, { width: 800, height: 600 })).toEqual({
      x: 300,
      y: 200,
    });
  });

  it("projects a flow point into minimap pixels using the svg viewBox", () => {
    const px = projectToMinimap(
      { x: 500, y: 250 },
      { x: 0, y: 0, width: 1000, height: 500 },
      { width: 200, height: 100 },
    );
    expect(px).toEqual({ x: 100, y: 50 });
  });

  it("clamps projected points to the minimap box", () => {
    const px = projectToMinimap(
      { x: 5000, y: -100 },
      { x: 0, y: 0, width: 1000, height: 500 },
      { width: 200, height: 100 },
    );
    expect(px).toEqual({ x: 200, y: 0 });
  });

  describe("isViewportBookmark", () => {
    it("accepts a well-formed bookmark with positive zoom", () => {
      expect(isViewportBookmark({ x: 0, y: 0, zoom: 1 })).toBe(true);
    });

    it("rejects malformed or non-positive-zoom values", () => {
      expect(isViewportBookmark(null)).toBe(false);
      expect(isViewportBookmark({})).toBe(false);
      expect(isViewportBookmark({ x: 0, y: 0 })).toBe(false);
      expect(isViewportBookmark({ x: 0, y: 0, zoom: 0 })).toBe(false);
      expect(isViewportBookmark({ x: 0, y: 0, zoom: Infinity })).toBe(false);
      expect(isViewportBookmark({ x: 0, y: 0, zoom: NaN })).toBe(false);
      expect(isViewportBookmark([1, 2, 3])).toBe(false);
    });
  });
});
