// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { syncBeatContextMainlineEdges } from "@/features/freezone/context/beatContextProjection";
import type { CanvasEdge, CanvasNode } from "@/features/canvas/domain/canvasNodes";

const nodes = [
  {
    id: "context_beat",
    type: "beatContextNode",
    position: { x: 0, y: 0 },
    data: {},
  },
  {
    id: "skill_frame_from_context",
    type: "skillNode",
    position: { x: 0, y: 0 },
    data: { skill_id: "freezone.frame_from_context" },
  },
  {
    id: "ref_identity_keep",
    type: "imageGenNode",
    position: { x: 0, y: 0 },
    data: {
      __freezone_source: {
        kind: "identity",
        role: "character_identity",
        meta: { identity_id: "沈月白_青年时期" },
      },
    },
  },
  {
    id: "ref_identity_new",
    type: "imageGenNode",
    position: { x: 0, y: 0 },
    data: {
      __freezone_source: {
        kind: "identity",
        role: "character_identity",
        meta: { identity_id: "陆辰_青年时期" },
      },
    },
  },
  {
    id: "ref_identity_removed",
    type: "imageGenNode",
    position: { x: 0, y: 0 },
    data: {
      __freezone_source: {
        kind: "identity",
        role: "character_identity",
        meta: { identity_id: "春柳_青年时期" },
      },
    },
  },
  {
    id: "ref_prop_removed",
    type: "imageGenNode",
    position: { x: 0, y: 0 },
    data: {
      __freezone_source: {
        kind: "prop",
        role: "prop_reference",
        meta: { prop_id: "账本" },
      },
    },
  },
] as CanvasNode[];

function roleEdge(
  id: string,
  source: string,
  role: "beat_context" | "identity" | "prop",
  targetHandle: string,
  referenceTarget?: Record<string, unknown>,
): CanvasEdge {
  return {
    id,
    source,
    target: "skill_frame_from_context",
    sourceHandle: "source",
    targetHandle,
    type: "disconnectableEdge",
    data: {
      edgeKind: "role_binding",
      role,
      preset_managed: true,
      ...(referenceTarget ? { reference_target: referenceTarget } : {}),
    },
  };
}

describe("syncBeatContextMainlineEdges", () => {
  it("recomputes frame_from_context identity and prop inputs from the current beat context", () => {
    const edges = [
      roleEdge("edge_context", "context_beat", "beat_context", "beat_context"),
      roleEdge(
        "edge_identity_keep",
        "ref_identity_keep",
        "identity",
        "identity:沈月白_青年时期",
        { kind: "identity", identity_id: "沈月白_青年时期" },
      ),
      roleEdge(
        "edge_identity_removed",
        "ref_identity_removed",
        "identity",
        "identity:春柳_青年时期",
        { kind: "identity", identity_id: "春柳_青年时期" },
      ),
      roleEdge(
        "edge_prop_removed",
        "ref_prop_removed",
        "prop",
        "prop:账本",
        { kind: "prop", prop_id: "账本" },
      ),
    ];

    const next = syncBeatContextMainlineEdges(
      "context_beat",
      ["沈月白_青年时期", "陆辰_青年时期"],
      [],
      nodes,
      edges,
    );

    expect(next.map((edge) => edge.id)).toContain("edge_context");
    expect(next.map((edge) => edge.id)).toContain("edge_identity_keep");
    expect(next.map((edge) => edge.id)).not.toContain("edge_identity_removed");
    expect(next.map((edge) => edge.id)).not.toContain("edge_prop_removed");
    expect(
      next
        .filter((edge) => (edge.data as { role?: unknown }).role === "identity")
        .map((edge) => edge.targetHandle)
        .sort(),
    ).toEqual(["identity:沈月白_青年时期", "identity:陆辰_青年时期"]);
    expect(next.some((edge) => (edge.data as { role?: unknown }).role === "prop")).toBe(false);
  });
});
