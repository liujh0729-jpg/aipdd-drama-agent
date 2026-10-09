// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { p } from "@/lib/api-path";

const STYLE_PREVIEW_ASSET_VERSION = "main-preset-png";

export function stylePreviewUrl(styleId: string): string {
  return `/${p`api/v1/styles/${styleId}/preview`}?v=${STYLE_PREVIEW_ASSET_VERSION}`;
}
