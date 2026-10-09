// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] L1「故事」栏目：项目故事源导入 / AI 优化 / 整本多集剧本生成。
// 链路：导入故事 → AI 优化 → 生成整本剧本 → 产物写入 uploads/（「剧本」模块剧本库）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, uploadApi } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse, TaskResponse } from "@/types/api";
import type { FormatCheck } from "@/lib/queries/ingest";

// ── 风格选项（与后端 STORY_STYLE_OPTIONS / director.style_library 对齐） ──────

/** 空串 = 自动推荐（后端 STORY_STYLE_AUTO）。label 走 storyPage.style.* i18n。 */
export const STORY_STYLE_VALUES = [
  "",
  "beat_punch",
  "spectacle",
  "suspense",
  "emotional",
  "prestige",
  "realism",
  "comedy",
  "info_pacing",
  "auteur",
] as const;

export type StoryStyleValue = (typeof STORY_STYLE_VALUES)[number];

export function storyStyleI18nKey(value: string): string {
  return value ? `storyPage.style.${value}` : "storyPage.style.auto";
}

// ── 类型 ─────────────────────────────────────────────────────────────────────

export interface ProjectStorySource {
  text: string;
  chars: number;
  billable_chars: number;
  filename: string;
}

export interface ProjectStoryImportResult {
  filename: string;
  chars: number;
  billable_chars: number;
}

export interface ProjectStoryOptimizeResult {
  story: string;
  notes: string[];
  changed: boolean;
}

export interface GeneratedScriptChapterSummary {
  number: number;
  title: string;
  preview: string;
  word_count: number;
}

export interface GeneratedScriptResult {
  status: string | null;
  filename: string | null;
  episodes: number | null;
  total_chars: number;
  billable_chars: number;
  chapter_summaries: GeneratedScriptChapterSummary[];
  format_check: FormatCheck | null;
  /** 任务失败时的真实错误（供前端弹错）。 */
  error: string | null;
  /** running 期间的进度（0~1）与当前步骤文案。 */
  progress: number | null;
  current_task: string | null;
}

// ── 查询 / 变更 ──────────────────────────────────────────────────────────────

/** 读取项目故事源（未导入时 text 为空串）。 */
export function useProjectStorySource(project: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.storySource(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/story/source`, { signal })
        .json<OkResponse<ProjectStorySource>>(),
    enabled: !!project && enabled,
  });
}

/** 导入故事：上传文件或粘贴文本，落盘为项目故事源。 */
export function useImportProjectStory(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { file?: File; text?: string }) => {
      const formData = new FormData();
      if (params.file) {
        formData.append("file", params.file);
      }
      if (typeof params.text === "string") {
        formData.append("text", params.text);
      }
      const response = await jsonWithBackendError<
        OkResponse<ProjectStoryImportResult> | ErrorResponse
      >(
        uploadApi.post(p`api/v1/projects/${project}/story/source/import`, {
          body: formData,
        }),
      );
      if (!response.ok) {
        // 回退文案交给页面层的 t() 补位（英文界面不泄漏中文）。
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.storySource(project) });
    },
  });
}

/** 整本故事 AI 诊断优化（结果不落盘，前端确认后再经导入接口保存）。 */
export function useOptimizeProjectStory(project: string) {
  return useMutation({
    mutationFn: async (params: { text?: string; story_style?: string }) => {
      const response = await jsonWithBackendError<
        OkResponse<ProjectStoryOptimizeResult> | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/story/source/optimize`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
  });
}

/** 触发整本剧本生成后台任务（novel_to_screenplay）。 */
export function useGenerateProjectScript(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      episode_count: number;
      story_style?: string;
    }) => {
      const response = await jsonWithBackendError<
        TaskResponse | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/story/generate-script`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.storyScriptJob(project),
      });
    },
  });
}

/** 拉取整本剧本生成进度与产物摘要。`poll` 为真时轮询任务进度。 */
export function useGeneratedScriptResult(
  project: string,
  enabled = true,
  poll = false,
) {
  return useQuery({
    queryKey: queryKeys.storyScriptJob(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/story/generate-script/result`, {
          signal,
        })
        .json<OkResponse<GeneratedScriptResult>>(),
    enabled: !!project && enabled,
    refetchInterval: poll ? 2500 : false,
  });
}

// ── 同步到「剧本」与分集查看/编辑 [aipdd fork delta] ───────────────────────────

export interface GeneratedScriptSyncResult {
  filename: string;
  chapters: number;
  total_chars: number;
  billable_chars: number;
}

export interface GeneratedScriptEpisodeDetail {
  number: number;
  title: string;
  body: string;
  chars: number;
}

/** 把已生成的整本剧本同步到「剧本」模块（写 novel.txt + 记录导入来源）。 */
export function useSyncGeneratedScript(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const response = await jsonWithBackendError<
        OkResponse<GeneratedScriptSyncResult> | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/story/generate-script/sync`, {
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
    onSuccess: () => {
      // 「剧本」模块按 chapterPreview 变体缓存，chapters 前缀失效即可覆盖。
      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
    },
  });
}

/** 读取生成剧本某一集的完整正文（抽屉打开时按需拉取）。 */
export function useGeneratedScriptEpisode(
  project: string,
  episodeNumber: number | null,
) {
  return useQuery({
    queryKey: queryKeys.storyScriptEpisode(project, episodeNumber ?? 0),
    queryFn: async ({ signal }) => {
      const response = await api
        .get(
          p`api/v1/projects/${project}/story/generate-script/episodes/${episodeNumber}`,
          { signal },
        )
        .json<OkResponse<GeneratedScriptEpisodeDetail> | ErrorResponse>();
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
    enabled: !!project && episodeNumber !== null,
  });
}

/** 保存生成剧本某一集的正文编辑（标题行由后端保留）。 */
export function useSaveGeneratedScriptEpisode(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { episodeNumber: number; body: string }) => {
      const response = await jsonWithBackendError<
        OkResponse<{ number: number; chars: number }> | ErrorResponse
      >(
        api.put(
          p`api/v1/projects/${project}/story/generate-script/episodes/${params.episodeNumber}`,
          { json: { body: params.body }, throwHttpErrors: false },
        ),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "",
        );
      }
      return response;
    },
    onSuccess: (_response, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.storyScriptJob(project),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.storyScriptEpisode(project, variables.episodeNumber),
      });
      // 若该产物已同步到「剧本」，后端会镜像这次编辑到 novel.txt。
      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
    },
  });
}