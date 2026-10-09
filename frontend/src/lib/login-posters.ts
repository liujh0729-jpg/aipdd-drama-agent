// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Poster } from "@/types/login";

/**
 * Mulberry32 PRNG. Small, fast, good enough for deterministic shuffling.
 * @see https://stackoverflow.com/a/47593316
 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** In-place Fisher–Yates shuffle driven by a PRNG. Returns the same array. */
function shuffle<T>(arr: T[], rand: () => number): T[] {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

/**
 * Day-of-year seed. Every user sees the same wall on the same day;
 * it rotates at midnight local time.
 */
export function daySeed(now: Date = new Date()): number {
  const start = new Date(now.getFullYear(), 0, 0);
  const diff = now.getTime() - start.getTime();
  return Math.floor(diff / 86400000) + now.getFullYear() * 1000;
}

/**
 * Given the full poster manifest, produce 6 columns.
 * Columns may contain repeats of posters from other columns but avoid
 * intra-column repeats where possible. Each column is long enough
 * that the duplicated marquee track looks full on a tall viewport.
 */
export function buildColumns(posters: Poster[], seed: number): Poster[][] {
  const rand = mulberry32(seed);
  const pool = shuffle([...posters], rand);

  const cols: Poster[][] = Array.from({ length: 6 }, () => []);
  // Deal round-robin so each column gets ~pool.length/6 unique items.
  pool.forEach((p, i) => cols[i % 6].push(p));

  // If any column has < 6 items, pad from a re-shuffled copy of pool
  // (ensuring the padded items weren't already in that column when possible).
  const minPerCol = 6;
  for (const col of cols) {
    if (col.length >= minPerCol) continue;
    const owned = new Set(col.map((p) => p.id));
    const filler = shuffle([...pool], rand).filter((p) => !owned.has(p.id));
    while (col.length < minPerCol && filler.length) {
      col.push(filler.shift()!);
    }
    // Fallback (shouldn't happen with 36 posters / 6 cols, but keeps invariant).
    while (col.length < minPerCol) col.push(pool[col.length % pool.length]);
  }

  return cols;
}
