// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useState } from "react";

/**
 * Read a divider position (percentage 0-100) from localStorage on mount.
 * Falls back to `defaultPercent` if the stored value is missing, malformed,
 * or out of [1, 99] range.
 */
export function useResizablePanePersistence(
  storageKey: string,
  defaultPercent: number,
): readonly [number, (value: number) => void] {
  const [percent, setPercentState] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw === null) return defaultPercent;
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) return defaultPercent;
      if (parsed < 1 || parsed > 99) return defaultPercent;
      return parsed;
    } catch {
      return defaultPercent;
    }
  });

  const setPercent = useCallback(
    (value: number) => {
      setPercentState(value);
      try {
        localStorage.setItem(storageKey, String(value));
      } catch {
        // localStorage may be disabled — silently no-op.
      }
    },
    [storageKey],
  );

  return [percent, setPercent] as const;
}
