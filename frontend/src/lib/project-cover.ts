// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Deterministic cover art for projects — gradient + initial derived from the
// project name. Used until the backend API returns real thumbnail/genre data.

export interface CoverPaletteStop {
  gradient: string;
  name: string;
  primary: string;
}

// 8-stop curated palette tuned for the dark theme. Each entry is a 135° two-
// stop linear gradient. Selected so that no two adjacent stops collide when
// projects are sorted alphabetically.
export const PROJECT_COVER_PALETTE: CoverPaletteStop[] = [
  { name: "teal",    primary: "#0d9488", gradient: "linear-gradient(135deg, #0d9488 0%, #22d3ee 100%)" },
  { name: "emerald", primary: "#059669", gradient: "linear-gradient(135deg, #059669 0%, #34d399 100%)" },
  { name: "cyan",    primary: "#0891b2", gradient: "linear-gradient(135deg, #0891b2 0%, #67e8f9 100%)" },
  { name: "violet",  primary: "#6d28d9", gradient: "linear-gradient(135deg, #6d28d9 0%, #a78bfa 100%)" },
  { name: "indigo",  primary: "#4338ca", gradient: "linear-gradient(135deg, #4338ca 0%, #818cf8 100%)" },
  { name: "rose",    primary: "#be123c", gradient: "linear-gradient(135deg, #be123c 0%, #fb7185 100%)" },
  { name: "amber",   primary: "#b45309", gradient: "linear-gradient(135deg, #b45309 0%, #fbbf24 100%)" },
  { name: "fuchsia", primary: "#a21caf", gradient: "linear-gradient(135deg, #a21caf 0%, #e879f9 100%)" },
];

// Inline SVG fractal-noise filter rendered as a data URI. Applied as an
// overlay via `background-image` with `mix-blend-overlay` + low opacity to
// kill banding on the gradients. Low baseFrequency = large grain.
export const NOISE_DATA_URI =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'>` +
      `<filter id='n'>` +
      `<feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/>` +
      `<feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.5 0'/>` +
      `</filter>` +
      `<rect width='100%' height='100%' filter='url(#n)' opacity='1'/>` +
    `</svg>`,
  );

function hashString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = (hash << 5) - hash + input.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function getProjectCover(name: string): {
  gradient: string;
  initial: string;
  paletteIndex: number;
  primary: string;
} {
  const trimmed = name.trim();
  const paletteIndex = hashString(trimmed) % PROJECT_COVER_PALETTE.length;
  const palette = PROJECT_COVER_PALETTE[paletteIndex];
  // Use the first rendered character (handles multi-byte chars like 中文).
  const initial = Array.from(trimmed)[0]?.toUpperCase() ?? "?";
  return { gradient: palette.gradient, initial, paletteIndex, primary: palette.primary };
}
