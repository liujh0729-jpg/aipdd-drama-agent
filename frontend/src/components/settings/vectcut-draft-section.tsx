// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 剪映草稿：配置 VectCut 服务上的草稿存储根目录。
// 保存草稿 / 资产库草稿上传 / 草稿包下载 / 草稿目录同步四个入口统一透传该目录；
// 未配置时留空，调用侧回退 VectCut 服务的部署默认（VECTCUT_DRAFT_DIR）。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useModelGatewayConfig,
  useSaveVectcutDraftDir,
} from "@/lib/queries/model-gateway";
import { cn } from "@/lib/utils";

const CHIP_CLASS = "rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium";

export function VectcutDraftSection() {
  const { t } = useTranslation();
  const statusQuery = useModelGatewayConfig();
  const saveDraftDir = useSaveVectcutDraftDir();

  const status = statusQuery.data?.data?.vectcut;
  const [draftDir, setDraftDir] = useState("");
  const seeded = useRef(false);

  // 只在首次拿到状态时用后端值填充输入框，避免后台刷新覆盖用户正在编辑的内容；
  // 保存成功后置回 false，让最新回显（trim 后的值）同步进输入框。
  useEffect(() => {
    if (status && !seeded.current) {
      setDraftDir(status.draftDir);
      seeded.current = true;
    }
  }, [status]);

  const configured = status?.configured ?? false;
  const dirty = status ? draftDir.trim() !== status.draftDir : false;

  const handleSave = () => {
    saveDraftDir.mutate(
      { draftDir: draftDir.trim() },
      {
        onSuccess: () => {
          seeded.current = false;
          toast.success(t("settings.vectcutDraft.saved"));
        },
        onError: (err) =>
          toast.error(
            err instanceof Error
              ? err.message
              : t("settings.vectcutDraft.saveFailed"),
          ),
      },
    );
  };

  const handleReset = () => {
    saveDraftDir.mutate(
      { draftDir: "" },
      {
        onSuccess: () => {
          seeded.current = false;
          toast.success(t("settings.vectcutDraft.resetDone"));
        },
        onError: (err) =>
          toast.error(
            err instanceof Error
              ? err.message
              : t("settings.vectcutDraft.saveFailed"),
          ),
      },
    );
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            configured ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.vectcutDraft.title")}
        </h3>
        {status && !configured ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.vectcutDraft.warningIconLabel")}
          />
        ) : null}
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.vectcutDraft.description")}
      </p>

      {statusQuery.error ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>
            {statusQuery.error.message || t("settings.vectcutDraft.loadFailed")}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void statusQuery.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : !status ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.vectcutDraft.loading")}
        </div>
      ) : (
        <>
          <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>
              {t("settings.vectcutDraft.statusLabel")}
              {": "}
            </span>
            {configured ? (
              <span className={cn(CHIP_CLASS, "text-emerald-300")}>
                {status.draftDir}
              </span>
            ) : (
              <span className="text-amber-300">
                {t("settings.vectcutDraft.statusDefault")}
              </span>
            )}
          </p>

          <div className="mt-4 space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
            <div className="grid grid-cols-[120px_1fr] items-start gap-3">
              <Label className="mt-1.5 justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
                {t("settings.vectcutDraft.fieldLabel")}
              </Label>
              <div className="space-y-1">
                <Input
                  value={draftDir}
                  placeholder={t("settings.vectcutDraft.placeholder")}
                  onChange={(event) => setDraftDir(event.target.value)}
                />
                <p className="text-[11px] text-muted-foreground/80">
                  {t("settings.vectcutDraft.fieldHint")}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                disabled={!dirty || saveDraftDir.isPending}
                onClick={handleSave}
              >
                {saveDraftDir.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : null}
                {t("settings.vectcutDraft.save")}
              </Button>
              {configured ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground"
                  disabled={saveDraftDir.isPending}
                  onClick={handleReset}
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                  {t("settings.vectcutDraft.reset")}
                </Button>
              ) : null}
              <span className="text-[11px] text-muted-foreground">
                {t("settings.vectcutDraft.saveHint")}
              </span>
            </div>
          </div>

          <div className="mt-4 space-y-1.5 text-xs leading-relaxed text-muted-foreground">
            <p>{t("settings.vectcutDraft.usageUpload")}</p>
            <p>{t("settings.vectcutDraft.usageDownload")}</p>
            <p>{t("settings.vectcutDraft.usageSync")}</p>
          </div>
        </>
      )}
    </section>
  );
}