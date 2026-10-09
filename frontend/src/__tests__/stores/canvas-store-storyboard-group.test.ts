// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES, isStoryboardGroupNode } from "@/features/canvas/domain/canvasNodes";
import {
  computeStoryboardCell,
  computeStoryboardGridLayout,
  resolveStoryboardCols,
} from "@/features/canvas/domain/storyboardGroup";
import { useCanvasStore } from "@/stores/canvasStore";

function seedImageNodes(count: number) {
  const nodes = Array.from({ length: count }, (_, index) => ({
    id: `n${index}`,
    type: CANVAS_NODE_TYPES.imageEdit,
    // Lay them left-to-right so reading order is deterministic.
    position: { x: index * 400, y: 0 },
    style: { width: 300, height: 200 },
    data: { imageUrl: `${index}.png` },
  }));
  useCanvasStore.getState().setCanvasData(nodes, []);
  return nodes.map((node) => node.id);
}

describe("storyboard grid layout", () => {
  it("packs 5 cells into a near-square grid (3×2) in reading order", () => {
    const layout = computeStoryboardGridLayout({
      count: 5,
      cellWidth: 320,
      cellHeight: 180,
    });
    expect(layout.cols).toBe(3);
    expect(layout.rows).toBe(2);
    expect(layout.cells).toHaveLength(5);
    // Cell 3 starts the second row at the left padding.
    expect(layout.cells[3].x).toBe(layout.cells[0].x);
    expect(layout.cells[3].y).toBeGreaterThan(layout.cells[0].y);
  });

  it("sizes a cell to contain the content at the target aspect", () => {
    // Content wider than 16:9 (640/300 ≈ 2.13) → width-bound.
    const wide = computeStoryboardCell(640, 300, "16:9");
    expect(wide.cellWidth).toBe(640);
    expect(wide.cellHeight).toBe(Math.round(640 / (16 / 9)));
    expect(wide.cellHeight).toBeGreaterThanOrEqual(300);
    // Portrait content, 16:9 → height-bound, never smaller than the content.
    const tall = computeStoryboardCell(300, 520, "16:9");
    expect(tall.cellHeight).toBe(520);
    expect(tall.cellWidth).toBeGreaterThanOrEqual(300);
  });

  it("honours an explicit column count", () => {
    expect(resolveStoryboardCols(5, 2)).toBe(2);
    expect(resolveStoryboardCols(5)).toBe(3);
    // Clamped to the member count.
    expect(resolveStoryboardCols(3, 9)).toBe(3);
  });
});

describe("canvasStore mergeStoryboardGroup", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("wraps members into a storyboard group laid out as a grid", () => {
    const ids = seedImageNodes(3);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids);
    expect(groupId).toBeTruthy();

    const state = useCanvasStore.getState();
    const group = state.nodes.find((node) => node.id === groupId);
    expect(group?.type).toBe(CANVAS_NODE_TYPES.group);
    expect(isStoryboardGroupNode(group)).toBe(true);
    expect(group?.data.storyboardAspect).toBe("16:9");
    expect(group?.data.storyboardCols).toBe(resolveStoryboardCols(3));

    const children = state.nodes.filter((node) => node.parentId === groupId);
    expect(children).toHaveLength(3);
    // Members are hidden thumbnails (the group renders compact previews itself).
    for (const child of children) {
      expect(child.hidden).toBe(true);
    }
  });

  it("reveals members again on ungroup / convert", () => {
    const ids = seedImageNodes(3);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;
    useCanvasStore.getState().convertStoryboardGroupToPlain(groupId);
    const state = useCanvasStore.getState();
    expect(state.nodes.filter((node) => node.parentId === groupId).every((n) => !n.hidden)).toBe(
      true
    );
  });

  it("refuses to merge fewer than two nodes", () => {
    const ids = seedImageNodes(1);
    expect(useCanvasStore.getState().mergeStoryboardGroup(ids)).toBeNull();
  });

  it("re-lays out the grid when columns change", () => {
    const ids = seedImageNodes(4);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;
    const before = useCanvasStore.getState().nodes.find((node) => node.id === groupId);

    useCanvasStore.getState().setStoryboardGroupConfig(groupId, { cols: 4 });
    const after = useCanvasStore.getState().nodes.find((node) => node.id === groupId);

    expect(after?.data.storyboardCols).toBe(4);
    // 4 columns is wider than the default 2×2, so the group grows horizontally.
    const beforeWidth = Number((before?.style as { width?: number })?.width);
    const afterWidth = Number((after?.style as { width?: number })?.width);
    expect(afterWidth).toBeGreaterThan(beforeWidth);
  });

  it("convertStoryboardGroupToPlain drops the storyboard marker but keeps members", () => {
    const ids = seedImageNodes(3);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;

    useCanvasStore.getState().convertStoryboardGroupToPlain(groupId);
    const state = useCanvasStore.getState();
    const group = state.nodes.find((node) => node.id === groupId);
    expect(isStoryboardGroupNode(group)).toBe(false);
    expect(group?.data.storyboardGroup).toBeUndefined();
    expect(state.nodes.filter((node) => node.parentId === groupId)).toHaveLength(3);
  });

  it("storyboard groups can still be ungrouped", () => {
    const ids = seedImageNodes(3);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;
    expect(useCanvasStore.getState().ungroupNode(groupId)).toBe(true);
    expect(useCanvasStore.getState().nodes.some((node) => node.id === groupId)).toBe(false);
  });

  it("adds image members from upload / history into empty slots", () => {
    const ids = seedImageNodes(2);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;

    useCanvasStore
      .getState()
      .addStoryboardMembers(groupId, [{ imageUrl: "/static/projects/p/new.png" }]);

    const state = useCanvasStore.getState();
    const members = state.nodes.filter((node) => node.parentId === groupId);
    expect(members).toHaveLength(3);
    const added = members.find((node) => node.type === CANVAS_NODE_TYPES.exportImage);
    expect(added).toBeTruthy();
    expect(added?.hidden).toBe(true);
    expect((added?.data as { imageUrl?: string }).imageUrl).toBe("/static/projects/p/new.png");
  });

  it("reorders members by moving a slot", () => {
    const ids = seedImageNodes(3); // reading order n0, n1, n2
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;

    // Move slot 0 → slot 2: order becomes n1, n2, n0.
    useCanvasStore.getState().reorderStoryboardMember(groupId, 0, 2);

    const order = useCanvasStore
      .getState()
      .nodes.filter((node) => node.parentId === groupId)
      .sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x)
      .map((node) => node.id);
    expect(order).toEqual([ids[1], ids[2], ids[0]]);
  });

  it("re-anchors external member edges to the group and restores on ungroup", () => {
    const ids = seedImageNodes(2);
    // An external node + an edge from a member to it.
    useCanvasStore.getState().setCanvasData(
      [
        ...ids.map((id, index) => ({
          id,
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: index * 400, y: 0 },
          style: { width: 300, height: 200 },
          data: { imageUrl: `${index}.png` },
        })),
        {
          id: "ext",
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 0, y: 600 },
          style: { width: 300, height: 200 },
          data: { imageUrl: "ext.png" },
        },
      ],
      [{ id: "e1", source: ids[0], target: "ext" }]
    );

    const groupId = useCanvasStore.getState().mergeStoryboardGroup(ids)!;
    // External edge re-anchored onto the group (visible), not hidden.
    const merged = useCanvasStore.getState().edges.find((e) => e.id === "e1");
    expect(merged?.source).toBe(groupId);
    expect(merged?.hidden).toBeFalsy();

    useCanvasStore.getState().ungroupNode(groupId);
    const restored = useCanvasStore.getState().edges.find((e) => e.id === "e1");
    expect(restored?.source).toBe(ids[0]);
  });

  it("hides internal member-member edges on merge", () => {
    const ids = seedImageNodes(2);
    useCanvasStore.getState().setCanvasData(
      ids.map((id, index) => ({
        id,
        type: CANVAS_NODE_TYPES.imageEdit,
        position: { x: index * 400, y: 0 },
        style: { width: 300, height: 200 },
        data: { imageUrl: `${index}.png` },
      })),
      [{ id: "e2", source: ids[0], target: ids[1] }]
    );

    useCanvasStore.getState().mergeStoryboardGroup(ids);
    expect(useCanvasStore.getState().edges.find((e) => e.id === "e2")?.hidden).toBe(true);
  });
});

describe("canvasStore fitGroupToChildren", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("grows the group box so an oversized child no longer overflows", () => {
    const group = "g1";
    const child = "c1";
    // Group box deliberately too small for the child.
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: group,
          type: CANVAS_NODE_TYPES.group,
          position: { x: 0, y: 0 },
          style: { width: 200, height: 150 },
          data: { label: "组" },
        },
        {
          id: child,
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 20, y: 34 },
          parentId: group,
          extent: "parent",
          width: 600,
          height: 420,
          data: { imageUrl: "a.png" },
        },
      ],
      []
    );

    useCanvasStore.getState().fitGroupToChildren(group);
    const fitted = useCanvasStore.getState().nodes.find((node) => node.id === group);
    const w = Number((fitted?.style as { width?: number })?.width);
    const h = Number((fitted?.style as { height?: number })?.height);
    // Must now enclose child right edge (20 + 600) and bottom (34 + 420) + padding.
    expect(w).toBeGreaterThanOrEqual(620);
    expect(h).toBeGreaterThanOrEqual(454);
  });

  it("is grow-only — never shrinks a box that already fits", () => {
    const group = "g2";
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: group,
          type: CANVAS_NODE_TYPES.group,
          position: { x: 0, y: 0 },
          style: { width: 1000, height: 800 },
          data: { label: "组" },
        },
        {
          id: "c2",
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 20, y: 34 },
          parentId: group,
          extent: "parent",
          width: 300,
          height: 200,
          data: { imageUrl: "a.png" },
        },
      ],
      []
    );

    useCanvasStore.getState().fitGroupToChildren(group);
    const after = useCanvasStore.getState().nodes.find((node) => node.id === group);
    expect(Number((after?.style as { width?: number })?.width)).toBe(1000);
    expect(Number((after?.style as { height?: number })?.height)).toBe(800);
  });
});
