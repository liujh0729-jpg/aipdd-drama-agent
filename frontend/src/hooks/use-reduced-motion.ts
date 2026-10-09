// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMediaQuery } from "./use-media-query";

/**
 * Returns true when the user has set OS-level "reduce motion". Use this to
 * swap animated behavior (smooth scrolls, staggered reveals) for instant
 * alternatives in JS — CSS animations are handled by the global @media rule
 * in index.css.
 */
export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
