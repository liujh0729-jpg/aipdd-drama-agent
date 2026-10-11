// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「配置备份 / 恢复」：整盘 settings.db 的导出下载与导入回灌。
// 后端端点见 src/novelvideo/api/routes/settings_backup.py（均 require_owner）。
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import type { OkResponse } from "@/types/api";

/** 备份 bundle 的信封（与后端 FORMAT/SCHEMA_VERSION 对齐）。 */
export interface ConfigBundle {
  format: string;
  schema_version: number;
  app_version?: string;
  generated_at?: string;
  count?: number;
  settings: Record<string, string>;
}

export interface ImportResult {
  applied: number;
  skipped: number;
  warnings: string[];
}

/**
 * 导出整盘配置并触发浏览器下载。后端已带 ``Content-Disposition`` 文件名，优先沿用，
 * 取不到再兜底一个带时间戳的名字。
 */
export async function downloadConfigBackup(): Promise<void> {
  const response = await api.get("api/v1/admin/settings/export");
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/i.exec(disposition);
  const filename = match?.[1] ?? `dramaclaw-config-${Date.now()}.json`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * 导入备份：把 bundle 原样 POST 回后端 merge 写回并失效缓存。成功后整盘配置已变，
 * 直接失效全部查询（刷新模型 / 渠道 / 自检等视图）——这是一次性重操作，代价可接受。
 */
export function useImportConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bundle: ConfigBundle) =>
      jsonWithBackendError<OkResponse<ImportResult>>(
        api.post("api/v1/admin/settings/import", {
          json: bundle,
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries(),
  });
}
