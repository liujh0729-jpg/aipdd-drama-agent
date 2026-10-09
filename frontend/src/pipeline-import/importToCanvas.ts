// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { CANVAS_NODE_TYPES, DEFAULT_NODE_WIDTH } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";
import type { ImportableAsset } from "./ImportPanel";

const COLUMNS = 4;
const NODE_W = DEFAULT_NODE_WIDTH;
const NODE_H = 240;
const GAP_X = 24;
const GAP_Y = 24;

/**
 * Drop a batch of imported assets into the current canvas as upload nodes.
 *
 * F3 ships a simple grid layout starting at the current viewport center; F5+
 * may add smarter placement (e.g. respecting the existing canvas viewport,
 * or grouping assets by source kind).
 */
export function importAssetsToCanvas(assets: ImportableAsset[]): void {
  if (assets.length === 0) return;

  const addNode = useCanvasStore.getState().addNode;
  const startX = 0;
  const startY = 0;

  for (let i = 0; i < assets.length; i++) {
    const col = i % COLUMNS;
    const row = Math.floor(i / COLUMNS);
    const position = {
      x: startX + col * (NODE_W + GAP_X),
      y: startY + row * (NODE_H + GAP_Y),
    };
    const asset = assets[i];
    addNode(CANVAS_NODE_TYPES.upload, position, {
      displayName: asset.label,
      imageUrl: asset.url,
      previewImageUrl: asset.url,
      aspectRatio: "1:1",
      sourceFileName: asset.label,
      // Hold onto the SuperTale provenance so a Commit back to pipeline can
      // pre-fill its target dropdown later.
      __freezone_source: {
        kind: asset.kind,
        meta: asset.meta,
      },
    } as Record<string, unknown>);
  }
}
