// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { createEventBus } from "@/task-center/event-bus";
import { EventBusContext } from "@/task-center/event-bus-context";
import { useTaskSubscribe } from "@/task-center/use-task-subscribe";

function wrap(bus = createEventBus()) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <EventBusContext.Provider value={bus}>{children}</EventBusContext.Provider>
  );
  return { Wrapper, bus };
}

describe("useTaskSubscribe", () => {
  it("fires onComplete for matching task_complete events", () => {
    const { Wrapper, bus } = wrap();
    const onComplete = vi.fn();
    renderHook(
      () =>
        useTaskSubscribe({
          match: (t) => t.task_type === "sketch_regen",
          onComplete,
        }),
      { wrapper: Wrapper },
    );
    bus.emit({
      type: "task_complete",
      task: sampleTask({ task_type: "sketch_regen", status: "completed" }),
      previous: sampleTask({ task_type: "sketch_regen", status: "running" }),
    });
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("does not fire onComplete for non-matching events", () => {
    const { Wrapper, bus } = wrap();
    const onComplete = vi.fn();
    renderHook(
      () => useTaskSubscribe({ match: (t) => t.task_type === "sketch_regen", onComplete }),
      { wrapper: Wrapper },
    );
    bus.emit({
      type: "task_complete",
      task: sampleTask({ task_type: "script_writer", status: "completed" }),
      previous: null,
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("fires onFailed for matching task_failed events", () => {
    const { Wrapper, bus } = wrap();
    const onFailed = vi.fn();
    renderHook(
      () => useTaskSubscribe({ match: () => true, onFailed }),
      { wrapper: Wrapper },
    );
    bus.emit({
      type: "task_failed",
      task: sampleTask({ status: "failed", error: "oops" }),
      previous: sampleTask({ status: "running" }),
    });
    expect(onFailed).toHaveBeenCalledOnce();
  });

  it("fires onProgress for running task_updated events", () => {
    const { Wrapper, bus } = wrap();
    const onProgress = vi.fn();
    renderHook(
      () => useTaskSubscribe({ match: () => true, onProgress }),
      { wrapper: Wrapper },
    );
    bus.emit({
      type: "task_updated",
      task: sampleTask({ status: "running", progress: 0.4 }),
      previous: null,
    });
    expect(onProgress).toHaveBeenCalledOnce();
  });

  it("does NOT fire onProgress for terminal task_updated events (avoid double-fire with onComplete)", () => {
    const { Wrapper, bus } = wrap();
    const onProgress = vi.fn();
    renderHook(
      () => useTaskSubscribe({ match: () => true, onProgress }),
      { wrapper: Wrapper },
    );
    bus.emit({
      type: "task_updated",
      task: sampleTask({ status: "completed" }),
      previous: null,
    });
    bus.emit({
      type: "task_updated",
      task: sampleTask({ status: "failed" }),
      previous: null,
    });
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("ignores task_removed events", () => {
    const { Wrapper, bus } = wrap();
    const allHandlers = {
      onComplete: vi.fn(),
      onFailed: vi.fn(),
      onProgress: vi.fn(),
    };
    renderHook(() => useTaskSubscribe({ match: () => true, ...allHandlers }), { wrapper: Wrapper });
    bus.emit({ type: "task_removed", taskKey: "abc" });
    expect(allHandlers.onComplete).not.toHaveBeenCalled();
    expect(allHandlers.onFailed).not.toHaveBeenCalled();
    expect(allHandlers.onProgress).not.toHaveBeenCalled();
  });

  it("unsubscribes on unmount", () => {
    const { Wrapper, bus } = wrap();
    const onComplete = vi.fn();
    const { unmount } = renderHook(
      () => useTaskSubscribe({ match: () => true, onComplete }),
      { wrapper: Wrapper },
    );
    unmount();
    bus.emit({ type: "task_complete", task: sampleTask({ status: "completed" }), previous: null });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("uses latest callback refs without re-subscribing", () => {
    const { Wrapper, bus } = wrap();
    const onComplete = vi.fn();
    const { rerender } = renderHook(
      ({ cb }: { cb: () => void }) =>
        useTaskSubscribe({ match: () => true, onComplete: cb }),
      { wrapper: Wrapper, initialProps: { cb: onComplete } },
    );
    const nextCb = vi.fn();
    rerender({ cb: nextCb });
    bus.emit({ type: "task_complete", task: sampleTask({ status: "completed" }), previous: null });
    expect(nextCb).toHaveBeenCalledOnce();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("throws helpful error outside provider", () => {
    // useEventBus is tested indirectly — render without wrapper and expect throw
    expect(() =>
      renderHook(() => useTaskSubscribe({ match: () => true })),
    ).toThrow(/must be used inside/i);
  });
});
