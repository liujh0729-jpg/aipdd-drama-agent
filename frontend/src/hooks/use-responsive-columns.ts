// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useState, type RefObject } from "react";

const BREAKPOINTS = [
  { minWidth: 520, columns: 3 },
  { minWidth: 0, columns: 2 },
] as const;

function columnsForWidth(width: number): number {
  for (const bp of BREAKPOINTS) {
    if (width >= bp.minWidth) return bp.columns;
  }
  return 2;
}

/**
 * Returns the number of gallery columns based on container width.
 * Uses ResizeObserver for live updates.
 */
export function useResponsiveColumns(ref: RefObject<HTMLElement | null>): number {
  const [columns, setColumns] = useState(3);

  const measure = useCallback(() => {
    if (ref.current) {
      setColumns(columnsForWidth(ref.current.clientWidth));
    }
  }, [ref]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, measure]);

  return columns;
}
