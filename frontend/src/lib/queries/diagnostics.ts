// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「系统整体自检」：一键聚合只读体检。
// 后端端点见 src/novelvideo/api/routes/env_setup.py 的 /admin/diagnostics
// （聚合逻辑在 src/novelvideo/diagnostics.py）。只读、无副作用。
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

/** 单条体检项：后端只回事实（level + detail），名称/修复文案前端按 section+id 取 i18n。 */
export interface DiagnosticItem {
  id: string;
  level: "pass" | "warn" | "fail" | "info";
  detail?: string | null;
}

export interface DiagnosticSection {
  id: "environment" | "model_gateway" | "default_models" | "storage";
  items: DiagnosticItem[];
}

export interface DiagnosticsSummary {
  total: number;
  pass: number;
  warn: number;
  fail: number;
  info: number;
  all_ok: boolean;
}

export interface DiagnosticsReport {
  sections: DiagnosticSection[];
  summary: DiagnosticsSummary;
}

/**
 * 系统整体自检报告。只读探测、无副作用；无常驻缓存（每次进入 / 点「重新检测」都应
 * 拿最新实况），故 staleTime 0，由用户显式 refetch 触发重检。
 */
export function useDiagnostics(enabled = true) {
  return useQuery({
    queryKey: queryKeys.diagnostics(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<DiagnosticsReport>>(
        api.get("api/v1/admin/diagnostics", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 0,
  });
}
