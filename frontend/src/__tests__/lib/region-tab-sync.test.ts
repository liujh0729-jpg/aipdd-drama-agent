// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { initRegionTabSync, broadcastSwitching } from "@/lib/region-tab-sync";
import { useRegionStore } from "@/stores/region-store";

describe("region-tab-sync", () => {
  beforeEach(() => {
    useRegionStore.setState({ isLocked: false, isSwitching: false, selectedRegionId: null });
    vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("receiving a foreign 'switching' message sets isLocked=true and schedules a reload", async () => {
    const cleanup = initRegionTabSync();
    const channel = new BroadcastChannel("region-switch");
    channel.postMessage({ type: "switching", newRegionId: "us-1" });
    // Let the BroadcastChannel deliver the message asynchronously.
    await new Promise((r) => setTimeout(r, 10));
    expect(useRegionStore.getState().isLocked).toBe(true);
    await new Promise((r) => setTimeout(r, 300));
    expect(window.location.reload).toHaveBeenCalled();
    channel.close();
    cleanup();
  });

  it("broadcastSwitching posts a 'switching' message", async () => {
    const channel = new BroadcastChannel("region-switch");
    const received = vi.fn();
    channel.onmessage = received;
    broadcastSwitching("us-1");
    await new Promise((r) => setTimeout(r, 10));
    expect(received).toHaveBeenCalled();
    channel.close();
  });
});
