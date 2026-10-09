// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import { canonicalProjectRouteParam, replaceProjectPathParam } from "@/lib/project-route";

describe("project route canonicalization", () => {
  const projects = [
    { id: "01KS77361FXAQNKQF2W4EWWVCW", name: "xuanchuanpian", status: "active" as const },
    { id: "01OTHER", name: "demo", status: "active" as const },
  ];

  it("does not accept legacy project names as route params", () => {
    expect(canonicalProjectRouteParam("xuanchuanpian", projects)).toBeNull();
  });

  it("keeps an existing project_id route param unchanged", () => {
    expect(canonicalProjectRouteParam("01KS77361FXAQNKQF2W4EWWVCW", projects)).toBe(
      "01KS77361FXAQNKQF2W4EWWVCW",
    );
  });

  it("replaces only the project path segment", () => {
    expect(
      replaceProjectPathParam(
        "/projects/xuanchuanpian/characters",
        "01KS77361FXAQNKQF2W4EWWVCW",
      ),
    ).toBe("/projects/01KS77361FXAQNKQF2W4EWWVCW/characters");
  });
});
