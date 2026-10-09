// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  createEmptyScene,
  decodeScene,
  encodeScene,
  nextItemId,
  TOPDOWN_DESIGN_SIZE,
  TOPDOWN_SCENE_VERSION,
  type TopDownScene,
} from "@/features/canvas/topdown/sceneModel";
import { buildSceneSvgMarkup } from "@/features/canvas/topdown/renderScene";

function sampleScene(): TopDownScene {
  return {
    version: TOPDOWN_SCENE_VERSION,
    size: TOPDOWN_DESIGN_SIZE,
    background: { kind: "none" },
    items: [
      { id: "p1", kind: "person", x: 100, y: 120, label: "A&B", color: "#e23b3b" },
      { id: "c1", kind: "camera", x: 300, y: 200, label: "Cam", angleDeg: 45 },
      { id: "r1", kind: "prop", x: 400, y: 400, w: 120, h: 80, shape: "sofa" },
      { id: "a1", kind: "arrow", points: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }], color: "#2f6fd0" },
      { id: "t1", kind: "text", x: 50, y: 60, text: "客厅 <tag>", color: "#111111" },
    ],
  };
}

describe("topdown sceneModel", () => {
  it("createEmptyScene returns the fixed design-size skeleton", () => {
    const scene = createEmptyScene();
    expect(scene.version).toBe(TOPDOWN_SCENE_VERSION);
    expect(scene.size).toBe(TOPDOWN_DESIGN_SIZE);
    expect(scene.background).toEqual({ kind: "none" });
    expect(scene.items).toEqual([]);
  });

  it("decodeScene(undefined) falls back to an empty scene", () => {
    expect(decodeScene(undefined)).toEqual(createEmptyScene());
  });

  it("encode/decode round-trips a well-formed scene", () => {
    const scene = sampleScene();
    expect(decodeScene(encodeScene(scene))).toEqual(scene);
  });

  it("decodeScene drops dirty elements and missing-point arrows", () => {
    const decoded = decodeScene({
      version: TOPDOWN_SCENE_VERSION,
      size: TOPDOWN_DESIGN_SIZE,
      background: { kind: "none" },
      items: [
        { kind: "bogus" },
        { id: "a", kind: "arrow", points: [{ x: 1, y: 1 }], color: "#000" },
        { id: "p", kind: "person", x: 5, y: 6 },
      ],
    });
    expect(decoded.items.map((it) => it.id)).toEqual(["p"]);
    // 缺字段的 person 用兜底值补全。
    const person = decoded.items[0];
    expect(person && person.kind === "person" && person.label).toBe("");
  });

  it("decodeScene keeps a valid image background and rejects a partial one", () => {
    expect(
      decodeScene({ background: { kind: "image", url: "https://x/y.png" }, items: [] }).background,
    ).toEqual({ kind: "image", url: "https://x/y.png", opacity: 1 });
    expect(
      decodeScene({ background: { kind: "image", url: "" }, items: [] }).background,
    ).toEqual({ kind: "none" });
  });

  it("nextItemId yields distinct ids", () => {
    expect(nextItemId("p")).not.toBe(nextItemId("p"));
  });
});

describe("topdown renderScene.buildSceneSvgMarkup", () => {
  const scene = sampleScene();
  const svg = buildSceneSvgMarkup(scene);

  it("wraps a sized viewBox and background fill", () => {
    expect(svg).toContain(`viewBox="0 0 ${TOPDOWN_DESIGN_SIZE} ${TOPDOWN_DESIGN_SIZE}"`);
    expect(svg).toContain('fill="#fbfaf5"');
  });

  it("maps each item kind to its element", () => {
    // person -> circle with its color stroke
    expect(svg).toMatch(/<circle[^>]*r="26"[^>]*stroke="#e23b3b"/);
    // camera -> rotate transform with angleDeg
    expect(svg).toContain('transform="rotate(45 300 200)"');
    // prop -> rect
    expect(svg).toMatch(/<rect[^>]*width="120"[^>]*height="80"/);
    // arrow -> polyline + polygon head
    expect(svg).toContain("<polyline");
    expect(svg).toContain("<polygon");
  });

  it("escapes text payloads", () => {
    expect(svg).toContain("A&amp;B");
    expect(svg).toContain("客厅 &lt;tag&gt;");
    expect(svg).not.toContain("<tag>");
  });

  it("excludes the background <image> when asked (rasterization path)", () => {
    const withBg: TopDownScene = {
      ...scene,
      background: { kind: "image", url: "https://cdn/base.png", opacity: 0.5 },
    };
    expect(buildSceneSvgMarkup(withBg)).toContain("<image");
    expect(buildSceneSvgMarkup(withBg, { includeBackgroundImage: false })).not.toContain("<image");
  });
});
