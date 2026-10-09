// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Single source of truth for project画幅 (aspect ratio) derivations.
 *
 * The whole UI follows ONE orientation per project. Every concrete value the
 * app needs — CSS aspect box, display label, crop ratio, generation params —
 * is derived from that single orientation here so callers never hardcode
 * "2:3" / "16:9" / "aspect-video" again.
 */

export type Orientation = "portrait" | "landscape";
export type ProjectAspectRatio = "2:3" | "16:9";
export type SpineTemplate = "drama" | "narrated";

export interface AspectSpec {
  orientation: Orientation;
  /** Human-facing ratio label, e.g. "2:3". */
  label: string;
  /** CSS aspect-ratio value, e.g. "2/3" (for arbitrary tailwind classes). */
  cssRatio: string;
  /** Tailwind aspect-box class. */
  aspectClass: string;
  /** width / height — used for crop-box math. */
  ratioValue: number;
  /** Sketch generation aspect param (backend-accepted union). */
  sketchAspect: "2:3" | "16:9";
  /** Render aspect_mode literal sent to the render pipeline. */
  renderAspect: "2:3" | "16:9";
}

const PORTRAIT: AspectSpec = {
  orientation: "portrait",
  label: "2:3",
  cssRatio: "2/3",
  aspectClass: "aspect-[2/3]",
  ratioValue: 2 / 3,
  sketchAspect: "2:3",
  renderAspect: "2:3",
};

const LANDSCAPE: AspectSpec = {
  orientation: "landscape",
  label: "16:9",
  cssRatio: "16/9",
  aspectClass: "aspect-video",
  ratioValue: 16 / 9,
  sketchAspect: "16:9",
  renderAspect: "16:9",
};

export function aspectSpec(orientation: Orientation): AspectSpec {
  return orientation === "landscape" ? LANDSCAPE : PORTRAIT;
}

/** Default orientation for a project before any explicit choice. */
export const DEFAULT_ORIENTATION: Orientation = "portrait";

export function orientationForAspectRatio(
  aspectRatio: string | null | undefined,
): Orientation | null {
  if (aspectRatio === "16:9") return "landscape";
  if (aspectRatio === "2:3") return "portrait";
  // Backward compatibility for projects that persisted the old vertical value.
  if (aspectRatio === "9:16") return "portrait";
  return null;
}

export function aspectRatioForOrientation(
  orientation: Orientation,
): ProjectAspectRatio {
  return aspectSpec(orientation).renderAspect;
}

export function orientationForSpineTemplate(
  spineTemplate: SpineTemplate | null | undefined,
): Orientation {
  return spineTemplate === "narrated" ? "landscape" : DEFAULT_ORIENTATION;
}

/**
 * Convert a "W:H" ratio label (e.g. "2:3", "16:9") into a CSS
 * `aspect-ratio` value ("W / H"). Use for single-image boxes whose true aspect
 * is variable — pass `spec.sketchAspect` for sketch cells, `spec.renderAspect`
 * for render/video frames.
 */
export function ratioToCss(ratio: string): string {
  const [w, h] = ratio.split(":");
  return `${w} / ${h}`;
}

/**
 * CSS `aspect-ratio` for a composite grid thumbnail. A grid is `cols × rows`
 * cells, each cell having `cellAspect` ("W:H"), so the whole image is
 * `(cols·W) / (rows·H)`. Keeps grid previews from squishing portrait cells
 * into a 16:9 box.
 */
export function gridAspectCss(
  cols: number,
  rows: number,
  cellAspect: string,
): string {
  const [w, h] = cellAspect.split(":").map(Number);
  const safeCols = Math.max(1, cols);
  const safeRows = Math.max(1, rows);
  if (!w || !h) return "1 / 1";
  return `${safeCols * w} / ${safeRows * h}`;
}

export interface CropBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const FULL_SOURCE_CROP_SIZE = 999_999;

export function centerCropBoxForRatio(
  width: number,
  height: number,
  ratio: number,
): CropBox {
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    !Number.isFinite(ratio) ||
    width <= 0 ||
    height <= 0 ||
    ratio <= 0
  ) {
    return {
      x: 0,
      y: 0,
      width: FULL_SOURCE_CROP_SIZE,
      height: FULL_SOURCE_CROP_SIZE,
    };
  }

  let cropWidth = width;
  let cropHeight = cropWidth / ratio;
  if (cropHeight > height) {
    cropHeight = height;
    cropWidth = cropHeight * ratio;
  }

  return {
    x: Math.max(0, Math.round((width - cropWidth) / 2)),
    y: Math.max(0, Math.round((height - cropHeight) / 2)),
    width: Math.max(1, Math.round(cropWidth)),
    height: Math.max(1, Math.round(cropHeight)),
  };
}

export function zoomCropBox(
  crop: CropBox,
  sourceWidth: number,
  sourceHeight: number,
  scale: number,
): CropBox {
  const safeWidth = Math.max(1, sourceWidth);
  const safeHeight = Math.max(1, sourceHeight);
  const currentWidth = Math.max(1, crop.width);
  const currentHeight = Math.max(1, crop.height);
  const maxScale = Math.min(safeWidth / currentWidth, safeHeight / currentHeight);
  const minScale = Math.min(
    maxScale,
    Math.max(16 / currentWidth, 16 / currentHeight),
  );
  const nextScale = Math.min(Math.max(scale, minScale), maxScale);
  const nextWidth = Math.max(1, Math.round(currentWidth * nextScale));
  const nextHeight = Math.max(1, Math.round(currentHeight * nextScale));
  const centerX = crop.x + currentWidth / 2;
  const centerY = crop.y + currentHeight / 2;

  return {
    x: Math.min(
      Math.max(0, Math.round(centerX - nextWidth / 2)),
      Math.max(0, safeWidth - nextWidth),
    ),
    y: Math.min(
      Math.max(0, Math.round(centerY - nextHeight / 2)),
      Math.max(0, safeHeight - nextHeight),
    ),
    width: nextWidth,
    height: nextHeight,
  };
}
