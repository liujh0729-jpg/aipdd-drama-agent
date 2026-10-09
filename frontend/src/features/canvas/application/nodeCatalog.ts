// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { canvasNodeDefinitions, getMenuNodeDefinitions } from '../domain/nodeRegistry';
import type { CanvasNodeType } from '../domain/canvasNodes';
import type { NodeCatalog } from './ports';

export const nodeCatalog: NodeCatalog = {
  getDefinition: (type: CanvasNodeType) => canvasNodeDefinitions[type],
  getMenuDefinitions: getMenuNodeDefinitions,
};
