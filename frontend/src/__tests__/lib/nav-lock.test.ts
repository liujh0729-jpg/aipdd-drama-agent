// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, vi } from "vitest";

describe("nav-lock", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("first acquire returns true, subsequent return false", async () => {
    const { tryAcquireNavLock, isNavLocked } = await import("@/lib/nav-lock");
    expect(isNavLocked()).toBe(false);
    expect(tryAcquireNavLock()).toBe(true);
    expect(isNavLocked()).toBe(true);
    expect(tryAcquireNavLock()).toBe(false);
  });
});
