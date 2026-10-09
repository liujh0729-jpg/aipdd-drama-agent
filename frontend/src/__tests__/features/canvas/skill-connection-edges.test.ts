// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { applySkillRoleBindingConnection } from "@/features/canvas/domain/skillConnectionEdges";
import type { CanvasEdge, CanvasNode } from "@/features/canvas/domain/canvasNodes";
import type { SkillDefinition } from "@/features/freezone/context/skillRoles";

const skill: SkillDefinition = {
  id: "freezone.sketch_from_context",
  provider: "freezone_mainline",
  display_name: "Sketch From Context",
  description: "",
  inputs: [
    {
      role: "background",
      label: "Background",
      accepts: { node_types: ["uploadNode"], media_kinds: ["image"] },
      required: false,
      cardinality: "single",
    },
  ],
  outputs: [],
};

const nodes = [
  {
    id: "old_background",
    type: "uploadNode",
    position: { x: 0, y: 0 },
    data: { imageUrl: "/old.png" },
  },
  {
    id: "new_background",
    type: "uploadNode",
    position: { x: 0, y: 0 },
    data: { imageUrl: "/new.png" },
  },
  {
    id: "skill",
    type: "skillNode",
    position: { x: 0, y: 0 },
    data: { skill_id: skill.id },
  },
] as CanvasNode[];

function roleEdge(data: Record<string, unknown> = {}): CanvasEdge {
  return {
    id: "edge_old_background_to_skill_background",
    source: "old_background",
    target: "skill",
    sourceHandle: "source",
    targetHandle: "background",
    type: "disconnectableEdge",
    data: {
      edgeKind: "role_binding",
      role: "background",
      ...data,
    },
  };
}

describe("applySkillRoleBindingConnection", () => {
  it("does not replace preset-managed single-input role bindings", () => {
    const edges = [roleEdge({ preset_managed: true })];

    const next = applySkillRoleBindingConnection({
      nodes,
      edges,
      skillSpec: skill,
      connection: {
        source: "new_background",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "background",
      },
    });

    expect(next).toBe(edges);
    expect(next).toHaveLength(1);
    expect(next[0]?.source).toBe("old_background");
  });

  it("replaces user-created single-input role bindings", () => {
    const edges = [roleEdge()];

    const next = applySkillRoleBindingConnection({
      nodes,
      edges,
      skillSpec: skill,
      connection: {
        source: "new_background",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "background",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]?.source).toBe("new_background");
    expect(next[0]?.targetHandle).toBe("background");
  });

  it("connects a dragged uploadNode image into a background input whose accepts use backend node-type names", () => {
    // 复刻真实 freezone.sketch_from_context 的 background accepts：node_types 用的是
    // 后端命名（uploadImageNode/sceneNode/...），不含前端真实的 uploadNode。
    const realSkill: SkillDefinition = {
      id: "freezone.sketch_from_context",
      provider: "freezone_mainline",
      display_name: "Sketch From Context",
      description: "",
      inputs: [
        {
          role: "background",
          label: "Background",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["selected_background", "background", "background_candidate"],
            candidate_origin_skill_ids: ["freezone.scene_360"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "single",
        },
      ],
      outputs: [],
    };
    const dragNodes = [
      {
        id: "frame",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: { imageUrl: "/frame.png", __freezone_source: { role: "current_frame" } },
      },
      { id: "skill", type: "skillNode", position: { x: 0, y: 0 }, data: { skill_id: realSkill.id } },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: dragNodes,
      edges: [],
      skillSpec: realSkill,
      connection: {
        source: "frame",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "background",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]?.source).toBe("frame");
    expect(next[0]?.targetHandle).toBe("background");
  });

  it("normalizes loose-mode drags from a skill input handle back to an uploaded image", () => {
    const realSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "background",
          label: "Background",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["selected_background", "background", "background_candidate"],
            candidate_origin_skill_ids: ["freezone.scene_360"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "single",
        },
      ],
      outputs: [],
    };
    const looseNodes = [
      {
        id: "image",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: { imageUrl: "/image.png" },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: realSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: looseNodes,
      edges: [],
      skillSpec: realSkill,
      connection: {
        source: "skill",
        target: "image",
        sourceHandle: "background",
        targetHandle: "source",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      source: "image",
      target: "skill",
      sourceHandle: "source",
      targetHandle: "background",
      data: {
        edgeKind: "role_binding",
        role: "background",
      },
    });
  });

  it("infers an unbound required image input when a plain uploaded image lands on a skill body", () => {
    const frameSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "beat_context",
          label: "Shot Context",
          accepts: { node_types: ["beatContextNode"] },
          required: true,
          cardinality: "single",
        },
        {
          role: "sketch",
          label: "Sketch",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["sketch"],
            candidate_origin_skill_ids: [
              "freezone.sketch_from_context",
              "freezone.sketch_from_director_combined",
            ],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: true,
          cardinality: "single",
        },
        {
          role: "background",
          label: "Background",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["selected_background", "background"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "single",
        },
      ],
      outputs: [],
    };
    const frameNodes = [
      {
        id: "plain_image",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: { imageUrl: "/plain.png" },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: frameSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: frameNodes,
      edges: [],
      skillSpec: frameSkill,
      connection: {
        source: "plain_image",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "target",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      source: "plain_image",
      target: "skill",
      targetHandle: "sketch",
      data: {
        edgeKind: "role_binding",
        role: "sketch",
      },
    });
  });

  it("infers the input role when the drop lands on the node body (ambiguous handle)", () => {
    const sceneSkill: SkillDefinition = {
      id: "freezone.scene_360",
      provider: "freezone_mainline",
      display_name: "Scene 360",
      description: "",
      inputs: [
        {
          role: "scene_master",
          label: "Scene master",
          accepts: { canonical_slot_kinds: ["scene_master"] },
          required: false,
          cardinality: "single",
        },
      ],
      outputs: [],
    };
    const sceneNodes = [
      {
        id: "scene",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          imageUrl: "/scene.png",
          __freezone_source: { role: "scene_master" },
          slot_target: { kind: "scene_master", scene_id: "厨房" },
        },
      },
      { id: "skill", type: "skillNode", position: { x: 0, y: 0 }, data: { skill_id: sceneSkill.id } },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: sceneNodes,
      edges: [],
      skillSpec: sceneSkill,
      // 用户落在节点本体 → targetHandle 为通用 "target"
      connection: {
        source: "scene",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "target",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]?.targetHandle).toBe("scene_master");
    expect((next[0]?.data as { role?: string })?.role).toBe("scene_master");
  });

  it("infers beat_context when a BeatContextNode drops on a skill node body", () => {
    const beatSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "beat_context",
          label: "Shot Context",
          accepts: { node_types: ["beatContextNode"] },
          required: true,
          cardinality: "single",
        },
      ],
      outputs: [],
    };
    const beatNodes = [
      {
        id: "context_beat",
        type: "beatContextNode",
        position: { x: 0, y: 0 },
        data: { content: "beat" },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: beatSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: beatNodes,
      edges: [],
      skillSpec: beatSkill,
      connection: {
        source: "context_beat",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "target",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]?.targetHandle).toBe("beat_context");
    expect((next[0]?.data as { role?: string })?.role).toBe("beat_context");
  });

  it("connects imported identity assets to dynamic identity handles on frame_from_context", () => {
    const frameSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "identity",
          label: "Identity",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["identity", "portrait"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "multi",
        },
      ],
      outputs: [],
    };
    const identityNodes = [
      {
        id: "identity",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          imageUrl: "/identity.png",
          __freezone_source: {
            kind: "identity",
            role: "character_identity",
            meta: { character: "陈默", identity_id: "陈默_青年时期" },
          },
          slot_target: {
            kind: "identity",
            character: "陈默",
            identity_id: "陈默_青年时期",
          },
        },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: frameSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: identityNodes,
      edges: [],
      skillSpec: frameSkill,
      connection: {
        source: "identity",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "identity:陈默_青年时期",
      },
    });

    expect(next).toHaveLength(1);
    expect(next[0]?.source).toBe("identity");
    expect(next[0]?.targetHandle).toBe("identity:陈默_青年时期");
    expect(next[0]?.data).toMatchObject({
      role: "identity",
      reference_target: { kind: "identity", identity_id: "陈默_青年时期" },
    });
  });

  it("does not rewrite a generic identity handle to an arbitrary beat-context identity", () => {
    const frameSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "beat_context",
          label: "Shot Context",
          accepts: { node_types: ["beatContextNode"] },
          required: true,
          cardinality: "single",
        },
        {
          role: "identity",
          label: "Identity",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["identity", "portrait"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "multi",
        },
      ],
      outputs: [],
    };
    const contextEdge: CanvasEdge = {
      id: "e-context-skill-beat_context",
      source: "context",
      target: "skill",
      sourceHandle: "source",
      targetHandle: "beat_context",
      type: "disconnectableEdge",
      data: {
        edgeKind: "role_binding",
        role: "beat_context",
      },
    };
    const contextNodes = [
      {
        id: "context",
        type: "beatContextNode",
        position: { x: 0, y: 0 },
        data: {
          beat_context: {
            schema: "beat_context.v1",
            source: "standalone",
            visual_description: "{{YELLOW}} and {{GREEN}} enter",
            detected_identities: ["YELLOW", "GREEN"],
          },
        },
      },
      {
        id: "identity_image",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: { imageUrl: "/identity.png" },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: frameSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: contextNodes,
      edges: [contextEdge],
      skillSpec: frameSkill,
      connection: {
        source: "identity_image",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "identity",
      },
    });

    expect(next).toHaveLength(2);
    const identityEdge = next.find((edge) => edge.source === "identity_image");
    expect(identityEdge?.targetHandle).toBe("identity");
    expect(identityEdge?.data).toMatchObject({
      role: "identity",
    });
    expect((identityEdge?.data as { reference_target?: unknown })?.reference_target).toBeUndefined();
  });

  it("lands a node-body identity drop on the first unbound beat-context identity handle", () => {
    const frameSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "beat_context",
          label: "Shot Context",
          accepts: { node_types: ["beatContextNode"] },
          required: true,
          cardinality: "single",
        },
        {
          role: "identity",
          label: "Identity",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["identity", "portrait"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "multi",
        },
      ],
      outputs: [],
    };
    const contextEdge: CanvasEdge = {
      id: "e-context-skill-beat_context",
      source: "context",
      target: "skill",
      sourceHandle: "source",
      targetHandle: "beat_context",
      type: "disconnectableEdge",
      data: {
        edgeKind: "role_binding",
        role: "beat_context",
      },
    };
    const contextNodes = [
      {
        id: "context",
        type: "beatContextNode",
        position: { x: 0, y: 0 },
        data: {
          beat_context: {
            schema: "beat_context.v1",
            source: "standalone",
            visual_description: "{{YELLOW}} and {{GREEN}} enter",
            detected_identities: ["YELLOW", "GREEN"],
          },
        },
      },
      {
        id: "identity_image",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          imageUrl: "/identity.png",
          __freezone_source: { role: "identity" },
        },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: frameSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: contextNodes,
      edges: [contextEdge],
      skillSpec: frameSkill,
      connection: {
        source: "identity_image",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "target",
      },
    });

    expect(next).toHaveLength(2);
    const identityEdge = next.find((edge) => edge.source === "identity_image");
    expect(identityEdge?.targetHandle).toBe("identity:YELLOW");
    expect(identityEdge?.data).toMatchObject({
      role: "identity",
      reference_target: { kind: "identity", identity_id: "YELLOW" },
    });
  });

  it("does not turn a node-body prop drop into a no-prop sentinel reference handle", () => {
    const frameSkill: SkillDefinition = {
      id: "freezone.frame_from_context",
      provider: "freezone_mainline",
      display_name: "Frame From Context",
      description: "",
      inputs: [
        {
          role: "beat_context",
          label: "Shot Context",
          accepts: { node_types: ["beatContextNode"] },
          required: true,
          cardinality: "single",
        },
        {
          role: "prop",
          label: "Prop",
          accepts: {
            node_types: [
              "imageGenNode",
              "imageNode",
              "uploadImageNode",
              "freezoneImageNode",
              "assetImageNode",
              "sceneNode",
              "identityNode",
              "propNode",
            ],
            canonical_slot_kinds: ["prop"],
            media_kinds: ["image"],
            has_field: ["image_url"],
          },
          required: false,
          cardinality: "multi",
        },
      ],
      outputs: [],
    };
    const contextEdge: CanvasEdge = {
      id: "e-context-skill-beat_context",
      source: "context",
      target: "skill",
      sourceHandle: "source",
      targetHandle: "beat_context",
      type: "disconnectableEdge",
      data: {
        edgeKind: "role_binding",
        role: "beat_context",
      },
    };
    const contextNodes = [
      {
        id: "context",
        type: "beatContextNode",
        position: { x: 0, y: 0 },
        data: {
          mainline_context: [
            {
              kind: "beat",
              projectId: "demo",
              episode: 1,
              beat: 2,
            },
          ],
          snapshot: {
            visualDescription: "no prop",
            detectedProps: ["__NO_PROP__"],
          },
        },
      },
      {
        id: "prop_image",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          imageUrl: "/prop.png",
          __freezone_source: { role: "prop" },
        },
      },
      {
        id: "skill",
        type: "skillNode",
        position: { x: 0, y: 0 },
        data: { skill_id: frameSkill.id },
      },
    ] as CanvasNode[];

    const next = applySkillRoleBindingConnection({
      nodes: contextNodes,
      edges: [contextEdge],
      skillSpec: frameSkill,
      connection: {
        source: "prop_image",
        target: "skill",
        sourceHandle: "source",
        targetHandle: "target",
      },
    });

    const propEdge = next.find((edge) => edge.source === "prop_image");
    expect(propEdge?.targetHandle).toBe("prop");
    expect((propEdge?.data as { reference_target?: unknown })?.reference_target).toBeUndefined();
  });
});
