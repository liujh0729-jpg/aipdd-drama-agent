// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Beat, SceneRef } from "./episode";

export interface Script {
  beats: Beat[];
  review_summary?: string;
  // identity_id → "#HEX COLOR_NAME" (e.g. "#FF00FF MAGENTA"). Assigned by
  // EpisodeOptimizer.assign_sketch_colors on the backend; the whole string
  // is the value — FE parses out the hex for UI.
  sketch_colors?: Record<string, string>;
}

export interface BeatUpdate {
  narration_segment?: string;
  visual_description?: string;
  scene_ref?: SceneRef | null;
  time_of_day?: string;
  video_prompt?: string;
  keyframe_prompt?: string;
  video_mode?: string;
  seedance2_config_json?: string;
  audio_type?: string;
  speaker?: string;
  detected_identities?: string[];
  detected_props?: string[];
}
