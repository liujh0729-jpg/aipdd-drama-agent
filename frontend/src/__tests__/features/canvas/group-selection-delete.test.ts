// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { collectBatchDeletableIds } from "@/features/canvas/domain/groupSelectionDelete";
import { CANVAS_NODE_TYPES, type CanvasNode } from "@/features/canvas/domain/canvasNodes";

function node(
  id: string,
  type: string,
  extra: Partial<CanvasNode> & { data?: Record<string, unknown> } = {},
): CanvasNode {
  const { data, ...rest } = extra;
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: (data ?? {}) as CanvasNode["data"],
    ...rest,
  } as CanvasNode;
}

describe("collectBatchDeletableIds", () => {
  it("re-includes an enclosing group when all its children are selected (the empty-group bug)", () => {
    // The marquee drops the group from `selected`, keeping only its children.
    const nodes = [
      node("g1", CANVAS_NODE_TYPES.group),
      node("a", CANVAS_NODE_TYPES.audio, { parentId: "g1" }),
      node("b", CANVAS_NODE_TYPES.textAnnotation, { parentId: "g1" }),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["a", "b"]);
    expect(new Set(deletable)).toEqual(new Set(["g1", "a", "b"]));
  });

  it("handles two fully-selected groups (exact user repro)", () => {
    const nodes = [
      node("g1", CANVAS_NODE_TYPES.group),
      node("a", CANVAS_NODE_TYPES.audio, { parentId: "g1" }),
      node("g2", CANVAS_NODE_TYPES.group),
      node("c", CANVAS_NODE_TYPES.audio, { parentId: "g2" }),
      node("d", CANVAS_NODE_TYPES.textAnnotation, { parentId: "g2" }),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["a", "c", "d"]);
    expect(new Set(deletable)).toEqual(new Set(["g1", "g2", "a", "c", "d"]));
  });

  it("does NOT delete a group when only some of its children are selected", () => {
    const nodes = [
      node("g1", CANVAS_NODE_TYPES.group),
      node("a", CANVAS_NODE_TYPES.audio, { parentId: "g1" }),
      node("b", CANVAS_NODE_TYPES.textAnnotation, { parentId: "g1" }),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["a"]);
    expect(new Set(deletable)).toEqual(new Set(["a"]));
  });

  it("keeps a group when it contains a preset-locked child that cannot be emptied", () => {
    const nodes = [
      node("g1", CANVAS_NODE_TYPES.group),
      node("a", CANVAS_NODE_TYPES.audio, { parentId: "g1" }),
      node("locked", CANVAS_NODE_TYPES.imageGen, {
        parentId: "g1",
        data: { preset_managed: true },
      }),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["a", "locked"]);
    // group excluded (can't be emptied), locked child excluded, only "a" deletable.
    expect(new Set(deletable)).toEqual(new Set(["a"]));
  });

  it("excludes a preset-managed group itself", () => {
    const nodes = [
      node("g1", CANVAS_NODE_TYPES.group, { data: { preset_managed: true } }),
      node("a", CANVAS_NODE_TYPES.audio, { parentId: "g1" }),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["g1", "a"]);
    expect(new Set(deletable)).toEqual(new Set(["a"]));
  });

  it("passes plain multi-selection through unchanged", () => {
    const nodes = [
      node("a", CANVAS_NODE_TYPES.audio),
      node("b", CANVAS_NODE_TYPES.imageGen),
    ];
    const deletable = collectBatchDeletableIds(nodes, ["a", "b"]);
    expect(new Set(deletable)).toEqual(new Set(["a", "b"]));
  });
});
