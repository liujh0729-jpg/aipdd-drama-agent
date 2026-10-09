// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Compact relative-time formatter for pool-image captions.
 *
 * Output examples:
 *   < 1 min   → "5s"       (integer seconds)
 *   < 1 hour  → "2m"       (integer minutes)
 *   < 1 day   → "5.2h"     (one decimal hours)
 *   ≥ 1 day   → "1.3d"     (one decimal days)
 *
 * Returns null for nullish / invalid inputs so callers can skip rendering.
 */
export function formatRelativeTime(
  iso: string | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;

  const diffSec = Math.max(0, Math.round((now - t) / 1000));
  if (diffSec < 60) return `${diffSec}s`;

  const diffMin = diffSec / 60;
  if (diffMin < 60) return `${Math.floor(diffMin)}m`;

  const diffHr = diffMin / 60;
  if (diffHr < 24) return `${(Math.floor(diffHr * 10) / 10).toFixed(1)}h`;

  const diffDay = diffHr / 24;
  return `${(Math.floor(diffDay * 10) / 10).toFixed(1)}d`;
}
