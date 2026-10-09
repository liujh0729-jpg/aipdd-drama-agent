// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useTaskCenterStore, type Filter } from "@/task-center/store";
import type { TaskState } from "@/task-center/types";
import { TaskRow } from "./task-row";
import { EmptyState } from "./empty-state";
import { cn } from "@/lib/utils";

const FILTERS: Filter[] = ["all", "running", "failed", "done"];

function filterTasks(arr: TaskState[], filter: Filter): TaskState[] {
  switch (filter) {
    case "running":
      return arr.filter(
        (t) =>
          t.status === "submitting" ||
          t.status === "queued" ||
          t.status === "pending" ||
          t.status === "starting" ||
          t.status === "running",
      );
    case "failed":
      return arr.filter((t) => t.status === "failed");
    case "done":
      return arr.filter((t) => t.status === "completed");
    default:
      return arr;
  }
}

function countByStatus(arr: TaskState[]) {
  return {
    all: arr.length,
    running: arr.filter(
      (t) =>
        t.status === "submitting" ||
        t.status === "queued" ||
        t.status === "pending" ||
        t.status === "starting" ||
        t.status === "running",
    ).length,
    failed: arr.filter((t) => t.status === "failed").length,
    done: arr.filter((t) => t.status === "completed").length,
  };
}

export function TaskList() {
  const { t } = useTranslation();
  const tasksMap = useTaskCenterStore((s) => s.tasks);
  const filter = useTaskCenterStore((s) => s.filter);
  const setFilter = useTaskCenterStore((s) => s.setFilter);
  const setSelected = useTaskCenterStore((s) => s.setSelected);
  const selectedTaskKey = useTaskCenterStore((s) => s.selectedTaskKey);

  const allTasks = useMemo(() => {
    // Newest first. Prefer `updated_at` so a task that just advanced bubbles
    // up; fall back to `created_at` when updates haven't landed yet. Both
    // are ISO-8601 strings, so lexicographic compare is correct.
    const arr = Array.from(tasksMap.values());
    arr.sort((a, b) => {
      const ta = a.updated_at || a.created_at || "";
      const tb = b.updated_at || b.created_at || "";
      if (ta === tb) return 0;
      return ta < tb ? 1 : -1;
    });
    return arr;
  }, [tasksMap]);
  const counts = useMemo(() => countByStatus(allTasks), [allTasks]);
  const tasks = useMemo(() => filterTasks(allTasks, filter), [allTasks, filter]);

  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: tasks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 36,
    overscan: 8,
  });

  return (
    <div className="flex h-full flex-col">
      <div
        role="tablist"
        className="flex shrink-0 items-center gap-1 border-b border-border/40 px-3 py-2"
      >
        {FILTERS.map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            type="button"
            onClick={() => setFilter(f)}
            className={cn(
              "rounded px-2 py-1 text-xs font-normal transition-colors",
              filter === f ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/50",
            )}
          >
            {t(`taskCenter.panel.filters.${f}`)} {counts[f]}
          </button>
        ))}
      </div>
      <div ref={parentRef} className="flex-1 overflow-auto">
        {tasks.length === 0 ? (
          <EmptyState variant={filter === "all" ? "all" : "filter"} />
        ) : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((vi) => {
              const task = tasks[vi.index];
              return (
                <div
                  key={task.task_key}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    right: 0,
                    height: vi.size,
                    transform: `translateY(${vi.start}px)`,
                  }}
                >
                  <TaskRow
                    task={task}
                    selected={selectedTaskKey === task.task_key}
                    onClick={() => setSelected(task.task_key)}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
