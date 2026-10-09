// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  isPointerButtonPressed,
  normalizeFlyCameraState,
} from "@/features/viewer-kit/three-d/engine/flyCamera";

describe("fly camera state", () => {
  const fallback = {
    azim: 10,
    elev: -5,
    distance: 2,
    focalPoint: [0, 0.5, 0] as [number, number, number],
  };

  it("keeps current focal point when restoring a legacy camera object", () => {
    expect(
      normalizeFlyCameraState(
        {
          position: [1, 2, 3],
        },
        fallback,
      ),
    ).toEqual(fallback);
  });

  it("restores valid camera fields and ignores malformed values", () => {
    expect(
      normalizeFlyCameraState(
        {
          azim: 35,
          elev: "bad",
          distance: 4,
          focalPoint: [1, "2", 3],
        },
        fallback,
      ),
    ).toEqual({
      azim: 35,
      elev: -5,
      distance: 4,
      focalPoint: [1, 2, 3],
    });
  });
});

describe("fly camera pointer dragging", () => {
  it("requires the drag button to still be held during pointer moves", () => {
    expect(isPointerButtonPressed(1, 0)).toBe(true);
    expect(isPointerButtonPressed(0, 0)).toBe(false);
    expect(isPointerButtonPressed(2, 0)).toBe(false);
    expect(isPointerButtonPressed(2, 2)).toBe(true);
    expect(isPointerButtonPressed(4, 1)).toBe(true);
  });
});
