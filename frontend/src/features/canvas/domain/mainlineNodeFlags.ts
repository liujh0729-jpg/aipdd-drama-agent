// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Per-node derived flags for "mainline behavior" — the 5 orthogonal questions
 * that drive visual, toolbar, and Push behavior:
 *
 *   isPresetManaged  — Is this node owned by the backend preset emit? (拓扑锁)
 *   isUserSpawned    — Did the user create this (drag/spawn/duplicate)? (可改可删)
 *   hasMainlineContext — Carries `mainline_context` array? (provenance / legacy)
 *   hasSlotTarget    — Carries Push default target? (Push 默认目标已知)
 *   hasCommittedSlot — Carries canonical slot URL? (canonical provenance)
 *
 * The legacy workflow-kind switch is intentionally absent. Visual / toolbar
 * logic should compose these flags directly.
 *
 * These flags are *derived* from `node.data` — never stored. Source of truth:
 *   data.preset_managed: true | false | undefined
 *   data.user_spawned: true | false | undefined
 *   data.mainline_context: MainlineContext[]
 *   data.slot_target: SlotTarget | undefined
 *   data.committed_slot_url: string | undefined
 *   data.committed_at: string | undefined
 */

import type { CanvasEdge, CanvasNode } from "./canvasNodes.ts";
import { isSlotTarget } from "./mainlineNodeTypes.ts";

export interface MainlineNodeFlags {
  isPresetManaged: boolean;
  isUserSpawned: boolean;
  hasMainlineContext: boolean;
  hasSlotTarget: boolean;
  hasCommittedSlot: boolean;
  hasCommittedAt: boolean;
}

function isNoReferenceValue(value: unknown): boolean {
  return value === "__NO_CHARACTER__" || value === "__NO_PROP__";
}

function isNoReferenceNodeData(data: unknown): boolean {
  if (!data || typeof data !== "object") {
    return false;
  }
  const value = data as {
    label?: unknown;
    displayName?: unknown;
    content?: unknown;
    prompt?: unknown;
    reference_target?: unknown;
    __freezone_source?: unknown;
  };
  if (
    isNoReferenceValue(value.label) ||
    isNoReferenceValue(value.displayName) ||
    isNoReferenceValue(value.content) ||
    isNoReferenceValue(value.prompt)
  ) {
    return true;
  }
  const referenceTarget =
    value.reference_target &&
    typeof value.reference_target === "object" &&
    !Array.isArray(value.reference_target)
      ? (value.reference_target as Record<string, unknown>)
      : null;
  if (
    referenceTarget?.identity_id === "__NO_CHARACTER__" ||
    referenceTarget?.prop_id === "__NO_PROP__"
  ) {
    return true;
  }
  const freezoneSource =
    value.__freezone_source &&
    typeof value.__freezone_source === "object" &&
    !Array.isArray(value.__freezone_source)
      ? (value.__freezone_source as Record<string, unknown>)
      : null;
  const meta =
    freezoneSource?.meta &&
    typeof freezoneSource.meta === "object" &&
    !Array.isArray(freezoneSource.meta)
      ? (freezoneSource.meta as Record<string, unknown>)
      : null;
  return meta?.identity_id === "__NO_CHARACTER__" || meta?.prop_id === "__NO_PROP__";
}

/**
 * Compute mainline flags for a single node. Pure, side-effect free, cheap —
 * safe to call inline in render. No caching needed; flat field reads.
 */
export function nodeMainlineFlags(node: CanvasNode): MainlineNodeFlags {
  // data is typed as a discriminated union of node datas; the mainline fields
  // are optional extras (allowed by the `[key: string]: unknown` index sig on
  // each data type). Read with `as` to avoid leaking the union shape.
  const data = node.data as {
    preset_managed?: unknown;
    user_spawned?: unknown;
    mainline_context?: unknown;
    slot_target?: unknown;
    committed_slot_url?: unknown;
    committed_at?: unknown;
  };
  return {
    isPresetManaged: data.preset_managed === true,
    isUserSpawned: data.user_spawned === true,
    hasMainlineContext:
      Array.isArray(data.mainline_context) && data.mainline_context.length > 0,
    hasSlotTarget: isSlotTarget(data.slot_target),
    hasCommittedSlot:
      typeof data.committed_slot_url === "string" &&
      data.committed_slot_url.length > 0,
    hasCommittedAt:
      typeof data.committed_at === "string" && data.committed_at.length > 0,
  };
}

export function isPresetManagedNode(node: CanvasNode): boolean {
  if (isNoReferenceNodeData(node.data)) {
    return false;
  }
  return nodeMainlineFlags(node).isPresetManaged;
}

export function isSystemManagedNodeData(data: unknown): boolean {
  if (!data || typeof data !== "object") {
    return false;
  }
  const value = data as {
    preset_managed?: unknown;
    projection_key?: unknown;
    user_spawned?: unknown;
  };
  if (isNoReferenceNodeData(data)) {
    return false;
  }
  if (value.user_spawned === true) {
    return false;
  }
  return (
    value.preset_managed === true ||
    (typeof value.projection_key === "string" &&
      value.projection_key.trim().length > 0)
  );
}

export function isPresetManagedEdge(edge: CanvasEdge): boolean {
  const data = edge.data as {
    preset_managed?: unknown;
    projection_key?: unknown;
    user_spawned?: unknown;
    reference_target?: unknown;
  } | undefined;
  const targetHandle =
    typeof edge.targetHandle === "string" && edge.targetHandle.trim()
      ? edge.targetHandle.trim()
      : "";
  const referenceTarget =
    data?.reference_target &&
    typeof data.reference_target === "object" &&
    !Array.isArray(data.reference_target)
      ? (data.reference_target as Record<string, unknown>)
      : null;
  if (
    targetHandle === "identity:__NO_CHARACTER__" ||
    targetHandle === "prop:__NO_PROP__" ||
    referenceTarget?.identity_id === "__NO_CHARACTER__" ||
    referenceTarget?.prop_id === "__NO_PROP__"
  ) {
    return false;
  }
  if (data?.user_spawned === true) {
    return false;
  }
  return (
    data?.preset_managed === true ||
    (typeof data?.projection_key === "string" &&
      data.projection_key.trim().length > 0)
  );
}

/**
 * Visual state classification — drives the 4 badge / border styles documented
 * in the plan. Returns a single label so render code stays a thin switch.
 *
 *   "preset_locked"    — preset_managed === true
 *   "candidate_pushable" — user_spawned + hasSlotTarget + not committed_at
 *   "context_only"     — hasMainlineContext (no slot_target)
 *   "ordinary"         — none of the above
 *
 * Note: preset_managed wins over hasSlotTarget when both true (preset emit
 * may set slot_target on its canonical nodes — those are still locked, not
 * "candidate"). user_spawned wins over context_only when slot_target present
 * until the candidate has been pushed.
 */
export type MainlineNodeVisualState =
  | "preset_locked"
  | "candidate_pushable"
  | "context_only"
  | "ordinary";

export function mainlineNodeVisualState(
  flags: MainlineNodeFlags,
): MainlineNodeVisualState {
  if (flags.isPresetManaged) return "preset_locked";
  if (flags.isUserSpawned && flags.hasSlotTarget && !flags.hasCommittedAt) {
    return "candidate_pushable";
  }
  if (flags.hasMainlineContext) return "context_only";
  return "ordinary";
}
