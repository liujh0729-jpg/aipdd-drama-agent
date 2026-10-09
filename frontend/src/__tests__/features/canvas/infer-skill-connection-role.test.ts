// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { inferSkillConnectionRole } from "@/features/freezone/context/inferSkillConnectionRole";

function sceneNode(role: string) {
  return {
    id: `scene_${role}`,
    type: "uploadNode",
    data: {
      imageUrl: "/scene.png",
      __freezone_source: { role },
      slot_target: { kind: role, scene_id: "厨房" },
    },
  };
}

function skillNode(skillId: string) {
  return { id: "skill", type: "skillNode", data: { skill_id: skillId } };
}

describe("inferSkillConnectionRole", () => {
  it("routes a scene_master source into the scene_master input of freezone.scene_360", () => {
    expect(
      inferSkillConnectionRole({
        sourceNode: sceneNode("scene_master"),
        targetNode: skillNode("freezone.scene_360"),
        requestedTargetHandle: "target",
      }),
    ).toBe("scene_master");
  });

  it("routes a scene_reverse_master source into the scene_reverse_master input", () => {
    expect(
      inferSkillConnectionRole({
        sourceNode: sceneNode("scene_reverse_master"),
        targetNode: skillNode("freezone.scene_360"),
        requestedTargetHandle: null,
      }),
    ).toBe("scene_reverse_master");
  });

  it("routes any image source into source_image for freezone.set_selected_background", () => {
    expect(
      inferSkillConnectionRole({
        sourceNode: sceneNode("scene_master"),
        targetNode: skillNode("freezone.set_selected_background"),
        requestedTargetHandle: "target",
      }),
    ).toBe("source_image");
  });

  it("maps a selected_background source onto the background input of frame_from_context", () => {
    const node = {
      id: "bg",
      type: "uploadNode",
      data: {
        imageUrl: "/bg.png",
        __freezone_source: { role: "selected_background" },
        slot_target: { kind: "selected_background", episode: 1, beat: 2 },
      },
    };
    expect(
      inferSkillConnectionRole({
        sourceNode: node,
        targetNode: skillNode("freezone.frame_from_context"),
        requestedTargetHandle: "",
      }),
    ).toBe("background");
  });

  it("returns null when the source cannot be classified (no misjudgement)", () => {
    const node = {
      id: "mystery",
      type: "uploadNode",
      data: { imageUrl: "/x.png" },
    };
    expect(
      inferSkillConnectionRole({
        sourceNode: node,
        targetNode: skillNode("freezone.frame_from_context"),
        requestedTargetHandle: "target",
      }),
    ).toBeNull();
  });

  it("respects an explicit, non-ambiguous targetHandle without inferring", () => {
    expect(
      inferSkillConnectionRole({
        sourceNode: sceneNode("scene_master"),
        targetNode: skillNode("freezone.frame_from_context"),
        requestedTargetHandle: "sketch",
      }),
    ).toBe("sketch");
  });
});
