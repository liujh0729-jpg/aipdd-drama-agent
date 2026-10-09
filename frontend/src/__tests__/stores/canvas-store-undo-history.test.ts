// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";
import type { NodeChange } from "@xyflow/react";

import { useCanvasStore, type CanvasNode } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";

describe("canvasStore undo history — 创建节点后一次撤销即可移除", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("自动测量(dimensions 变更)不产生多余撤销步,一次 undo 即删掉新节点", () => {
    const nodeId = useCanvasStore
      .getState()
      .addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});

    expect(useCanvasStore.getState().nodes).toHaveLength(1);
    // addNode 压入一步历史。
    expect(useCanvasStore.getState().history.past).toHaveLength(1);

    // 模拟 ReactFlow 对新节点的自动测量:一条 dimensions 变更(无 resizing)。
    useCanvasStore.getState().onNodesChange([
      {
        id: nodeId,
        type: "dimensions",
        dimensions: { width: 320, height: 200 },
      } as NodeChange<CanvasNode>,
    ]);

    // 自动测量不应再压入历史(否则需要两次 undo 才能删掉节点)。
    expect(useCanvasStore.getState().history.past).toHaveLength(1);

    // 一次 undo 即可移除刚创建的节点。
    expect(useCanvasStore.getState().undo()).toBe(true);
    expect(useCanvasStore.getState().nodes).toHaveLength(0);
  });

  it("用户 resize 结束(dimensions + resizing:false)仍会压入一步历史", () => {
    const nodeId = useCanvasStore
      .getState()
      .addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});
    expect(useCanvasStore.getState().history.past).toHaveLength(1);

    useCanvasStore.getState().onNodesChange([
      {
        id: nodeId,
        type: "dimensions",
        resizing: false,
        dimensions: { width: 400, height: 300 },
      } as NodeChange<CanvasNode>,
    ]);

    // resize 是真实编辑,应额外压入历史。
    expect(useCanvasStore.getState().history.past).toHaveLength(2);
  });
});
