// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  canvasCropRectFromFrame,
  fovToFocal,
  fovToZoom,
  normalizePanoDegrees,
  zoomToFov,
} from "@/features/viewer-kit/pano/panoCapture";
import { legacyPanoInputToManifest } from "@/features/viewer-kit/pano/panoManifest";

describe("pano capture camera math", () => {
  it("converts between FOV and Photo Sphere Viewer zoom", () => {
    expect(fovToZoom(70)).toBeCloseTo(60.61, 2);
    expect(zoomToFov(fovToZoom(70))).toBeCloseTo(70, 5);
  });

  it("reports the same focal-length approximation as the legacy SuperTale pano viewer", () => {
    expect(fovToFocal(160)).toBe(3);
    expect(fovToFocal(70)).toBe(26);
    expect(fovToFocal(8)).toBe(257);
  });

  it("normalizes yaw-style degrees into the -180 to 180 range", () => {
    expect(normalizePanoDegrees(270)).toBe(-90);
    expect(normalizePanoDegrees(-181)).toBe(179);
    expect(normalizePanoDegrees(540)).toBe(-180);
  });

  it("maps viewport crop frames into viewer canvas coordinates", () => {
    expect(
      canvasCropRectFromFrame(
        { x: 100, y: 50, width: 400, height: 225 },
        { width: 1000, height: 500 },
        { width: 2000, height: 1000 },
      ),
    ).toEqual({
      x: 200,
      y: 100,
      width: 800,
      height: 450,
    });
  });

  it("maps legacy direct pano inputs into the current manifest contract", () => {
    expect(
      legacyPanoInputToManifest({
        world: "demo",
        pano: "basement",
        pano_url: "/tmp/pano.png",
        pano_fs: "/Users/demo/pano.png",
        display_name: "地下室",
      }),
    ).toMatchObject({
      viewer_kind: "pano360",
      mode: "scene",
      project: "demo",
      scene_id: "basement",
      display_name: "地下室",
      source: {
        slot_kind: "scene_director_pano_360",
        url: "/tmp/pano.png",
        fs: "/Users/demo/pano.png",
      },
      allowed_destinations: ["view", "download"],
    });
  });
});
