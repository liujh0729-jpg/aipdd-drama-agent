// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface SceneAsset {
  name: string;
  aliases?: string[];
  scene_type?: string;
  base_scene_id?: string;
  variant_id?: string;
  time_of_day?: string;
  environment_prompt?: string;
  variant_prompt?: string;
  effective_environment_prompt?: string;
  description?: string;
  derived_from_scene?: string;
  spatial_layout_image?: string;
  notes?: string;
  master_path?: string | null;
  master_url?: string | null;
  reverse_master_path?: string | null;
  reverse_master_url?: string | null;
  pano_path?: string | null;
  pano_url?: string | null;
  custom_scene_path?: string | null;
  custom_scene_url?: string | null;
  stage_3gs?: SceneStage3gsStatus;
}

export type ScenePanoSource = "master" | "text";

export type SceneStagePlySource = "master" | "reverse" | "pano";

export interface SceneStage3gsFile {
  ready: boolean;
  path: string;
  url: string;
  size_bytes: number;
  size_mb: number;
}

export interface SceneStage3gsStatus {
  stage_dir: string;
  manifest_ready: boolean;
  source: string;
  active_source: string;
  active: SceneStage3gsFile;
  custom: SceneStage3gsFile;
  master: SceneStage3gsFile;
  reverse: SceneStage3gsFile;
  pano: SceneStage3gsFile;
}
