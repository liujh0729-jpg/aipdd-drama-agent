// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse } from "@/types/api";

export interface PosePoint {
  x: number;
  y: number;
}

export interface PoseStroke {
  points: PosePoint[];
  width?: number;
  colorHex?: string;
  eraser?: boolean;
}

export interface PoseSkeleton {
  identityId: string;
  colorHex: string;
  colorName?: string;
  joints: Record<string, PosePoint>;
  lineWidth?: number;
  headRadius?: number;
  visible?: boolean;
  active?: boolean;
}

export interface PosePreset {
  label: string;
  joints: Record<string, { x: number; y: number }>;
}

export interface SketchPoseEditorData {
  beat_num: number;
  sketch_url: string;
  width: number;
  height: number;
  candidates: Array<{
    identity_id: string;
    color_hex: string;
    color_name: string;
  }>;
  skeleton_edges: Array<[string, string]>;
  pose_presets: Record<string, PosePreset>;
  skeletons: PoseSkeleton[];
}

export interface SketchPoseEditorState {
  strokes: PoseStroke[];
  skeletons: PoseSkeleton[];
}

export interface SketchPoseEditorSaveResult {
  beat_num: number;
  sketch_url: string;
}

export interface SketchCrop {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SketchCropResult {
  beat_num: number;
  sketch_url: string;
  width: number;
  height: number;
}

export function useSketchPoseEditor(
  project: string,
  episode: number,
  beatNum: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.sketchPoseEditor(project, episode, beatNum),
    queryFn: ({ signal }) =>
      api
        .get(
          p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/sketch/pose-editor`,
          { signal },
        )
        .json<ApiResponse<SketchPoseEditorData>>(),
    enabled: !!project && episode > 0 && beatNum > 0 && enabled,
  });
}

export function useSaveSketchPoseEditor(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      beatNum,
      state,
    }: {
      beatNum: number;
      state: SketchPoseEditorState;
    }) =>
      api
        .post(
          p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/sketch/pose-editor`,
          { json: state },
        )
        .json<ApiResponse<SketchPoseEditorSaveResult>>(),
    onSuccess: (_res, { beatNum }) => {
      qc.invalidateQueries({
        queryKey: queryKeys.sketchPoseEditor(project, episode, beatNum),
      });
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) });
      qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) });
    },
  });
}

export function useCropSketch(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ beatNum, crop }: { beatNum: number; crop: SketchCrop }) =>
      api
        .post(
          p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/sketch/crop`,
          { json: crop },
        )
        .json<ApiResponse<SketchCropResult>>(),
    onSuccess: (_res, { beatNum }) => {
      qc.invalidateQueries({
        queryKey: queryKeys.sketchPoseEditor(project, episode, beatNum),
      });
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) });
      qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) });
    },
  });
}
