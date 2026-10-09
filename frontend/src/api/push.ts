// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "./client";

export type PushTargetKind =
  | "frame"
  | "sketch"
  | "director_render"
  | "selected_background"
  | "identity"
  | "identity_costume"
  | "identity_portrait"
  | "portrait"
  | "scene_master"
  | "scene_reverse_master"
  | "scene_spatial_layout"
  | "scene_360"
  | "scene_director_world"
  | "scene_director_pano_360"
  | "scene_3gs_active_ply"
  | "scene_3gs_master_ply"
  | "scene_3gs_reverse_ply"
  | "scene_3gs_pano_ply"
  | "scene_3gs_custom_scene"
  | "scene_3gs_collision_glb"
  | "prop_ref"
  | "video"
  | "beat_audio";

export interface PushTargetFrame {
  kind: "frame";
  episode: number;
  beat: number;
}
export interface PushTargetSketch {
  kind: "sketch";
  episode: number;
  beat: number;
}
export interface PushTargetDirectorRender {
  kind: "director_render";
  episode: number;
  beat: number;
}
export interface PushTargetSelectedBackground {
  kind: "selected_background";
  episode: number;
  beat: number;
}
export interface PushTargetIdentity {
  kind: "identity";
  character: string;
  identity_id: string;
}
export interface PushTargetIdentityCostume {
  kind: "identity_costume";
  character: string;
  identity_id: string;
}
export interface PushTargetIdentityPortrait {
  kind: "identity_portrait";
  character: string;
  identity_id: string;
}
export interface PushTargetPortrait {
  kind: "portrait";
  character: string;
}
export interface PushTargetSceneMaster {
  kind: "scene_master";
  scene_id: string;
}
export interface PushTargetScene360 {
  kind: "scene_360";
  scene_id: string;
}
export interface PushTargetSceneDirectorWorld {
  kind: "scene_director_world";
  scene_id: string;
}
export interface PushTargetSceneReverseMaster {
  kind: "scene_reverse_master";
  scene_id: string;
}
export interface PushTargetSceneSpatialLayout {
  kind: "scene_spatial_layout";
  scene_id: string;
}
export interface PushTargetSceneDirectorPano360 {
  kind: "scene_director_pano_360";
  scene_id: string;
}
export interface PushTargetScene3gsActivePly {
  kind: "scene_3gs_active_ply";
  scene_id: string;
}
export interface PushTargetScene3gsMasterPly {
  kind: "scene_3gs_master_ply";
  scene_id: string;
}
export interface PushTargetScene3gsReversePly {
  kind: "scene_3gs_reverse_ply";
  scene_id: string;
}
export interface PushTargetScene3gsPanoPly {
  kind: "scene_3gs_pano_ply";
  scene_id: string;
}
export interface PushTargetScene3gsCustomScene {
  kind: "scene_3gs_custom_scene";
  scene_id: string;
}
export interface PushTargetScene3gsCollisionGlb {
  kind: "scene_3gs_collision_glb";
  scene_id: string;
}
export interface PushTargetPropRef {
  kind: "prop_ref";
  prop_id: string;
}
export interface PushTargetVideo {
  kind: "video";
  episode: number;
  beat: number;
}
export interface PushTargetBeatAudio {
  kind: "beat_audio";
  episode: number;
  beat: number;
}
export type PushTarget =
  | PushTargetFrame
  | PushTargetSketch
  | PushTargetDirectorRender
  | PushTargetSelectedBackground
  | PushTargetIdentity
  | PushTargetIdentityCostume
  | PushTargetIdentityPortrait
  | PushTargetPortrait
  | PushTargetSceneMaster
  | PushTargetScene360
  | PushTargetSceneReverseMaster
  | PushTargetSceneSpatialLayout
  | PushTargetSceneDirectorWorld
  | PushTargetSceneDirectorPano360
  | PushTargetScene3gsActivePly
  | PushTargetScene3gsMasterPly
  | PushTargetScene3gsReversePly
  | PushTargetScene3gsPanoPly
  | PushTargetScene3gsCustomScene
  | PushTargetScene3gsCollisionGlb
  | PushTargetPropRef
  | PushTargetVideo
  | PushTargetBeatAudio;

export interface PushResult {
  target_path: string;
  target_url: string;
  backup: string | null;
  stale_marked?: number;
  affected_count?: number;
}

export interface ImpactBeat {
  episode: number;
  beat: number;
  visual_description?: string;
}

export interface ImpactResult {
  target: PushTarget;
  affected_beats: ImpactBeat[];
  affected_count: number;
}

export async function pushToPipeline(
  project: string,
  source_url: string,
  target: PushTarget,
  options?: { mark_stale?: boolean },
): Promise<PushResult> {
  return await apiCall<PushResult>(
    `projects/${encodeURIComponent(project)}/freezone/push`,
    {
      method: "POST",
      json: { source_url, target, mark_stale: options?.mark_stale ?? false },
    },
  );
}

export async function fetchPushImpact(
  project: string,
  target: PushTarget,
): Promise<ImpactResult> {
  return await apiCall<ImpactResult>(
    `projects/${encodeURIComponent(project)}/freezone/impact`,
    {
      method: "POST",
      json: { target },
    },
  );
}
