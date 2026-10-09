// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebouncedValue } from "@/hooks/use-debounced-value";

describe("useDebouncedValue", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns the initial value immediately", () => {
    const { result } = renderHook(() => useDebouncedValue(1, 350));
    expect(result.current).toBe(1);
  });

  it("delays updates until the value settles for the delay window", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 350),
      { initialProps: { value: 1 } },
    );

    rerender({ value: 2 });
    expect(result.current).toBe(1); // not yet

    act(() => {
      vi.advanceTimersByTime(349);
    });
    expect(result.current).toBe(1); // still within window

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(2); // settled
  });

  it("coalesces a burst of changes into the final value", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 350),
      { initialProps: { value: 8 } },
    );

    // Mimic dragging the duration slider: 8 -> 10 -> 4 -> 5 in quick succession.
    for (const value of [10, 4, 5]) {
      rerender({ value });
      act(() => {
        vi.advanceTimersByTime(100); // each shorter than the 350ms window
      });
    }
    expect(result.current).toBe(8); // no intermediate value ever committed

    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(result.current).toBe(5); // only the settled value lands
  });
});
