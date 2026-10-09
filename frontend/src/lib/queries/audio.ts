// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] WP-D: 音频积分报价（POST .../audio/billing-quote）随额度体系
// 一起拆了；音色未配置等前置条件改由真实生成端点回 voice_prereq_required。
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import type { ErrorResponse, TaskResponse } from "@/types/api";

export interface GenerateAudioParams {
  beatNumbers?: number[];
  mode?: "sync_changed" | "redo_selected" | string;
}

export function useGenerateAudio(project: string, episode: number) {
  return useMutation({
    mutationFn: (params?: GenerateAudioParams) => {
      const body: { beat_numbers?: number[]; mode?: string } = {};
      if (params?.beatNumbers) body.beat_numbers = params.beatNumbers;
      if (params?.mode) body.mode = params.mode;
      return jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/episodes/${episode}/audio/generate`, {
          json: body,
          throwHttpErrors: false,
        }),
      );
    },
  });
}

export function useRegenerateBeatAudio(project: string, episode: number) {
  return useMutation({
    mutationFn: (beatNum: number) =>
      jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(
          p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/audio`,
          { throwHttpErrors: false },
        ),
      ),
  });
}
