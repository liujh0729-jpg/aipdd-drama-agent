// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「自动化生成分镜图」的产物选择面板。
//
// 一枚按钮管两种产物（分镜草图 / 渲染图），所以点下去不能只是「确认一次」：
// 这里把勾什么交给用户，并如实说出多选时的执行顺序——两者都选时先生成草图，
// 再据草图生成渲染图（顺序由服务端计划器保证，面板只负责把它讲清楚）。
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { StoryboardTarget } from "@/lib/episode-auto-pipeline";

/** 打开面板时的默认勾选：只出草图。渲染图是更贵的一步，不该由默认替用户点上。 */
export const DEFAULT_STORYBOARD_TARGETS: StoryboardTarget[] = ["sketch"];

export interface AutoStoryboardDialogProps {
  open: boolean;
  /** 已选中的集数，写进说明里，避免用户以为是对全集动手。 */
  episodeCount: number;
  onOpenChange(open: boolean): void;
  onConfirm(targets: StoryboardTarget[]): void;
}

const TARGET_ORDER: StoryboardTarget[] = ["sketch", "render"];

export function AutoStoryboardDialog({
  open,
  episodeCount,
  onOpenChange,
  onConfirm,
}: AutoStoryboardDialogProps) {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<StoryboardTarget[]>(
    DEFAULT_STORYBOARD_TARGETS,
  );

  // 每次重新打开都回到默认：上一轮的勾选不该悄悄留到这一轮，
  // 尤其渲染图那种耗时/耗额的产物。
  useEffect(() => {
    if (open) setTargets(DEFAULT_STORYBOARD_TARGETS);
  }, [open]);

  const toggle = (target: StoryboardTarget, checked: boolean) => {
    setTargets((prev) => {
      const next = new Set(prev);
      if (checked) next.add(target);
      else next.delete(target);
      // 固定顺序回传：调用方按「先草图后渲染」读这个数组。
      return TARGET_ORDER.filter((item) => next.has(item));
    });
  };

  const bothSelected = targets.length === TARGET_ORDER.length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("episode.list.autoStoryboardDialog.title")}</DialogTitle>
          <DialogDescription>
            {t("episode.list.autoStoryboardDialog.description", {
              count: episodeCount,
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {TARGET_ORDER.map((target) => (
            <label
              key={target}
              className="flex cursor-pointer items-start gap-2.5 rounded-[8px] border border-border bg-muted/40 p-2.5"
            >
              <Checkbox
                className="mt-0.5"
                checked={targets.includes(target)}
                onCheckedChange={(checked) => toggle(target, checked === true)}
              />
              <span className="space-y-0.5">
                <span className="block text-sm font-medium">
                  {t(`episode.list.autoStoryboardDialog.targets.${target}.label`)}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {t(`episode.list.autoStoryboardDialog.targets.${target}.hint`)}
                </span>
              </span>
            </label>
          ))}
          {bothSelected && (
            <p className="rounded-[8px] border border-primary/30 bg-primary/[0.06] px-2.5 py-2 text-xs leading-5 text-foreground">
              {t("episode.list.autoStoryboardDialog.chainHint")}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t("episode.list.autoStoryboardDialog.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={targets.length === 0}
            onClick={() => {
              onOpenChange(false);
              onConfirm(targets);
            }}
          >
            {t("episode.list.autoStoryboardDialog.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
