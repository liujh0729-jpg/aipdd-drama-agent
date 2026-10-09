// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from "react";
import type { TaskState } from "./types";
import { useEventBus } from "./event-bus-context";

export interface UseTaskSubscribeOptions {
  match: (task: TaskState) => boolean;
  onComplete?: (task: TaskState) => void;
  onFailed?: (task: TaskState) => void;
  onProgress?: (task: TaskState) => void;
}

export function useTaskSubscribe(opts: UseTaskSubscribeOptions): void {
  const bus = useEventBus();
  const matchRef = useRef(opts.match);
  matchRef.current = opts.match;
  const onCompleteRef = useRef(opts.onComplete);
  onCompleteRef.current = opts.onComplete;
  const onFailedRef = useRef(opts.onFailed);
  onFailedRef.current = opts.onFailed;
  const onProgressRef = useRef(opts.onProgress);
  onProgressRef.current = opts.onProgress;

  useEffect(() => {
    const off = bus.on("*", (e) => {
      if (e.type === "task_removed") return;
      if (!matchRef.current(e.task)) return;
      if (e.type === "task_complete") onCompleteRef.current?.(e.task);
      else if (e.type === "task_failed") onFailedRef.current?.(e.task);
      else if (e.type === "task_updated") {
        // Don't double-fire: task_complete / task_failed also emit task_updated upstream.
        // onProgress is for in-flight progress only.
        if (
          e.task.status === "completed" ||
          e.task.status === "failed" ||
          e.task.status === "cancelled"
        ) return;
        onProgressRef.current?.(e.task);
      }
    });
    return off;
  }, [bus]);
}
