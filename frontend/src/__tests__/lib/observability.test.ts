// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { initObservability, getObservabilityTags } from "@/lib/observability";
import { useRegionStore } from "@/stores/region-store";

describe("observability", () => {
  beforeEach(() => {
    useRegionStore.setState({ selectedRegionId: null });
  });

  it("initObservability exposes the current region via getObservabilityTags", () => {
    useRegionStore.setState({ selectedRegionId: "cn-1" });
    initObservability();
    expect(getObservabilityTags().region_id).toBe("cn-1");
  });

  it("region_id is null when no region is selected", () => {
    useRegionStore.setState({ selectedRegionId: null });
    initObservability();
    expect(getObservabilityTags().region_id).toBeNull();
  });

  it("tags update when the region changes after init", () => {
    initObservability();
    useRegionStore.setState({ selectedRegionId: "us-1" });
    expect(getObservabilityTags().region_id).toBe("us-1");
  });
});
