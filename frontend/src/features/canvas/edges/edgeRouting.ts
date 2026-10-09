// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Position } from '@xyflow/react';

import { DEFAULT_NODE_WIDTH, type CanvasNode } from '@/features/canvas/domain/canvasNodes';

interface Point {
  x: number;
  y: number;
}

interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface RouteResult {
  path: string;
  labelX: number;
  labelY: number;
}

interface BuildOrthogonalRouteInput {
  sourceId?: string;
  targetId?: string;
  sourceX: number;
  sourceY: number;
  sourcePosition: Position;
  targetX: number;
  targetY: number;
  targetPosition: Position;
  nodes: CanvasNode[];
  smartAvoidance: boolean;
}

const DEFAULT_NODE_HEIGHT = 200;
const EXPANDED_NODE_PADDING = 14;
const ENTRY_OFFSET = 24;
const LANE_GAP = 20;
const EPS = 0.0001;

function getOutDirection(position: Position, fallbackSign: number): number {
  if (position === Position.Right) {
    return 1;
  }
  if (position === Position.Left) {
    return -1;
  }
  return fallbackSign >= 0 ? 1 : -1;
}

function getInDirection(position: Position, fallbackSign: number): number {
  if (position === Position.Left) {
    return -1;
  }
  if (position === Position.Right) {
    return 1;
  }
  return fallbackSign <= 0 ? -1 : 1;
}

function nodeToRect(node: CanvasNode): Rect {
  const width =
    node.measured?.width ??
    (typeof node.style?.width === 'number' ? node.style.width : null) ??
    DEFAULT_NODE_WIDTH;
  const height =
    node.measured?.height ??
    (typeof node.style?.height === 'number' ? node.style.height : null) ??
    DEFAULT_NODE_HEIGHT;
  return {
    left: node.position.x - EXPANDED_NODE_PADDING,
    top: node.position.y - EXPANDED_NODE_PADDING,
    right: node.position.x + width + EXPANDED_NODE_PADDING,
    bottom: node.position.y + height + EXPANDED_NODE_PADDING,
  };
}

function buildRectangles(nodes: CanvasNode[], sourceId?: string, targetId?: string): Rect[] {
  return nodes
    .filter((node) => node.id !== sourceId && node.id !== targetId)
    .map(nodeToRect);
}

function verticalIntersectsRect(x: number, y1: number, y2: number, rect: Rect): boolean {
  if (x <= rect.left + EPS || x >= rect.right - EPS) {
    return false;
  }
  const top = Math.min(y1, y2);
  const bottom = Math.max(y1, y2);
  return bottom > rect.top + EPS && top < rect.bottom - EPS;
}

function horizontalIntersectsRect(y: number, x1: number, x2: number, rect: Rect): boolean {
  if (y <= rect.top + EPS || y >= rect.bottom - EPS) {
    return false;
  }
  const left = Math.min(x1, x2);
  const right = Math.max(x1, x2);
  return right > rect.left + EPS && left < rect.right - EPS;
}

function polylineIntersectsAnyRect(points: Point[], rects: Rect[]): boolean {
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const isVertical = Math.abs(from.x - to.x) < EPS;

    for (const rect of rects) {
      if (isVertical) {
        if (verticalIntersectsRect(from.x, from.y, to.y, rect)) {
          return true;
        }
      } else if (Math.abs(from.y - to.y) < EPS) {
        if (horizontalIntersectsRect(from.y, from.x, to.x, rect)) {
          return true;
        }
      }
    }
  }
  return false;
}

function getMidpoint(points: Point[]): Point {
  let totalLength = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    totalLength += Math.hypot(to.x - from.x, to.y - from.y);
  }

  if (totalLength < EPS) {
    return points[0] ?? { x: 0, y: 0 };
  }

  let traversed = 0;
  const half = totalLength / 2;
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const segmentLength = Math.hypot(to.x - from.x, to.y - from.y);
    if (traversed + segmentLength >= half) {
      const ratio = (half - traversed) / segmentLength;
      return {
        x: from.x + (to.x - from.x) * ratio,
        y: from.y + (to.y - from.y) * ratio,
      };
    }
    traversed += segmentLength;
  }

  return points[points.length - 1] ?? { x: 0, y: 0 };
}

function toSvgPath(points: Point[]): string {
  if (points.length === 0) {
    return '';
  }
  const [first, ...rest] = points;
  return `M ${first.x} ${first.y} ${rest.map((point) => `L ${point.x} ${point.y}`).join(' ')}`;
}

function buildPointsForLane(
  sourceX: number,
  sourceY: number,
  sourceOutX: number,
  targetX: number,
  targetY: number,
  targetInX: number,
  laneY: number
): Point[] {
  return [
    { x: sourceX, y: sourceY },
    { x: sourceOutX, y: sourceY },
    { x: sourceOutX, y: laneY },
    { x: targetInX, y: laneY },
    { x: targetInX, y: targetY },
    { x: targetX, y: targetY },
  ];
}

function candidatePenalty(laneY: number, sourceY: number, targetY: number): number {
  const midY = (sourceY + targetY) / 2;
  return (
    Math.abs(laneY - midY) * 0.45 +
    Math.abs(laneY - sourceY) * 0.3 +
    Math.abs(laneY - targetY) * 0.25
  );
}

function pickLaneY(
  sourceX: number,
  sourceY: number,
  sourceOutX: number,
  targetX: number,
  targetY: number,
  targetInX: number,
  rects: Rect[]
): number {
  const minX = Math.min(sourceOutX, targetInX, sourceX, targetX);
  const maxX = Math.max(sourceOutX, targetInX, sourceX, targetX);
  const candidates = new Set<number>([sourceY, targetY, (sourceY + targetY) / 2]);

  for (const rect of rects) {
    if (rect.right < minX || rect.left > maxX) {
      continue;
    }
    candidates.add(rect.top - LANE_GAP);
    candidates.add(rect.bottom + LANE_GAP);
  }

  const sorted = Array.from(candidates).sort(
    (left, right) => candidatePenalty(left, sourceY, targetY) - candidatePenalty(right, sourceY, targetY)
  );

  for (const laneY of sorted) {
    const points = buildPointsForLane(sourceX, sourceY, sourceOutX, targetX, targetY, targetInX, laneY);
    if (!polylineIntersectsAnyRect(points, rects)) {
      return laneY;
    }
  }

  const upperBound = rects.length > 0 ? Math.min(...rects.map((rect) => rect.top)) - LANE_GAP : sourceY - 80;
  const lowerBound =
    rects.length > 0 ? Math.max(...rects.map((rect) => rect.bottom)) + LANE_GAP : targetY + 80;
  return candidatePenalty(upperBound, sourceY, targetY) <= candidatePenalty(lowerBound, sourceY, targetY)
    ? upperBound
    : lowerBound;
}

export function buildOrthogonalRoute(input: BuildOrthogonalRouteInput): RouteResult {
  const {
    sourceId,
    targetId,
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    nodes,
    smartAvoidance,
  } = input;

  const horizontalSign = targetX - sourceX >= 0 ? 1 : -1;
  const sourceOutDirection = getOutDirection(sourcePosition, horizontalSign);
  const targetInDirection = getInDirection(targetPosition, horizontalSign);
  const sourceOutX = sourceX + sourceOutDirection * ENTRY_OFFSET;
  const targetInX = targetX + targetInDirection * ENTRY_OFFSET;

  let laneY = (sourceY + targetY) / 2;
  if (smartAvoidance) {
    const rects = buildRectangles(nodes, sourceId, targetId);
    laneY = pickLaneY(sourceX, sourceY, sourceOutX, targetX, targetY, targetInX, rects);
  }

  const points = buildPointsForLane(sourceX, sourceY, sourceOutX, targetX, targetY, targetInX, laneY);
  const midpoint = getMidpoint(points);
  return {
    path: toSvgPath(points),
    labelX: midpoint.x,
    labelY: midpoint.y,
  };
}
