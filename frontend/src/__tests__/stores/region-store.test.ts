// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("@/lib/cluster-config", () => ({
  clusterConfig: {
    mode: "multi-region",
    regions: [
      { id: "cn-1", displayName: "华东一区" },
      { id: "us-1", displayName: "美西一区" },
    ],
  },
}));

describe("region-store", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it("initializes with no selected region", async () => {
    const { useRegionStore } = await import("@/stores/region-store");
    expect(useRegionStore.getState().selectedRegionId).toBeNull();
    expect(useRegionStore.getState().isSwitching).toBe(false);
    expect(useRegionStore.getState().isLocked).toBe(false);
  });

  it("setRegion + clearRegion update state", async () => {
    const { useRegionStore } = await import("@/stores/region-store");
    useRegionStore.getState().setRegion("cn-1");
    expect(useRegionStore.getState().selectedRegionId).toBe("cn-1");
    useRegionStore.getState().clearRegion();
    expect(useRegionStore.getState().selectedRegionId).toBeNull();
  });

  it("rehydrate preserves whatever was persisted (no sanitize at hydrate time)", async () => {
    localStorage.setItem(
      "supertale-region",
      JSON.stringify({ state: { selectedRegionId: "gone-1" }, version: 1 }),
    );
    const { useRegionStore } = await import("@/stores/region-store");
    expect(useRegionStore.getState().selectedRegionId).toBe("gone-1");
  });

  it("sanitizeAgainstConfig clears a selected id that is not in clusterConfig.regions", async () => {
    localStorage.setItem(
      "supertale-region",
      JSON.stringify({ state: { selectedRegionId: "gone-1" }, version: 1 }),
    );
    const { useRegionStore } = await import("@/stores/region-store");
    useRegionStore.getState().sanitizeAgainstConfig();
    expect(useRegionStore.getState().selectedRegionId).toBeNull();
  });

  it("sanitizeAgainstConfig keeps a selected id that IS in clusterConfig.regions", async () => {
    localStorage.setItem(
      "supertale-region",
      JSON.stringify({ state: { selectedRegionId: "cn-1" }, version: 1 }),
    );
    const { useRegionStore } = await import("@/stores/region-store");
    useRegionStore.getState().sanitizeAgainstConfig();
    expect(useRegionStore.getState().selectedRegionId).toBe("cn-1");
  });

  it("setSwitching / setLocked toggle transient flags", async () => {
    const { useRegionStore } = await import("@/stores/region-store");
    useRegionStore.getState().setSwitching(true);
    expect(useRegionStore.getState().isSwitching).toBe(true);
    useRegionStore.getState().setLocked(true);
    expect(useRegionStore.getState().isLocked).toBe(true);
  });
});
