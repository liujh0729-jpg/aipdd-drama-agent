// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 「俯视图（机位走位图）」的场景数据模型（无 React）。
 *
 * 设计空间是固定像素坐标（默认 1280×1280 正方形），编辑器用 SVG 渲染，导出时按同一
 * 坐标栅格化。整个结构是纯 JSON、可直接塞进画布节点的 `data.draftScene`（对 store 透明，
 * 口径同 compose 的 `draftTimeline`）。
 */

export const TOPDOWN_SCENE_VERSION = 1 as const;
export const TOPDOWN_DESIGN_SIZE = 1280;

/** 走位箭头 / 人物默认配色（影视 blocking 常见：红=主角走位、蓝=次角、黄=镜头运动）。 */
export const TOPDOWN_PALETTE = [
  '#e23b3b',
  '#2f6fd0',
  '#e0a800',
  '#2e9e5b',
  '#8a4fd0',
  '#111111',
] as const;

export interface TopDownPoint {
  x: number;
  y: number;
}

export type TopDownPropShape =
  | 'sofa'
  | 'table'
  | 'tv'
  | 'door'
  | 'window'
  | 'stairs'
  | 'rect';

export interface TopDownPersonItem {
  id: string;
  kind: 'person';
  x: number;
  y: number;
  label: string;
  color: string;
}

export interface TopDownCameraItem {
  id: string;
  kind: 'camera';
  x: number;
  y: number;
  label: string;
  /** 机位朝向（角度制，0=正右、顺时针）。编辑器里可拖旋转手柄调整。 */
  angleDeg: number;
}

export interface TopDownPropItem {
  id: string;
  kind: 'prop';
  x: number;
  y: number;
  w: number;
  h: number;
  shape: TopDownPropShape;
  label?: string;
}

export interface TopDownArrowItem {
  id: string;
  kind: 'arrow';
  /** 折线顶点（≥2 个），末端画箭头。 */
  points: TopDownPoint[];
  color: string;
  label?: string;
}

export interface TopDownTextItem {
  id: string;
  kind: 'text';
  x: number;
  y: number;
  text: string;
  color: string;
}

export type TopDownItem =
  | TopDownPersonItem
  | TopDownCameraItem
  | TopDownPropItem
  | TopDownArrowItem
  | TopDownTextItem;

export type TopDownItemKind = TopDownItem['kind'];

export type TopDownBackground =
  | { kind: 'none' }
  | { kind: 'image'; url: string; opacity?: number };

export interface TopDownScene {
  version: typeof TOPDOWN_SCENE_VERSION;
  /** 设计空间边长（正方形），导出画布尺寸。 */
  size: number;
  background: TopDownBackground;
  items: TopDownItem[];
}

export function createEmptyScene(): TopDownScene {
  return {
    version: TOPDOWN_SCENE_VERSION,
    size: TOPDOWN_DESIGN_SIZE,
    background: { kind: 'none' },
    items: [],
  };
}

let idSeq = 0;
/** 生成元素 id（前端本地唯一即可，落库后不跨端比较）。 */
export function nextItemId(prefix = 'it'): string {
  idSeq += 1;
  return `${prefix}-${Date.now().toString(36)}-${idSeq.toString(36)}`;
}

const num = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

const str = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback;

function normalizePoint(value: unknown): TopDownPoint {
  const obj = (value ?? {}) as Record<string, unknown>;
  return { x: num(obj.x, 0), y: num(obj.y, 0) };
}

function normalizeItem(value: unknown): TopDownItem | null {
  const obj = (value ?? {}) as Record<string, unknown>;
  const id = str(obj.id) || nextItemId();
  switch (obj.kind) {
    case 'person':
      return {
        id,
        kind: 'person',
        x: num(obj.x, 0),
        y: num(obj.y, 0),
        label: str(obj.label),
        color: str(obj.color, TOPDOWN_PALETTE[0]),
      };
    case 'camera':
      return {
        id,
        kind: 'camera',
        x: num(obj.x, 0),
        y: num(obj.y, 0),
        label: str(obj.label, 'Cam'),
        angleDeg: num(obj.angleDeg, 0),
      };
    case 'prop':
      return {
        id,
        kind: 'prop',
        x: num(obj.x, 0),
        y: num(obj.y, 0),
        w: num(obj.w, 120),
        h: num(obj.h, 80),
        shape: (str(obj.shape, 'rect') as TopDownPropShape) || 'rect',
        label: obj.label === undefined ? undefined : str(obj.label),
      };
    case 'arrow': {
      const points = Array.isArray(obj.points) ? obj.points.map(normalizePoint) : [];
      if (points.length < 2) return null;
      return {
        id,
        kind: 'arrow',
        points,
        color: str(obj.color, TOPDOWN_PALETTE[0]),
        label: obj.label === undefined ? undefined : str(obj.label),
      };
    }
    case 'text':
      return {
        id,
        kind: 'text',
        x: num(obj.x, 0),
        y: num(obj.y, 0),
        text: str(obj.text),
        color: str(obj.color, TOPDOWN_PALETTE[5]),
      };
    default:
      return null;
  }
}

/**
 * 把节点里存的 unknown 草稿规整成合法场景：字段缺失 / 类型漂移 / 脏元素一律兜底或丢弃，
 * 保证编辑器与渲染器拿到的永远是结构完整的 {@link TopDownScene}。
 */
export function decodeScene(raw: unknown): TopDownScene {
  if (!raw || typeof raw !== 'object') {
    return createEmptyScene();
  }
  const obj = raw as Record<string, unknown>;
  const items = Array.isArray(obj.items)
    ? obj.items
        .map(normalizeItem)
        .filter((item): item is TopDownItem => item !== null)
    : [];
  const bg = (obj.background ?? {}) as Record<string, unknown>;
  const background: TopDownBackground =
    bg.kind === 'image' && typeof bg.url === 'string' && bg.url.length > 0
      ? { kind: 'image', url: bg.url, opacity: num(bg.opacity, 1) }
      : { kind: 'none' };
  return {
    version: TOPDOWN_SCENE_VERSION,
    size: num(obj.size, TOPDOWN_DESIGN_SIZE),
    background,
    items,
  };
}

/** 序列化成可直接写进节点 data 的 JSON（当前结构已是纯 JSON，保留同名出口对齐 compose 口径）。 */
export function encodeScene(scene: TopDownScene): unknown {
  return scene;
}

/** 元素边界框中心（供拖拽命中/属性条定位用）。 */
export function itemCenter(item: TopDownItem): TopDownPoint {
  if (item.kind === 'arrow') {
    const first = item.points[0] ?? { x: 0, y: 0 };
    const last = item.points[item.points.length - 1] ?? first;
    return { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 };
  }
  if (item.kind === 'prop') {
    return { x: item.x + item.w / 2, y: item.y + item.h / 2 };
  }
  return { x: item.x, y: item.y };
}
