// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  isExportImageNode,
  isImageEditNode,
  isUploadNode,
  type CanvasEdge,
  type CanvasNode,
} from '../domain/canvasNodes';
import type { GraphImageResolver } from './ports';

export class DefaultGraphImageResolver implements GraphImageResolver {
  collectInputImages(nodeId: string, nodes: CanvasNode[], edges: CanvasEdge[]): string[] {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const sourceNodeIds = edges
      .filter((edge) => edge.target === nodeId)
      .map((edge) => edge.source);

    const images = sourceNodeIds
      .map((sourceId) => nodeById.get(sourceId))
      .flatMap((node) => extractUpstreamImages(node));

    return [...new Set(images)];
  }
}

/**
 * Pure projection of a single node into its referenceable image URLs. Exported
 * so the per-node subscription hook (`useUpstreamImages`) can map a shallow-
 * selected slice of upstream nodes without re-walking the whole graph.
 */
export function extractUpstreamImages(node: CanvasNode | undefined): string[] {
  if (!node) {
    return [];
  }

  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    return node.data.imageUrl ? [node.data.imageUrl] : [];
  }

  return [];
}
