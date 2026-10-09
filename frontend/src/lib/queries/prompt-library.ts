// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 提示词库：内置提示词列表 / 覆盖 / 恢复默认。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export interface PromptLibraryItem {
  key: string;
  /** 分组 id：character / video / freezone / grid。 */
  group: string;
  /** 后端内置的中文名（i18n 缺失时的兜底）。 */
  label: string;
  /** 后端内置的中文说明（i18n 缺失时的兜底）。 */
  description: string;
  /** 模板中可用的占位符。 */
  variables: string[];
  default: string;
  value: string;
  is_overridden: boolean;
  updated_at: string | null;
}

export interface PromptLibraryPayload {
  /** 覆盖集合版本号，保存后变化。 */
  revision: string;
  groups: string[];
  items: PromptLibraryItem[];
}

/** 与后端 MAX_OVERRIDE_CHARS 保持一致的单条覆盖长度上限。 */
export const PROMPT_OVERRIDE_MAX_CHARS = 20_000;

export function usePromptLibrary(enabled = true) {
  return useQuery({
    queryKey: queryKeys.promptLibrary(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/prompt-library", { signal })
        .json<OkResponse<PromptLibraryPayload>>(),
    enabled,
  });
}

export function useSavePromptOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      api
        .put(`api/v1/prompt-library/${encodeURIComponent(key)}`, {
          json: { value },
          timeout: 60_000,
        })
        .json<OkResponse<PromptLibraryPayload> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.promptLibrary() });
    },
  });
}

export function useResetPromptOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      api
        .delete(`api/v1/prompt-library/${encodeURIComponent(key)}`, {
          timeout: 60_000,
        })
        .json<OkResponse<PromptLibraryPayload> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.promptLibrary() });
    },
  });
}