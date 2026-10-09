// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import {
  getProjectCover,
  PROJECT_COVER_PALETTE,
  NOISE_DATA_URI,
} from "@/lib/project-cover";

describe("getProjectCover", () => {
  it("returns the first rendered character uppercased", () => {
    expect(getProjectCover("ko").initial).toBe("K");
    expect(getProjectCover("test").initial).toBe("T");
    expect(getProjectCover("中文项目").initial).toBe("中");
    expect(getProjectCover("").initial).toBe("?");
  });

  it("picks a gradient from the curated palette", () => {
    const { gradient } = getProjectCover("ko");
    expect(PROJECT_COVER_PALETTE.map((p) => p.gradient)).toContain(gradient);
  });

  it("is deterministic — same name → same gradient", () => {
    expect(getProjectCover("my-project").gradient).toBe(
      getProjectCover("my-project").gradient,
    );
  });

  it("distributes across the palette — different inputs hit different stops", () => {
    const seen = new Set<string>();
    for (const name of ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]) {
      seen.add(getProjectCover(name).gradient);
    }
    expect(seen.size).toBeGreaterThanOrEqual(4);
  });

  it("exports exactly 8 palette entries", () => {
    expect(PROJECT_COVER_PALETTE).toHaveLength(8);
  });

  it("exports an SVG noise data URI", () => {
    expect(NOISE_DATA_URI.startsWith("data:image/svg+xml")).toBe(true);
    expect(NOISE_DATA_URI).toContain("feTurbulence");
  });
});
