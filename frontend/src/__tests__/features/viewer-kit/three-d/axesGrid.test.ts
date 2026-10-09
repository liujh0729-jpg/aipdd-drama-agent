// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { computeAxesGridWindow } from "@/features/viewer-kit/three-d/engine/axesGrid";

describe("axes grid", () => {
  it("centers the drawn grid around the camera ground projection", () => {
    const grid = computeAxesGridWindow({
      cameraPosition: { x: 48.4, y: 8, z: -33.2 },
      cameraFarClip: 1000,
    });

    expect(grid.minX).toBeLessThanOrEqual(0);
    expect(grid.maxX).toBeGreaterThanOrEqual(95);
    expect(grid.minZ).toBeLessThanOrEqual(-80);
    expect(grid.maxZ).toBeGreaterThanOrEqual(10);
  });

  it("expands the drawn grid as the camera rises", () => {
    const low = computeAxesGridWindow({
      cameraPosition: { x: 0, y: 4, z: 0 },
      cameraFarClip: 1000,
    });
    const high = computeAxesGridWindow({
      cameraPosition: { x: 0, y: 40, z: 0 },
      cameraFarClip: 1000,
    });

    expect(high.extent).toBeGreaterThan(low.extent);
    expect(high.maxX - high.minX).toBeGreaterThan(low.maxX - low.minX);
  });
});
