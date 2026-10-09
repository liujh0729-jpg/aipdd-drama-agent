// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSelection } from "@/hooks/use-selection";
import { useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";

describe("useSelection", () => {
  beforeEach(() => {
    useEpisodeWorkbenchStore.getState().reset();
  });

  it("starts in none mode with no active beat", () => {
    const { result } = renderHook(() => useSelection());
    expect(result.current.state.mode).toBe("none");
    expect(result.current.activeBeat).toBeNull();
  });

  it("selectSingle transitions to single mode and sets active beat", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.selectSingle(3));
    expect(result.current.state).toEqual({ mode: "single", beatNum: 3 });
    expect(result.current.activeBeat).toBe(3);
  });

  it("toggleCheck enters multi mode", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleCheck(5));
    expect(result.current.state.mode).toBe("multi");
    expect((result.current.state as { checked: Set<number> }).checked.has(5)).toBe(true);
  });

  it("toggleCheck in multi mode toggles beat", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(2));
    expect((result.current.state as { checked: Set<number> }).checked.size).toBe(2);
    act(() => result.current.toggleCheck(1));
    expect((result.current.state as { checked: Set<number> }).checked.size).toBe(1);
  });

  it("unchecking last beat returns to none", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(1));
    expect(result.current.state.mode).toBe("none");
  });

  it("clearSelection returns to none and clears activeBeat", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.handleCardClick(5));
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(2));
    act(() => result.current.clearSelection());
    expect(result.current.state.mode).toBe("none");
    expect(result.current.activeBeat).toBeNull();
  });


  it("card body click sets activeBeat and single mode", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.handleCardClick(3));
    expect(result.current.activeBeat).toBe(3);
    expect(result.current.state).toEqual({ mode: "single", beatNum: 3 });
  });

  it("card body click overrides any existing multi selection", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(2));
    act(() => result.current.handleCardClick(3));
    expect(result.current.activeBeat).toBe(3);
    expect(result.current.state).toEqual({ mode: "single", beatNum: 3 });
  });

  it("checkbox toggle enters multi mode and clears activeBeat", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.handleCardClick(5));
    expect(result.current.activeBeat).toBe(5);
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(2));
    expect(result.current.activeBeat).toBeNull();
    expect(result.current.state.mode).toBe("multi");
  });

  it("restores scoped selection after the beat route remounts", () => {
    const scope = { project: "demo", episode: 1 };
    const { result, unmount } = renderHook(() => useSelection(scope));

    act(() => result.current.handleCardClick(5));
    act(() => result.current.toggleCheck(1));
    act(() => result.current.toggleCheck(2));

    unmount();

    const restored = renderHook(() => useSelection(scope));
    expect(restored.result.current.activeBeat).toBeNull();
    expect(restored.result.current.state.mode).toBe("multi");
    expect(
      (restored.result.current.state as { checked: Set<number> }).checked,
    ).toEqual(new Set([1, 2]));
  });

  it("keeps scoped selection isolated by episode", () => {
    const ep1 = { project: "demo", episode: 1 };
    const ep2 = { project: "demo", episode: 2 };

    const first = renderHook(() => useSelection(ep1));
    act(() => first.result.current.handleCardClick(3));

    const second = renderHook(() => useSelection(ep2));
    expect(second.result.current.activeBeat).toBeNull();
    expect(second.result.current.state.mode).toBe("none");
  });

  // [aipdd fork delta] selectMany：全选/取消全选的批量选中动作。
  it("selectMany enters multi mode with all given beats and clears activeBeat", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.handleCardClick(5));
    act(() => result.current.selectMany([1, 2, 3]));
    expect(result.current.state.mode).toBe("multi");
    expect(
      (result.current.state as { checked: Set<number> }).checked,
    ).toEqual(new Set([1, 2, 3]));
    expect(result.current.activeBeat).toBeNull();
  });

  it("selectMany with an empty list returns to none", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.selectMany([1, 2]));
    expect(result.current.state.mode).toBe("multi");
    act(() => result.current.selectMany([]));
    expect(result.current.state.mode).toBe("none");
  });

  it("selectMany persists scoped selection", () => {
    const scope = { project: "demo", episode: 1 };
    const { result, unmount } = renderHook(() => useSelection(scope));
    act(() => result.current.selectMany([2, 4]));
    unmount();
    const restored = renderHook(() => useSelection(scope));
    expect(restored.result.current.state.mode).toBe("multi");
    expect(
      (restored.result.current.state as { checked: Set<number> }).checked,
    ).toEqual(new Set([2, 4]));
  });
});
