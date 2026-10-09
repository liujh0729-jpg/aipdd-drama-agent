// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type PanoViewerMode = "scene" | "beat";

export type PanoDestination =
  | "view"
  | "download"
  | "canvas_screenshot_node"
  | "beat_selected_background";

export interface PanoViewerManifest {
  viewer_kind: "pano360";
  mode: PanoViewerMode;
  project: string;
  scene_id: string;
  display_name: string;
  source: {
    slot_kind: "scene_director_pano_360" | "scene_360_candidate";
    url: string;
    fs?: string;
  };
  correction: {
    front_yaw_deg: number;
    sphere_correction_deg: {
      roll: number;
      pitch: number;
      yaw: number;
    };
  };
  beat_context?: {
    episode: number;
    beat: number;
    visual_description?: string;
    detected_identities: string[];
    detected_props: string[];
  };
  allowed_destinations: PanoDestination[];
}

export interface PanoCaptureResult {
  blob: Blob;
  width: number;
  height: number;
  aspect: "16:9" | "4:3" | "1:1" | "9:16" | "2:3";
  yaw_deg: number;
  pitch_deg: number;
  fov_deg: number;
  crop: {
    x: number;
    y: number;
    width: number;
    height: number;
    coordinate_space: "viewer_canvas";
  };
  source: PanoViewerManifest["source"];
}

export interface PanoCaptureSaveResult {
  anchor_id?: string;
  rel_path?: string;
  path?: string;
}

export interface LegacyPanoManifestInput {
  world?: string;
  pano?: string;
  pano_url?: string;
  pano_fs?: string;
  src?: string;
  display_name?: string;
}

export function legacyPanoInputToManifest(input: LegacyPanoManifestInput): PanoViewerManifest {
  const project = input.world?.trim() || "direct";
  const sceneId = input.pano?.trim() || "direct_pano";
  const url = input.pano_url?.trim() || input.src?.trim() || "";
  return {
    viewer_kind: "pano360",
    mode: "scene",
    project,
    scene_id: sceneId,
    display_name: input.display_name?.trim() || sceneId,
    source: {
      slot_kind: "scene_director_pano_360",
      url,
      fs: input.pano_fs?.trim() || undefined,
    },
    correction: {
      front_yaw_deg: 0,
      sphere_correction_deg: {
        roll: 0,
        pitch: 0,
        yaw: 0,
      },
    },
    allowed_destinations: ["view", "download"],
  };
}
