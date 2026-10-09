// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const routeSource = readFileSync(
  "src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx",
  "utf-8",
);
const actionPanelSource = readFileSync(
  "src/components/episode/beat-workbench/action-panel.tsx",
  "utf-8",
);

describe("beats sub-section deep links", () => {
  it("keeps sub params as first-class route state", () => {
    expect(routeSource).toContain("targetSection");
    expect(routeSource).toContain("targetSection={targetSection}");
    expect(actionPanelSource).toContain("targetSection?: SectionId | null");
    expect(actionPanelSource).toContain("next.add(targetSection)");
  });
});
