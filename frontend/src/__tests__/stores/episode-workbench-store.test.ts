// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  normalizeLastEpisodeLocation,
  normalizeSelection,
} from "@/stores/episode-workbench-store";

describe("episode workbench store", () => {
  it("drops malformed persisted multi selections instead of throwing", () => {
    expect(() =>
      normalizeSelection({
        mode: "multi",
        checked: {},
        activeBeat: 6,
      }),
    ).not.toThrow();

    expect(
      normalizeSelection({
        mode: "multi",
        checked: {},
        activeBeat: 6,
      }),
    ).toEqual({ mode: "none", activeBeat: null });
  });

  it("drops malformed persisted single selections", () => {
    expect(
      normalizeSelection({
        mode: "single",
        beatNum: undefined,
        activeBeat: 6,
      }),
    ).toEqual({ mode: "none", activeBeat: null });
  });

  it("keeps only current-project episode routes as remembered episode locations", () => {
    expect(
      normalizeLastEpisodeLocation(
        "proj-a",
        "/projects/proj-a/episodes/3/script?beat=2",
      ),
    ).toBe("/projects/proj-a/episodes/3/script?beat=2");

    expect(
      normalizeLastEpisodeLocation("proj-a", "/projects/proj-a/freezone?canvas=beat-3"),
    ).toBeNull();
    expect(
      normalizeLastEpisodeLocation("proj-a", "/projects/proj-b/episodes/3/script"),
    ).toBeNull();
  });
});
