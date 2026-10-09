// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 一键流水线的「运行前设置」对话框：把需求里要求用户可选的生成参数
// （模型 / 导演风格 / 画幅 / 集数与时长 / 是否生成故事版）集中收集，
// 再由 PipelineControlBar 透传给服务端权威计划器。故事正文不在这里输入：
// 整链以「故事」栏目里的正文为唯一真源（运行前自动落盘），弹窗只回显字数。本组件不复制
// 任何阶段状态机——只做输入编排。样式遵循 DESIGN.md（圆角/间距/层级 tokens）。
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";
import { STORY_STYLE_VALUES, storyStyleI18nKey } from "@/lib/queries/story-project";
import { cn } from "@/lib/utils";

/** 一键流水线的一次运行输入（全部可选；留空即交后端智能默认/推导）。 */
export interface PipelineRunSettings {
  /** 图片生成模型 id（freezone 图片目录之一）；留空走后端默认模型链。 */
  model: string;
  /** 导演风格 key（'' = 自动推荐，对齐后端 STORY_STYLE_AUTO）。 */
  storyStyle: string;
  /** 画幅：竖屏 2:3 / 横屏 16:9。 */
  aspectRatio: "2:3" | "16:9";
  /** 目标集数；留空则由总时长/单集时长推导。 */
  episodeCount: number | null;
  /** 总时长（秒）；与单集时长一起推导集数。 */
  totalSeconds: number | null;
  /** 单集时长（秒）；同时推导目标镜数。 */
  secondsPerEpisode: number | null;
  /** 是否在分镜脚本阶段一并产出「故事版」（需提示词库配置了 story_version 系统提示）。 */
  includeStoryVersion: boolean;
}

export const EMPTY_PIPELINE_RUN_SETTINGS: PipelineRunSettings = {
  model: "",
  storyStyle: "",
  aspectRatio: "2:3",
  episodeCount: null,
  totalSeconds: null,
  secondsPerEpisode: null,
  includeStoryVersion: false,
};

/** 解析数字输入：空串→null，非法→null，否则取整（秒/集数都为整数）。 */
function parseNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

const ASPECTS: { value: PipelineRunSettings["aspectRatio"]; key: string }[] = [
  { value: "2:3", key: "pipeline.settings.aspectPortrait" },
  { value: "16:9", key: "pipeline.settings.aspectLandscape" },
];

export interface PipelineSettingsDialogProps {
  project: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  value: PipelineRunSettings;
  onChange(next: PipelineRunSettings): void;
  /** 提示词库是否配置了故事版系统提示；否则故事版开关禁用。 */
  storyVersionAvailable: boolean;
  /** 页面上当前故事正文字数；仅用于回显「将使用哪份故事」（undefined = 不由本页提供）。 */
  storyCharCount?: number;
}

export function PipelineSettingsDialog({
  project,
  open,
  onOpenChange,
  value,
  onChange,
  storyVersionAvailable,
  storyCharCount,
}: PipelineSettingsDialogProps) {
  const { t } = useTranslation();
  const imageCatalog = useFreezoneImageModels(project);

  const set = <K extends keyof PipelineRunSettings>(
    key: K,
    next: PipelineRunSettings[K],
  ) => onChange({ ...value, [key]: next });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("pipeline.settings.title")}</DialogTitle>
          <DialogDescription>{t("pipeline.settings.desc")}</DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
          {/* 故事来源回显：正文在「故事」栏目编辑，本弹窗不再重复录入。 */}
          {storyCharCount !== undefined ? (
            <p className="rounded-[8px] border border-border bg-muted/40 px-2.5 py-2 text-xs leading-5 text-muted-foreground">
              {t("pipeline.settings.storyFromPage", { count: storyCharCount })}
            </p>
          ) : null}

          {/* 画幅。 */}
          <div className="space-y-1.5">
            <Label>{t("pipeline.settings.aspect")}</Label>
            <div
              role="radiogroup"
              aria-label={t("pipeline.settings.aspect")}
              className="inline-flex items-center gap-0.5 rounded-[8px] border border-border bg-card p-0.5"
            >
              {ASPECTS.map((a) => (
                <button
                  key={a.value}
                  type="button"
                  role="radio"
                  aria-checked={value.aspectRatio === a.value}
                  onClick={() => set("aspectRatio", a.value)}
                  className={cn(
                    "rounded-[6px] px-3 py-1 text-xs font-medium transition-colors outline-none",
                    "focus-visible:ring-3 focus-visible:ring-ring/50",
                    value.aspectRatio === a.value
                      ? "bg-foreground/90 text-background"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {t(a.key)}
                  <span className="ml-1 font-mono text-[10px] opacity-70">
                    {a.value}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* 模型 + 导演风格。 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{t("pipeline.settings.model")}</Label>
              <Select
                value={value.model || "__default__"}
                onValueChange={(next) =>
                  set("model", next === "__default__" ? "" : String(next ?? ""))
                }
                disabled={imageCatalog.isLoading}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("pipeline.settings.modelDefault")} />
                </SelectTrigger>
                <SelectContent alignItemWithTrigger={false}>
                  <SelectItem value="__default__">
                    {t("pipeline.settings.modelDefault")}
                  </SelectItem>
                  {imageCatalog.models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>{t("pipeline.settings.style")}</Label>
              <Select
                value={value.storyStyle || "__auto__"}
                onValueChange={(next) =>
                  set("storyStyle", next === "__auto__" ? "" : String(next ?? ""))
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("storyPage.style.auto")} />
                </SelectTrigger>
                <SelectContent alignItemWithTrigger={false}>
                  <SelectItem value="__auto__">
                    {t("storyPage.style.auto")}
                  </SelectItem>
                  {STORY_STYLE_VALUES.filter((v) => v !== "").map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(storyStyleI18nKey(v))}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* 时长：集数 / 总时长 / 单集时长。 */}
          <div className="space-y-1.5">
            <Label>{t("pipeline.settings.duration")}</Label>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label
                  htmlFor="pipeline-episode-count"
                  className="text-xs font-normal text-muted-foreground"
                >
                  {t("pipeline.settings.episodeCount")}
                </Label>
                <Input
                  id="pipeline-episode-count"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={value.episodeCount ?? ""}
                  onChange={(e) => set("episodeCount", parseNumber(e.target.value))}
                  className="h-8 text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label
                  htmlFor="pipeline-total-seconds"
                  className="text-xs font-normal text-muted-foreground"
                >
                  {t("pipeline.settings.totalSeconds")}
                </Label>
                <Input
                  id="pipeline-total-seconds"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={value.totalSeconds ?? ""}
                  onChange={(e) => set("totalSeconds", parseNumber(e.target.value))}
                  className="h-8 text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label
                  htmlFor="pipeline-per-episode-seconds"
                  className="text-xs font-normal text-muted-foreground"
                >
                  {t("pipeline.settings.secondsPerEpisode")}
                </Label>
                <Input
                  id="pipeline-per-episode-seconds"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={value.secondsPerEpisode ?? ""}
                  onChange={(e) =>
                    set("secondsPerEpisode", parseNumber(e.target.value))
                  }
                  className="h-8 text-sm"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("pipeline.settings.durationHint")}
            </p>
          </div>

          {/* 故事版开关。 */}
          <label
            className={cn(
              "flex items-start gap-2.5 rounded-[8px] border border-border bg-muted/40 p-2.5",
              !storyVersionAvailable && "opacity-60",
            )}
          >
            <Checkbox
              className="mt-0.5"
              disabled={!storyVersionAvailable}
              checked={value.includeStoryVersion && storyVersionAvailable}
              onCheckedChange={(checked) =>
                set("includeStoryVersion", checked === true)
              }
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium">
                {t("pipeline.settings.includeStoryVersion")}
              </span>
              <span className="block text-xs text-muted-foreground">
                {storyVersionAvailable
                  ? t("pipeline.settings.includeStoryVersionHint")
                  : t("pipeline.settings.storyVersionMissing")}
              </span>
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>
            {t("pipeline.settings.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
