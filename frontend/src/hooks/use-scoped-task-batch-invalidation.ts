// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useRef } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";

import { useTaskSubscribe } from "@/task-center/use-task-subscribe";
import type { TaskState } from "@/task-center/types";

type MatchBy = "scope" | "task_id";

function taskIdentifier(task: TaskState, matchBy: MatchBy): string | null {
  return matchBy === "task_id" ? task.task_id : task.scope;
}

/**
 * Tracks a *set* of concurrently-dispatched tasks and invalidates the given
 * query keys as EACH tracked task reaches a terminal state. Match by `scope`
 * (default) or `task_id` — pick whichever identifier the dispatch mutation
 * hands back.
 *
 * Why this exists: the per-component `useTaskController` follows a single
 * `activeScope`. A batch that spawns N tasks through one controller therefore
 * loses all but the last — every `start({ scope })` overwrites the previous, so
 * only one task's per-task SSE stream is watched and the others' completions
 * never invalidate `grids`/`beats` (the page looks like it never refreshed).
 * The episode-wide `useEpisodeImageTaskInvalidation` fallback can't be relied on
 * either: `sketch_regen` rows carry `beat_num=None`/often no `episode`, so its
 * `task.episode === episode` match misses.
 *
 * Two shapes hit this:
 *  - sketch batch loops one mutation per grid → match by each returned `scope`.
 *  - render `execute` returns N `task_ids` but only a non-matching umbrella
 *    `location__…` scope → match by each `task_id`.
 *
 * This hook matches on identifier membership via the global task-center bus
 * (which sees every task regardless of which stream is open), so every
 * dispatched task's completion reliably refreshes the page. Call `track(id)`
 * for each scope / task_id the dispatch returns.
 */
export function useScopedTaskBatchInvalidation(opts: {
  project: string;
  taskType: string;
  invalidateKeys: QueryKey[];
  matchBy?: MatchBy;
}): { track: (id: string | null | undefined) => void } {
  const { project, taskType, matchBy = "scope" } = opts;
  const queryClient = useQueryClient();
  const pendingRef = useRef<Set<string>>(new Set());

  // Keep the latest keys in a ref so the subscription below never re-subscribes
  // just because the caller passed a fresh inline array.
  const invalidateKeysRef = useRef(opts.invalidateKeys);
  invalidateKeysRef.current = opts.invalidateKeys;

  const track = useCallback((id: string | null | undefined) => {
    if (id) pendingRef.current.add(id);
  }, []);

  const settle = useCallback(
    (task: TaskState, invalidate: boolean) => {
      const id = taskIdentifier(task, matchBy);
      if (id == null) return;
      if (!pendingRef.current.delete(id)) return;
      if (!invalidate) return;
      invalidateKeysRef.current.forEach((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      );
    },
    [queryClient, matchBy],
  );

  useTaskSubscribe({
    // `pendingRef` is read live (useTaskSubscribe stores `match` in a ref that
    // it refreshes every render), so membership reflects the latest tracked set
    // without re-binding the listener.
    match: useCallback(
      (task: TaskState) => {
        if (task.task_type !== taskType) return false;
        if ((task.project_id ?? task.project) !== project) return false;
        const id = taskIdentifier(task, matchBy);
        return id != null && pendingRef.current.has(id);
      },
      [taskType, project, matchBy],
    ),
    onComplete: useCallback((task: TaskState) => settle(task, true), [settle]),
    onFailed: useCallback((task: TaskState) => settle(task, false), [settle]),
  });

  return { track };
}
