// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装」：部署环境运行依赖的只读探测。
// 后端端点见 src/novelvideo/api/routes/env_setup.py 的 /admin/env-setup/status。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

/** 单个运行依赖的探测结果（version / detail 可空，按 id 在前端取图标与文案）。 */
export interface EnvDependency {
  id: string;
  installed: boolean;
  version: string | null;
  detail: string | null;
}

export interface EnvStatus {
  items: EnvDependency[];
}

/**
 * 部署环境依赖就绪清单。只读探测、无副作用，常驻 30s 缓存即可
 * （打开设置时刷新一次，不需要轮询）。
 */
export function useEnvStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.envStatus(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<EnvStatus>>(
        api.get("api/v1/admin/env-setup/status", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 30_000,
  });
}

/** 待办计划单项：后端回事实，名称/用途/修复文案前端按 id 取 i18n。 */
export interface EnvPlanItem {
  id: string;
  status: "ready" | "installable" | "missing";
  installed: boolean;
  auto_installable: boolean;
  action: string | null;
  version: string | null;
  detail: string | null;
  guide: string | null;
  optional: boolean;
}

export interface EnvPlanSummary {
  total: number;
  ready: number;
  installable: number;
  missing: number;
  all_ready: boolean;
}

export interface EnvPlan {
  items: EnvPlanItem[];
  summary: EnvPlanSummary;
  world: Record<string, unknown>;
}

/** 环境自检 → 待办计划。安装进行中轮询（2s），否则常驻 15s 缓存。 */
export function useEnvPlan(enabled = true) {
  return useQuery({
    queryKey: queryKeys.envPlan(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<EnvPlan>>(
        api.get("api/v1/admin/env-setup/plan", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 5_000,
    refetchInterval: (query) =>
      (query.state.data?.data?.world as { installing?: boolean } | undefined)?.installing
        ? 2_000
        : false,
  });
}

/** 逐项安装：触发一个可自动装项（当前为 world），成功后刷新计划。 */
export function useEnvInstall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (item: string) =>
      jsonWithBackendError<OkResponse<{ ok: boolean; item?: string | null }>>(
        api.post("api/v1/admin/env-setup/install", {
          json: { item },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.envPlan() }),
  });
}
