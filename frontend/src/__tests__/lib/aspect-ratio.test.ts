// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  aspectRatioForOrientation,
  orientationForAspectRatio,
  orientationForSpineTemplate,
  zoomCropBox,
} from "@/lib/aspect-ratio";

describe("aspect ratio helpers", () => {
  it("maps project_config aspect_ratio to UI orientation", () => {
    expect(orientationForAspectRatio("16:9")).toBe("landscape");
    expect(orientationForAspectRatio("2:3")).toBe("portrait");
    expect(orientationForAspectRatio("9:16")).toBe("portrait");
    expect(orientationForAspectRatio(undefined)).toBeNull();
  });

  it("maps UI orientation back to persisted project_config aspect_ratio", () => {
    expect(aspectRatioForOrientation("landscape")).toBe("16:9");
    expect(aspectRatioForOrientation("portrait")).toBe("2:3");
  });

  it("uses narrated projects as the landscape default", () => {
    expect(orientationForSpineTemplate("narrated")).toBe("landscape");
    expect(orientationForSpineTemplate("drama")).toBe("portrait");
  });

  it("zooms a crop box around its center while clamping to the source image", () => {
    expect(
      zoomCropBox({ x: 100, y: 50, width: 400, height: 200 }, 1000, 600, 0.5),
    ).toEqual({ x: 200, y: 100, width: 200, height: 100 });
    expect(
      zoomCropBox({ x: 200, y: 100, width: 200, height: 100 }, 300, 180, 2),
    ).toEqual({ x: 0, y: 30, width: 300, height: 150 });
  });
});
