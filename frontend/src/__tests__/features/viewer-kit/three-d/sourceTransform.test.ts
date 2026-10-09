// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM,
  buildSogPivotTransform,
  constrainSourceTransformForType,
  normalizeDirectorWorldSourceTransform,
  sourceTransformFromLegacyWorld,
} from "@/features/viewer-kit/three-d/sourceTransform";

describe("director world source transform", () => {
  it("normalizes missing and invalid fields to stable defaults", () => {
    expect(normalizeDirectorWorldSourceTransform({
      xOffset: 2,
      yOffset: Number.NaN,
      scale: -1,
      yawDeg: 45,
    })).toEqual({
      ...DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM,
      xOffset: 2,
      yawDeg: 45,
    });
  });

  it("migrates legacy splatYOffset onto the active source transform", () => {
    expect(sourceTransformFromLegacyWorld({
      splatYOffset: 1.25,
      activeSourceId: "master",
    })).toEqual({
      sourceId: "master",
      transform: {
        ...DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM,
        yOffset: 1.25,
      },
    });
  });

  it("keeps splat content centered by rotating and scaling around sceneCenter", () => {
    const result = buildSogPivotTransform({
      sceneCenter: [0, 1, -8],
      transform: {
        ...DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM,
        yOffset: 0.5,
        pitchDeg: 12,
        scale: 1.25,
      },
    });

    expect(result.pivotPosition).toEqual([0, 1.5, -8]);
    expect(result.pivotEulerDeg).toEqual([12, 0, 0]);
    expect(result.pivotScale).toBe(1.25);
    expect(result.splatLocalPosition).toEqual([-0, -1, 8]);
  });

  it("keeps full calibration for pano360 sources", () => {
    expect(constrainSourceTransformForType({
      ...DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM,
      xOffset: 10,
      yOffset: 4,
      zOffset: -3,
      yawDeg: 20,
      pitchDeg: 5,
      rollDeg: -7,
      scale: 1.5,
    }, "pano360")).toEqual({
      xOffset: 10,
      yOffset: 4,
      zOffset: -3,
      yawDeg: 20,
      pitchDeg: 5,
      rollDeg: -7,
      scale: 1.5,
    });
  });
});
