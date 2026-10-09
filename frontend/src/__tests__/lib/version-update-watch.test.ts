// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { deployedVersionDiffers } from "@/lib/version-update-watch";
import {
  dismissUpdateAvailable,
  markUpdateAvailable,
  resetUpdateAvailableForTests,
  useUpdateAvailable,
} from "@/lib/app-update-available";

describe("deployedVersionDiffers", () => {
  it("is false when the deployed version matches the running one", () => {
    expect(deployedVersionDiffers("260630-abc123", "260630-abc123")).toBe(false);
  });

  it("is true when the deployed version differs from the running one", () => {
    expect(deployedVersionDiffers("260701-def456", "260630-abc123")).toBe(true);
  });

  it("is false when the manifest could not be read (null)", () => {
    // A failed/garbage fetch must never nag — better a missed nudge than a
    // false one on every poll.
    expect(deployedVersionDiffers(null, "260630-abc123")).toBe(false);
  });
});

describe("app-update-available store", () => {
  afterEach(() => {
    resetUpdateAvailableForTests();
  });

  it("flips to available and back to hidden on dismiss", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    expect(result.current).toBe(false);

    act(() => markUpdateAvailable());
    expect(result.current).toBe(true);

    act(() => dismissUpdateAvailable());
    expect(result.current).toBe(false);
  });

  it("is idempotent: a second markUpdateAvailable after dismiss stays hidden", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    act(() => markUpdateAvailable());
    act(() => dismissUpdateAvailable());
    act(() => markUpdateAvailable());
    // Once dismissed for this session we never re-nag.
    expect(result.current).toBe(false);
  });

  it("ignores dismiss before an update is available", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    act(() => dismissUpdateAvailable());
    expect(result.current).toBe(false);
  });
});
