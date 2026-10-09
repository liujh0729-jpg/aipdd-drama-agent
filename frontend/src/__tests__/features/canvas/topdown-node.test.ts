// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";
import {
  canvasNodeDefinitions,
  DOWNSTREAM_SPAWN_WHITELIST,
  getMenuNodeDefinitions,
} from "@/features/canvas/domain/nodeRegistry";
import { extractUpstreamContent } from "@/features/canvas/application/graphContentResolver";

function fakeTopDownNode(data: Record<string, unknown>): CanvasNode {
  return {
    id: "topdown-1",
    type: CANVAS_NODE_TYPES.topDownView,
    position: { x: 0, y: 0 },
    data,
  } as unknown as CanvasNode;
}

describe("topDownView node registry", () => {
  it("is visible in the create menu with an image-only default shape", () => {
    expect(getMenuNodeDefinitions().map((item) => item.type)).toContain(
      CANVAS_NODE_TYPES.topDownView,
    );
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.topDownView];
    expect(definition.visibleInMenu).toBe(true);
    expect(definition.menuIcon).toBe("topDownView");
    expect(definition.createDefaultData()).toMatchObject({
      imageUrl: null,
      previewImageUrl: null,
      baseImageUrl: null,
    });
  });

  it("spawns image-class downstream nodes once it has an exported picture", () => {
    // 导出后俯视图按图片参与下游，白名单应与图片节点同源（至少能接 imageGen）。
    expect(DOWNSTREAM_SPAWN_WHITELIST[CANVAS_NODE_TYPES.topDownView]).toContain(
      CANVAS_NODE_TYPES.imageGen,
    );
  });
});

describe("topDownView upstream content", () => {
  it("exposes its exported imageUrl to downstream as the reference image", () => {
    const content = extractUpstreamContent(fakeTopDownNode({ imageUrl: "https://cdn/a.png" }));
    expect(content.imageUrl).toBe("https://cdn/a.png");
  });

  it("falls back to previewImageUrl when no export yet", () => {
    const content = extractUpstreamContent(
      fakeTopDownNode({ previewImageUrl: "https://cdn/prev.png" }),
    );
    expect(content.imageUrl).toBe("https://cdn/prev.png");
  });

  it("carries no image when neither url is set", () => {
    const content = extractUpstreamContent(fakeTopDownNode({}));
    expect(content.imageUrl).toBeUndefined();
  });
});
