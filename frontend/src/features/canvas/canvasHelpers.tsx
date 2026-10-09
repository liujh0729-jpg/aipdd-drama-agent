// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { type HandleType, type Viewport } from '@xyflow/react';
import { type SkillDefinition } from '@/features/freezone/context/skillRoles';
import { CANVAS_NODE_TYPES, type CanvasEdge, type CanvasNode, type CanvasNodeType, DEFAULT_NODE_WIDTH } from '@/features/canvas/domain/canvasNodes';
import { isSupportedMediaFile } from '@/features/canvas/application/videoFileTypes';
import { getDownstreamSpawnTypes, getUpstreamSpawnTypes, getAllowedDownstreamTargetTypes, getAllowedUpstreamSourceTypes, isManualConnectionAllowed } from '@/features/canvas/domain/nodeRegistry';

export const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 };
export const DEFAULT_EDGE_OPTIONS = { type: 'disconnectableEdge' };
export const REACT_FLOW_PRO_OPTIONS = { hideAttribution: true };
export const CANVAS_NODE_DRAG_FOCUS_CLASS = 'canvas-node-drag-focus';
// 拖线吸附半径(px,以光标到目标 handle 的距离计)。节点的 target handle 在左边
// 缘的一个小点上,而节点本身宽 300~400px;半径太小(默认 20 / 旧值 50)时,把线
// 拖到节点中部就已超出 handle 的吸附范围,既不自动吸附也没有「会连上」的视觉反
// 馈,用户只能去瞄那个小点。调大到能覆盖到节点中部,拖到节点这一大片区域内即可
// 自动吸附连线(React Flow 原生会高亮合法 handle 并把连线吸过去);更远处落到节
// 点本体仍有 handleConnectEnd 里的 DOM 命中兜底。
export const CONNECTION_SNAP_RADIUS = 160;
// 手动「+」连线时，光标离目标节点边缘多近（屏幕像素）就算进入其「可落点区域」——
// 不必压到节点里面才触发落点高亮/建边，节点周围一圈邻域内即可吸附。
export const MANUAL_DROP_PROXIMITY_PX = 56;
export const MULTI_SELECTION_KEY_CODES = ['Control', 'Meta'];
export const PAN_ACTIVATION_KEY_CODE = 'Space';
// Pan the canvas only by holding the middle mouse button (scroll-wheel) and dragging
// (button 1). Left drag (0) runs the custom marquee box-select on the empty pane;
// right click (2) opens the canvas context menu.
export const PAN_ON_DRAG_BUTTONS = [1];
// 抓手工具（H）下左键也平移。ReactFlow 见到数组里有 0 会给 pane 挂 .draggable，
// 抓手光标由它自带的样式负责。
export const PAN_ON_DRAG_BUTTONS_HAND = [0, 1];
export const NODE_SPAWN_PLUS_HIDE_DELAY_MS = 400;
/** 小地图 hover 离开后的收起延迟，见 setMinimapHover 处的注释。 */
export const MINIMAP_HIDE_DELAY_MS = 180;

export function resolveCenteredViewport(
  container: HTMLElement | null,
  nodes: CanvasNode[],
  zoom = 1
): Viewport {
  if (!container) {
    return DEFAULT_VIEWPORT;
  }
  const rect = container.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) {
    return DEFAULT_VIEWPORT;
  }

  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const topLevelNodes = nodes.filter((node) => !node.parentId);
  if (topLevelNodes.length === 0) {
    return {
      x: rect.width / 2,
      y: rect.height / 2,
      zoom: safeZoom,
    };
  }

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const node of topLevelNodes) {
    const width = node.measured?.width
      ?? (typeof node.width === 'number' ? node.width : DEFAULT_NODE_WIDTH);
    const height = node.measured?.height
      ?? (typeof node.height === 'number' ? node.height : 200);
    minX = Math.min(minX, node.position.x);
    minY = Math.min(minY, node.position.y);
    maxX = Math.max(maxX, node.position.x + width);
    maxY = Math.max(maxY, node.position.y + height);
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY)) {
    return {
      x: rect.width / 2,
      y: rect.height / 2,
      zoom: safeZoom,
    };
  }

  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return {
    x: rect.width / 2 - centerX * safeZoom,
    y: rect.height / 2 - centerY * safeZoom,
    zoom: safeZoom,
  };
}

export interface PendingConnectStart {
  nodeId: string;
  handleType: HandleType;
  handleId?: string | null;
  start?: {
    x: number;
    y: number;
  };
}

export interface PreviewConnectionVisual {
  d: string;
  stroke: string;
  strokeWidth: number;
  strokeLinecap: 'butt' | 'round' | 'square';
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface ClipboardSnapshot {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  /**
   * 复制时所在的项目 id。粘贴到不同项目时据此触发跨项目资产迁移
   * （把媒体重新上传到目标项目）。null = 复制时不在项目路由下。
   */
  sourceProject: string | null;
}

export interface DuplicateOptions {
  explicitOffset?: { x: number; y: number };
  disableOffsetIteration?: boolean;
  suppressSelect?: boolean;
  suppressPersist?: boolean;
  /**
   * Paste from a serialized clipboard snapshot instead of looking the source
   * nodes up by id in the live canvas — lets paste work after the originals are
   * deleted and across canvases.
   */
  sourceSnapshot?: ClipboardSnapshot;
  /** Place the pasted group's top-left at this flow position (cursor paste). */
  targetFlowPosition?: { x: number; y: number };
  /** Select every pasted node (not just the first) — used by paste. */
  selectAll?: boolean;
}

export interface DuplicateResult {
  firstNodeId: string | null;
  idMap: Map<string, string>;
}

export interface MarqueeSelectionState {
  start: { x: number; y: number };
  current: { x: number; y: number };
}

export const ALT_DRAG_COPY_Z_INDEX = 2000;
export const GENERATION_JOB_POLL_INTERVAL_MS = 1400;
export const MARQUEE_SELECTION_MIN_DISTANCE = 6;
// Where the batch-connect "+" spawns its new downstream node relative to the
// selection's bounding box: this far to the right, and lifted by ~half a node so
// the fan-in lands roughly centered on the selection.
export const BATCH_CONNECT_SPAWN_GAP = 140;
export const BATCH_CONNECT_SPAWN_VERTICAL_OFFSET = 160;
export const NODE_PLACEMENT_PREVIEW_WIDTH = 320;
export const NODE_PLACEMENT_PREVIEW_HEIGHT = 200;

export interface GenerationStoryboardMetadata {
  gridRows: number;
  gridCols: number;
  frameNotes: string[];
}

export function getNodeSize(node: CanvasNode): { width: number; height: number } {
  const styleWidth = typeof node.style?.width === 'number' ? node.style.width : null;
  const styleHeight = typeof node.style?.height === 'number' ? node.style.height : null;
  return {
    width: node.measured?.width ?? styleWidth ?? DEFAULT_NODE_WIDTH,
    height: node.measured?.height ?? styleHeight ?? 200,
  };
}

// 「导演世界」源节点与它的「导演世界输出」组互为一体:拖动任意一方,另一方应按
// 相同位移联动。两者在数据上是各自独立、仅靠连线相连的节点(源节点不是组的子节点),
// 所以 React Flow 不会自动带动。这里从图里推导配对关系——兼容历史画布、无需迁移:
// 组里的 capture 子节点(带 captureMetadata)由源节点连线产出,故
// 「源节点 ←→ 子节点.parentId(组)」即为配对。返回被拖节点应联动的另一方 id。
//
// 仅限「两者都是顶层节点」的场景(原始诉求)。若被拖节点本身在某个组内(有 parentId),
// 一律不联动:否则当源节点和它的 capture 子节点同处一个组时,会把「自己的父组」当成
// partner,拖动→每帧移动父组→父组带动包括自己在内的所有子节点再移一次,双重位移互相
// 打架,表现为卡顿/重合。组内成员的整体性已由 React Flow 的父子关系保证,无需联动。
export function findLinkedCapturePartnerIds(
  draggedId: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): string[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node] as const));
  const dragged = nodeById.get(draggedId);
  if (!dragged || dragged.parentId) {
    return [];
  }

  const isCaptureChild = (node: CanvasNode | undefined): boolean =>
    Boolean(
      node?.parentId &&
        (node.data as { captureMetadata?: unknown } | undefined)?.captureMetadata,
    );

  const partners = new Set<string>();

  // 拖的是组:找出它的 capture 子节点,再回溯连到这些子节点的源节点。
  if (dragged.type === CANVAS_NODE_TYPES.group) {
    const childIds = new Set(
      nodes.filter((node) => node.parentId === draggedId && isCaptureChild(node)).map((node) => node.id),
    );
    if (childIds.size === 0) {
      return [];
    }
    for (const edge of edges) {
      if (!childIds.has(edge.target)) {
        continue;
      }
      const source = nodeById.get(edge.source);
      if (source && !source.parentId && source.id !== draggedId) {
        partners.add(source.id);
      }
    }
    return [...partners];
  }

  // 拖的是源节点:顺着连线找它产出的 capture 子节点,取它们所属的组(顶层组)。
  for (const edge of edges) {
    if (edge.source !== draggedId) {
      continue;
    }
    const target = nodeById.get(edge.target);
    if (!isCaptureChild(target) || !target?.parentId) {
      continue;
    }
    const group = nodeById.get(target.parentId);
    if (group?.type === CANVAS_NODE_TYPES.group && !group.parentId) {
      partners.add(group.id);
    }
  }
  return [...partners];
}

export function hasRectCollision(
  candidateRect: { x: number; y: number; width: number; height: number },
  nodes: CanvasNode[],
  ignoreNodeIds: Set<string>
): boolean {
  const margin = 18;
  return nodes.some((node) => {
    if (ignoreNodeIds.has(node.id)) {
      return false;
    }
    const size = getNodeSize(node);
    return (
      candidateRect.x < node.position.x + size.width + margin &&
      candidateRect.x + candidateRect.width + margin > node.position.x &&
      candidateRect.y < node.position.y + size.height + margin &&
      candidateRect.y + candidateRect.height + margin > node.position.y
    );
  });
}

export function rectsIntersect(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number }
): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

export function isCanvasPaneTarget(target: EventTarget | null, wrapperElement: HTMLElement): boolean {
  const element = target as HTMLElement | null;
  if (!element || !wrapperElement.contains(element)) {
    return false;
  }
  if (!element.closest('.react-flow__pane')) {
    return false;
  }
  return !element.closest(
    '.react-flow__node, .react-flow__edge, .react-flow__controls, .react-flow__minimap, .nodrag, .nopan, button, input, textarea, select, [role="button"]'
  );
}

export function cloneNodeData<T>(value: T): T {
  if (typeof structuredClone === 'function') {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element) {
    return false;
  }
  const tagName = element.tagName.toLowerCase();
  return (
    tagName === 'input' ||
    tagName === 'textarea' ||
    tagName === 'select' ||
    element.isContentEditable ||
    Boolean(element.closest('[role="textbox"]'))
  );
}

export function isSpacePanKey(event: KeyboardEvent): boolean {
  return event.code === PAN_ACTIVATION_KEY_CODE || event.key === ' ' || event.key === 'Spacebar';
}

export function resolveClipboardImageFile(event: ClipboardEvent): File | null {
  const clipboardItems = event.clipboardData?.items;
  if (!clipboardItems) {
    return null;
  }

  for (const item of Array.from(clipboardItems)) {
    if (!item.type.startsWith('image/')) {
      continue;
    }

    const file = item.getAsFile();
    if (!file) {
      continue;
    }

    const existingName = typeof file.name === 'string' ? file.name.trim() : '';
    if (existingName) {
      return file;
    }

    const subtype = item.type.split('/')[1]?.split('+')[0] || 'png';
    return new File([file], `pasted-image.${subtype}`, {
      type: file.type || item.type,
      lastModified: Date.now(),
    });
  }

  return null;
}

// 从一次 OS 文件拖放里挑出图片 / 视频 / 音频文件，口径见 isSupportedMediaFile
// （与 UploadNode 的媒体分流一致）。非媒体文件被忽略。
export function collectDroppedMediaFiles(dataTransfer: DataTransfer): File[] {
  const files = dataTransfer.files;
  if (!files || files.length === 0) {
    return [];
  }
  return Array.from(files).filter(isSupportedMediaFile);
}

export function resolveAllowedNodeTypes(
  handleType: HandleType,
  originNodeType?: CanvasNodeType,
): CanvasNodeType[] {
  // 拖线落空时弹出的「创建新节点」菜单，与 NodeSpawnPlusOverlay 的 + 菜单共用
  // 同一份产品白名单，两侧都住在 nodeRegistry 里（连同建边规则一起，便于测试
  // 「菜单候选 vs 建边规则」是否打架）。
  return handleType === 'source'
    ? getDownstreamSpawnTypes(originNodeType)
    : getUpstreamSpawnTypes(originNodeType);
}

// 3D 世界节点的目标允许的手动拖线源：任何可以承载图片或文本结果的节点。
export const THREE_D_WORLD_MANUAL_SOURCE_TYPES = new Set<CanvasNodeType>([
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.exportImage,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.storyboardGen,
  CANVAS_NODE_TYPES.textAnnotation,
]);

// 360° 全景查看器的下游手动连线只允许图片类节点。
export const PANO_360_DOWNSTREAM_IMAGE_TYPES = new Set<CanvasNodeType>([
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.exportImage,
]);

export function canNodeTypeBeManualConnectionSource(
  type: CanvasNodeType,
  targetType?: CanvasNodeType,
): boolean {
  if (targetType === CANVAS_NODE_TYPES.threeDWorld) {
    return THREE_D_WORLD_MANUAL_SOURCE_TYPES.has(type);
  }
  if (type === CANVAS_NODE_TYPES.pano360Viewer) {
    return targetType ? PANO_360_DOWNSTREAM_IMAGE_TYPES.has(targetType) : true;
  }
  // 受类型白名单约束的连接（音频←文本、音频→视频/视频合成）走领域层统一规则。
  // 这里查的是「手工」建边规则：视频→音频那条溯源边由「人声/背景音分离」程序建立，
  // 建边收口放行，但不该让用户自己拖出来。
  if (
    targetType &&
    (getAllowedUpstreamSourceTypes(targetType) || getAllowedDownstreamTargetTypes(type))
  ) {
    return isManualConnectionAllowed(type, targetType);
  }
  // 只要 getDownstreamSpawnTypes 给这种类型留了至少一个合法下游，就允许从右侧
  // source handle 拖线创建 —— 这样 + 菜单和拖线菜单始终对齐，新增节点类型时
  // 只需要更新 getDownstreamSpawnTypes 一处。
  return getDownstreamSpawnTypes(type).length > 0;
}

export function canNodeBeManualConnectionSource(
  nodeId: string | null | undefined,
  nodes: CanvasNode[],
  targetNodeId?: string | null | undefined,
): boolean {
  if (!nodeId) {
    return false;
  }
  const node = nodes.find((item) => item.id === nodeId);
  if (!node) return false;
  const targetType = targetNodeId
    ? nodes.find((item) => item.id === targetNodeId)?.type
    : undefined;
  return canNodeTypeBeManualConnectionSource(node.type, targetType);
}

export function getClientPosition(event: MouseEvent | TouchEvent): { x: number; y: number } | null {
  if ('clientX' in event && 'clientY' in event) {
    return { x: event.clientX, y: event.clientY };
  }

  const touch = 'changedTouches' in event
    ? event.changedTouches[0] ?? event.touches[0]
    : null;
  if (!touch) {
    return null;
  }

  return { x: touch.clientX, y: touch.clientY };
}

export function createPreviewPath(line: PreviewConnectionLine): string {
  const { start, end, handleType } = line;
  const deltaX = end.x - start.x;
  const curveStrength = Math.max(36, Math.min(120, Math.abs(deltaX) * 0.4));
  const handleDirection = handleType === 'source' ? 1 : -1;
  const isReverseDrag = deltaX * handleDirection < 0;
  const effectiveDirection = isReverseDrag ? -handleDirection : handleDirection;
  const startControlX = start.x + effectiveDirection * curveStrength;
  const endControlX = end.x - effectiveDirection * curveStrength;

  return `M ${start.x} ${start.y} C ${startControlX} ${start.y}, ${endControlX} ${end.y}, ${end.x} ${end.y}`;
}

export function cssEscape(value: string): string {
  return typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
    ? CSS.escape(value)
    : value.replace(/["\\]/g, '\\$&');
}

export function handleIdFromElement(
  element: Element | null | undefined,
  nodeId: string,
  handleType: HandleType,
): string | null {
  const handleElement = element?.closest?.('.react-flow__handle') as HTMLElement | null;
  if (!handleElement) return null;
  if (handleElement.dataset.nodeid !== nodeId) return null;
  if (!handleElement.classList.contains(handleType)) return null;
  const handleId = handleElement.dataset.handleid;
  return typeof handleId === 'string' && handleId.trim() ? handleId.trim() : null;
}

export function isVisibleConnectionHandle(element: HTMLElement): boolean {
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  if (
    element.classList.contains('!pointer-events-none')
    || element.classList.contains('opacity-0')
    || element.classList.contains('!opacity-0')
  ) {
    return false;
  }
  const style = window.getComputedStyle(element);
  return style.pointerEvents !== 'none' && style.opacity !== '0' && style.display !== 'none';
}

export function nearestHandleIdAtPoint({
  nodeElement,
  nodeId,
  handleType,
  clientPosition,
  maxDistance = 28,
}: {
  nodeElement: HTMLElement | null | undefined;
  nodeId: string;
  handleType: HandleType;
  clientPosition: { x: number; y: number };
  maxDistance?: number;
}): string | null {
  if (!nodeElement) return null;
  let best: { id: string; distance: number } | null = null;
  const handles = Array.from(
    nodeElement.querySelectorAll<HTMLElement>('.react-flow__handle'),
  );
  for (const handle of handles) {
    if (handle.dataset.nodeid !== nodeId || !handle.classList.contains(handleType)) {
      continue;
    }
    if (!isVisibleConnectionHandle(handle)) {
      continue;
    }
    const handleId = handle.dataset.handleid;
    if (!handleId) {
      continue;
    }
    const rect = handle.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const distance = Math.hypot(centerX - clientPosition.x, centerY - clientPosition.y);
    if (distance <= maxDistance && (!best || distance < best.distance)) {
      best = { id: handleId, distance };
    }
  }
  return best?.id ?? null;
}

export function resolveConnectEndHandleId({
  eventTarget,
  nodeElement,
  nodeId,
  handleType,
  clientPosition,
}: {
  eventTarget: Element | null;
  nodeElement: HTMLElement | null | undefined;
  nodeId: string;
  handleType: HandleType;
  clientPosition: { x: number; y: number };
}): string | null {
  return (
    handleIdFromElement(eventTarget, nodeId, handleType) ??
    handleIdFromElement(document.elementFromPoint(clientPosition.x, clientPosition.y), nodeId, handleType) ??
    nearestHandleIdAtPoint({
      nodeElement,
      nodeId,
      handleType,
      clientPosition,
    })
  );
}

export interface PreviewConnectionLine {
  start: { x: number; y: number };
  end: { x: number; y: number };
  handleType: HandleType;
}

export interface PlusConnectDragParams {
  nodeId: string;
  handleType: HandleType;
  clientPosition: { x: number; y: number };
}

export interface PendingNodePlacement {
  type: CanvasNodeType;
  initialData?: Partial<Record<string, unknown>>;
  skill?: SkillDefinition;
}

export interface CanvasProps {
  onBlankPaneClick?: () => void;
  controlsPlacement?: 'bottom-right' | 'top-right';
}
