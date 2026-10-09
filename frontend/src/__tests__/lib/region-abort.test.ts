// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi } from "vitest";
import { regionAbortController, resetRegionAbortController } from "@/lib/region-abort";

describe("region-abort", () => {
  it("exposes an AbortController whose signal can be observed", () => {
    expect(regionAbortController().signal.aborted).toBe(false);
  });

  it("abort() propagates to listeners", () => {
    const listener = vi.fn();
    regionAbortController().signal.addEventListener("abort", listener);
    regionAbortController().abort();
    expect(listener).toHaveBeenCalled();
  });

  it("resetRegionAbortController swaps in a fresh (non-aborted) controller", () => {
    regionAbortController().abort();
    expect(regionAbortController().signal.aborted).toBe(true);
    resetRegionAbortController();
    expect(regionAbortController().signal.aborted).toBe(false);
  });
});
