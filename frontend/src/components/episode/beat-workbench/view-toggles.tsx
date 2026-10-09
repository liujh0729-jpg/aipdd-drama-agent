// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ViewToggleId } from "@/hooks/use-view-toggles";
import type { SelectionState } from "@/hooks/use-selection";

const TOGGLES: { id: ViewToggleId; labelKey: string }[] = [
  { id: "sketch", labelKey: "episode.beat.sectionSketch" },
  { id: "render", labelKey: "episode.beat.sectionRender" },
];

interface ViewTogglesProps {
  toggles: Set<ViewToggleId>;
  onToggle: (id: ViewToggleId) => void;
  selection: SelectionState;
  totalBeats: number;
  onClearSelection: () => void;
  onSelectAll: () => void;
  onBatchRegenSketch: () => void;
  onBatchRegenRender: () => void;
  onBatchGenVideoPrompt: () => void;
  legendSlot?: React.ReactNode;
}

export function ViewToggles({
  toggles,
  onToggle,
  selection,
  totalBeats,
  onClearSelection,
  onSelectAll,
  onBatchRegenSketch,
  onBatchRegenRender,
  onBatchGenVideoPrompt,
  legendSlot,
}: ViewTogglesProps) {
  const { t } = useTranslation();
  const checkedCount = selection.mode === "multi" ? selection.checked.size : 0;
  const hasSelection = checkedCount > 0;
  // [aipdd fork delta] 全选/取消全选：勾满整集时按钮切换为「取消全选」。
  const allSelected = totalBeats > 0 && checkedCount >= totalBeats;

  return (
    <div className="flex shrink-0 flex-col">
      <div className="flex min-h-10 items-center gap-2 border-b border-white/[0.055] px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-y-1.5">
          <div className="flex items-center gap-1.5">
            {TOGGLES.map(({ id, labelKey }) => {
              const active = toggles.has(id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => onToggle(id)}
                  className={cn(
                    "inline-flex h-[22px] items-center gap-1.5 rounded-[5px] px-1.5 text-[11px] font-medium transition-colors",
                    active
                      ? "bg-white/[0.035] text-foreground"
                      : "text-muted-foreground hover:bg-foreground/[0.035] hover:text-foreground",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-3 items-center justify-center rounded-[3px] border transition-colors",
                      active
                        ? "border-primary/70 bg-primary/15 text-primary"
                        : "border-white/[0.09] bg-foreground/[0.025] text-transparent",
                    )}
                    aria-hidden
                >
                    <Check className="size-2" />
                  </span>
                  {t(labelKey)}
                </button>
              );
            })}
          </div>
          <div className="ml-5 flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onSelectAll}
              className="inline-flex h-[22px] items-center rounded-[5px] border border-white/[0.09] bg-foreground/[0.025] px-2 text-[11px] font-medium text-foreground/80 transition-colors hover:border-white/[0.16] hover:bg-foreground/[0.05] hover:text-foreground"
            >
              {t(allSelected ? "episode.workbench.view.deselectAll" : "episode.workbench.view.selectAll")}
            </button>
            {hasSelection ? (
              <>
                <span className="tabular-nums text-[11px] text-foreground/70">
                  {t("episode.workbench.view.selectedCount", { count: checkedCount })}
                </span>
                <button
                  type="button"
                  onClick={onBatchRegenSketch}
                  className="inline-flex h-[22px] items-center rounded-[5px] border border-primary/35 bg-primary/[0.07] px-2 text-[11px] font-medium text-primary/90 transition-colors hover:border-primary/55 hover:bg-primary/[0.12] hover:text-primary"
                >
                  {t("episode.workbench.view.batchRegenSketch")}
                </button>
                <button
                  type="button"
                  onClick={onBatchRegenRender}
                  className="inline-flex h-[22px] items-center rounded-[5px] border border-primary/35 bg-primary/[0.07] px-2 text-[11px] font-medium text-primary/90 transition-colors hover:border-primary/55 hover:bg-primary/[0.12] hover:text-primary"
                >
                  {t("episode.workbench.view.batchRegenRender")}
                </button>
                <button
                  type="button"
                  onClick={onBatchGenVideoPrompt}
                  className="inline-flex h-[22px] items-center rounded-[5px] border border-primary/35 bg-primary/[0.07] px-2 text-[11px] font-medium text-primary/90 transition-colors hover:border-primary/55 hover:bg-primary/[0.12] hover:text-primary"
                >
                  {t("episode.workbench.view.batchGenVideoPrompt")}
                </button>
                <button
                  type="button"
                  onClick={onClearSelection}
                  className="inline-flex h-[22px] items-center rounded-md px-2 text-[11px] text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
                >
                  {t("episode.workbench.view.clear")}
                </button>
              </>
            ) : (
              <span className="text-[11px] text-foreground/60">
                {t("episode.workbench.view.selectionHint")}
              </span>
            )}
          </div>
        </div>
        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground/75">
          {t("episode.workbench.view.totalShots", { count: totalBeats })}
        </span>
      </div>
      {legendSlot}
    </div>
  );
}
