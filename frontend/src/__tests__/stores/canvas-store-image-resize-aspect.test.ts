// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";

/**
 * 缩放图片节点结束后，节点框必须吸附回图片真实比例（aspectRatio），否则 object-contain
 * 显示的图片会在偏离比例的节点里露出底色形成黑边。回归断言：显式 width/height 与
 * style 必须同步更新（React Flow 渲染时显式尺寸优先于 style）。
 */
describe("image node resize snaps to aspect ratio (no letterbox)", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  function node(id: string) {
    return useCanvasStore.getState().nodes.find((n) => n.id === id)!;
  }

  it("snaps a distorted (too-tall) resized box back to the image aspect ratio", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "img",
          type: CANVAS_NODE_TYPES.exportImage,
          position: { x: 0, y: 0 },
          width: 400,
          height: 400,
          style: { width: 400, height: 400 },
          data: { imageUrl: "x.png", aspectRatio: "2:1" },
        },
      ],
      [],
    );

    // Simulate a NodeResizer resize-end producing a square (distorted) box.
    useCanvasStore.getState().onNodesChange([
      {
        id: "img",
        type: "dimensions",
        resizing: false,
        setAttributes: true,
        dimensions: { width: 400, height: 400 },
      },
    ]);

    const n = node("img");
    // 2:1 fitted inside a 400x400 box → 400x200, removing top/bottom black bars.
    expect(n.width).toBe(400);
    expect(n.height).toBe(200);
    expect(n.style?.width).toBe(400);
    expect(n.style?.height).toBe(200);
    expect((n.data as { isSizeManuallyAdjusted?: boolean }).isSizeManuallyAdjusted).toBe(true);
  });

  it("leaves an already-aspect-correct resized box untouched", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "img",
          type: CANVAS_NODE_TYPES.exportImage,
          position: { x: 0, y: 0 },
          width: 600,
          height: 300,
          style: { width: 600, height: 300 },
          data: { imageUrl: "x.png", aspectRatio: "2:1" },
        },
      ],
      [],
    );

    useCanvasStore.getState().onNodesChange([
      {
        id: "img",
        type: "dimensions",
        resizing: false,
        setAttributes: true,
        dimensions: { width: 600, height: 300 },
      },
    ]);

    const n = node("img");
    expect(n.width).toBe(600);
    expect(n.height).toBe(300);
  });
});
