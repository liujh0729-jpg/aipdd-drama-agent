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
  useRenderSettings,
  useUpdateRenderSettings,
  type RenderSettingsUpdate,
} from "@/lib/queries/render-settings";
import {
  WORKBENCH_SELECT_CONTENT_CLASS,
  WORKBENCH_SELECT_ITEM_CLASS,
  WORKBENCH_SELECT_TRIGGER_CLASS,
} from "./toolbar-select-styles";

interface RenderSettingsControlsProps {
  project: string;
}

export function RenderModelSelect({ project }: { project: string }) {
  const { t } = useTranslation();
  const settings = useRenderSettings(project);
  const updateSettings = useUpdateRenderSettings(project);
  const data = settings.data?.data;
  const options = useMemo(
    () => Object.entries(data?.options ?? {}),
    [data?.options],
  );
  const selectedLabel =
    options.find(([value]) => value === data?.render_image_selection)?.[1] ??
    data?.render_image_selection ??
    t("episode.renderSettings.modelPlaceholder");

  const patchSettings = async (patch: RenderSettingsUpdate) => {
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
        {t("episode.renderSettings.model")}
      </Label>
      <Select
        value={data.render_image_selection}
        onValueChange={(value) => {
          if (value && value !== data.render_image_selection) {
            void patchSettings({ render_image_selection: value });
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

/** Full controls block — kept for backwards compat. */
export function RenderSettingsControls({
  project,
}: RenderSettingsControlsProps) {
  const { t } = useTranslation();
  const settings = useRenderSettings(project);
  const updateSettings = useUpdateRenderSettings(project);
  const data = settings.data?.data;
  const options = useMemo(
    () => Object.entries(data?.options ?? {}),
    [data?.options],
  );
  const selectedLabel =
    options.find(([value]) => value === data?.render_image_selection)?.[1] ??
    data?.render_image_selection ??
    t("episode.renderSettings.modelPlaceholder");

  const patchSettings = async (patch: RenderSettingsUpdate) => {
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

  return (
    <div className="flex min-h-7 flex-wrap items-center gap-1.5">
      <Label className="text-xs text-muted-foreground">
        {t("episode.renderSettings.model")}
      </Label>
      <Select
        value={data.render_image_selection}
        onValueChange={(value) => {
          if (value && value !== data.render_image_selection) {
            void patchSettings({ render_image_selection: value });
          }
        }}
        disabled={disabled || options.length === 0}
      >
        <SelectTrigger className="h-7 w-52 text-xs">
          <SelectValue>{() => selectedLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {options.map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
