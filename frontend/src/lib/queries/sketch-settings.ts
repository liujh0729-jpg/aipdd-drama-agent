// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export interface SketchSettingsData {
  sketch_image_selection: string;
  options: Record<string, string>;
}

export interface SketchSettingsUpdate {
  sketch_image_selection?: string;
}

export type SketchAspectRatio = "2:3" | "16:9";

export function useSketchSettings(project: string) {
  return useQuery({
    queryKey: queryKeys.sketchSettings(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/sketch-settings`, { signal })
        .json<OkResponse<SketchSettingsData>>(),
    enabled: !!project,
  });
}

export function useUpdateSketchSettings(project: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: SketchSettingsUpdate) =>
      api
        .patch(p`api/v1/projects/${project}/sketch-settings`, { json: params })
        .json<OkResponse<SketchSettingsData> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.sketchSettings(project) });
    },
  });
}
