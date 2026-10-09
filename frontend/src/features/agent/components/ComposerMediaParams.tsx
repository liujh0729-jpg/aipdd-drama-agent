// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 输入区媒体参数选择器：图片模式给分辨率，视频模式给分辨率 + 时长。
//
// 档位来自当前生效模型（Agent 设置里的 image_model / video_model，决议链与后端
// body→settings→default 的前两级一致）在媒体目录里的声明，解析口径见 mediaParams。
// 组件只把「用户显式选择」上抛给父级（onChange），不主动写默认值进 value ——
// 展示默认由 display* 函数算，未触碰时回合不携带参数。
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Clock, Monitor, Sparkles } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatResolutionLabel } from "@/features/canvas/domain/mediaModelOptions";
import { useFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";
import { useFreezoneVideoModels } from "@/features/canvas/hooks/useFreezoneVideoModels";
import { useAgentSettings, type AgentDialogMode } from "@/lib/queries/agent";

import {
  displayImageSize,
  displayVideoDuration,
  displayVideoHdResolution,
  displayVideoResolution,
  imageSizeOptions,
  matchCatalogModel,
  videoDurationBounds,
  videoDurationChoices,
  videoResolutionOptions,
  VIDEO_HD_RESOLUTION_OPTIONS,
  type AgentMediaParamsValue,
} from "../mediaParams";

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export interface ComposerMediaParamsProps {
  /** 当前项目（媒体目录按项目下发）。 */
  project: string;
  mode: AgentDialogMode;
  value: AgentMediaParamsValue;
  onChange: (value: AgentMediaParamsValue) => void;
}

export function ComposerMediaParams({
  project,
  mode,
  value,
  onChange,
}: ComposerMediaParamsProps) {
  const { t } = useTranslation();
  const settings = useAgentSettings().data?.data;
  const imageCatalog = useFreezoneImageModels(project);
  const videoCatalog = useFreezoneVideoModels(project);

  const imageModelName = str(settings?.image_model) || str(settings?.default_image_model);
  const videoModelName = str(settings?.video_model) || str(settings?.default_video_model);
  const imageModel = useMemo(
    () => matchCatalogModel(imageCatalog.models, imageModelName),
    [imageCatalog.models, imageModelName],
  );
  const videoModel = useMemo(
    () => matchCatalogModel(videoCatalog.models, videoModelName),
    [videoCatalog.models, videoModelName],
  );

  const sizeOptions = useMemo(() => imageSizeOptions(imageModel), [imageModel]);
  const resolutionOptions = useMemo(() => videoResolutionOptions(videoModel), [videoModel]);
  const durationBounds = useMemo(() => videoDurationBounds(videoModel), [videoModel]);
  const durationChoices = useMemo(() => videoDurationChoices(durationBounds), [durationBounds]);

  const displaySize = displayImageSize(value.imageSize, sizeOptions);
  const displayResolution = displayVideoResolution(value.videoResolution, resolutionOptions);
  const displayDuration = displayVideoDuration(value.videoDuration, durationBounds);
  // 超清开关：选了档位 = 开启（粘性设置，与其余媒体参数同语义）。
  const hdEnabled = Boolean(value.videoHdResolution);

  const showImageSize = mode === "image" && sizeOptions.length > 0;
  const showVideoParams = mode === "video";

  // 目录 / 模型变化后，把「已显式设定但已失效」的值收敛回写（如换到不支持 4K 的
  // 模型）。只收敛用户真正选过的字段，不把展示默认写进 value。
  useEffect(() => {
    if (showImageSize && value.imageSize && value.imageSize !== displaySize) {
      onChange({ ...value, imageSize: displaySize });
      return;
    }
    if (!showVideoParams) return;
    if (value.videoResolution && value.videoResolution !== displayResolution) {
      onChange({ ...value, videoResolution: displayResolution });
      return;
    }
    if (value.videoDuration != null && value.videoDuration !== displayDuration) {
      onChange({ ...value, videoDuration: displayDuration });
    }
  }, [
    showImageSize,
    showVideoParams,
    value,
    displaySize,
    displayResolution,
    displayDuration,
    onChange,
  ]);

  if (!showImageSize && !showVideoParams) return null;

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {showImageSize ? (
        <Select
          items={sizeOptions.map((option) => ({
            value: option,
            label: formatResolutionLabel(option),
          }))}
          value={displaySize}
          onValueChange={(next) => {
            if (next && next !== value.imageSize) onChange({ ...value, imageSize: next });
          }}
        >
          <SelectTrigger
            size="sm"
            className="h-7 w-[74px] min-w-0 text-[11px]"
            aria-label={t("agent.chat.params.resolution")}
            title={t("agent.chat.params.resolution")}
          >
            <Monitor className="size-3 shrink-0 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {sizeOptions.map((option) => (
              <SelectItem key={option} value={option} className="text-xs">
                {formatResolutionLabel(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
      {showVideoParams ? (
        <>
          <Select
            items={resolutionOptions.map((option) => ({
              value: option,
              label: formatResolutionLabel(option),
            }))}
            value={displayResolution}
            onValueChange={(next) => {
              if (next && next !== value.videoResolution) {
                onChange({ ...value, videoResolution: next });
              }
            }}
          >
            <SelectTrigger
              size="sm"
              className="h-7 w-[78px] min-w-0 text-[11px]"
              aria-label={t("agent.chat.params.resolution")}
              title={t("agent.chat.params.resolution")}
            >
              <Monitor className="size-3 shrink-0 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {resolutionOptions.map((option) => (
                <SelectItem key={option} value={option} className="text-xs">
                  {formatResolutionLabel(option)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            items={durationChoices.map((seconds) => ({
              value: String(seconds),
              label: t("agent.chat.params.durationValue", { seconds }),
            }))}
            value={String(displayDuration)}
            onValueChange={(next) => {
              const parsed = Number(next);
              if (Number.isFinite(parsed) && parsed !== value.videoDuration) {
                onChange({ ...value, videoDuration: parsed });
              }
            }}
          >
            <SelectTrigger
              size="sm"
              className="h-7 w-[78px] min-w-0 text-[11px]"
              aria-label={t("agent.chat.params.duration")}
              title={t("agent.chat.params.duration")}
            >
              <Clock className="size-3 shrink-0 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {durationChoices.map((seconds) => (
                <SelectItem key={seconds} value={String(seconds)} className="text-xs">
                  {t("agent.chat.params.durationValue", { seconds })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* 超清开关（req22）：打开后基础分辨率成片再走高清增强，可选目标档位。 */}
          <button
            type="button"
            role="switch"
            aria-checked={hdEnabled}
            aria-label={t("agent.chat.params.hd")}
            title={t("agent.chat.params.hdHint")}
            onClick={() => {
              if (hdEnabled) {
                onChange({ ...value, videoHdResolution: undefined });
              } else {
                onChange({ ...value, videoHdResolution: displayVideoHdResolution(undefined) });
              }
            }}
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-white/[0.05] hover:text-foreground"
          >
            <Sparkles className="size-3" />
            <span className="whitespace-nowrap">{t("agent.chat.params.hd")}</span>
            <span
              className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
                hdEnabled ? "bg-[rgb(var(--accent-rgb))]" : "bg-white/15"
              }`}
            >
              <span
                className={`pointer-events-none inline-block size-3 transform rounded-full bg-white shadow transition-transform ${
                  hdEnabled ? "translate-x-[13px]" : "translate-x-0.5"
                }`}
              />
            </span>
          </button>
          {hdEnabled ? (
            <Select
              items={VIDEO_HD_RESOLUTION_OPTIONS.map((option) => ({
                value: option.value,
                label: option.label,
              }))}
              value={displayVideoHdResolution(value.videoHdResolution)}
              onValueChange={(next) => {
                if (next && next !== value.videoHdResolution) {
                  onChange({ ...value, videoHdResolution: next });
                }
              }}
            >
              <SelectTrigger
                size="sm"
                className="h-7 w-[64px] min-w-0 text-[11px]"
                aria-label={t("agent.chat.params.hdResolution")}
                title={t("agent.chat.params.hdResolution")}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {VIDEO_HD_RESOLUTION_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value} className="text-xs">
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
        </>
      ) : null}
    </div>
  );
}