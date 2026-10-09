// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { queryKeys } from "@/lib/query-keys";
import { createEventBus } from "@/task-center/event-bus";
import { EventBusContext } from "@/task-center/event-bus-context";
import { useEpisodeImageTaskInvalidation } from "@/hooks/use-episode-image-task-invalidation";

function wrap() {
  const bus = createEventBus();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <EventBusContext.Provider value={bus}>{children}</EventBusContext.Provider>
    </QueryClientProvider>
  );
  return { Wrapper, bus, invalidateSpy };
}

describe("useEpisodeImageTaskInvalidation", () => {
  it("invalidates beat image data for every completed image task scope in the current episode", () => {
    const { Wrapper, bus, invalidateSpy } = wrap();
    renderHook(() => useEpisodeImageTaskInvalidation("demo", 1), {
      wrapper: Wrapper,
    });

    bus.emit({
      type: "task_complete",
      task: sampleTask({
        task_type: "sketch_regen",
        task_key: "sketch_regen:scope-a",
        episode: 1,
        scope: "scope-a",
        status: "completed",
      }),
      previous: sampleTask({ task_type: "sketch_regen", status: "running" }),
    });
    bus.emit({
      type: "task_complete",
      task: sampleTask({
        task_type: "sketch_regen",
        task_key: "sketch_regen:scope-b",
        episode: 1,
        scope: "scope-b",
        status: "completed",
      }),
      previous: sampleTask({ task_type: "sketch_regen", status: "running" }),
    });

    const beatInvalidations = invalidateSpy.mock.calls.filter(
      ([opts]) =>
        JSON.stringify(opts?.queryKey) === JSON.stringify(queryKeys.beats("demo", 1)),
    );
    const gridInvalidations = invalidateSpy.mock.calls.filter(
      ([opts]) =>
        JSON.stringify(opts?.queryKey) === JSON.stringify(queryKeys.grids("demo", 1)),
    );
    expect(beatInvalidations).toHaveLength(2);
    expect(gridInvalidations).toHaveLength(2);
  });

  it("ignores unrelated task completions", () => {
    const { Wrapper, bus, invalidateSpy } = wrap();
    renderHook(() => useEpisodeImageTaskInvalidation("demo", 1), {
      wrapper: Wrapper,
    });

    bus.emit({
      type: "task_complete",
      task: sampleTask({
        task_type: "sketch_regen",
        episode: 2,
        status: "completed",
      }),
      previous: sampleTask({ task_type: "sketch_regen", status: "running" }),
    });
    bus.emit({
      type: "task_complete",
      task: sampleTask({
        task_type: "script_writer",
        episode: 1,
        status: "completed",
      }),
      previous: sampleTask({ task_type: "script_writer", status: "running" }),
    });
    bus.emit({
      type: "task_complete",
      task: sampleTask({
        task_type: "sketch_regen",
        project: "other",
        episode: 1,
        status: "completed",
      }),
      previous: sampleTask({ task_type: "sketch_regen", project: "other", status: "running" }),
    });

    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
