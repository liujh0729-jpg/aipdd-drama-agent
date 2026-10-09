// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Canonical mainline node fields, used across canvas / drag-in / spawn /
 * Push paths. `SlotTarget` is a deliberate alias for the existing `PushTarget`
 * discriminated union — every commit eventually flows through the same backend
 * `/freezone/push` route, so there is exactly one set of legal kinds + required
 * fields. Inventing a parallel type would invite divergence (frontend / backend
 * already share `PushTarget` ↔ `slots.py:PushTarget = SlotTarget` Pydantic
 * mirror).
 *
 * Naming reminder — do not confuse the two layers:
 *   - `PushTargetKind` (this file's domain, 19 values): slot 落点 kind.
 *     e.g. "sketch" / "frame" / "identity" / "scene_master" — these are the
 *     short verbs the Push route writes against.
 *   - `PresetRef.role` (preset emit domain, ~22 values): asset discovery role.
 *     e.g. "current_sketch" / "current_frame" / "character_identity" — used
 *     when preset-emitting nodes; not for Push routing.
 *
 * Node `data.slot_target` carries a `SlotTarget` (= `PushTarget`) shape, NOT
 * a role string.
 */

import type { PushTarget, PushTargetKind } from "../../../api/push.ts";

export type { PushTargetKind } from "../../../api/push.ts";

/** Alias for `PushTarget` expressing node intent: "this node's Push default target". */
export type SlotTarget = PushTarget;

/** Structural validator for node data and restored canvas JSON. */
export function isSlotTarget(value: unknown): value is SlotTarget {
  if (!value || typeof value !== "object") return false;
  const kind = (value as { kind?: unknown }).kind;
  if (typeof kind !== "string") return false;
  // Cheapest correctness check: kind must be one of the known PushTargetKind values.
  // We rely on the runtime kind check rather than re-encoding required fields per
  // kind (that's the backend Pydantic discriminator's job — frontend errors here
  // surface as 4xx from the Push route, not silent corruption).
  return SLOT_TARGET_KINDS.has(kind as PushTargetKind);
}

/**
 * 已废弃 / 改名的旧 slot kind → 当前 canonical kind 的迁移表。
 * 旧画布 JSON 或旧 client 持久化的节点可能仍带这些 kind。
 */
const LEGACY_SLOT_TARGET_KIND_MAP: Readonly<Record<string, PushTargetKind>> = {
  // 旧 2:1 panorama slot 已被 Director Pano 360 取代。
  scene_360: "scene_director_pano_360",
  // 后端把 3GS「上传」slot 改名为「自定义场景」(同为 scene_id 作用域)。
  scene_3gs_uploaded_ply: "scene_3gs_custom_scene",
};

/**
 * 读取(可能来自旧画布的)slot_target:先把已废弃的 legacy kind 迁移到当前
 * canonical kind,再做合法性校验,返回归一化后的 SlotTarget;非法返回 null。
 * 所有「读 node.data.slot_target / 资产 slot_target」的入口都应走这里,
 * 避免旧数据因 kind 改名而静默失去 commit 能力。
 */
export function coerceSlotTarget(value: unknown): SlotTarget | null {
  if (!value || typeof value !== "object") return null;
  const kind = (value as { kind?: unknown }).kind;
  if (typeof kind !== "string") return null;
  const mapped = LEGACY_SLOT_TARGET_KIND_MAP[kind];
  const normalized = mapped ? { ...(value as object), kind: mapped } : value;
  return isSlotTarget(normalized) ? normalized : null;
}

const SLOT_TARGET_KINDS: ReadonlySet<PushTargetKind> = new Set<PushTargetKind>([
  "frame",
  "sketch",
  "director_render",
  "selected_background",
  "identity",
  "identity_costume",
  "identity_portrait",
  "portrait",
  "scene_master",
  "scene_reverse_master",
  "scene_spatial_layout",
  "scene_director_world",
  "scene_director_pano_360",
  "scene_3gs_master_ply",
  "scene_3gs_reverse_ply",
  "scene_3gs_pano_ply",
  "scene_3gs_custom_scene",
  "prop_ref",
  "video",
  "beat_audio",
]);

/**
 * Canonical equality for two slot targets. Same kind + same scoping fields.
 *
 * Compares only the fields that matter for routing — episode/beat for
 * beat-scoped, character/identity_id for character-scoped, scene_id for
 * scene-scoped, prop_id for prop_ref. JSON-stringify is avoided because key
 * ordering / extra fields would create false negatives.
 */
export function slotTargetsEqual(a: SlotTarget, b: SlotTarget): boolean {
  if (a.kind !== b.kind) return false;
  switch (a.kind) {
    case "frame":
    case "sketch":
    case "director_render":
    case "selected_background":
    case "video":
    case "beat_audio":
      return (
        a.episode === (b as typeof a).episode &&
        a.beat === (b as typeof a).beat
      );
    case "identity":
    case "identity_costume":
    case "identity_portrait":
      return (
        a.character === (b as typeof a).character &&
        a.identity_id === (b as typeof a).identity_id
      );
    case "portrait":
      return a.character === (b as typeof a).character;
    case "scene_master":
    case "scene_reverse_master":
    case "scene_spatial_layout":
    case "scene_360":
    case "scene_director_world":
    case "scene_director_pano_360":
    case "scene_3gs_active_ply":
    case "scene_3gs_master_ply":
    case "scene_3gs_reverse_ply":
    case "scene_3gs_pano_ply":
    case "scene_3gs_custom_scene":
    case "scene_3gs_collision_glb":
      return a.scene_id === (b as typeof a).scene_id;
    case "prop_ref":
      return a.prop_id === (b as typeof a).prop_id;
    default: {
      // Exhaustiveness — if a new kind lands in PushTarget without updating
      // this switch, TS will error here.
      const _exhaustive: never = a;
      return _exhaustive;
    }
  }
}
