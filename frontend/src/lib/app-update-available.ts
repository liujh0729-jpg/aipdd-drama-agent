// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

// Non-blocking "a new version is deployed" signal. Distinct from
// chunk-load-recovery (which blocks the whole app once a chunk 404s): this one
// is proactive and dismissible — the current version still works, we just nudge
// the user to refresh when convenient.
type UpdateState = "idle" | "available" | "dismissed";
type Listener = () => void;

let state: UpdateState = "idle";
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function markUpdateAvailable(): void {
  if (state !== "idle") return;
  state = "available";
  notify();
}

export function dismissUpdateAvailable(): void {
  if (state !== "available") return;
  state = "dismissed";
  notify();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): boolean {
  return state === "available";
}

export function useUpdateAvailable(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function resetUpdateAvailableForTests(): void {
  state = "idle";
  listeners.clear();
}
