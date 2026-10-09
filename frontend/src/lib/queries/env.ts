// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装」：部署环境运行依赖的只读探测。
// 后端端点见 src/novelvideo/api/routes/env_setup.py 的 /admin/env-setup/status。
import { useQuery } from "@tanstack/react-query";

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
