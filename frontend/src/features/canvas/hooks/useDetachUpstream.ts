// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback } from 'react';

import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 返回一个用于「取消引用上游素材」的函数。
 *
 * 取消引用的本质是删除「上游节点 -> 当前节点」之间的连线，
 * 画布会在连线变化时自动持久化，无需额外处理。
 *
 * @param nodeId 当前节点 id（连线的 target）
 * @returns detach(sourceNodeId) —— 删除来自 sourceNodeId 的全部入边
 */
export function useDetachUpstream(nodeId: string) {
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);

  return useCallback(
    (sourceNodeId: string) => {
      const { edges } = useCanvasStore.getState();
      edges
        .filter((edge) => edge.source === sourceNodeId && edge.target === nodeId)
        .forEach((edge) => deleteEdge(edge.id));
    },
    [nodeId, deleteEdge],
  );
}
