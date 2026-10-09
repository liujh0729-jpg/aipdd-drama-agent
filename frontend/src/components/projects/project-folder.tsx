// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import styles from "./project-folder.module.css";

function darkenColor(hex: string, percent: number): string {
  let color = hex.startsWith("#") ? hex.slice(1) : hex;
  if (color.length === 3) {
    color = color
      .split("")
      .map((c) => c + c)
      .join("");
  }

  const num = Number.parseInt(color, 16);
  if (Number.isNaN(num)) return hex;

  const r = Math.max(0, Math.min(255, Math.floor(((num >> 16) & 0xff) * (1 - percent))));
  const g = Math.max(0, Math.min(255, Math.floor(((num >> 8) & 0xff) * (1 - percent))));
  const b = Math.max(0, Math.min(255, Math.floor((num & 0xff) * (1 - percent))));

  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1).toUpperCase()}`;
}

function lightenColor(hex: string, percent: number): string {
  let color = hex.startsWith("#") ? hex.slice(1) : hex;
  if (color.length === 3) {
    color = color
      .split("")
      .map((c) => c + c)
      .join("");
  }

  const num = Number.parseInt(color, 16);
  if (Number.isNaN(num)) return hex;

  const r = (num >> 16) & 0xff;
  const g = (num >> 8) & 0xff;
  const b = num & 0xff;
  const nextR = Math.max(0, Math.min(255, Math.floor(r + (255 - r) * percent)));
  const nextG = Math.max(0, Math.min(255, Math.floor(g + (255 - g) * percent)));
  const nextB = Math.max(0, Math.min(255, Math.floor(b + (255 - b) * percent)));

  return `#${((1 << 24) + (nextR << 16) + (nextG << 8) + nextB).toString(16).slice(1).toUpperCase()}`;
}

export function ProjectFolder({
  color,
  initial,
  width = 100,
  size = 1,
  badges,
  className,
}: {
  color: string;
  initial: string;
  width?: number | string;
  size?: number;
  badges?: React.ReactNode;
  className?: string;
}) {
  const style = {
    "--folder-color": color,
    "--folder-front-highlight": lightenColor(color, 0.28),
    "--folder-back-color": darkenColor(color, 0.10),
    "--paper-1": "rgba(255, 255, 255, 0.44)",
    "--paper-2": "rgba(255, 255, 255, 0.70)",
    "--paper-3": "rgba(255, 255, 255, 0.96)",
    "--folder-width": typeof width === "number" ? `${width}px` : width,
    "--folder-size": size,
  } as React.CSSProperties;

  return (
    <div className={className ? `${styles.folder} ${className}` : styles.folder} style={style}>
      <div className={styles.back} aria-hidden="true">
        <div className={`${styles.paper} ${styles.paper1}`} />
        <div className={`${styles.paper} ${styles.paper2}`} />
        <div className={`${styles.paper} ${styles.paper3}`} />
        <div className={`${styles.front} ${styles.right}`} />
        <div className={styles.front}>
          {badges ? <div className={styles.badges}>{badges}</div> : null}
          <span className={styles.initial}>{initial}</span>
        </div>
      </div>
    </div>
  );
}
