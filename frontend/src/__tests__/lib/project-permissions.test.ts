// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  canDeleteProject,
  canManageProjectGrants,
} from "@/lib/project-permissions";
import type { ProjectRole, ProjectSummary } from "@/types/project";

const runtimeState = vi.hoisted(() => ({ isCeRuntime: false }));

vi.mock("@/lib/runtime-config", () => ({
  isCeRuntime: () => runtimeState.isCeRuntime,
}));

function summaryWithRole(role: ProjectRole): ProjectSummary {
  return { effectiveRole: role } as ProjectSummary;
}

describe("canManageProjectGrants (edition gating)", () => {
  beforeEach(() => {
    runtimeState.isCeRuntime = false;
  });

  it("allows admin and owner in EE runtime", () => {
    expect(canManageProjectGrants(summaryWithRole("admin"))).toBe(true);
    expect(canManageProjectGrants(summaryWithRole("owner"))).toBe(true);
  });

  it("denies viewer and editor in EE runtime", () => {
    expect(canManageProjectGrants(summaryWithRole("viewer"))).toBe(false);
    expect(canManageProjectGrants(summaryWithRole("editor"))).toBe(false);
  });

  it("denies everyone in CE runtime — even owner (no grants concept)", () => {
    runtimeState.isCeRuntime = true;
    expect(canManageProjectGrants(summaryWithRole("owner"))).toBe(false);
    expect(canManageProjectGrants(summaryWithRole("admin"))).toBe(false);
  });
});

describe("canDeleteProject (CE lifecycle stays available)", () => {
  beforeEach(() => {
    runtimeState.isCeRuntime = false;
  });

  it("owner can delete regardless of edition", () => {
    expect(canDeleteProject(summaryWithRole("owner"))).toBe(true);
    runtimeState.isCeRuntime = true;
    expect(canDeleteProject(summaryWithRole("owner"))).toBe(true);
  });

  it("non-owner cannot delete", () => {
    expect(canDeleteProject(summaryWithRole("admin"))).toBe(false);
  });
});
