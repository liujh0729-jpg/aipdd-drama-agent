// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { useSaveStatusStore } from "@/stores/save-status-store";
import { useSeenPoolStore } from "@/stores/seen-pool-store";
import { useTaskCenterStore } from "@/task-center/store";
import type { TaskState } from "@/task-center/types";

// Each store's reset() must return the region-scoped data fields to their
// initial values. This mirrors the central switch flow, which calls every
// store's reset() before queryClient.clear() + hard-reload.

beforeEach(() => {
  // Defensive: clear persist layer so tests don't bleed across each other.
  localStorage.clear();
});

function makeTask(overrides: Partial<TaskState> = {}): TaskState {
  const now = new Date().toISOString();
  return {
    task_key: "k1",
    task_id: "t1",
    task_type: "ingest",
    username: "alice",
    project: "p1",
    episode: 1,
    beat_num: null,
    scope: null,
    status: "running",
    progress: 0,
    current_task: "",
    result: null,
    error: null,
    logs: [],
    created_at: now,
    updated_at: now,
    completed_at: "",
    ...overrides,
  };
}

describe("per-store reset() actions", () => {
  it("save-status-store.reset() empties all scopes", () => {
    useSaveStatusStore.setState({
      scopes: {
        a: { status: "saving", lastSavedAt: 123 },
        "b.c": { status: "error", lastSavedAt: null, error: "boom" },
        "d.e.f": { status: "saved", lastSavedAt: 456 },
      },
    });
    expect(Object.keys(useSaveStatusStore.getState().scopes)).toHaveLength(3);

    useSaveStatusStore.getState().reset();

    expect(useSaveStatusStore.getState().scopes).toEqual({});
  });

  it("seen-pool-store.reset() empties the seen map", () => {
    useSeenPoolStore.getState().markSeen("p1", 1, "pool-a");
    useSeenPoolStore.getState().markSeen("p1", 1, "pool-b");
    useSeenPoolStore.getState().markSeen("p2", 4, "pool-c");
    expect(Object.keys(useSeenPoolStore.getState().seen)).toHaveLength(2);

    useSeenPoolStore.getState().reset();

    expect(useSeenPoolStore.getState().seen).toEqual({});
    expect(useSeenPoolStore.getState().isSeen("p1", 1, "pool-a")).toBe(false);
  });

  it("task-center-store.reset() returns to initial state", () => {
    useTaskCenterStore.getState().upsert(makeTask({ task_id: "t1" }));
    useTaskCenterStore
      .getState()
      .upsert(makeTask({ task_id: "t2", task_key: "k2", status: "completed" }));
    useTaskCenterStore.getState().setHealth("connected");
    useTaskCenterStore.getState().setProject("p1");
    useTaskCenterStore.getState().setLastEventAt(999);
    useTaskCenterStore.getState().setSelected("t1");
    useTaskCenterStore.getState().setFilter("running");
    useTaskCenterStore.getState().markAutoExpanded();
    useTaskCenterStore.getState().markHydrated();

    // Sanity — we actually populated the store.
    expect(useTaskCenterStore.getState().tasks.size).toBe(2);
    expect(useTaskCenterStore.getState().streamHealth).toBe("connected");
    expect(useTaskCenterStore.getState().projectId).toBe("p1");
    expect(useTaskCenterStore.getState().selectedTaskKey).toBe("t1");
    expect(useTaskCenterStore.getState().filter).toBe("running");
    expect(useTaskCenterStore.getState().autoExpandedThisSession).toBe(true);
    expect(useTaskCenterStore.getState().isHydrated).toBe(true);
    expect(useTaskCenterStore.getState().lastEventAt).toBe(999);

    useTaskCenterStore.getState().reset();

    const s = useTaskCenterStore.getState();
    expect(s.tasks).toBeInstanceOf(Map);
    expect(s.tasks.size).toBe(0);
    expect(s.streamHealth).toBe("connecting");
    expect(s.projectId).toBeNull();
    expect(s.lastEventAt).toBeNull();
    expect(s.selectedTaskKey).toBeNull();
    expect(s.filter).toBe("all");
    expect(s.autoExpandedThisSession).toBe(false);
    expect(s.isHydrated).toBe(false);
  });
});
