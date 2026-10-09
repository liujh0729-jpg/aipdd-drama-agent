// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
let currentCanvasMetadata: Record<string, unknown> | null = null;

export function setFreezoneCanvasMetadata(metadata: Record<string, unknown> | null): void {
  currentCanvasMetadata = metadata;
}

export function getFreezoneCanvasMetadata(): Record<string, unknown> | null {
  return currentCanvasMetadata;
}
