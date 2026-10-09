// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  isStaleByTtl,
  pruneLocalStorageByPrefix,
  registerStorageReclaimer,
  safeLocalStorageSet,
} from "@/lib/localStorageQuota";
import type {
  CanvasEdge,
  CanvasHistoryState,
  CanvasMutationSource,
  CanvasNode,
} from "@/stores/canvasStore";

export const CANVAS_DRAFT_MAX_BYTES = 1_500_000;
const CANVAS_DRAFT_VERSION = 1;
const CANVAS_DRAFT_PREFIX = "supertale-freezone:canvas-draft:";
const CANVAS_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

// Sibling per-canvas keys written elsewhere (useCanvasSync). They share the
// draft's lifecycle/TTL so none of them can pile up unbounded and exhaust the
// origin's localStorage quota. Kept here (not imported from useCanvasSync) so
// the prune stays free of React/hook imports.
const CANVAS_HISTORY_PREFIX = "freezone:canvas-history:";
const CANVAS_CONFLICT_PREFIX = "freezone:conflict:";
const CANVAS_VIEWPORT_PREFIX = "freezone:canvas-viewport:";
// Unified time-to-live for every per-canvas key.
export const FREEZONE_CANVAS_TTL_MS = CANVAS_DRAFT_TTL_MS;

export interface CanvasDraftMutationState {
  userEditsSinceHydrate: number;
  lastMutationSource: CanvasMutationSource | null;
  pendingClearIntent: boolean;
}

export interface CanvasDraftInput {
  baseRevision: number | null;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  viewport: unknown;
  metadata: Record<string, unknown> | null;
  history: CanvasHistoryState | null;
  mutation: CanvasDraftMutationState;
  updatedAt: number;
}

export interface StoredCanvasDraft extends CanvasDraftInput {
  version: typeof CANVAS_DRAFT_VERSION;
  project: string;
  canvasId: string;
  signature: string;
}

function draftStorageKey(project: string, canvasId: string): string {
  return `${CANVAS_DRAFT_PREFIX}${encodeURIComponent(project)}:${encodeURIComponent(canvasId)}`;
}

function stableCanvasShape(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  metadata: Record<string, unknown> | null,
): unknown {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.type,
      position: node.position,
      width: node.width,
      height: node.height,
      style: node.style,
      parentId: node.parentId,
      extent: node.extent,
      data: node.data,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      type: edge.type,
      data: edge.data,
    })),
    metadata: metadata ?? null,
  };
}

export function canvasDraftSignature(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  metadata: Record<string, unknown> | null,
): string {
  return stableStringify(stableCanvasShape(nodes, edges, metadata));
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortJsonValue(value));
}

function sortJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJsonValue);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const input = value as Record<string, unknown>;
  const output: Record<string, unknown> = {};
  for (const key of Object.keys(input).sort()) {
    const item = input[key];
    if (item !== undefined) {
      output[key] = sortJsonValue(item);
    }
  }
  return output;
}

function isMutationState(value: unknown): value is CanvasDraftMutationState {
  if (!value || typeof value !== "object") return false;
  const mutation = value as Partial<CanvasDraftMutationState>;
  return (
    typeof mutation.userEditsSinceHydrate === "number" &&
    (mutation.lastMutationSource === null ||
      mutation.lastMutationSource === "user_edit" ||
      mutation.lastMutationSource === "delete_to_empty" ||
      mutation.lastMutationSource === "manual_clear") &&
    typeof mutation.pendingClearIntent === "boolean"
  );
}

function parseStoredDraft(
  value: unknown,
  project: string,
  canvasId: string,
): StoredCanvasDraft | null {
  if (!value || typeof value !== "object") return null;
  const draft = value as Partial<StoredCanvasDraft>;
  if (
    draft.version !== CANVAS_DRAFT_VERSION ||
    draft.project !== project ||
    draft.canvasId !== canvasId ||
    !Array.isArray(draft.nodes) ||
    !Array.isArray(draft.edges) ||
    typeof draft.signature !== "string" ||
    typeof draft.updatedAt !== "number" ||
    !(typeof draft.baseRevision === "number" || draft.baseRevision === null) ||
    !isMutationState(draft.mutation)
  ) {
    return null;
  }
  const history =
    draft.history &&
    typeof draft.history === "object" &&
    Array.isArray((draft.history as Partial<CanvasHistoryState>).past) &&
    Array.isArray((draft.history as Partial<CanvasHistoryState>).future)
      ? (draft.history as CanvasHistoryState)
      : null;
  return {
    version: CANVAS_DRAFT_VERSION,
    project,
    canvasId,
    baseRevision: draft.baseRevision,
    nodes: draft.nodes as CanvasNode[],
    edges: draft.edges as CanvasEdge[],
    viewport: draft.viewport ?? null,
    metadata: (draft.metadata as Record<string, unknown> | null) ?? null,
    history,
    mutation: draft.mutation,
    updatedAt: draft.updatedAt,
    signature: draft.signature,
  };
}

export function readCanvasDraft(
  project: string,
  canvasId: string,
): StoredCanvasDraft | null {
  try {
    const raw = localStorage.getItem(draftStorageKey(project, canvasId));
    if (!raw) return null;
    return parseStoredDraft(JSON.parse(raw) as unknown, project, canvasId);
  } catch {
    return null;
  }
}

export function clearCanvasDraft(project: string, canvasId: string): void {
  try {
    localStorage.removeItem(draftStorageKey(project, canvasId));
  } catch {
    // Best-effort cleanup.
  }
}

export function writeCanvasDraft(
  project: string,
  canvasId: string,
  input: CanvasDraftInput,
): boolean {
  const draft: StoredCanvasDraft = {
    version: CANVAS_DRAFT_VERSION,
    project,
    canvasId,
    ...input,
    signature: canvasDraftSignature(input.nodes, input.edges, input.metadata),
  };
  const withoutHistory: StoredCanvasDraft = { ...draft, history: null };

  const key = draftStorageKey(project, canvasId);
  try {
    const serialized = JSON.stringify(draft);
    if (serialized.length <= CANVAS_DRAFT_MAX_BYTES) {
      if (safeLocalStorageSet(key, serialized)) {
        return true;
      }
    }
  } catch {
    // Fall through and try the no-history draft.
  }

  try {
    const serialized = JSON.stringify(withoutHistory);
    if (serialized.length <= CANVAS_DRAFT_MAX_BYTES && safeLocalStorageSet(key, serialized)) {
      return true;
    }
    clearCanvasDraft(project, canvasId);
    return false;
  } catch {
    clearCanvasDraft(project, canvasId);
    return false;
  }
}

function isFreezoneCanvasKey(key: string): boolean {
  return (
    key.startsWith(CANVAS_DRAFT_PREFIX) ||
    key.startsWith(CANVAS_HISTORY_PREFIX) ||
    key.startsWith(CANVAS_CONFLICT_PREFIX) ||
    key.startsWith(CANVAS_VIEWPORT_PREFIX)
  );
}

// Extract the epoch-ms freshness stamp for a parsed per-canvas entry, or null
// when the entry carries no usable timestamp (stale/foreign schema → reclaim).
function freezoneEntryTimestamp(key: string, parsed: unknown): number | null {
  if (!parsed || typeof parsed !== "object") return null;
  if (key.startsWith(CANVAS_CONFLICT_PREFIX)) {
    const ts = (parsed as { timestamp?: unknown }).timestamp;
    if (typeof ts !== "string") return null;
    const ms = Date.parse(ts);
    return Number.isNaN(ms) ? null : ms;
  }
  const updatedAt = (parsed as { updatedAt?: unknown }).updatedAt;
  return typeof updatedAt === "number" ? updatedAt : null;
}

function shouldReclaimFreezoneEntry(key: string, raw: string, now: number): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return true; // malformed → reclaim
  }
  // Viewport blobs are raw `{x,y,zoom}` with no timestamp and are tiny; only
  // reclaim them when the JSON is unusable, never on age.
  if (key.startsWith(CANVAS_VIEWPORT_PREFIX)) {
    return !parsed || typeof parsed !== "object";
  }
  const updatedAt = freezoneEntryTimestamp(key, parsed);
  if (updatedAt == null) return true; // no usable timestamp → stale schema
  return isStaleByTtl(updatedAt, now, FREEZONE_CANVAS_TTL_MS);
}

/**
 * Sweep every per-canvas freezone localStorage key (draft, undo history,
 * conflict snapshot, viewport) and drop the ones that are malformed or older
 * than the unified TTL. This is the garbage collector that keeps any single
 * key class — most importantly `freezone:canvas-history:*` — from being kept
 * around forever and eventually blowing the storage quota.
 */
export function pruneFreezoneCanvasStorage(now = Date.now()): void {
  pruneLocalStorageByPrefix(isFreezoneCanvasKey, (key, raw) =>
    shouldReclaimFreezoneEntry(key, raw, now),
  );
}

/** @deprecated Back-compat alias — use {@link pruneFreezoneCanvasStorage}. */
export function pruneOldCanvasDrafts(now = Date.now()): void {
  pruneFreezoneCanvasStorage(now);
}

// Register the prune as a quota reclaimer at import time so that a write under
// quota pressure anywhere (including the small `settings-storage` blob) frees
// stale canvas keys and retries. Importing this module for its side effect is
// enough to wire it up.
registerStorageReclaimer(() => {
  pruneFreezoneCanvasStorage();
});
