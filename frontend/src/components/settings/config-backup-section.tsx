// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「配置备份 / 恢复」：导出整盘配置为 JSON，重装后导入还原。
// 备份 = settings.db 全表（含密钥），恢复 = 上传 bundle 二次确认后 merge 写回。
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, Loader2, ShieldAlert, Upload } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import {
  downloadConfigBackup,
  useImportConfig,
  type ConfigBundle,
} from "@/lib/queries/config-backup";
import { cn } from "@/lib/utils";

const BUNDLE_FORMAT = "dramaclaw-config-backup";

function looksLikeBundle(value: unknown): value is ConfigBundle {
  if (!value || typeof value !== "object") return false;
  const bundle = value as Partial<ConfigBundle>;
  return (
    bundle.format === BUNDLE_FORMAT &&
    !!bundle.settings &&
    typeof bundle.settings === "object"
  );
}

export function ConfigBackupSection() {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const importConfig = useImportConfig();
  const [exporting, setExporting] = useState(false);
  const [pending, setPending] = useState<ConfigBundle | null>(null);

  const handleExport = async () => {
    setExporting(true);
    try {
      await downloadConfigBackup();
      toast.success(t("settings.configBackup.exportDone"));
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("settings.configBackup.exportFailed"),
      );
    } finally {
      setExporting(false);
    }
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const bundle = JSON.parse(await file.text());
      // 形状校验：非本系统备份或 settings 缺失 → 直接提示，不进确认面板。
      if (!looksLikeBundle(bundle)) {
        toast.error(t("settings.configBackup.invalidFile"));
        return;
      }
      setPending(bundle);
    } catch {
      toast.error(t("settings.configBackup.invalidFile"));
    } finally {
      // 允许再次选同一文件触发 change。
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleConfirmImport = async () => {
    if (!pending) return;
    try {
      const res = await importConfig.mutateAsync(pending);
      const data = res.data;
      toast.success(
        t("settings.configBackup.importDone", {
          applied: data.applied,
          skipped: data.skipped,
        }),
      );
      for (const warning of data.warnings ?? []) toast.warning(warning);
      setPending(null);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("settings.configBackup.importFailed"),
      );
    }
  };

  const pendingCount = pending ? Object.keys(pending.settings).length : 0;

  return (
    <section className="px-5 py-5">
      <h3 className="font-heading text-sm font-medium text-foreground">
        {t("settings.configBackup.title")}
      </h3>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.configBackup.description")}
      </p>

      <p className="mt-3 flex items-start gap-1.5 rounded-[8px] border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
        <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        <span>{t("settings.configBackup.sensitiveNotice")}</span>
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void handleExport()}
          disabled={exporting}
          className={cn(buttonVariants({ size: "sm", variant: "outline" }), "gap-1.5")}
        >
          {exporting ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Download className="size-3.5" aria-hidden />
          )}
          {t("settings.configBackup.export")}
        </button>

        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className={cn(buttonVariants({ size: "sm" }), "gap-1.5")}
        >
          <Upload className="size-3.5" aria-hidden />
          {t("settings.configBackup.import")}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => void handleFile(event.target.files?.[0])}
        />
      </div>

      {pending ? (
        <div className="mt-4 rounded-lg border border-border bg-white/[0.02] p-3">
          <p className="text-xs font-medium text-foreground">
            {t("settings.configBackup.preview", { n: pendingCount })}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.configBackup.previewMeta", {
              version: pending.app_version || "—",
              generatedAt: pending.generated_at
                ? new Date(pending.generated_at).toLocaleString()
                : "—",
            })}
          </p>
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleConfirmImport()}
              disabled={importConfig.isPending}
              className={cn(buttonVariants({ size: "sm" }), "gap-1.5")}
            >
              {importConfig.isPending ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : null}
              {t("settings.configBackup.confirm")}
            </button>
            <button
              type="button"
              onClick={() => setPending(null)}
              disabled={importConfig.isPending}
              className={cn(buttonVariants({ size: "sm", variant: "ghost" }))}
            >
              {t("settings.configBackup.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
