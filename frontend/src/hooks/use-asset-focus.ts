// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from "react";

const HIGHLIGHT_CLASSES = [
  "ring-2",
  "ring-primary/70",
  "ring-offset-2",
  "ring-offset-background",
  "rounded-[10px]",
];

/**
 * Scrolls the card matching `focusId` into view and briefly rings it, so a
 * `?type=&id=` deep link lands the user on the right asset. Cards opt in by
 * setting `data-asset-id` on a wrapper inside the returned container ref.
 */
export function useAssetFocus(
  focusId: string | null | undefined,
  ready: boolean,
) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!focusId || !ready) return;
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-asset-id="${CSS.escape(focusId)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add(...HIGHLIGHT_CLASSES);
    const timer = window.setTimeout(() => {
      el.classList.remove(...HIGHLIGHT_CLASSES);
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [focusId, ready]);

  return containerRef;
}
