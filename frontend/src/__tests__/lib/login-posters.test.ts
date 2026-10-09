// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import { buildColumns, mulberry32 } from "@/lib/login-posters";
import type { Poster } from "@/types/login";

function makePosters(n: number): Poster[] {
  return Array.from({ length: n }, (_, i) => ({
    id: String(i + 1).padStart(2, "0"),
    src_avif: `/login-posters/${String(i + 1).padStart(2, "0")}.avif`,
    src_webp: `/login-posters/${String(i + 1).padStart(2, "0")}.webp`,
    alt: "",
    dominant_hex: "#000000",
  }));
}

describe("mulberry32", () => {
  it("produces the same sequence for the same seed", () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    for (let i = 0; i < 10; i++) expect(a()).toBe(b());
  });

  it("produces different sequences for different seeds", () => {
    const a = mulberry32(1);
    const b = mulberry32(2);
    expect(a()).not.toBe(b());
  });
});

describe("buildColumns", () => {
  const posters = makePosters(36);

  it("returns exactly 6 columns", () => {
    const cols = buildColumns(posters, 123);
    expect(cols).toHaveLength(6);
  });

  it("is deterministic for the same seed", () => {
    const a = buildColumns(posters, 99);
    const b = buildColumns(posters, 99);
    expect(a).toEqual(b);
  });

  it("differs across seeds", () => {
    const a = buildColumns(posters, 1);
    const b = buildColumns(posters, 2);
    expect(a).not.toEqual(b);
  });

  it("each column has at least 6 items (so marquee track duplication looks full)", () => {
    const cols = buildColumns(posters, 7);
    for (const col of cols) expect(col.length).toBeGreaterThanOrEqual(6);
  });
});
