// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, uploadApi } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse, TaskResponse } from "@/types/api";
import type { Chapter } from "@/types/episode";
import type { SpineTemplate } from "@/types/project";

export interface FormatCheckIssue {
  code: string;
  line: number | null;
  /** 后端产的中文原文，仅作 i18n 的 defaultValue，不要直接渲染。 */
  message: string;
  /** 同上。 */
  fix: string;
  /** 结构化插值参数，交给 localizeFormatCheck 拼本地化文案。 */
  params?: Record<string, unknown>;
}

export interface FormatCheck {
  level: "ok" | "warning" | "blocking";
  /** 后端产的中文原文，仅作 i18n 的 defaultValue，不要直接渲染。 */
  summary: string;
  summary_code?: string;
  summary_params?: Record<string, unknown>;
  issues?: FormatCheckIssue[];
  metrics?: Record<string, number>;
  scene_header_status?: "standard" | "repairable" | "missing";
}

export interface UploadResult {
  filename: string;
  size: number;
  total_chars?: number;
  billable_chars?: number;
  count?: number;
  chapters?: Chapter[];
  format_check?: FormatCheck;
}

interface ChaptersResult {
  chapters: Chapter[];
  total_chars: number;
  billable_chars?: number;
  count?: number;
  source_filename?: string;
  /** Client-only marker: upload parsing succeeded, but Cognee ingest has not completed. */
  preview_only?: boolean;
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  type: string;
  degree: number;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphSnapshot {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
}

export function useUploadNovel(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      file,
      spineTemplate,
    }: {
      file: File;
      spineTemplate: SpineTemplate;
    }) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("spine_template", spineTemplate);
      const response = await jsonWithBackendError<OkResponse<UploadResult> | ErrorResponse>(
        uploadApi.post(p`api/v1/projects/${project}/ingest/upload`, { body: formData }),
      );
      if (!response.ok) {
        throw new Error(response.error);
      }
      return response;
    },
    onSuccess: (response, variables) => {
      const preview = response.data;
      if (
        Array.isArray(preview.chapters) &&
        typeof preview.total_chars === "number"
      ) {
        queryClient.setQueryData<OkResponse<ChaptersResult>>(
          queryKeys.chapterPreview(project, variables.spineTemplate),
          {
            ok: true,
            data: {
              chapters: preview.chapters,
              total_chars: preview.total_chars,
              billable_chars: preview.billable_chars,
              count: preview.count,
              source_filename: preview.filename,
              preview_only: true,
            },
          },
        );
        return;
      }

      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
    },
  });
}

export function useChapters(
  project: string,
  spineTemplate: SpineTemplate,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.chapterPreview(project, spineTemplate),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/chapters`, {
          signal,
          searchParams: { spine_template: spineTemplate },
        })
        .json<OkResponse<ChaptersResult>>(),
    enabled: !!project && enabled,
  });
}

export function useKnowledgeGraph(project: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.knowledgeGraph(project),
    // Ladybug graph reads run in a backend executor thread and cannot be stopped
    // safely midway. Do not consume React Query's unmount signal: let the request
    // finish and populate the cache when users briefly switch pages.
    queryFn: () =>
      api
        .get(p`api/v1/projects/${project}/ingest/graph`)
        .json<OkResponse<KnowledgeGraphSnapshot>>(),
    enabled: !!project && enabled,
    staleTime: 30_000,
  });
}

export function useStartIngest(project: string) {
  return useMutation({
    mutationFn: async (params: {
      filename: string;
      rebuild?: boolean;
      spine_template?: SpineTemplate;
    }) => {
      const response = await jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/ingest/start`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(response.error);
      }
      return response;
    },
  });
}

// ── 导入后自动化：生成图谱 / 一键做到资产 [aipdd fork delta] ──────────────────

/** 触发知识图谱生成任务。结构化项目导入后没有图谱，这里是唯一的建图入口。 */
export function useBuildKnowledgeGraph(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/ingest/graph/build`, {
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.knowledgeGraph(project),
      });
    },
  });
}

/** 一键自动化：角色 → 分集 → 场景 → 图谱，只做到资产为止，不出图。 */
export function useStartIngestAuto(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { targetEpisodes?: number } = {}) =>
      jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/ingest/auto`, {
          json: { target_episodes: params.targetEpisodes ?? 10 },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      // 任务结束时资产才会出现，先让页面知道旧结果已经不可信。
      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
      queryClient.invalidateQueries({
        queryKey: queryKeys.knowledgeGraph(project),
      });
    },
  });
}

// ── 场景头自动补全（消费默认文本 LLM）[aipdd fork delta] ──────────────────────

export interface AutoSceneHeadersTaskResponse {
  ok: boolean;
  task_type?: string;
  task_id?: string;
  task_key?: string;
  backend?: string;
  queue?: string | null;
  message?: string;
  error?: string;
}

export interface AutoSceneHeadersResult {
  status: string | null;
  filename: string | null;
  text: string | null;
  scene_header_status?: "standard" | "repairable" | "missing";
  format_check?: FormatCheck | null;
  // [aipdd fork delta] 任务失败时后端回传的真实错误（供前端弹错）。
  error?: string | null;
  // [aipdd fork delta] running 期间的进度（0~1）与当前步骤文案，用于“补全中”横幅。
  progress?: number | null;
  current_task?: string | null;
}

/** 触发场景头自动补全后台任务（不改动原文，产出增强文本）。 */
export function useAutoSceneHeaders(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { filename: string }) => {
      const response = await jsonWithBackendError<
        AutoSceneHeadersTaskResponse | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/ingest/auto-scene-headers`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "触发失败",
        );
      }
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.autoSceneHeaders(project),
      });
    },
  });
}

/** 拉取增强文本与导入格式复检。`poll` 为真时轮询后台任务进度。 */
export function useAutoSceneHeadersResult(
  project: string,
  enabled = true,
  poll = false,
) {
  return useQuery({
    queryKey: queryKeys.autoSceneHeaders(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/ingest/auto-scene-headers/result`, {
          signal,
        })
        .json<OkResponse<AutoSceneHeadersResult>>(),
    enabled: !!project && enabled,
    refetchInterval: poll ? 2500 : false,
  });
}

/** 将人工编辑后的增强文本写回增强文件，供导入确认使用。 */
export function useApplyAutoSceneHeaders(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { filename: string; text: string }) => {
      const response = await jsonWithBackendError<
        | OkResponse<{
            filename: string;
            scene_header_status?: "standard" | "repairable" | "missing";
            format_check?: FormatCheck | null;
          }>
        | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/ingest/auto-scene-headers/apply`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(
          "error" in response && response.error ? response.error : "保存失败",
        );
      }
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.autoSceneHeaders(project),
      });
    },
  });
}

/** 保存章节正文编辑（标题行由后端保留，写回 novel.txt 与导入源文件）。 */
export function useSaveChapter(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { chapterNumber: number; body: string }) => {
      const response = await jsonWithBackendError<
        | OkResponse<{
            number: number;
            chars: number;
            source_filename: string | null;
          }>
        | ErrorResponse
      >(
        api.put(p`api/v1/projects/${project}/chapters/${params.chapterNumber}`, {
          json: { body: params.body },
          throwHttpErrors: false,
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
      // 「剧本」页章节列表按 chapterPreview(spineTemplate) 变体缓存，
      // chapters 前缀失效即可覆盖全部变体。
      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
    },
  });
}
