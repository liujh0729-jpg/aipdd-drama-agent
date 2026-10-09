// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import { resolveImage } from "@/lib/resolve-image";
import type { PoolImage } from "@/lib/queries/sketches";

function makeImg(overrides: Partial<PoolImage>): PoolImage {
  return {
    id: "img1", type: "sketch", mode: "1x1_2-3_sketch", grid_index: 0,
    cell_index: 0, row: 0, col: 0, original_beat: 1, cell_url: "/cell.png",
    grid_url: "/grid.png", grid_path: "grid.png", stale: false, ...overrides,
  };
}

describe("resolveImage", () => {
  it("prefers the current canonical URL over assigned pool images", () => {
    const img = makeImg({ id: "sk1", type: "sketch", cell_url: "/pool.png" });
    const result = resolveImage([img], { "1": "sk1" }, 1, "sketch", "/canonical.png");
    expect(result.url).toBe("/canonical.png");
    expect(result.poolImage).toBeNull();
  });

  it("does not use assigned pool image as the current sketch", () => {
    const img = makeImg({ id: "sk1", type: "sketch", cell_url: "/sk.png" });
    const result = resolveImage([img], { "1": "sk1" }, 1, "sketch", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("skips assigned pool image when type does not match", () => {
    const img = makeImg({ id: "rn1", type: "render", cell_url: "/rn.png" });
    const result = resolveImage([img], { "1": "rn1" }, 1, "sketch", null);
    expect(result.url).toBeNull();
  });

  it("does not use newest pool candidate as the current sketch", () => {
    const old = makeImg({ id: "a", generated_at: "2026-01-01T00:00:00Z", stale: false, cell_url: "/old.png" });
    const fresh = makeImg({ id: "b", generated_at: "2026-04-01T00:00:00Z", stale: false, cell_url: "/new.png" });
    const result = resolveImage([old, fresh], {}, 1, "sketch", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("does not use pool freshness ordering for the current sketch", () => {
    const staleNew = makeImg({ id: "a", generated_at: "2026-04-01T00:00:00Z", stale: true, cell_url: "/stale.png" });
    const freshOld = makeImg({ id: "b", generated_at: "2026-01-01T00:00:00Z", stale: false, cell_url: "/fresh.png" });
    const result = resolveImage([staleNew, freshOld], {}, 1, "sketch", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("does not fall back to a stale pool candidate for the current sketch", () => {
    const stale = makeImg({ id: "a", stale: true, cell_url: "/stale.png" });
    const result = resolveImage([stale], {}, 1, "sketch", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("does not use a render candidate as the sketch current image", () => {
    const render = makeImg({ id: "r", type: "render", cell_url: "/r.png" });
    const result = resolveImage([render], {}, 1, "sketch", null);
    expect(result.url).toBeNull();
  });

  it("does not use assigned pool image as the current render", () => {
    const img = makeImg({ id: "rn1", type: "render", cell_url: "/rn.png" });
    const result = resolveImage([img], { "1": "rn1" }, 1, "render", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("does not use newest pool candidate as the current render", () => {
    const old = makeImg({ id: "a", type: "render", generated_at: "2026-01-01T00:00:00Z", cell_url: "/old.png" });
    const fresh = makeImg({ id: "b", type: "render", generated_at: "2026-04-01T00:00:00Z", cell_url: "/new.png" });
    const result = resolveImage([old, fresh], {}, 1, "render", null);
    expect(result.url).toBeNull();
    expect(result.poolImage).toBeNull();
  });

  it("returns the current canonical URL when no pool images exist", () => {
    const result = resolveImage([], {}, 1, "sketch", "/canonical.png");
    expect(result.url).toBe("/canonical.png");
  });

  it("returns the current canonical render URL when pool images exist", () => {
    const img = makeImg({ id: "rn1", type: "render", cell_url: "/pool.png" });
    const result = resolveImage([img], { "1": "rn1" }, 1, "render", "/frame.png");
    expect(result.url).toBe("/frame.png");
    expect(result.poolImage).toBeNull();
  });
});
