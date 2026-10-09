// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Single source of truth for "child node inherits parent's mainline fields".
 *
 * Any path that creates a new node from an existing one MUST use this helper:
 *   - edit overlay spawn (LightEditor / MultiAngle / Outpaint / Redraw / Crop)
 *   - duplicate node action
 *   - node derivation from an existing source node
 *   - history materialization from an existing record
 *   - any future toolbar/derivation entry point
 *
 * Why centralize: per the review, `user_spawned: true` cannot be sprinkled
 * across every spawn site — copy/paste, duplicate-count, drag-drop, restore
 * each introduce drift. One helper ⇒ one invariant.
 *
 * Invariants enforced:
 *   - child always gets `user_spawned: true`
 *   - child NEVER gets `preset_managed: true` (preset_managed is owned by
 *     backend preset emit only)
 *   - `mainline_context` is propagated when source has it (provenance and
 *     legacy context display stay attached to derivatives)
 *   - `committed_slot_url` is propagated when source has it (the child keeps a
 *     stable pointer to the canonical slot it was derived from)
 *   - `slot_target` is propagated by default (1→1 edits "stay on the same
 *     slot"); callers can opt out via `inheritSlotTarget: false` for
 *     exploration-style flows where the user must re-select target before
 *     Push (currently no caller uses this — multi-dim users confirmed all N
 *     children inherit the same slot_target).
 */

import type { MainlineContext } from "@/features/freezone/context/mainlineContext";
import type { SlotTarget } from "@/features/canvas/domain/mainlineNodeTypes";

/** Read shape — what we look for on the source node's data. */
export interface MainlineFieldsSource {
  mainline_context?: MainlineContext[] | undefined;
  slot_target?: SlotTarget | undefined;
  committed_slot_url?: string | undefined;
  projection_key?: string | undefined;
}

/** Write shape — what we add into the child node's data. */
export interface InheritedMainlineFields {
  user_spawned: true;
  mainline_context?: MainlineContext[];
  slot_target?: SlotTarget;
  committed_slot_url?: string;
  source_projection_key?: string;
}

export interface InheritOptions {
  /** Default true — child inherits source's slot_target. */
  inheritSlotTarget?: boolean;
}

/**
 * Merge `childPatch` with inherited mainline fields. Returns a new object —
 * `childPatch` is not mutated. The result is shaped to be spread directly into
 * `addNode(type, position, { ...result, ...rest })` or `updateNodeData`.
 *
 * Generic so callers retain their data-type narrowing:
 *
 *   const data = inheritMainlineFields(source, { displayName, imageUrl: null });
 *   addNode(CANVAS_NODE_TYPES.exportImage, pos, data);
 */
export function inheritMainlineFields<T extends Record<string, unknown>>(
  source: { data: MainlineFieldsSource } | null | undefined,
  childPatch: T,
  options: InheritOptions = {},
): T & InheritedMainlineFields {
  const { inheritSlotTarget = true } = options;
  // Start from a shallow copy so caller's patch wins for any non-mainline key.
  const out: Record<string, unknown> = { ...childPatch };
  const childProjectionKey =
    typeof out.projection_key === "string" && out.projection_key.length > 0
      ? out.projection_key
      : undefined;

  // Always mark user_spawned. Never touch preset_managed (must stay falsy on
  // anything we spawn).
  out.user_spawned = true;
  delete out.preset_managed;
  delete out.projection_key;
  if (childProjectionKey) {
    out.source_projection_key = childProjectionKey;
  }

  const srcData = source?.data;
  if (srcData) {
    if (
      !out.source_projection_key &&
      typeof srcData.projection_key === "string" &&
      srcData.projection_key.length > 0
    ) {
      out.source_projection_key = srcData.projection_key;
    }
    if (Array.isArray(srcData.mainline_context) && srcData.mainline_context.length > 0) {
      out.mainline_context = srcData.mainline_context;
    }
    if (inheritSlotTarget && srcData.slot_target) {
      out.slot_target = srcData.slot_target;
    }
    if (typeof srcData.committed_slot_url === "string" && srcData.committed_slot_url.length > 0) {
      out.committed_slot_url = srcData.committed_slot_url;
    }
  }

  return out as T & InheritedMainlineFields;
}
