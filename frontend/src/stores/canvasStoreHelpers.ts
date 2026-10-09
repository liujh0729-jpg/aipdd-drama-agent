// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { CANVAS_NODE_TYPES, DEFAULT_ASPECT_RATIO, DEFAULT_NODE_WIDTH, EXPORT_RESULT_NODE_DEFAULT_WIDTH, EXPORT_RESULT_NODE_LAYOUT_HEIGHT, EXPORT_RESULT_NODE_MIN_HEIGHT, EXPORT_RESULT_NODE_MIN_WIDTH, type ActiveToolDialog, type CanvasEdge, type CanvasNode, type CanvasNodeData, type CanvasNodeType, type StoryboardExportOptions, type StoryboardFrameItem } from '@/features/canvas/domain/canvasNodes';
import { nodeHasSourceHandle, nodeHasTargetHandle, isUpstreamConnectionAllowed } from '@/features/canvas/domain/nodeRegistry';
import { nodeCatalog } from '@/features/canvas/application/nodeCatalog';
import { aspectRatioFromImageDimensions, ensureAtLeastOneMinEdge, resolveMinEdgeFittedSize, resolveSizeInsideTargetBox } from '@/features/canvas/application/imageNodeSizing';
import { scopeProjectionGraphIds } from '@/features/freezone/projectionGraphIds';
import type { CanvasHistorySnapshot, CanvasHistoryState } from './canvasStore';

export const MAX_HISTORY_STEPS = 50;
export const SKILL_NODE_DEFAULT_MEASURED = { width: 380, height: 520 };
export const BEAT_CONTEXT_NODE_DEFAULT_MEASURED = { width: 420, height: 560 };
export const IMAGE_NODE_VISUAL_MIN_EDGE = 300;
export const STORYBOARD_SPLIT_NODE_MIN_WIDTH = 440;
export const STORYBOARD_SPLIT_NODE_MAX_WIDTH = 860;
export const STORYBOARD_SPLIT_FRAME_TARGET_WIDTH = 150;
export const STORYBOARD_SPLIT_FRAME_NOTE_HEIGHT = 40;
export const STORYBOARD_SPLIT_NODE_CHROME_HEIGHT = 70;
export const STORYBOARD_SPLIT_GRID_GAP = 1;

/** True when `nextNodeCount === 0 && prevNodeCount > 0` — used to flag a removal that empties the canvas. */
export function isDeleteToEmpty(prevNodeCount: number, nextNodeCount: number): boolean {
  return prevNodeCount > 0 && nextNodeCount === 0;
}

export function normalizeHandleId(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed === 'null' || trimmed === 'undefined') {
    return undefined;
  }
  return trimmed;
}

export function defaultSkillSourceHandle(node: CanvasNode, edge: CanvasEdge): string | undefined {
  if (node.type !== CANVAS_NODE_TYPES.skill) {
    return undefined;
  }
  const skillId = (node.data as { skill_id?: unknown }).skill_id;
  const role = (edge.data as { role?: unknown } | undefined)?.role;
  if (skillId === 'freezone.scene_360' && role === 'scene_360_canonical') {
    return 'scene_360_candidate';
  }
  return undefined;
}

export function isNoReferenceEdge(edge: CanvasEdge): boolean {
  const targetHandle =
    typeof edge.targetHandle === 'string' && edge.targetHandle.trim()
      ? edge.targetHandle.trim()
      : '';
  if (targetHandle === 'identity:__NO_CHARACTER__' || targetHandle === 'prop:__NO_PROP__') {
    return true;
  }
  const data = edge.data;
  const referenceTarget =
    data && typeof data === 'object' && !Array.isArray(data)
      ? (data as Record<string, unknown>).reference_target
      : undefined;
  if (!referenceTarget || typeof referenceTarget !== 'object' || Array.isArray(referenceTarget)) {
    return false;
  }
  const target = referenceTarget as Record<string, unknown>;
  return target.identity_id === '__NO_CHARACTER__' || target.prop_id === '__NO_PROP__';
}

export function isNoReferenceNode(node: CanvasNode): boolean {
  const data = node.data as {
    label?: unknown;
    displayName?: unknown;
    content?: unknown;
    prompt?: unknown;
    reference_target?: unknown;
    __freezone_source?: unknown;
  };
  if (
    data.label === '__NO_CHARACTER__' ||
    data.label === '__NO_PROP__' ||
    data.displayName === '__NO_CHARACTER__' ||
    data.displayName === '__NO_PROP__' ||
    data.content === '__NO_CHARACTER__' ||
    data.content === '__NO_PROP__' ||
    data.prompt === '__NO_CHARACTER__' ||
    data.prompt === '__NO_PROP__'
  ) {
    return true;
  }
  const referenceTarget =
    data.reference_target && typeof data.reference_target === 'object' && !Array.isArray(data.reference_target)
      ? (data.reference_target as Record<string, unknown>)
      : null;
  if (referenceTarget?.identity_id === '__NO_CHARACTER__' || referenceTarget?.prop_id === '__NO_PROP__') {
    return true;
  }
  const freezoneSource =
    data.__freezone_source && typeof data.__freezone_source === 'object' && !Array.isArray(data.__freezone_source)
      ? (data.__freezone_source as Record<string, unknown>)
      : null;
  const meta =
    freezoneSource?.meta && typeof freezoneSource.meta === 'object' && !Array.isArray(freezoneSource.meta)
      ? (freezoneSource.meta as Record<string, unknown>)
      : null;
  return meta?.identity_id === '__NO_CHARACTER__' || meta?.prop_id === '__NO_PROP__';
}

export function edgeDataRecord(edge: CanvasEdge): Record<string, unknown> {
  return edge.data && typeof edge.data === 'object' && !Array.isArray(edge.data)
    ? (edge.data as Record<string, unknown>)
    : {};
}

export function sourceRolePriority(node: CanvasNode | undefined): number {
  const data = node?.data as { __freezone_source?: unknown } | undefined;
  const source =
    data?.__freezone_source &&
    typeof data.__freezone_source === 'object' &&
    !Array.isArray(data.__freezone_source)
      ? (data.__freezone_source as Record<string, unknown>)
      : null;
  const role = typeof source?.role === 'string' ? source.role.trim() : '';
  if (role === 'character_identity' || role === 'prop_reference') {
    return 0;
  }
  if (role === 'character_portrait') {
    return 1;
  }
  return 2;
}

export function referenceEdgeKey(edge: CanvasEdge): string | null {
  const data = edgeDataRecord(edge);
  const role = String(data.role || '').trim();
  if (role !== 'identity' && role !== 'prop') {
    return null;
  }
  const targetHandle = typeof edge.targetHandle === 'string' ? edge.targetHandle.trim() : '';
  if (!targetHandle.startsWith(`${role}:`)) {
    return null;
  }
  const referenceId = targetHandle.slice(role.length + 1).trim();
  if (!referenceId) {
    return null;
  }
  return `${edge.target}:${role}:${referenceId}`;
}

export function dedupeReferenceInputEdges(edges: CanvasEdge[], nodeMap: ReadonlyMap<string, CanvasNode>): CanvasEdge[] {
  const selectedIndexByKey = new Map<string, number>();
  const droppedIndexes = new Set<number>();
  for (const [index, edge] of edges.entries()) {
    const key = referenceEdgeKey(edge);
    if (!key) {
      continue;
    }
    const existingIndex = selectedIndexByKey.get(key);
    if (existingIndex === undefined) {
      selectedIndexByKey.set(key, index);
      continue;
    }
    const currentPriority = sourceRolePriority(nodeMap.get(edge.source));
    const existingPriority = sourceRolePriority(nodeMap.get(edges[existingIndex].source));
    if (currentPriority < existingPriority) {
      droppedIndexes.add(existingIndex);
      selectedIndexByKey.set(key, index);
    } else {
      droppedIndexes.add(index);
    }
  }
  return edges.filter((_edge, index) => !droppedIndexes.has(index));
}

export function normalizeEdgesWithNodes(rawEdges: CanvasEdge[], nodes: CanvasNode[]): CanvasEdge[] {
  const nodeMap = new Map(nodes.map((node) => [node.id, node] as const));

  const normalizedEdges = rawEdges
    .filter((edge) => {
      if (isNoReferenceEdge(edge)) {
        return false;
      }
      const sourceNode = nodeMap.get(edge.source);
      const targetNode = nodeMap.get(edge.target);
      if (!sourceNode || !targetNode) {
        return false;
      }
      if (!nodeHasSourceHandle(sourceNode.type) || !nodeHasTargetHandle(targetNode.type)) {
        return false;
      }
      // 丢弃违反上游类型规则的历史遗留边（如音频←非文本）。
      return isUpstreamConnectionAllowed(sourceNode.type, targetNode.type);
    })
    .map((edge) => ({
      ...edge,
      type: edge.type ?? 'disconnectableEdge',
      sourceHandle:
        normalizeHandleId((edge as CanvasEdge & { sourceHandle?: unknown }).sourceHandle) ??
        defaultSkillSourceHandle(nodeMap.get(edge.source) as CanvasNode, edge) ??
        'source',
      targetHandle:
        normalizeHandleId((edge as CanvasEdge & { targetHandle?: unknown }).targetHandle) ?? 'target',
    }));

  const referenceDedupedEdges = dedupeReferenceInputEdges(normalizedEdges, nodeMap);
  const edgeIndexById = new Map<string, number>();
  const dedupedEdges: CanvasEdge[] = [];
  for (const edge of referenceDedupedEdges) {
    const existingIndex = edgeIndexById.get(edge.id);
    if (existingIndex === undefined) {
      edgeIndexById.set(edge.id, dedupedEdges.length);
      dedupedEdges.push(edge);
      continue;
    }
    dedupedEdges[existingIndex] = edge;
  }
  return dedupedEdges;
}

export function normalizeNodes(rawNodes: CanvasNode[]): CanvasNode[] {
  const normalizedNodes = rawNodes
    .map((node) => {
      if (!Object.values(CANVAS_NODE_TYPES).includes(node.type as CanvasNodeType)) {
        return null;
      }

      const definition = nodeCatalog.getDefinition(node.type as CanvasNodeType);
      const mergedData = {
        ...definition.createDefaultData(),
        ...(node.data as Partial<CanvasNodeData>),
      } as CanvasNodeData;

      if (node.type === CANVAS_NODE_TYPES.storyboardSplit) {
        const frames = (mergedData as { frames?: StoryboardFrameItem[] }).frames ?? [];
        const firstFrameAspectRatio = frames.find((frame) => typeof frame.aspectRatio === 'string')
          ?.aspectRatio;
        const normalizedFrameAspectRatio =
          (typeof (mergedData as { frameAspectRatio?: unknown }).frameAspectRatio === 'string'
            ? (mergedData as { frameAspectRatio?: string }).frameAspectRatio
            : null) ??
          firstFrameAspectRatio ??
          DEFAULT_ASPECT_RATIO;

        (mergedData as { frameAspectRatio: string }).frameAspectRatio = normalizedFrameAspectRatio;
        (mergedData as { frames: StoryboardFrameItem[] }).frames = frames.map((frame, index) => ({
          id: frame.id,
          imageUrl: frame.imageUrl ?? null,
          previewImageUrl: frame.previewImageUrl ?? null,
          aspectRatio:
            typeof frame.aspectRatio === 'string'
              ? frame.aspectRatio
              : normalizedFrameAspectRatio,
          note: frame.note ?? '',
          order: Number.isFinite(frame.order) ? frame.order : index,
        }));

        const rawExportOptions = (mergedData as { exportOptions?: Partial<StoryboardExportOptions> })
          .exportOptions;
        const rawFontSize = Number.isFinite(rawExportOptions?.fontSize)
          ? Number(rawExportOptions?.fontSize)
          : createDefaultStoryboardExportOptions().fontSize;
        const normalizedFontSize = rawFontSize > 20
          ? Math.round(rawFontSize / 6)
          : rawFontSize;
        (mergedData as { exportOptions: StoryboardExportOptions }).exportOptions = {
          ...createDefaultStoryboardExportOptions(),
          ...(rawExportOptions ?? {}),
          fontSize: Math.max(1, Math.min(20, Math.round(normalizedFontSize))),
        };
      }

      if ('aspectRatio' in mergedData && !mergedData.aspectRatio) {
        mergedData.aspectRatio = DEFAULT_ASPECT_RATIO;
      }

      // Keep generation state only when there is a recoverable handle: either a
      // canvasAiGateway job id (Canvas.tsx export poll) or a freezone task key
      // (resumeNodeGeneration). Otherwise an interrupted run would spin forever.
      if ('isGenerating' in mergedData && mergedData.isGenerating) {
        const generationJobId =
          typeof (mergedData as { generationJobId?: unknown }).generationJobId === 'string'
            ? (mergedData as { generationJobId?: string }).generationJobId?.trim() ?? ''
            : '';
        const generationTaskKey =
          typeof (mergedData as { generationTaskKey?: unknown }).generationTaskKey === 'string'
            ? (mergedData as { generationTaskKey?: string }).generationTaskKey?.trim() ?? ''
            : '';
        const skillRunId =
          typeof (mergedData as { skillRunId?: unknown }).skillRunId === 'string'
            ? (mergedData as { skillRunId?: string }).skillRunId?.trim() ?? ''
            : '';
        if (!generationJobId && !generationTaskKey && !skillRunId) {
          mergedData.isGenerating = false;
          if ('generationStartedAt' in mergedData) {
            mergedData.generationStartedAt = null;
          }
        }
      }

      const normalizedNode = {
        ...node,
        type: node.type as CanvasNodeType,
        data: mergedData,
      } as CanvasNode;

      if (node.type === CANVAS_NODE_TYPES.skill && !node.measured) {
        normalizedNode.measured = SKILL_NODE_DEFAULT_MEASURED;
      } else if (node.type === CANVAS_NODE_TYPES.beatContext && !node.measured) {
        normalizedNode.measured = BEAT_CONTEXT_NODE_DEFAULT_MEASURED;
      }

      return isNoReferenceNode(normalizedNode) ? null : normalizedNode;
    })
    .filter((node): node is CanvasNode => Boolean(node));
  return sortParentNodesBeforeChildren(detachMissingParents(dedupeNodesById(normalizedNodes)));
}

export function nodeHydratePriority(node: CanvasNode): number {
  const data = node.data && typeof node.data === 'object' && !Array.isArray(node.data)
    ? (node.data as Record<string, unknown>)
    : {};
  if (
    data.preset_managed === true ||
    data.projection_archived === true ||
    (typeof data.projection_key === 'string' && data.projection_key.trim())
  ) {
    return 2;
  }
  return 1;
}

export function dedupeNodesById(nodes: CanvasNode[]): CanvasNode[] {
  const order: string[] = [];
  const indexById = new Map<string, number>();
  const deduped: CanvasNode[] = [];
  for (const node of nodes) {
    const existingIndex = indexById.get(node.id);
    if (existingIndex === undefined) {
      indexById.set(node.id, deduped.length);
      order.push(node.id);
      deduped.push(node);
      continue;
    }
    const existing = deduped[existingIndex];
    if (nodeHydratePriority(node) >= nodeHydratePriority(existing)) {
      deduped[existingIndex] = node;
    }
  }
  return order.map((id) => deduped[indexById.get(id)!]);
}

export function detachMissingParents(nodes: CanvasNode[]): CanvasNode[] {
  const nodeIds = new Set(nodes.map((node) => node.id));
  return nodes.map((node) => {
    if (!node.parentId || nodeIds.has(node.parentId)) {
      return node;
    }
    return {
      ...node,
      parentId: undefined,
      extent: undefined,
    };
  });
}

export function sortParentNodesBeforeChildren(nodes: CanvasNode[]): CanvasNode[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node] as const));
  const originalIndex = new Map(nodes.map((node, index) => [node.id, index] as const));
  const visited = new Set<string>();
  const visiting = new Set<string>();
  const sorted: CanvasNode[] = [];

  const visit = (node: CanvasNode) => {
    if (visited.has(node.id)) return;
    if (visiting.has(node.id)) {
      sorted.push(node);
      visited.add(node.id);
      return;
    }
    visiting.add(node.id);
    if (node.parentId) {
      const parent = nodeById.get(node.parentId);
      if (parent) {
        visit(parent);
      }
    }
    visiting.delete(node.id);
    if (!visited.has(node.id)) {
      sorted.push(node);
      visited.add(node.id);
    }
  };

  for (const node of [...nodes].sort((left, right) =>
    (originalIndex.get(left.id) ?? 0) - (originalIndex.get(right.id) ?? 0)
  )) {
    visit(node);
  }
  return sorted;
}

export function normalizeCanvasData(
  rawNodes: CanvasNode[],
  rawEdges: CanvasEdge[],
): CanvasHistorySnapshot {
  const scoped = scopeProjectionGraphIds(rawNodes, rawEdges);
  const normalizedNodes = normalizeNodes(scoped.nodes);
  return {
    nodes: normalizedNodes,
    edges: normalizeEdgesWithNodes(scoped.edges, normalizedNodes),
  };
}

export function normalizeHistory(history?: CanvasHistoryState): CanvasHistoryState {
  if (!history) {
    return { past: [], future: [] };
  }

  const normalizeSnapshot = (snapshot: CanvasHistorySnapshot): CanvasHistorySnapshot => {
    return normalizeCanvasData(snapshot.nodes, snapshot.edges);
  };

  return {
    past: history.past.slice(-MAX_HISTORY_STEPS).map(normalizeSnapshot),
    future: history.future.slice(-MAX_HISTORY_STEPS).map(normalizeSnapshot),
  };
}

export function createSnapshot(nodes: CanvasNode[], edges: CanvasEdge[]): CanvasHistorySnapshot {
  return { nodes, edges };
}

export function collectNodeIdsWithDescendants(nodes: CanvasNode[], seedIds: string[]): Set<string> {
  const deleteSet = new Set(seedIds);
  let changed = true;

  while (changed) {
    changed = false;
    for (const node of nodes) {
      if (!node.parentId || deleteSet.has(node.id)) {
        continue;
      }
      if (deleteSet.has(node.parentId)) {
        deleteSet.add(node.id);
        changed = true;
      }
    }
  }

  return deleteSet;
}

// 未测量且未显式设尺寸的节点（刚 spawn、尚未渲染）按类型的设计尺寸估算，数值与
// 各节点组件内的 DEFAULT_WIDTH/HEIGHT 对齐。否则 groupNodes / fitGroupToChildren
// 会按 320×200 低估大节点（如视频节点 580×380），算出的组边界包不住成员。
export const FALLBACK_NODE_SIZES: Partial<Record<string, { width: number; height: number }>> = {
  [CANVAS_NODE_TYPES.video]: { width: 580, height: 380 },
  [CANVAS_NODE_TYPES.textAnnotation]: { width: 440, height: 320 },
  [CANVAS_NODE_TYPES.audio]: { width: 480, height: 210 },
  [CANVAS_NODE_TYPES.upload]: { width: 320, height: 350 },
};

export function getNodeSize(node: CanvasNode): { width: number; height: number } {
  const fallback = (node.type && FALLBACK_NODE_SIZES[node.type]) || undefined;
  return {
    width:
      typeof node.measured?.width === 'number'
        ? node.measured.width
        : typeof node.width === 'number'
          ? node.width
          : (fallback?.width ?? DEFAULT_NODE_WIDTH),
    height:
      typeof node.measured?.height === 'number'
        ? node.measured.height
        : typeof node.height === 'number'
          ? node.height
          : (fallback?.height ?? 200),
  };
}

export function isImageAutoResizableType(type: CanvasNodeType): boolean {
  return type === CANVAS_NODE_TYPES.upload
    || type === CANVAS_NODE_TYPES.imageEdit
    || type === CANVAS_NODE_TYPES.exportImage
    || type === CANVAS_NODE_TYPES.imageGen
    || type === CANVAS_NODE_TYPES.video;
}

export function withManualSizeLock(node: CanvasNode): CanvasNode {
  const nodeData = node.data as CanvasNodeData & {
    isSizeManuallyAdjusted?: boolean;
    aspectRatio?: string;
  };

  // 缩放结束时把节点框吸附回图片真实比例：图片用 object-contain 显示，一旦节点
  // 宽高比偏离图片比例（自由缩放、或历史上被拉歪并保存的旧节点）就会露出容器
  // 底色形成黑边。按「在缩放后的框内、保持图片比例的最大尺寸」吸附即可消除黑边。
  const aspectRatio = typeof nodeData.aspectRatio === 'string' ? nodeData.aspectRatio : '';
  const currentWidth =
    (typeof node.width === 'number' ? node.width : null)
    ?? (typeof node.measured?.width === 'number' ? node.measured.width : null)
    ?? (typeof node.style?.width === 'number' ? node.style.width : null);
  const currentHeight =
    (typeof node.height === 'number' ? node.height : null)
    ?? (typeof node.measured?.height === 'number' ? node.measured.height : null)
    ?? (typeof node.style?.height === 'number' ? node.style.height : null);

  let snapped: { width: number; height: number } | null = null;
  if (aspectRatio && typeof currentWidth === 'number' && typeof currentHeight === 'number') {
    const fitted = resolveSizeInsideTargetBox(aspectRatio, {
      width: currentWidth,
      height: currentHeight,
    });
    if (Math.abs(fitted.width - currentWidth) > 1 || Math.abs(fitted.height - currentHeight) > 1) {
      snapped = fitted;
    }
  }

  if (nodeData.isSizeManuallyAdjusted && !snapped) {
    return node;
  }

  return {
    ...node,
    ...(snapped
      ? {
          width: snapped.width,
          height: snapped.height,
          style: { ...(node.style ?? {}), width: snapped.width, height: snapped.height },
        }
      : {}),
    data: {
      ...node.data,
      isSizeManuallyAdjusted: true,
    } as CanvasNodeData,
  };
}

export function resolveAutoImageNodeDimensions(
  aspectRatio: string,
  options?: {
    minWidth?: number;
    minHeight?: number;
  }
): { width: number; height: number } {
  const minWidth = options?.minWidth ?? EXPORT_RESULT_NODE_MIN_WIDTH;
  const minHeight = options?.minHeight ?? EXPORT_RESULT_NODE_MIN_HEIGHT;
  return resolveMinEdgeFittedSize(aspectRatio, { minWidth, minHeight });
}

export function resolveGeneratedImageNodeDimensions(
  aspectRatio: string,
  options?: {
    minWidth?: number;
    minHeight?: number;
  }
): { width: number; height: number } {
  const size = resolveSizeInsideTargetBox(aspectRatio, {
    width: EXPORT_RESULT_NODE_DEFAULT_WIDTH,
    height: EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
  });
  const minWidth = options?.minWidth ?? IMAGE_NODE_VISUAL_MIN_EDGE;
  const minHeight = options?.minHeight ?? IMAGE_NODE_VISUAL_MIN_EDGE;

  return ensureAtLeastOneMinEdge(size, { minWidth, minHeight });
}

export function parseAspectRatioValue(aspectRatio: string | undefined): number {
  const [rawWidth = "1", rawHeight = "1"] = (aspectRatio || DEFAULT_ASPECT_RATIO).split(":");
  const width = Number(rawWidth);
  const height = Number(rawHeight);

  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 1;
  }

  return width / height;
}

export function resolveStoryboardSplitNodeDimensions(
  rows: number,
  cols: number,
  frameAspectRatio: string | undefined
): { width: number; height: number } {
  const safeRows = Math.max(1, Math.floor(rows));
  const safeCols = Math.max(1, Math.floor(cols));
  const width = Math.round(Math.max(
    STORYBOARD_SPLIT_NODE_MIN_WIDTH,
    Math.min(
      STORYBOARD_SPLIT_NODE_MAX_WIDTH,
      safeCols * STORYBOARD_SPLIT_FRAME_TARGET_WIDTH + (safeCols - 1) * STORYBOARD_SPLIT_GRID_GAP + 16
    )
  ));
  const contentWidth = Math.max(1, width - 16);
  const frameWidth = Math.max(
    1,
    (contentWidth - (safeCols - 1) * STORYBOARD_SPLIT_GRID_GAP) / safeCols
  );
  const frameImageHeight = frameWidth / parseAspectRatioValue(frameAspectRatio);
  const frameHeight = frameImageHeight + STORYBOARD_SPLIT_FRAME_NOTE_HEIGHT;
  const gridHeight = safeRows * frameHeight + (safeRows - 1) * STORYBOARD_SPLIT_GRID_GAP;
  const height = Math.round(Math.max(
    320,
    Math.min(1600, gridHeight + STORYBOARD_SPLIT_NODE_CHROME_HEIGHT)
  ));

  return { width, height };
}

export function resolveDerivedAspectRatio(
  sourceNode: CanvasNode | undefined,
  fallbackAspectRatio: string
): string {
  if (!sourceNode) {
    return fallbackAspectRatio;
  }

  if (sourceNode.type === CANVAS_NODE_TYPES.storyboardGen) {
    const data = sourceNode.data as { requestAspectRatio?: string; aspectRatio?: string };
    const preferred = data.requestAspectRatio && data.requestAspectRatio !== 'auto'
      ? data.requestAspectRatio
      : data.aspectRatio;
    return preferred || fallbackAspectRatio;
  }

  if (sourceNode.type === CANVAS_NODE_TYPES.storyboardSplit) {
    const data = sourceNode.data as { frameAspectRatio?: string; aspectRatio?: string };
    return data.frameAspectRatio || data.aspectRatio || fallbackAspectRatio;
  }

  if (sourceNode.type === CANVAS_NODE_TYPES.imageEdit) {
    const data = sourceNode.data as { requestAspectRatio?: string; aspectRatio?: string };
    const preferred = data.requestAspectRatio && data.requestAspectRatio !== 'auto'
      ? data.requestAspectRatio
      : data.aspectRatio;
    return preferred || fallbackAspectRatio;
  }

  const imageLikeAspect = (sourceNode.data as { aspectRatio?: string }).aspectRatio;
  return imageLikeAspect || fallbackAspectRatio;
}

export function maybeApplyImageAutoResize(node: CanvasNode, patch: Partial<CanvasNodeData>): CanvasNode {
  if (!isImageAutoResizableType(node.type)) {
    return node;
  }

  const isVideo = node.type === CANVAS_NODE_TYPES.video;
  const nodeData = node.data as CanvasNodeData & {
    imageUrl?: string | null;
    videoUrl?: string | null;
    aspectRatio?: string;
    widthPx?: number;
    heightPx?: number;
    isSizeManuallyAdjusted?: boolean;
  };
  const patchData = patch as Partial<CanvasNodeData> & {
    imageUrl?: string | null;
    videoUrl?: string | null;
    aspectRatio?: string;
    widthPx?: number;
    heightPx?: number;
    isSizeManuallyAdjusted?: boolean;
  };

  // 视频以 widthPx/heightPx/aspectRatio 作为触发点 —— 这些只有在 <video> 元素
  // 拿到 metadata 后才会更新，避免拿到 videoUrl 但 metadata 未就绪时就用默认
  // aspectRatio 错误地 resize 一次。
  const hasImageRelatedChange = isVideo
    ? ('aspectRatio' in patchData || 'widthPx' in patchData || 'heightPx' in patchData)
    : ('imageUrl' in patchData || 'previewImageUrl' in patchData || 'aspectRatio' in patchData);
  if (!hasImageRelatedChange) {
    return node;
  }

  const isSizeManuallyAdjusted = patchData.isSizeManuallyAdjusted ?? nodeData.isSizeManuallyAdjusted ?? false;
  if (isSizeManuallyAdjusted) {
    return node;
  }

  // 没有实际媒体内容时不要乱改尺寸 —— 视频以 videoUrl、图片以 imageUrl 作为「已加载」信号。
  const nextAssetUrl = isVideo
    ? (patchData.videoUrl ?? nodeData.videoUrl)
    : (patchData.imageUrl ?? nodeData.imageUrl);
  if (typeof nextAssetUrl !== 'string' || nextAssetUrl.trim().length === 0) {
    return node;
  }

  // 视频节点的展示比例应跟随视频真实像素（widthPx/heightPx），而不是生成预设
  // aspectRatio（那只是「下一次生成」想要的比例，可能与既有视频本身的像素比
  // 不一致——例如 9:16 的素材视频，预设却是 16:9，否则节点会被撑成 16:9 给视频
  // 加黑边）。拿不到像素时再回退到预设 aspectRatio。
  const presetAspectRatio = patchData.aspectRatio ?? nodeData.aspectRatio ?? DEFAULT_ASPECT_RATIO;
  const videoPixelAspectRatio = isVideo
    ? (() => {
        const w = patchData.widthPx ?? nodeData.widthPx;
        const h = patchData.heightPx ?? nodeData.heightPx;
        return typeof w === 'number' && typeof h === 'number' && w > 0 && h > 0
          ? aspectRatioFromImageDimensions(w, h)
          : null;
      })()
    : null;
  const nextAspectRatio = videoPixelAspectRatio ?? presetAspectRatio;
  // 各节点类型的内部 MIN_WIDTH / MIN_HEIGHT 决定了 React Flow 给它的 width /
  // height 会不会被 clamp 掉 —— 这里直接对齐节点自己的下限，否则 store 算
  // 出来的尺寸会被节点 component 再 clamp 一次，比例又被拉成方块。
  const resizeMins = (() => {
    if (node.type === CANVAS_NODE_TYPES.exportImage) {
      return { minWidth: EXPORT_RESULT_NODE_MIN_WIDTH, minHeight: EXPORT_RESULT_NODE_MIN_HEIGHT };
    }
    if (node.type === CANVAS_NODE_TYPES.video) {
      return { minWidth: 480, minHeight: 280 };
    }
    if (node.type === CANVAS_NODE_TYPES.imageGen) {
      return { minWidth: 480, minHeight: 260 };
    }
    return undefined;
  })();
  const nextSize = resizeMins
    ? resolveAutoImageNodeDimensions(nextAspectRatio, resizeMins)
    : resolveAutoImageNodeDimensions(nextAspectRatio);

  return {
    ...node,
    width: nextSize.width,
    height: nextSize.height,
    style: {
      ...(node.style ?? {}),
      width: nextSize.width,
      height: nextSize.height,
    },
  };
}

export function resolveAbsolutePosition(
  node: CanvasNode,
  nodeMap: Map<string, CanvasNode>
): { x: number; y: number } {
  let x = node.position.x;
  let y = node.position.y;
  let currentParentId = node.parentId;
  const visited = new Set<string>();

  while (currentParentId && !visited.has(currentParentId)) {
    visited.add(currentParentId);
    const parent = nodeMap.get(currentParentId);
    if (!parent) {
      break;
    }
    x += parent.position.x;
    y += parent.position.y;
    currentParentId = parent.parentId;
  }

  return { x, y };
}

/**
 * Undo the edge changes mergeStoryboardGroup made: re-anchor edges that were
 * re-pointed onto the group back to their original member, and reveal the hidden
 * internal (member ↔ member) edges. Used by ungroup / convert-to-plain.
 */
export function restoreStoryboardEdges(
  edges: CanvasEdge[],
  groupNodeId: string,
  childIds: Set<string>
): CanvasEdge[] {
  return edges.map((edge) => {
    let next = edge;
    const data = next.data as Record<string, unknown> | undefined;
    if (next.source === groupNodeId && typeof data?.__sbOrigSource === 'string') {
      const { __sbOrigSource, ...restData } = data;
      next = { ...next, source: __sbOrigSource, data: restData };
    }
    const data2 = next.data as Record<string, unknown> | undefined;
    if (next.target === groupNodeId && typeof data2?.__sbOrigTarget === 'string') {
      const { __sbOrigTarget, ...restData } = data2;
      next = { ...next, target: __sbOrigTarget, data: restData };
    }
    if ((childIds.has(next.source) || childIds.has(next.target)) && next.hidden) {
      next = { ...next, hidden: false };
    }
    return next;
  });
}

export function pushSnapshot(
  snapshots: CanvasHistorySnapshot[],
  snapshot: CanvasHistorySnapshot
): CanvasHistorySnapshot[] {
  const last = snapshots[snapshots.length - 1];
  if (last && last.nodes === snapshot.nodes && last.edges === snapshot.edges) {
    return snapshots;
  }

  const next = [...snapshots, snapshot];
  if (next.length > MAX_HISTORY_STEPS) {
    next.shift();
  }
  return next;
}

export function getDerivedNodePosition(nodes: CanvasNode[], sourceNodeId: string): { x: number; y: number } {
  const sourceNode = nodes.find((node) => node.id === sourceNodeId);
  if (!sourceNode) {
    return { x: 100, y: 100 };
  }

  return {
    x: sourceNode.position.x + DEFAULT_NODE_WIDTH + 100,
    y: sourceNode.position.y,
  };
}

export function resolveSelectedNodeId(selectedNodeId: string | null, nodes: CanvasNode[]): string | null {
  if (!selectedNodeId) {
    return null;
  }
  return nodes.some((node) => node.id === selectedNodeId) ? selectedNodeId : null;
}

export function resolveActiveToolDialog(
  activeToolDialog: ActiveToolDialog | null,
  nodes: CanvasNode[]
): ActiveToolDialog | null {
  if (!activeToolDialog) {
    return null;
  }
  return nodes.some((node) => node.id === activeToolDialog.nodeId) ? activeToolDialog : null;
}

export function createDefaultStoryboardExportOptions(): StoryboardExportOptions {
  return {
    showFrameIndex: false,
    showFrameNote: false,
    notePlacement: 'overlay',
    imageFit: 'cover',
    frameIndexPrefix: 'S',
    cellGap: 8,
    outerPadding: 0,
    fontSize: 4,
    backgroundColor: '#0f1115',
    textColor: '#f8fafc',
  };
}
