// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";

function incomingTo(target: string) {
  return useCanvasStore.getState().edges.filter((edge) => edge.target === target);
}

describe("canvasStore.onConnect — 3D 世界节点入边唯一", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("已有上游时拒绝把第二个节点连入 3D 世界节点,保留首个上游", () => {
    const store = useCanvasStore.getState();
    const imgA = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, { imageUrl: "a.png" });
    const imgB = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 200 }, { imageUrl: "b.png" });
    const world = store.addNode(CANVAS_NODE_TYPES.threeDWorld, { x: 400, y: 100 }, {});

    useCanvasStore.getState().onConnect({
      source: imgA,
      target: world,
      sourceHandle: "source",
      targetHandle: "target",
    });
    expect(incomingTo(world)).toHaveLength(1);

    // 第二个上游应被拒绝。
    useCanvasStore.getState().onConnect({
      source: imgB,
      target: world,
      sourceHandle: "source",
      targetHandle: "target",
    });
    const incoming = incomingTo(world);
    expect(incoming).toHaveLength(1);
    expect(incoming[0]?.source).toBe(imgA);
  });

  it("非 3D 世界节点不受单上游限制(可接受多个上游)", () => {
    const store = useCanvasStore.getState();
    const imgA = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});
    const imgB = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 200 }, {});
    const gen = store.addNode(CANVAS_NODE_TYPES.imageGen, { x: 400, y: 100 }, {});

    useCanvasStore.getState().onConnect({
      source: imgA,
      target: gen,
      sourceHandle: "source",
      targetHandle: "target",
    });
    useCanvasStore.getState().onConnect({
      source: imgB,
      target: gen,
      sourceHandle: "source",
      targetHandle: "target",
    });
    expect(incomingTo(gen)).toHaveLength(2);
  });
});
