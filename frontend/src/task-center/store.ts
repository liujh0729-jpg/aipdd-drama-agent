// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import type { TaskState, StreamHealth } from "./types";
import { isTerminal } from "./derivations";

export type Filter = "all" | "running" | "failed" | "done";

interface TaskCenterState {
  projectId: string | null;
  tasks: Map<string, TaskState>;
  streamHealth: StreamHealth;
  lastEventAt: number | null;
  selectedTaskKey: string | null;
  filter: Filter;
  autoExpandedThisSession: boolean;
  /**
   * True once the initial `GET /tasks` hydration has landed AND the stream has
   * delivered at least one heartbeat/task_updated — i.e., we're in the "live" phase.
   * Before this flips, provider MUST NOT fire toasts or auto-expand for any
   * observed task (prevents false positives for preexisting terminal tasks).
   */
  isHydrated: boolean;

  hydrate(tasks: TaskState[]): void;
  upsert(task: TaskState): TaskState | null;
  remove(taskKey: string): void;
  setProject(projectId: string | null): void;
  setHealth(h: StreamHealth): void;
  setLastEventAt(ts: number): void;
  setSelected(k: string | null): void;
  setFilter(f: Filter): void;
  markAutoExpanded(): void;
  markHydrated(): void;
  prune(now?: number): void;
  reset(): void;
}

const ONE_HOUR_MS = 60 * 60 * 1000;

function isActiveTask(t: TaskState): boolean {
  return (
    t.status === "submitting" ||
    t.status === "queued" ||
    t.status === "pending" ||
    t.status === "starting" ||
    t.status === "running"
  );
}

export const useTaskCenterStore = create<TaskCenterState>((set, get) => ({
  projectId: null,
  tasks: new Map(),
  streamHealth: "connecting",
  lastEventAt: null,
  selectedTaskKey: null,
  filter: "all",
  autoExpandedThisSession: false,
  isHydrated: false,

  hydrate: (tasks) => {
    // Backend project task state is keyed by `task_key`, so the task center is
    // also a current-state view keyed by `task_key`. Task run history belongs in
    // a separate history surface, not in the live task center count.
    const existing = get().tasks;
    const next = new Map<string, TaskState>();
    for (const t of tasks) {
      const prev = existing.get(t.task_key);
      if (!prev || Date.parse(t.updated_at) >= Date.parse(prev.updated_at)) {
        next.set(t.task_key, t);
      } else {
        next.set(t.task_key, prev);
      }
    }
    set({ tasks: next });
  },
  upsert: (task) => {
    const prev = get().tasks.get(task.task_key) ?? null;
    const next = new Map(get().tasks);
    next.set(task.task_key, task);
    set({ tasks: next });
    return prev;
  },
  remove: (taskKey) => {
    const next = new Map(get().tasks);
    next.delete(taskKey);
    set({ tasks: next });
  },
  setProject: (projectId) => set({ projectId }),
  setHealth: (h) => set({ streamHealth: h }),
  setLastEventAt: (ts) => set({ lastEventAt: ts }),
  setSelected: (k) => set({ selectedTaskKey: k }),
  setFilter: (f) => set({ filter: f }),
  markAutoExpanded: () => set({ autoExpandedThisSession: true }),
  markHydrated: () => set({ isHydrated: true }),
  prune: (now = Date.now()) => {
    const existing = get().tasks;
    const next = new Map(existing);
    for (const [k, t] of existing) {
      if (!isTerminal(t)) continue;
      // If completed_at is missing or unparseable, leave the task alone.
      // Only evict when we have an authoritative timestamp and it's older than 1h.
      if (!t.completed_at) continue;
      const ts = Date.parse(t.completed_at);
      if (Number.isNaN(ts)) continue;
      if (now - ts > ONE_HOUR_MS) next.delete(k);
    }
    if (next.size !== existing.size) set({ tasks: next });
  },
  reset: () =>
    set({
      projectId: null,
      tasks: new Map(),
      streamHealth: "connecting",
      lastEventAt: null,
      selectedTaskKey: null,
      filter: "all",
      autoExpandedThisSession: false,
      isHydrated: false,
    }),
}));

// ---- selectors ----

const tasksArray = (s: TaskCenterState): TaskState[] => Array.from(s.tasks.values());

export const selectRunningTasks = (s: TaskCenterState): TaskState[] =>
  tasksArray(s).filter(isActiveTask);

export const selectTerminalTasks = (s: TaskCenterState): TaskState[] =>
  tasksArray(s).filter(isTerminal);

export const selectLeadingRunning = (s: TaskCenterState): TaskState | null => {
  const running = selectRunningTasks(s);
  if (!running.length) return null;
  return running.slice().sort((a, b) => {
    const ca = Date.parse(a.created_at);
    const cb = Date.parse(b.created_at);
    if (ca !== cb) return ca - cb;
    return a.task_key.localeCompare(b.task_key);
  })[0];
};

export const selectLastCompletion = (
  s: TaskCenterState,
  windowMs: number = 5 * 60 * 1000,
  now: number = Date.now(),
): TaskState | null => {
  const terminal = selectTerminalTasks(s);
  const recent = terminal.filter(
    (t) => t.completed_at && now - Date.parse(t.completed_at) <= windowMs,
  );
  if (!recent.length) return null;
  return recent.reduce((a, b) =>
    Date.parse(a.completed_at) > Date.parse(b.completed_at) ? a : b,
  );
};

export const selectFilteredTasks = (s: TaskCenterState): TaskState[] => {
  const arr = tasksArray(s);
  switch (s.filter) {
    case "running":
      return arr.filter(isActiveTask);
    case "failed":
      return arr.filter((t) => t.status === "failed");
    case "done":
      return arr.filter((t) => t.status === "completed");
    default:
      return arr;
  }
};

export const selectCountByStatus = (s: TaskCenterState) => {
  const arr = tasksArray(s);
  return {
    all: arr.length,
    running: arr.filter(isActiveTask).length,
    failed: arr.filter((t) => t.status === "failed").length,
    done: arr.filter((t) => t.status === "completed").length,
  };
};
