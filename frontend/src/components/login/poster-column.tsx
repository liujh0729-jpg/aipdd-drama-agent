// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Poster } from "@/types/login";
import styles from "./poster-wall.module.css";

interface PosterColumnProps {
  posters: Poster[];
  direction: "up" | "down";
  duration: number; // seconds
  delay: number; // seconds (negative allowed)
  /** CSS class applied to the outer column (e.g. responsive hide utilities). */
  className?: string;
}

/**
 * One animated column. The track is duplicated so the CSS keyframe
 * (0% → -50%) loops seamlessly.
 */
export function PosterColumn({
  posters,
  direction,
  duration,
  delay,
  className,
}: PosterColumnProps) {
  // Duplicate once for seamless marquee loop.
  const track = [...posters, ...posters];

  return (
    <div data-testid="poster-column" className={`${styles.column} ${className ?? ""}`}>
      <div
        data-testid="poster-track"
        className={`${styles.track} ${direction === "down" ? styles.trackDown : styles.trackUp}`}
        style={{
          animationDuration: `${duration}s`,
          animationDelay: `${delay}s`,
        }}
        aria-hidden="true"
      >
        {track.map((p, i) => (
          <div
            key={`${p.id}-${i}`}
            className={styles.tile}
            style={{ backgroundColor: p.dominant_hex }}
          >
            <picture>
              <source srcSet={p.src_avif} type="image/avif" />
              <source srcSet={p.src_webp} type="image/webp" />
              <img
                src={p.src_webp}
                alt=""
                loading={i === 0 ? "eager" : "lazy"}
                fetchPriority={i === 0 ? "high" : "auto"}
                decoding="async"
                className={styles.tileImg}
              />
            </picture>
          </div>
        ))}
      </div>
    </div>
  );
}
