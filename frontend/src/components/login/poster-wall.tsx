// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { PosterColumn } from "./poster-column";
import { buildColumns, daySeed } from "@/lib/login-posters";
import type { Poster } from "@/types/login";
import styles from "./poster-wall.module.css";

interface PosterWallProps {
  posters: Poster[];
  /** Override seed for tests. Defaults to today's day-of-year. */
  seed?: number;
}

/** Per-column motion parameters (see spec §4.3). */
const COLUMN_CONFIG = [
  { direction: "up",   duration: 42, delay: 0 },
  { direction: "down", duration: 56, delay: -8 },
  { direction: "up",   duration: 68, delay: -22 },
  { direction: "down", duration: 48, delay: -4 },
  { direction: "up",   duration: 60, delay: -15 },
  { direction: "down", duration: 74, delay: -30 },
] as const;

/** CSS classes to hide extra columns at narrower breakpoints. */
const HIDE_CLASS = [
  "",                    // col 1: always visible
  "",                    // col 2: always visible
  "",                    // col 3: always visible (mobile = 3 cols)
  styles.hideBelowSm,    // col 4: hidden <640px (tablet+ = 4 cols)
  styles.hideBelowMd,    // col 5: hidden <768px (desktop+ = 5 cols)
  styles.hideBelowXl,    // col 6: hidden <1280px (wide = 6 cols)
];

export function PosterWall({ posters, seed }: PosterWallProps) {
  const effectiveSeed = seed ?? daySeed();
  const columns = buildColumns(posters, effectiveSeed);

  return (
    <>
      <div data-testid="poster-wall" aria-hidden="true" className={styles.wall}>
        {columns.map((col, i) => (
          <PosterColumn
            key={i}
            posters={col}
            direction={COLUMN_CONFIG[i].direction}
            duration={COLUMN_CONFIG[i].duration}
            delay={COLUMN_CONFIG[i].delay}
            className={HIDE_CLASS[i]}
          />
        ))}
      </div>
      <div className={styles.veil} aria-hidden="true" />
      <div className={styles.grain} aria-hidden="true" />
    </>
  );
}
