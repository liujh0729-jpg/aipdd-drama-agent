// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

import type { FreezoneProjectionStatusItem } from "@/api/canvas";

let currentProjectionStatuses = new Map<string, FreezoneProjectionStatusItem>();
const listeners = new Set<() => void>();

function emitProjectionStatusChange(): void {
  for (const listener of listeners) {
    listener();
  }
}

function subscribeProjectionStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setCanvasProjectionStatuses(
  statuses: FreezoneProjectionStatusItem[],
): void {
  currentProjectionStatuses = new Map(
    statuses
      .filter((status) => status.projection_key.trim().length > 0)
      .map((status) => [status.projection_key, status]),
  );
  emitProjectionStatusChange();
}

export function clearCanvasProjectionStatuses(): void {
  if (currentProjectionStatuses.size === 0) {
    return;
  }
  currentProjectionStatuses = new Map();
  emitProjectionStatusChange();
}

export function markCanvasProjectionFresh(projectionKey: string): void {
  const current = currentProjectionStatuses.get(projectionKey);
  if (!current || current.stale === false) {
    return;
  }
  currentProjectionStatuses = new Map(currentProjectionStatuses);
  currentProjectionStatuses.set(projectionKey, {
    ...current,
    stale: false,
  });
  emitProjectionStatusChange();
}

export function getCanvasProjectionStatus(
  projectionKey: string | null | undefined,
): FreezoneProjectionStatusItem | null {
  if (!projectionKey) {
    return null;
  }
  return currentProjectionStatuses.get(projectionKey) ?? null;
}

export function useCanvasProjectionStatus(
  projectionKey: string | null | undefined,
): FreezoneProjectionStatusItem | null {
  return useSyncExternalStore(
    subscribeProjectionStatus,
    () => getCanvasProjectionStatus(projectionKey),
    () => null,
  );
}
