// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { createEventBus, type TaskEventBus } from "@/task-center/event-bus";
import { EventBusContext } from "@/task-center/event-bus-context";
import { useScopedTaskBatchInvalidation } from "@/hooks/use-scoped-task-batch-invalidation";

function wrap(bus: TaskEventBus, qc: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <EventBusContext.Provider value={bus}>{children}</EventBusContext.Provider>
    </QueryClientProvider>
  );
  return Wrapper;
}

function complete(scope: string, project = "demo") {
  return {
    type: "task_complete" as const,
    task: sampleTask({
      task_type: "sketch_regen",
      status: "completed",
      scope,
      project,
    }),
    previous: null,
  };
}

describe("useScopedTaskBatchInvalidation", () => {
  it("invalidates once for EACH tracked scope's completion (not just the last)", () => {
    const bus = createEventBus();
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    const { result } = renderHook(
      () =>
        useScopedTaskBatchInvalidation({
          project: "demo",
          taskType: "sketch_regen",
          invalidateKeys: [["grids"], ["beats"]],
        }),
      { wrapper: wrap(bus, qc) },
    );

    // Dispatch two concurrent grids — exactly the case that overwrote a single
    // controller's activeScope before this hook existed.
    result.current.track("scope_a");
    result.current.track("scope_b");

    bus.emit(complete("scope_a"));
    bus.emit(complete("scope_b"));

    // Two keys × two completions = four invalidations. The second scope is no
    // longer lost.
    expect(spy).toHaveBeenCalledTimes(4);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["grids"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["beats"] });
  });

  it("ignores untracked scopes, other projects, and double-completions", () => {
    const bus = createEventBus();
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    const { result } = renderHook(
      () =>
        useScopedTaskBatchInvalidation({
          project: "demo",
          taskType: "sketch_regen",
          invalidateKeys: [["grids"]],
        }),
      { wrapper: wrap(bus, qc) },
    );

    result.current.track("scope_a");

    bus.emit(complete("scope_untracked")); // never tracked → ignored
    bus.emit(complete("scope_a", "other_project")); // wrong project → ignored
    bus.emit(complete("scope_a")); // matches → invalidate, then prune
    bus.emit(complete("scope_a")); // already pruned → ignored

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["grids"] });
  });

  it("matches by task_id when matchBy is set (render execute fan-out)", () => {
    const bus = createEventBus();
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    const { result } = renderHook(
      () =>
        useScopedTaskBatchInvalidation({
          project: "demo",
          taskType: "selected_regen",
          matchBy: "task_id",
          invalidateKeys: [["grids"]],
        }),
      { wrapper: wrap(bus, qc) },
    );

    // execute fanned out into two grid tasks; we track them by id, not scope.
    result.current.track("task-a");
    result.current.track("task-b");

    const completeById = (taskId: string) => ({
      type: "task_complete" as const,
      task: sampleTask({
        task_type: "selected_regen",
        status: "completed",
        task_id: taskId,
        // Distinct grid scopes the FE never learned — id is what we match on.
        scope: `4x4_16-9__${taskId}`,
        project: "demo",
      }),
      previous: null,
    });

    bus.emit(completeById("task-a"));
    bus.emit(completeById("task-b"));

    // Both grid tasks invalidate — the second is no longer lost.
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["grids"] });
  });

  it("prunes a scope on failure without invalidating", () => {
    const bus = createEventBus();
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    const { result } = renderHook(
      () =>
        useScopedTaskBatchInvalidation({
          project: "demo",
          taskType: "sketch_regen",
          invalidateKeys: [["grids"]],
        }),
      { wrapper: wrap(bus, qc) },
    );

    result.current.track("scope_a");
    bus.emit({
      type: "task_failed",
      task: sampleTask({
        task_type: "sketch_regen",
        status: "failed",
        scope: "scope_a",
        project: "demo",
      }),
      previous: null,
    });
    // Failure prunes the scope but does not invalidate (no new data).
    expect(spy).not.toHaveBeenCalled();

    // A late completion for the now-pruned scope is ignored too.
    bus.emit(complete("scope_a"));
    expect(spy).not.toHaveBeenCalled();
  });
});
