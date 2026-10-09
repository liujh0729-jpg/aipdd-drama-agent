// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { TOP_TABS, topTabForPathname } from "@/lib/episode-nav";

describe("episode top tabs", () => {
  it("keeps the three workbench tabs in pipeline order", () => {
    const ids = TOP_TABS.map((tab) => tab.id);
    expect(ids).toEqual(["script", "beats", "compose"]);
  });

  it("maps each route segment to its tab", () => {
    expect(topTabForPathname("/projects/demo/episodes/1/script")).toBe("script");
    expect(topTabForPathname("/projects/demo/episodes/1/beats")).toBe("beats");
    expect(topTabForPathname("/projects/demo/episodes/1/compose")).toBe(
      "compose",
    );
  });

  it("keeps unknown sub-paths on the script default", () => {
    expect(topTabForPathname("/projects/demo/episodes/1")).toBe("script");
    expect(topTabForPathname("/projects/demo/episodes/1/overview")).toBe(
      "script",
    );
  });

  it("assigns the script stage to the first tab", () => {
    const script = TOP_TABS[0];
    expect(script.id).toBe("script");
    expect(script.routeSegment).toBe("/script");
    expect(script.labelKey).toBe("episode.nav.script");
    expect(script.stageIds).toEqual(["script"]);
  });
});