// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「系统整体自检」：一键看清环境 / 模型 / 调用就绪 / 存储整盘状态。
// 后端只回事实（level + detail），名称 / 修复文案按 section + id 取 i18n。运行依赖
// 直接复用「环境安装」的依赖词条，分类默认模型复用「模型与渠道」的栏目词条。
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Check,
  Database,
  HardDrive,
  Info,
  Loader2,
  Network,
  RefreshCw,
  Shapes,
  X,
  type LucideIcon,
} from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { useDiagnostics, type DiagnosticItem } from "@/lib/queries/diagnostics";
import { cn } from "@/lib/utils";

const LEVEL_ICON: Record<DiagnosticItem["level"], LucideIcon> = {
  pass: Check,
  warn: AlertTriangle,
  fail: X,
  info: Info,
};

const LEVEL_BADGE: Record<DiagnosticItem["level"], string> = {
  pass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  fail: "border-red-500/30 bg-red-500/10 text-red-300",
  info: "border-white/10 bg-white/[0.03] text-muted-foreground",
};

const SECTION_ICON: Record<string, LucideIcon> = {
  environment: Network,
  model_gateway: Database,
  default_models: Shapes,
  storage: HardDrive,
};

/** 按 section + id 取展示名称：能复用既有条词条就复用，避免重复建键。 */
function itemLabel(t: (key: string) => string, sectionId: string, id: string): string {
  if (sectionId === "environment") return t(`settings.envSetup.dependencies.${id}.name`);
  if (sectionId === "default_models") return t(`settings.modelConfig.modelList.tabs.${id}`);
  return t(`settings.diagnostics.items.${id}.name`);
}

/** 修复指引：未就绪项按来源取文案。 */
function itemFix(t: (key: string) => string, sectionId: string, id: string): string {
  if (sectionId === "environment") return t(`settings.envSetup.dependencies.${id}.fix`);
  if (sectionId === "default_models") return t("settings.diagnostics.fix.defaultModel");
  return t(`settings.diagnostics.items.${id}.fix`);
}

export function DiagnosticsSection({
  onNavigate,
}: {
  /** 从设置弹窗注入的页间跳转（未设默认模型一键跳「模型与渠道」）。 */
  onNavigate?: (page: "models") => void;
}) {
  const { t } = useTranslation();
  const query = useDiagnostics();

  const report = query.data?.data;
  const summary = report?.summary;

  return (
    <section className="px-5 py-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.diagnostics.title")}
        </h3>
        <button
          type="button"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          className={cn(
            buttonVariants({ size: "xs", variant: "outline" }),
            "h-6 shrink-0 gap-1 px-2 text-[11px]",
          )}
        >
          <RefreshCw className={cn("size-3", query.isFetching && "animate-spin")} aria-hidden />
          {t("settings.diagnostics.refresh")}
        </button>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.diagnostics.description")}
      </p>

      {query.isLoading ? (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          {t("settings.diagnostics.loading")}
        </div>
      ) : query.error || !report ? (
        <div className="mt-3 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{t("settings.diagnostics.loadFailed")}</span>
          <button
            type="button"
            onClick={() => void query.refetch()}
            className={cn(buttonVariants({ size: "sm", variant: "outline" }))}
          >
            {t("common.retry")}
          </button>
        </div>
      ) : (
        <>
          <p
            className={cn(
              "mt-3 flex items-center gap-1.5 rounded-[8px] border px-3 py-2 text-xs",
              summary?.all_ok
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : "border-amber-500/30 bg-amber-500/10 text-amber-300",
            )}
          >
            {summary?.all_ok ? (
              <Check className="size-3.5" aria-hidden />
            ) : (
              <AlertTriangle className="size-3.5" aria-hidden />
            )}
            {summary?.all_ok
              ? t("settings.diagnostics.allReady")
              : t("settings.diagnostics.attention", {
                  n: (summary?.warn ?? 0) + (summary?.fail ?? 0),
                })}
          </p>

          <div className="mt-4 space-y-5">
            {report.sections.map((section) => {
              const SectionIcon = SECTION_ICON[section.id] ?? Info;
              return (
                <div key={section.id}>
                  <h4 className="flex items-center gap-1.5 text-[11px] font-normal uppercase tracking-wide text-muted-foreground">
                    <SectionIcon className="size-3.5" aria-hidden />
                    {t(`settings.diagnostics.sections.${section.id}`)}
                  </h4>
                  <div className="mt-2 overflow-hidden rounded-lg border border-border bg-white/[0.02]">
                    {section.items.map((item, index) => (
                      <DiagnosticRow
                        key={item.id}
                        sectionId={section.id}
                        item={item}
                        last={index === section.items.length - 1}
                        onNavigate={onNavigate}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

function DiagnosticRow({
  sectionId,
  item,
  last,
  onNavigate,
}: {
  sectionId: string;
  item: DiagnosticItem;
  last: boolean;
  onNavigate?: (page: "models") => void;
}) {
  const { t } = useTranslation();
  const Icon = LEVEL_ICON[item.level] ?? Info;
  const needsAttention = item.level === "warn" || item.level === "fail";

  return (
    <div
      className={cn(
        "flex items-start gap-3 px-3 py-2.5",
        !last && "border-b border-white/[0.06]",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          item.level === "pass" ? "text-emerald-400" : "",
          item.level === "fail" ? "text-red-300" : "",
          item.level === "warn" ? "text-amber-300" : "",
          item.level === "info" ? "text-muted-foreground" : "",
        )}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {itemLabel(t, sectionId, item.id)}
          </span>
          <span
            className={cn(
              "inline-flex shrink-0 items-center rounded-md border px-2 py-0.5 text-[11px]",
              LEVEL_BADGE[item.level],
            )}
          >
            {t(`settings.diagnostics.status.${item.level}`)}
          </span>
        </div>
        {item.detail ? (
          <p className="mt-1 truncate text-[11px] leading-relaxed text-muted-foreground">
            {item.detail}
          </p>
        ) : null}
        {needsAttention ? (
          <p className="mt-1 flex items-start gap-1.5 text-[11px] leading-relaxed text-amber-300/90">
            <span>{itemFix(t, sectionId, item.id)}</span>
          </p>
        ) : null}
        {sectionId === "default_models" && item.level === "fail" && onNavigate ? (
          <button
            type="button"
            onClick={() => onNavigate("models")}
            className={cn(
              buttonVariants({ size: "xs", variant: "outline" }),
              "mt-1.5 h-6 px-2 text-[11px]",
            )}
          >
            {t("settings.diagnostics.goToModels")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
