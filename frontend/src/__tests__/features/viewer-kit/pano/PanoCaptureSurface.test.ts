// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  panoCaptureOverlayRect,
  panoCaptureOverlayStyle,
  resizePanoCaptureFrame,
} from "@/features/viewer-kit/pano/PanoCaptureSurface";

describe("panoCaptureOverlayStyle", () => {
  it("fits landscape frames by aspect ratio instead of giving every landscape frame the same width", () => {
    const bounds = { width: 2000, height: 800 };
    const wide = panoCaptureOverlayStyle("16:9", bounds);
    const standard = panoCaptureOverlayStyle("4:3", bounds);

    expect(wide).toMatchObject({
      aspectRatio: "16 / 9",
      height: "752px",
    });
    expect(standard).toMatchObject({
      aspectRatio: "4 / 3",
      height: "752px",
    });
    expect(Number.parseInt(String(wide.width), 10)).toBeGreaterThan(
      Number.parseInt(String(standard.width), 10),
    );
  });

  it("keeps portrait and square frame ratios explicit before the viewport is measured", () => {
    expect(panoCaptureOverlayStyle("9:16", null)).toMatchObject({
      aspectRatio: "9 / 16",
      width: "calc(100% - 48px)",
      maxHeight: "calc(100% - 48px)",
    });
    expect(panoCaptureOverlayStyle("1:1", null)).toMatchObject({
      aspectRatio: "1 / 1",
    });
  });

  it("returns a centered draggable frame rect for measured viewports", () => {
    const rect = panoCaptureOverlayRect("16:9", { width: 2000, height: 800 });

    expect(rect).toEqual({
      x: 332,
      y: 24,
      width: 1337,
      height: 752,
    });
  });

  it("resizes crop frames around their center while preserving aspect ratio", () => {
    const zoomed = resizePanoCaptureFrame(
      { x: 100, y: 100, width: 400, height: 225 },
      { width: 1000, height: 700 },
      "16:9",
      1.25,
    );

    expect(zoomed).toEqual({
      x: 50,
      y: 72,
      width: 500,
      height: 281,
    });
  });
});
