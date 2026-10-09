// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 输入区的回合媒体参数（图片分辨率 / 视频分辨率 + 时长）。
//
// 档位事实来源与画布完全一致：当前生效模型在「媒体模型」目录（/freezone/image|
// video/models）里声明的 resolutionOptions / minDuration / maxDuration。图片没声明
// 就不给选择器（模型不接受该参数，不下发）；视频按画布视频节点口径兜底通用档位。
//
// 「展示默认」与「用户显式设定」是两回事：只有用户显式选过的值才随回合发送
// （AgentMediaParamsValue 里的字段非 undefined），未触碰时保持空 —— 此时参数留给
// LLM 按消息内容自行决定（比如用户消息里说「3 秒镜头」）。
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import {
  clampDurationSeconds,
  defaultVideoResolution,
  pickAllowedOption,
  resolveModelSizeOptions,
  videoDurationBoundsForModel,
  videoResolutionOptionsForModel,
} from "@/features/canvas/domain/mediaModelOptions";
import { displayModelName } from "@/lib/provider-labels";

/** 用户显式设定过的回合媒体参数（undefined = 未设定，不随回合发送）。 */
export interface AgentMediaParamsValue {
  imageSize?: string;
  videoResolution?: string;
  videoDuration?: number;
  /** 视频超清档位（req22）：非空 = 开启两步处理（生成后再走高清增强）。 */
  videoHdResolution?: string;
}

/**
 * 超清目标档位（与后端 /freezone/video/upscale 同口径：按长边 1920/2560/3840）。
 * 与基础分辨率目录档位无关：它是高清增强算法的目标，固定三档。
 */
export const VIDEO_HD_RESOLUTION_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: "1080p", label: "1080P" },
  { value: "2k", label: "2K" },
  { value: "4k", label: "4K" },
];

/** 超清展示值：已选且在档位内保持，否则退首档 1080p（开关打开时的默认目标）。 */
export function displayVideoHdResolution(value: string | undefined): string {
  const selected = typeof value === "string" ? value.trim().toLowerCase() : "";
  return VIDEO_HD_RESOLUTION_OPTIONS.some((option) => option.value === selected)
    ? selected
    : VIDEO_HD_RESOLUTION_OPTIONS[0].value;
}

/** 未显式设定时，视频时长选择器展示的默认秒数（对齐画布视频节点默认时长）。 */
export const DEFAULT_VIDEO_DURATION_SECONDS = 5;

/** 归一化模型名：去渠道前缀 + 小写（与目录条目的 id / apiModel / label 同口径比较）。 */
function normalizeModelName(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  return raw ? displayModelName(raw).toLowerCase() : "";
}

/** 在目录里按归一化名找当前生效模型；找不到返回 null（选择器退化为各自兜底口径）。 */
export function matchCatalogModel(
  models: readonly ModelOption[],
  modelName: string,
): ModelOption | null {
  const needle = normalizeModelName(modelName);
  if (!needle) return null;
  for (const model of models) {
    if (
      [model.id, model.apiModel, model.label].some(
        (candidate) => normalizeModelName(candidate) === needle,
      )
    ) {
      return model;
    }
  }
  return null;
}

/** 图片分辨率档位（空数组 = 模型未声明该能力，调用方隐藏选择器且不下发）。 */
export function imageSizeOptions(model: ModelOption | null): string[] {
  return resolveModelSizeOptions(model);
}

/** 图片分辨率的展示值：显式选择有效时保持，否则退到目录首项（画布 ImageGenNode 口径）。 */
export function displayImageSize(value: string | undefined, options: readonly string[]): string {
  return pickAllowedOption(value, options);
}

/** 视频分辨率档位（无声明兜底通用档，与画布视频节点同口径）。 */
export function videoResolutionOptions(model: ModelOption | null): string[] {
  return videoResolutionOptionsForModel(model);
}

/** 视频分辨率的展示值：显式选择有效（忽略大小写）时保持，否则退到默认档（优先 720p）。 */
export function displayVideoResolution(
  value: string | undefined,
  options: readonly string[],
): string {
  const selected = typeof value === "string" ? value.trim() : "";
  const matched = selected
    ? options.find((option) => option.toLowerCase() === selected.toLowerCase())
    : undefined;
  return matched ?? defaultVideoResolution(options);
}

/** 视频时长边界（秒；目录未声明时 5-15）。 */
export function videoDurationBounds(model: ModelOption | null): { min: number; max: number } {
  return videoDurationBoundsForModel(model);
}

/** 时长选择器的候选整数秒列表（min..max 闭区间）。 */
export function videoDurationChoices(bounds: { min: number; max: number }): number[] {
  const choices: number[] = [];
  for (let value = bounds.min; value <= bounds.max; value += 1) choices.push(value);
  return choices;
}

/** 视频时长的展示值：显式选择收敛到边界内，否则用默认 5 秒（收敛后）。 */
export function displayVideoDuration(
  value: number | undefined,
  bounds: { min: number; max: number },
): number {
  const base =
    typeof value === "number" && Number.isFinite(value) && value > 0
      ? value
      : DEFAULT_VIDEO_DURATION_SECONDS;
  return clampDurationSeconds(base, bounds);
}