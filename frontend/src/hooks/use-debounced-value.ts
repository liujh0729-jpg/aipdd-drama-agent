// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from "react";

/**
 * Return a copy of `value` that only updates after it has stayed unchanged for
 * `delayMs`. Rapidly-changing inputs (a dragged slider, keystrokes) collapse
 * into a single trailing update once they settle.
 *
 * Compares by identity, so it's meant for primitives (number, string, boolean).
 * For objects, debounce the underlying primitives instead — a fresh object
 * literal each render would reset the timer every render and never settle.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
