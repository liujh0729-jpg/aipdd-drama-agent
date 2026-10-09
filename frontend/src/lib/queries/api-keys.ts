// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → API 密钥：一把 key 同时用于外部 MCP 与模型广场。
// 后端端点见 src/novelvideo/api/routes/api_keys.py。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export interface ApiKeyRecord {
  id: string;
  name: string;
  /** 列表展示用前缀（st-xxxxxxxx…）；明文只在生成响应里出现一次。 */
  key_prefix: string;
  created_at: string;
  last_used_at: string;
  revoked_at: string;
  revoked: boolean;
}

/** 生成响应：明文 key 只在这一处出现。 */
export interface ApiKeyCreated extends ApiKeyRecord {
  key: string;
}

export function useApiKeys(enabled = true) {
  return useQuery({
    queryKey: queryKeys.apiKeys(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<ApiKeyRecord[]>>(
        api.get("api/v1/api-keys", { throwHttpErrors: false }),
      ),
    enabled,
  });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string }) =>
      jsonWithBackendError<OkResponse<ApiKeyCreated> | ErrorResponse>(
        api.post("api/v1/api-keys", { json: input, throwHttpErrors: false }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys() });
    },
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) =>
      jsonWithBackendError<
        OkResponse<{ id: string; revoked: boolean }> | ErrorResponse
      >(
        api.delete(`api/v1/api-keys/${encodeURIComponent(keyId)}`, {
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys() });
    },
  });
}