// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import {
  useTaskCenterStore,
  selectRunningTasks,
  selectLeadingRunning,
  selectLastCompletion,
  selectFilteredTasks,
  selectCountByStatus,
} from "@/task-center/store";

beforeEach(() => {
  useTaskCenterStore.getState().reset();
});

describe("store.hydrate + upsert + remove", () => {
  it("hydrate replaces tasks", () => {
    useTaskCenterStore
      .getState()
      .hydrate([
        sampleTask({ task_id: "a", task_key: "task:a" }),
        sampleTask({ task_id: "b", task_key: "task:b" }),
      ]);
    expect(useTaskCenterStore.getState().tasks.size).toBe(2);
  });

  it("hydrate preserves newer in-memory tasks when BE payload is stale", () => {
    const s = useTaskCenterStore.getState();
    s.upsert(sampleTask({ task_id: "a1", task_key: "k", progress: 0.9, updated_at: "2026-04-18T15:00:00Z" }));
    s.hydrate([sampleTask({ task_id: "a2", task_key: "k", progress: 0.1, updated_at: "2026-04-18T14:00:00Z" })]);
    expect(useTaskCenterStore.getState().tasks.get("k")!.progress).toBe(0.9);
  });

  it("upsert is idempotent by task_key", () => {
    useTaskCenterStore.getState().upsert(sampleTask({ task_id: "a1", task_key: "k", progress: 0.1 }));
    useTaskCenterStore.getState().upsert(sampleTask({ task_id: "a2", task_key: "k", progress: 0.5 }));
    expect(useTaskCenterStore.getState().tasks.size).toBe(1);
    expect(useTaskCenterStore.getState().tasks.get("k")!.progress).toBe(0.5);
    expect(useTaskCenterStore.getState().tasks.get("k")!.task_id).toBe("a2");
  });

  it("same task_key re-run with new task_id replaces current task center row", () => {
    const s = useTaskCenterStore.getState();
    s.upsert(sampleTask({ task_key: "k", task_id: "run-1", status: "completed" }));
    s.upsert(sampleTask({ task_key: "k", task_id: "run-2", status: "running" }));
    expect(useTaskCenterStore.getState().tasks.size).toBe(1);
    expect(useTaskCenterStore.getState().tasks.get("k")?.task_id).toBe("run-2");
  });

  it("collapses duplicate task_key startup events into the newest task_id", () => {
    const s = useTaskCenterStore.getState();
    s.upsert(sampleTask({ task_key: "k", task_id: "startup-1", status: "starting" }));
    s.upsert(sampleTask({ task_key: "k", task_id: "startup-2", status: "running" }));

    const tasks = useTaskCenterStore.getState().tasks;
    expect(tasks.size).toBe(1);
    expect(tasks.has("startup-1")).toBe(false);
    expect(tasks.get("k")?.status).toBe("running");
    expect(tasks.get("k")?.task_id).toBe("startup-2");
  });

  it("upsert returns previous state (or null if new)", () => {
    const s = useTaskCenterStore.getState();
    expect(s.upsert(sampleTask({ task_id: "a1", task_key: "k", progress: 0.1 }))).toBeNull();
    const prev = useTaskCenterStore
      .getState()
      .upsert(sampleTask({ task_id: "a2", task_key: "k", progress: 0.5 }));
    expect(prev?.progress).toBe(0.1);
  });

  it("remove deletes by task_key", () => {
    const s = useTaskCenterStore.getState();
    s.upsert(sampleTask({ task_id: "a", task_key: "k" }));
    s.remove("k");
    expect(useTaskCenterStore.getState().tasks.size).toBe(0);
  });

  it("markHydrated sets isHydrated true", () => {
    expect(useTaskCenterStore.getState().isHydrated).toBe(false);
    useTaskCenterStore.getState().markHydrated();
    expect(useTaskCenterStore.getState().isHydrated).toBe(true);
  });
});

describe("store selectors", () => {
  it("selectRunningTasks filters", () => {
    const s = useTaskCenterStore.getState();
    s.hydrate([
      sampleTask({ task_id: "s", task_key: "s", status: "submitting" }),
      sampleTask({ task_id: "q", task_key: "q", status: "queued" }),
      sampleTask({ task_id: "a", task_key: "a", status: "running" }),
      sampleTask({ task_id: "b", status: "completed" }),
    ]);
    expect(selectRunningTasks(useTaskCenterStore.getState())).toHaveLength(3);
  });

  it("selectLeadingRunning picks earliest created_at; ties broken by task_key", () => {
    const s = useTaskCenterStore.getState();
    s.hydrate([
      sampleTask({ task_id: "new", task_key: "new", status: "running", created_at: "2026-04-18T14:00:02Z" }),
      sampleTask({ task_id: "old", task_key: "old", status: "running", created_at: "2026-04-18T14:00:00Z" }),
      sampleTask({ task_id: "tie1", task_key: "tie1", status: "running", created_at: "2026-04-18T14:00:01Z" }),
      sampleTask({ task_id: "tie0", task_key: "tie0", status: "running", created_at: "2026-04-18T14:00:01Z" }),
    ]);
    expect(selectLeadingRunning(useTaskCenterStore.getState())!.task_key).toBe("old");
  });

  it("selectLastCompletion picks latest terminal within window", () => {
    const now = Date.now();
    const recent = new Date(now - 60_000).toISOString();
    const old = new Date(now - 10 * 60_000).toISOString();
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_id: "old", task_key: "old", status: "completed", completed_at: old }),
      sampleTask({ task_id: "recent", task_key: "recent", status: "completed", completed_at: recent }),
    ]);
    const pick = selectLastCompletion(useTaskCenterStore.getState(), 5 * 60_000, now);
    expect(pick!.task_key).toBe("recent");
  });

  it("selectLastCompletion treats cancelled as terminal", () => {
    const now = Date.now();
    const recent = new Date(now - 60_000).toISOString();
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_id: "cancelled", task_key: "cancelled", status: "cancelled", completed_at: recent }),
      sampleTask({ task_id: "running", task_key: "running", status: "running" }),
    ]);
    const pick = selectLastCompletion(useTaskCenterStore.getState(), 5 * 60_000, now);
    expect(pick!.task_key).toBe("cancelled");
  });

  it("selectFilteredTasks honors the filter", () => {
    const s = useTaskCenterStore.getState();
    s.hydrate([
      sampleTask({ task_id: "r", task_key: "r", status: "running" }),
      sampleTask({ task_id: "f", task_key: "f", status: "failed" }),
    ]);
    s.setFilter("failed");
    expect(selectFilteredTasks(useTaskCenterStore.getState()).map((t) => t.task_key)).toEqual(["f"]);
  });

  it("selectCountByStatus returns {all, running, failed, done}", () => {
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_id: "s", task_key: "task:s", status: "submitting" }),
      sampleTask({ task_id: "0", task_key: "task:0", status: "queued" }),
      sampleTask({ task_id: "1", task_key: "task:1", status: "running" }),
      sampleTask({ task_id: "2", task_key: "task:2", status: "running" }),
      sampleTask({ task_id: "3", task_key: "task:3", status: "failed" }),
      sampleTask({ task_id: "4", task_key: "task:4", status: "completed" }),
    ]);
    expect(selectCountByStatus(useTaskCenterStore.getState())).toEqual({ all: 6, running: 4, failed: 1, done: 1 });
  });
});

describe("store pruning", () => {
  it("prune removes terminal tasks older than 1h", () => {
    const s = useTaskCenterStore.getState();
    const longAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    const recent = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    s.hydrate([
      sampleTask({ task_id: "stale", task_key: "stale", status: "completed", completed_at: longAgo }),
      sampleTask({ task_id: "fresh", task_key: "fresh", status: "completed", completed_at: recent }),
      sampleTask({ task_id: "running", task_key: "running", status: "running" }),
    ]);
    s.prune();
    const state = useTaskCenterStore.getState();
    expect(state.tasks.has("stale")).toBe(false);
    expect(state.tasks.has("fresh")).toBe(true);
    expect(state.tasks.has("running")).toBe(true);
  });

  it("prune does NOT evict terminal tasks with missing/malformed completed_at", () => {
    // Backend sometimes omits completed_at on failures; evicting on that would
    // prematurely hide failures from the user. Only evict when we have an
    // authoritative timestamp AND it's older than 1h.
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_id: "empty", task_key: "empty", status: "completed", completed_at: "" }),
      sampleTask({ task_id: "garbage", task_key: "garbage", status: "completed", completed_at: "not-a-date" }),
    ]);
    useTaskCenterStore.getState().prune();
    const state = useTaskCenterStore.getState();
    expect(state.tasks.has("empty")).toBe(true);
    expect(state.tasks.has("garbage")).toBe(true);
  });
});
