// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useResizablePanePersistence } from "@/hooks/use-resizable-pane-persistence";

describe("useResizablePanePersistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the default when storage is empty", () => {
    const { result } = renderHook(() =>
      useResizablePanePersistence("test-key", 35),
    );
    expect(result.current[0]).toBe(35);
  });

  it("returns the persisted value when storage has a valid number", () => {
    localStorage.setItem("test-key", "55");
    const { result } = renderHook(() =>
      useResizablePanePersistence("test-key", 35),
    );
    expect(result.current[0]).toBe(55);
  });

  it("ignores malformed storage and returns the default", () => {
    localStorage.setItem("test-key", "not-a-number");
    const { result } = renderHook(() =>
      useResizablePanePersistence("test-key", 35),
    );
    expect(result.current[0]).toBe(35);
  });

  it("ignores out-of-range values and returns the default", () => {
    localStorage.setItem("test-key", "150");
    const { result } = renderHook(() =>
      useResizablePanePersistence("test-key", 35),
    );
    expect(result.current[0]).toBe(35);
  });

  it("persists when the setter is called", () => {
    const { result } = renderHook(() =>
      useResizablePanePersistence("test-key", 35),
    );
    act(() => result.current[1](42));
    expect(localStorage.getItem("test-key")).toBe("42");
  });
});
