// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 3D 世界：运行时按需安装 world 依赖（3DGS/SHARP）+ 开关 + 下载源。
// 后端端点见 src/novelvideo/api/routes/world.py 的 /admin/world/*。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export type WorldInstallState = "not_installed" | "installing" | "ready" | "failed";

export interface WorldSources {
  pip_index_url: string;
  torch_index_url: string;
  npm_registry: string;
  hf_endpoint: string;
  https_proxy: string;
}

export interface WorldStatus {
  enabled: boolean;
  install_state: WorldInstallState;
  /** 后台安装线程是否存活：驱动前端轮询与按钮禁用。 */
  installing: boolean;
  installed: boolean;
  da2_ready: boolean;
  progress: number;
  message: string;
  installed_at: string;
  sources: WorldSources;
  venv_path: string;
  splat_transform: boolean;
  can_install: boolean;
  log_tail: string;
}

/**
 * 3D 世界状态。安装进行中时轮询（2s），让用户看到进度推进；否则常驻 15s 缓存。
 */
export function useWorldStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.worldStatus(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<WorldStatus>>(
        api.get("api/v1/admin/world/status", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 5_000,
    refetchInterval: (query) =>
      query.state.data?.data?.installing ? 2_000 : false,
  });
}

export function useUpdateWorldSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<WorldSources>) =>
      jsonWithBackendError<OkResponse<WorldStatus>>(
        api.put("api/v1/admin/world/sources", {
          json: input,
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.worldStatus() }),
  });
}

export function useSetWorldEnabled() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      jsonWithBackendError<OkResponse<WorldStatus>>(
        api.post("api/v1/admin/world/enable", {
          json: { enabled },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.worldStatus() }),
  });
}

export function useStartWorldInstall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<WorldStatus>>(
        api.post("api/v1/admin/world/install", { throwHttpErrors: false }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.worldStatus() }),
  });
}

export function useUninstallWorld() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<WorldStatus>>(
        api.post("api/v1/admin/world/uninstall", { throwHttpErrors: false }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.worldStatus() }),
  });
}
