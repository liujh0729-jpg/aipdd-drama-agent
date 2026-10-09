// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  centerInitialCrop,
  pixelCropFromPercentCrop,
} from "@/features/canvas/ui/BackgroundCropperDialog";

describe("BackgroundCropperDialog initial crop", () => {
  it("creates a completed 16:9 pixel crop for wide images", () => {
    const percentCrop = centerInitialCrop(2000, 1000, 16 / 9);
    const pixelCrop = pixelCropFromPercentCrop(percentCrop, 2000, 1000);

    expect(pixelCrop.unit).toBe("px");
    expect(pixelCrop.x).toBeCloseTo(111.11, 1);
    expect(pixelCrop.y).toBe(0);
    expect(pixelCrop.width).toBeCloseTo(1777.78, 1);
    expect(pixelCrop.height).toBe(1000);
  });

  it("creates a completed 16:9 pixel crop for tall images", () => {
    const percentCrop = centerInitialCrop(1000, 1000, 16 / 9);
    const pixelCrop = pixelCropFromPercentCrop(percentCrop, 1000, 1000);

    expect(pixelCrop.unit).toBe("px");
    expect(pixelCrop.x).toBe(0);
    expect(pixelCrop.y).toBeCloseTo(218.75, 1);
    expect(pixelCrop.width).toBe(1000);
    expect(pixelCrop.height).toBeCloseTo(562.5, 1);
  });

  it("keeps full-frame export for already-16:9 images", () => {
    const percentCrop = centerInitialCrop(1600, 900, 16 / 9);
    const pixelCrop = pixelCropFromPercentCrop(percentCrop, 1600, 900);

    expect(percentCrop.x).toBe(0);
    expect(percentCrop.y).toBe(0);
    expect(percentCrop.width).toBe(100);
    expect(percentCrop.height).toBe(100);
    expect(pixelCrop.width).toBe(1600);
    expect(pixelCrop.height).toBe(900);
    expect(pixelCrop.width / pixelCrop.height).toBeCloseTo(16 / 9, 3);
  });

  it("creates a completed 2:3 pixel crop when requested", () => {
    const percentCrop = centerInitialCrop(1200, 900, 2 / 3);
    const pixelCrop = pixelCropFromPercentCrop(percentCrop, 1200, 900);

    expect(pixelCrop.unit).toBe("px");
    expect(pixelCrop.x).toBeCloseTo(300, 1);
    expect(pixelCrop.y).toBe(0);
    expect(pixelCrop.width).toBeCloseTo(600, 1);
    expect(pixelCrop.height).toBe(900);
    expect(pixelCrop.width / pixelCrop.height).toBeCloseTo(2 / 3, 3);
  });
});
