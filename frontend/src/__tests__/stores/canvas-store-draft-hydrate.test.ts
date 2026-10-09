// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

describe("canvasStore draft hydrate", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("restores draft content and dirty mutation state atomically", () => {
    useCanvasStore.getState().hydrateCanvasDraft({
      nodes: [
        {
          id: "draft-node",
          type: CANVAS_NODE_TYPES.upload,
          position: { x: 12, y: 34 },
          data: { imageUrl: "/static/draft.png" },
        },
      ],
      edges: [],
      history: {
        past: [
          {
            nodes: [],
            edges: [],
          },
        ],
        future: [],
      },
      mutation: {
        userEditsSinceHydrate: 4,
        lastMutationSource: "manual_clear",
        pendingClearIntent: true,
      },
    });

    const state = useCanvasStore.getState();
    expect(state.nodes).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "draft-node" })]),
    );
    expect(state.history.past).toHaveLength(1);
    expect(state.userEditsSinceHydrate).toBe(4);
    expect(state.lastMutationSource).toBe("manual_clear");
    expect(state.pendingClearIntent).toBe(true);
  });
});
