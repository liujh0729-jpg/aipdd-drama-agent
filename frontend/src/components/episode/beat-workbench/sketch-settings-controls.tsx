// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Loader2 } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useSketchSettings,
  useUpdateSketchSettings,
  type SketchAspectRatio,
  type SketchSettingsUpdate,
} from "@/lib/queries/sketch-settings";
import {
  WORKBENCH_SELECT_CONTENT_CLASS,
  WORKBENCH_SELECT_ITEM_CLASS,
  WORKBENCH_SELECT_TRIGGER_CLASS,
} from "./toolbar-select-styles";

interface SketchSettingsControlsProps {
  project: string;
  aspectRatio: SketchAspectRatio;
  onAspectRatioChange: (aspectRatio: SketchAspectRatio) => void;
}

/** Internal control piece — used by BatchBar to compose controls in one row. */
export function SketchModelSelect({
  project,
}: {
  project: string;
}) {
  const { t } = useTranslation();
  const settings = useSketchSettings(project);
  const updateSettings = useUpdateSketchSettings(project);
  const data = settings.data?.data;
  const options = useMemo(
    () => Object.entries(data?.options ?? {}),
    [data?.options],
  );
  const selectedLabel =
    options.find(([value]) => value === data?.sketch_image_selection)?.[1] ??
    data?.sketch_image_selection ??
    t("episode.sketchSettings.modelPlaceholder");

  const patchSettings = async (patch: SketchSettingsUpdate) => {
    try {
      const res = await updateSettings.mutateAsync(patch);
      if (res.ok === false) toast.error(res.error || t("common.error"));
    } catch {
      toast.error(t("common.error"));
    }
  };

  if (settings.isLoading) {
    return (
      <div className="flex h-[26px] min-w-40 items-center justify-center text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
      </div>
    );
  }
  if (!data) return null;

  const disabled = updateSettings.isPending;

  return (
    <div className="flex items-center gap-2">
      <Label className="whitespace-nowrap text-[11px] text-muted-foreground">
        {t("episode.sketchSettings.model")}
      </Label>
      <Select
        value={data.sketch_image_selection}
        onValueChange={(value) => {
          if (value && value !== data.sketch_image_selection) {
            void patchSettings({ sketch_image_selection: value });
          }
        }}
        disabled={disabled || options.length === 0}
      >
        <SelectTrigger className={cn(WORKBENCH_SELECT_TRIGGER_CLASS, "w-28")}>
          <SelectValue>{() => selectedLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent
          align="start"
          sideOffset={8}
          alignItemWithTrigger={false}
          className={WORKBENCH_SELECT_CONTENT_CLASS}
        >
          {options.map(([value, label]) => (
            <SelectItem
              key={value}
              value={value}
              className={WORKBENCH_SELECT_ITEM_CLASS}
            >
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function SketchAspectCheckbox({
  aspectRatio,
  onAspectRatioChange,
  disabled = false,
  flat = false,
}: {
  aspectRatio: SketchAspectRatio;
  onAspectRatioChange: (aspectRatio: SketchAspectRatio) => void;
  disabled?: boolean;
  flat?: boolean;
}) {
  const { t } = useTranslation();
  const selectedValue = aspectRatio === "16:9" ? "16:9" : "2:3";

  return (
    <div className={cn(
      "flex items-center gap-2.5 text-[11px]",
      flat
        ? ""
        : "rounded-md border border-border/70 px-2",
    )}>
      <Label className="whitespace-nowrap text-[11px] text-muted-foreground">
        {t("episode.sketchSettings.aspectRatio")}
      </Label>
      <Select
        value={selectedValue}
        disabled={disabled}
        onValueChange={(value) => {
          if (!value) return;
          onAspectRatioChange(value === "16:9" ? "16:9" : "2:3");
        }}
      >
        <SelectTrigger
          aria-label={t("episode.sketchSettings.aspectRatio")}
          className={cn(WORKBENCH_SELECT_TRIGGER_CLASS, "w-[70px]")}
        >
          <SelectValue>{() => selectedValue}</SelectValue>
        </SelectTrigger>
        <SelectContent
          align="start"
          sideOffset={8}
          alignItemWithTrigger={false}
          className={WORKBENCH_SELECT_CONTENT_CLASS}
        >
          <SelectItem value="2:3" className={WORKBENCH_SELECT_ITEM_CLASS}>
            2:3
          </SelectItem>
          <SelectItem value="16:9" className={WORKBENCH_SELECT_ITEM_CLASS}>
            16:9
          </SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

/** Full controls block (model + aspect) — kept for backwards compat. */
export function SketchSettingsControls({
  project,
  aspectRatio,
  onAspectRatioChange,
}: SketchSettingsControlsProps) {
  const { t } = useTranslation();
  const settings = useSketchSettings(project);
  const updateSettings = useUpdateSketchSettings(project);
  const data = settings.data?.data;
  const options = useMemo(
    () => Object.entries(data?.options ?? {}),
    [data?.options],
  );

  const patchSettings = async (patch: SketchSettingsUpdate) => {
    try {
      const res = await updateSettings.mutateAsync(patch);
      if (res.ok === false) toast.error(res.error || t("common.error"));
    } catch {
      toast.error(t("common.error"));
    }
  };

  if (settings.isLoading) {
    return (
      <div className="flex h-7 min-w-44 items-center justify-center text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
      </div>
    );
  }
  if (!data) return null;

  const disabled = updateSettings.isPending;
  const selectedLabel =
    options.find(([value]) => value === data?.sketch_image_selection)?.[1] ??
    data?.sketch_image_selection ??
    t("episode.sketchSettings.modelPlaceholder");

  return (
    <div className="flex min-h-7 flex-wrap items-center gap-1.5">
      <Label className="text-xs text-muted-foreground">
        {t("episode.sketchSettings.model")}
      </Label>
      <Select
        value={data.sketch_image_selection}
        onValueChange={(value) => {
          if (value && value !== data.sketch_image_selection) {
            void patchSettings({ sketch_image_selection: value });
          }
        }}
        disabled={disabled || options.length === 0}
      >
        <SelectTrigger className="h-7 w-52 text-xs">
          <SelectValue>{() => selectedLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {options.map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div title={t("episode.sketchSettings.aspectChangeHint")}>
        <SketchAspectCheckbox
          aspectRatio={aspectRatio}
          onAspectRatioChange={onAspectRatioChange}
          disabled={disabled}
        />
      </div>
    </div>
  );
}
