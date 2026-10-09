// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";
import { useSeenPoolStore } from "@/stores/seen-pool-store";

describe("useSeenPoolStore markSeen cap", () => {
  beforeEach(() => {
    useSeenPoolStore.getState().reset();
  });

  it("caps each scope to the most recent 500 ids", () => {
    const { markSeen } = useSeenPoolStore.getState();
    for (let i = 0; i < 600; i += 1) {
      markSeen("proj", 1, `id-${i}`);
    }
    const pool = useSeenPoolStore.getState().seen["proj:1"];
    expect(pool).toHaveLength(500);
    // Oldest ids evicted, newest retained.
    expect(pool).not.toContain("id-0");
    expect(pool).toContain("id-599");
    expect(pool).toContain("id-100");
  });

  it("ignores duplicate ids without growing the pool", () => {
    const { markSeen, isSeen } = useSeenPoolStore.getState();
    markSeen("proj", 2, "dup");
    markSeen("proj", 2, "dup");
    expect(useSeenPoolStore.getState().seen["proj:2"]).toEqual(["dup"]);
    expect(isSeen("proj", 2, "dup")).toBe(true);
  });
});
