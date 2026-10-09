// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMediaQuery } from "@/hooks/use-media-query";

type MqlListener = (event: { matches: boolean }) => void;

interface MockMediaQueryList {
  matches: boolean;
  addEventListener: (type: "change", cb: MqlListener) => void;
  removeEventListener: (type: "change", cb: MqlListener) => void;
  _trigger: (matches: boolean) => void;
  _listenerCount: () => number;
}

function createMockMql(initialMatches: boolean): MockMediaQueryList {
  let listeners: MqlListener[] = [];
  return {
    matches: initialMatches,
    addEventListener: (_type, cb) => {
      listeners.push(cb);
    },
    removeEventListener: (_type, cb) => {
      listeners = listeners.filter((l) => l !== cb);
    },
    _trigger(matches: boolean) {
      this.matches = matches;
      listeners.forEach((cb) => cb({ matches }));
    },
    _listenerCount: () => listeners.length,
  };
}

describe("useMediaQuery", () => {
  let mql: MockMediaQueryList;
  let originalMatchMedia: typeof window.matchMedia | undefined;

  beforeEach(() => {
    originalMatchMedia = window.matchMedia;
  });

  afterEach(() => {
    if (originalMatchMedia) {
      Object.defineProperty(window, "matchMedia", {
        value: originalMatchMedia,
        writable: true,
        configurable: true,
      });
    } else {
      // @ts-expect-error -- intentionally clearing
      delete window.matchMedia;
    }
  });

  function installMql(initial: boolean) {
    mql = createMockMql(initial);
    Object.defineProperty(window, "matchMedia", {
      value: vi.fn(() => mql),
      writable: true,
      configurable: true,
    });
  }

  it("reads matchMedia synchronously for the first client render", () => {
    installMql(false);
    const { result } = renderHook(() => useMediaQuery("(min-width: 1024px)"));
    expect(result.current).toBe(false);
  });

  it("returns true when matchMedia matches", () => {
    installMql(true);
    const { result } = renderHook(() => useMediaQuery("(min-width: 1024px)"));
    // Effect runs synchronously in RTL after render — value reflects mql.matches.
    expect(result.current).toBe(true);
  });

  it("updates when matchMedia change fires", () => {
    installMql(false);
    const { result } = renderHook(() => useMediaQuery("(min-width: 1024px)"));
    expect(result.current).toBe(false);
    act(() => {
      mql._trigger(true);
    });
    expect(result.current).toBe(true);
    act(() => {
      mql._trigger(false);
    });
    expect(result.current).toBe(false);
  });

  it("cleans up the listener on unmount", () => {
    installMql(false);
    const { unmount } = renderHook(() => useMediaQuery("(min-width: 1024px)"));
    expect(mql._listenerCount()).toBe(1);
    unmount();
    expect(mql._listenerCount()).toBe(0);
  });
});
