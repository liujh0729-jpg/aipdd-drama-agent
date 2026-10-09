// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  addSkeletonToFrame,
  hitTestPoseJoint,
  movePoseDrag,
  removeSkeletonFromFrame,
  resetSkeletonPoses,
  type PoseDragState,
} from "@/lib/sketch-pose-editor-model";
import type { PoseSkeleton } from "@/lib/queries/sketch-pose-editor";

function skeleton(overrides: Partial<PoseSkeleton> = {}): PoseSkeleton {
  return {
    identityId: "hero",
    colorHex: "#22d3ee",
    visible: true,
    active: true,
    joints: {
      nose: { x: 50, y: 20 },
      neck: { x: 50, y: 50 },
      left_wrist: { x: 20, y: 70 },
      right_wrist: { x: 80, y: 70 },
    },
    ...overrides,
  };
}

describe("sketch pose editor model", () => {
  it("hit-tests visible active joints before inactive skeletons", () => {
    const hit = hitTestPoseJoint(
      [
        skeleton({
          identityId: "background",
          active: false,
          joints: { nose: { x: 52, y: 20 } },
        }),
        skeleton({ identityId: "hero", active: true }),
      ],
      { x: 51, y: 20 },
      18,
    );

    expect(hit).toEqual({ skeletonIndex: 1, jointKey: "nose" });
  });

  it("moves a single dragged joint and clamps it to image bounds", () => {
    const drag: PoseDragState = {
      skeletonIndex: 0,
      jointKey: "left_wrist",
      bodyDrag: false,
      startPoint: { x: 20, y: 70 },
      startJoints: skeleton().joints,
    };

    const [next] = movePoseDrag([skeleton()], drag, { x: -5, y: 300 }, 100, 120);

    expect(next.joints.left_wrist).toEqual({ x: 0, y: 120 });
    expect(next.joints.neck).toEqual({ x: 50, y: 50 });
  });

  it("moves the whole skeleton when dragging the nose or neck", () => {
    const drag: PoseDragState = {
      skeletonIndex: 0,
      jointKey: "neck",
      bodyDrag: true,
      startPoint: { x: 50, y: 50 },
      startJoints: skeleton().joints,
    };

    const [next] = movePoseDrag([skeleton()], drag, { x: 60, y: 40 }, 100, 120);

    expect(next.joints.neck).toEqual({ x: 60, y: 40 });
    expect(next.joints.nose).toEqual({ x: 60, y: 10 });
    expect(next.joints.left_wrist).toEqual({ x: 30, y: 60 });
  });

  it("adds, removes, and resets frame skeletons without losing visibility", () => {
    const initial = [
      skeleton({
        visible: false,
        active: false,
        joints: { nose: { x: 10, y: 10 }, neck: { x: 10, y: 30 } },
      }),
    ];

    const added = addSkeletonToFrame(initial, "hero");
    expect(added[0]).toMatchObject({ visible: true, active: true });

    const removed = removeSkeletonFromFrame(added, "hero");
    expect(removed[0]).toMatchObject({ visible: false, active: false });

    const moved = [
      {
        ...added[0],
        joints: { nose: { x: 90, y: 90 }, neck: { x: 90, y: 100 } },
      },
    ];
    const reset = resetSkeletonPoses(moved, initial);
    expect(reset[0].visible).toBe(true);
    expect(reset[0].joints).toEqual(initial[0].joints);
  });
});
