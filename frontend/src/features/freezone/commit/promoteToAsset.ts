// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchPushImpact,
  pushToPipeline,
  type ImpactResult,
  type PushResult,
  type PushTarget,
} from "@/api/push";

export interface PromoteToAssetOptions {
  mark_stale?: boolean;
}

/**
 * Commit is the boundary between free canvas experiments and canonical
 * SuperTale assets. The backend endpoint is still /freezone/push; this wrapper
 * keeps UI/product code from treating it as a generic file copy.
 */
export async function promoteToAsset(
  project: string,
  sourceUrl: string,
  target: PushTarget,
  options?: PromoteToAssetOptions,
): Promise<PushResult> {
  validateCommitTarget(target);
  return await pushToPipeline(project, sourceUrl, target, options);
}

export async function previewAssetImpact(
  project: string,
  target: PushTarget,
): Promise<ImpactResult> {
  validateCommitTarget(target);
  return await fetchPushImpact(project, target);
}

function validateCommitTarget(target: PushTarget): void {
  if (target.kind === "scene_director_world") {
    throw new Error("Scene director world commit requires canvas node state.");
  }
  if (
    (target.kind === "frame" ||
      target.kind === "sketch" ||
      target.kind === "director_render" ||
      target.kind === "video" ||
      target.kind === "beat_audio") &&
    (!Number.isFinite(target.episode) || !Number.isFinite(target.beat))
  ) {
    throw new Error("Beat-scoped asset target requires episode and beat.");
  }
  if (
    (target.kind === "identity" ||
      target.kind === "identity_costume" ||
      target.kind === "identity_portrait") &&
    (!target.character || !target.identity_id)
  ) {
    throw new Error("Identity asset target requires character and identity_id.");
  }
  if (target.kind === "portrait" && !target.character) {
    throw new Error("Portrait asset target requires character.");
  }
  if (isSceneTargetKind(target.kind) && !(target as unknown as Record<string, unknown>).scene_id) {
    throw new Error("Scene asset target requires scene_id.");
  }
}

function isSceneTargetKind(kind: PushTarget["kind"]): boolean {
  return (
    kind === "scene_master" ||
    kind === "scene_reverse_master" ||
    kind === "scene_spatial_layout" ||
    kind === "scene_director_world" ||
    kind === "scene_director_pano_360" ||
    kind === "scene_3gs_master_ply" ||
    kind === "scene_3gs_reverse_ply" ||
    kind === "scene_3gs_pano_ply" ||
    kind === "scene_3gs_custom_scene"
  );
}
