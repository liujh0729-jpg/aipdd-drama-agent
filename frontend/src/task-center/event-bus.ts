// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { TaskEvent, TaskEventType, TaskEventListener } from "./types";

export interface TaskEventBus {
  on(type: TaskEventType | "*", listener: TaskEventListener): () => void;
  emit(event: TaskEvent): void;
}

export function createEventBus(): TaskEventBus {
  const listeners = new Map<TaskEventType | "*", Set<TaskEventListener>>();

  return {
    on(type, listener) {
      let set = listeners.get(type);
      if (!set) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(listener);
      return () => {
        set!.delete(listener);
      };
    },
    emit(event) {
      const typed = listeners.get(event.type);
      const wildcard = listeners.get("*");
      const all = [...(typed ?? []), ...(wildcard ?? [])];
      for (const l of all) {
        try {
          l(event);
        } catch (err) {
          console.error("[task-center] listener threw:", err);
        }
      }
    },
  };
}
