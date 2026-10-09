// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { PoolImage } from "@/lib/queries/sketches";

export interface ResolvedImage {
  url: string | null;
  poolImage: PoolImage | null;
}

export function resolveImage(
  _images: PoolImage[],
  _assignments: Record<string, string>,
  _beatNum: number,
  _imageType: "sketch" | "render",
  currentUrl: string | null,
): ResolvedImage {
  if (currentUrl) return { url: currentUrl, poolImage: null };
  return { url: null, poolImage: null };
}
