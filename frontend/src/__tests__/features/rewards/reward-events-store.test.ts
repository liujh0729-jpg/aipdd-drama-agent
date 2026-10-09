// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import {
  capPersistedRewardEvents,
  type AccessoryRewardEvent,
} from "@/features/rewards/reward-events-store";

function event(
  id: string,
  status: AccessoryRewardEvent["status"],
  createdAt: string,
): AccessoryRewardEvent {
  return {
    id,
    type: "accessory",
    rewardId: "piko-accessory-founder-medal",
    source: "points",
    status,
    createdAt,
  };
}

describe("capPersistedRewardEvents", () => {
  it("keeps every event when under the limit", () => {
    const events = [event("a", "claimed", "2026-01-01T00:00:00Z")];
    expect(capPersistedRewardEvents(events)).toBe(events);
  });

  it("always retains actionable (pending/seen) events and trims old claimed ones", () => {
    const claimed = Array.from({ length: 250 }, (_, i) =>
      event(`c${i}`, "claimed", `2026-01-01T00:${String(i).padStart(2, "0")}:00Z`),
    );
    const pending = event("p", "pending", "2020-01-01T00:00:00Z");
    const seen = event("s", "seen", "2020-01-01T00:00:00Z");
    const result = capPersistedRewardEvents([pending, ...claimed, seen]);

    expect(result.length).toBe(200);
    // Actionable events survive despite being the oldest.
    expect(result.some((e) => e.id === "p")).toBe(true);
    expect(result.some((e) => e.id === "s")).toBe(true);
    // The newest claimed events are kept; the oldest are dropped.
    expect(result.some((e) => e.id === "c249")).toBe(true);
    expect(result.some((e) => e.id === "c0")).toBe(false);
  });

  it("preserves original ordering for the retained subset", () => {
    const events = Array.from({ length: 300 }, (_, i) =>
      event(`c${i}`, "claimed", `2026-01-01T${String(i % 24).padStart(2, "0")}:00:00Z`),
    );
    const result = capPersistedRewardEvents(events);
    const indices = result.map((e) => events.indexOf(e));
    const sorted = [...indices].sort((a, b) => a - b);
    expect(indices).toEqual(sorted);
  });
});
