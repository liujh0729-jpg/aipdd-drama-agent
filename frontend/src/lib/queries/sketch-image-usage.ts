// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export interface SketchImageUsage {
  today_requests: number;
  total_requests: number;
}

export interface ImageGenerationGuard {
  attempt_count: number;
  next_attempt: number;
  level: "none" | "confirm" | "locked";
  message: string;
}

export interface ImageGenerationPasswordVerification {
  verified: boolean;
}

export function useSketchImageUsage(project: string, episode: number) {
  return useQuery({
    queryKey: queryKeys.sketchImageUsage(project, episode),
    queryFn: ({ signal }) =>
      api
        .get(
          p`api/v1/projects/${project}/episodes/${episode}/sketch-image-usage`,
          { signal },
        )
        .json<OkResponse<SketchImageUsage>>(),
    enabled: !!project && episode > 0,
  });
}

export function useImageGenerationGuard(project: string, episode: number) {
  return useMutation({
    mutationFn: ({
      taskType,
      scope,
      subject,
    }: {
      taskType: string;
      scope: string;
      subject: string;
    }) =>
      api
        .get(p`api/v1/projects/${project}/episodes/${episode}/image-generation-guard`, {
          searchParams: {
            task_type: taskType,
            scope,
            subject,
          },
        })
        .json<OkResponse<ImageGenerationGuard>>(),
  });
}

export function useVerifyImageGenerationPassword(project: string, episode: number) {
  return useMutation({
    mutationFn: ({ password }: { password: string }) =>
      api
        .post(p`api/v1/projects/${project}/episodes/${episode}/image-generation-guard/verify-password`, {
          json: { password },
        })
        .json<OkResponse<ImageGenerationPasswordVerification>>(),
  });
}
