// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 媒体模型能力的统一解析入口。
 *
 * 每个模型支持哪些分辨率 / 画面比例 / 画质，都由后台「媒体模型」配置下发，
 * 经 `/freezone/image|video/models` 进入前端。画布上的所有生成 / 编辑面板都
 * 必须从这里读，不允许各自维护一份常量 —— 否则后台改了配置前端不跟随。
 *
 * 这里的 FALLBACK_VIDEO_* 常量只是「视频通用候选档」常量（供画布在目录未声明时
 * 仍有一份默认比例 / 分辨率可用，见 VideoNode），**不再**作为模型能力的解析兜底：
 * 真实目录条目没声明某项能力，就按「这个模型不接受
 * 该参数」处理 —— 不给下拉、不下发。否则后台改了这个模型支持的档位，
 * 前端仍然会拿自己的常量凑一份出来发给网关。
 *
 * [aipdd fork delta] 图片侧以前配套的 FALLBACK_IMAGE_* 常量随写死的兜底模型
 * 列表（SHARED_MODELS）一同移除：图片的档位与模型清单都以目录为唯一来源。
 */

/** 目录条目里与「能力」相关的字段，image 与 video 条目都满足这个形状。 */
export interface MediaModelCapabilities {
  resolutionOptions?: string[] | null;
  ratioOptions?: string[] | null;
  qualityOptions?: string[] | null;
}

/** 目录未声明分辨率时的视频兜底档位。 */
export const FALLBACK_VIDEO_RESOLUTION_OPTIONS: readonly string[] = [
  '480p',
  '720p',
  '1080p',
];

/**
 * 目录未声明画面比例时的视频兜底档位。
 *
 * 首项 `auto` 表示跟随首帧 / 参考图，是前端语义而非某个模型的能力；目录一旦
 * 声明了 ratioOptions 就完全以目录为准，不再注入 auto。
 */
export const FALLBACK_VIDEO_ASPECT_OPTIONS: readonly string[] = [
  'auto',
  '16:9',
  '4:3',
  '1:1',
  '3:4',
  '9:16',
  '21:9',
];

function normalizeOptions(values: string[] | null | undefined): string[] {
  return (values ?? []).map((item) => item.trim()).filter(Boolean);
}

/**
 * 分辨率在界面上一律使用小写展示。
 *
 * 只用于显示，不改变节点保存值、计费档位或提交给后端的原始值；这样也能兼容
 * 旧目录里仍然存在的 `2K` / `1080P`。
 */
export function formatResolutionLabel(value: string): string {
  return value.trim().toLowerCase();
}

function normalizeRatioOptions(values: string[] | null | undefined): string[] {
  return [
    ...new Set(
      normalizeOptions(values).map((item) =>
        ['auto', 'adaptive'].includes(item.toLowerCase()) ? 'auto' : item,
      ),
    ),
  ];
}

/**
 * 选中模型支持的分辨率档位。
 *
 * [aipdd fork delta] 没有兜底：目录没声明就返回空数组，调用方据此隐藏选择器并且
 * 不下发该参数 —— 档位的事实来源只有模型自己的声明一处。
 */
export function resolveModelSizeOptions(
  model: MediaModelCapabilities | null | undefined,
): string[] {
  return normalizeOptions(model?.resolutionOptions);
}

/**
 * 视频模型支持的分辨率档位。
 *
 * [aipdd fork delta] 与画布视频节点的 `videoQualityOptionsForModel` 同口径：
 * 目录有声明就用声明，没声明兜底 FALLBACK_VIDEO_RESOLUTION_OPTIONS —— 视频是
 * 任务式接口、分辨率总有系统默认档，不像图像那样「不声明即不下发」。
 */
export function videoResolutionOptionsForModel(
  model: MediaModelCapabilities | null | undefined,
): string[] {
  const options = normalizeOptions(model?.resolutionOptions);
  return options.length > 0 ? options : [...FALLBACK_VIDEO_RESOLUTION_OPTIONS];
}

/**
 * 视频分辨率的默认档：优先 720p（与画布视频节点 `normalizeVideoQuality` 同口径），
 * 没有 720p 就用目录首项。
 */
export function defaultVideoResolution(options: readonly string[]): string {
  return options.find((option) => option.toLowerCase() === '720p') ?? options[0] ?? '720p';
}

/** 目录未声明视频时长边界时的兜底（秒；与画布 videoNodeHelpers.DEFAULT_DURATION_* 同值）。 */
export const FALLBACK_VIDEO_DURATION_MIN = 5;
export const FALLBACK_VIDEO_DURATION_MAX = 15;

/**
 * 目录条目声明的时长边界（秒）。
 *
 * 未声明 / 非法值时按画布视频节点口径退到 5-15（见 `videoDurationBoundsForModel`）。
 */
export function videoDurationBoundsForModel(
  model: { minDuration?: number | null; maxDuration?: number | null } | null | undefined,
): { min: number; max: number } {
  const min = Number(model?.minDuration);
  const max = Number(model?.maxDuration);
  const resolvedMin = Number.isFinite(min) && min > 0 ? min : FALLBACK_VIDEO_DURATION_MIN;
  const resolvedMax = Number.isFinite(max) && max >= resolvedMin ? max : FALLBACK_VIDEO_DURATION_MAX;
  return { min: resolvedMin, max: resolvedMax };
}

/** 把时长收敛为边界内的整数秒（与画布视频节点 `clampVideoDuration` 同口径）。 */
export function clampDurationSeconds(
  value: number,
  bounds: { min: number; max: number },
): number {
  return Math.min(Math.max(Math.round(value), bounds.min), bounds.max);
}

/** 选中模型支持的画面比例；同 `resolveModelSizeOptions`，无兜底。 */
export function resolveModelAspectOptions(
  model: MediaModelCapabilities | null | undefined,
): string[] {
  return normalizeRatioOptions(model?.ratioOptions);
}

/**
 * 选中模型支持的画质档位。
 *
 * 目录没声明就代表这个模型不接受 quality 参数，此时下发 quality 会被网关
 * 拒掉。后端同一条口径：`_resolve_catalog_request` 只在条目有 qualityOptions
 * 时才给请求 schema 打 includeQuality。
 */
export function resolveModelQualityOptions(
  model: MediaModelCapabilities | null | undefined,
): string[] {
  return normalizeOptions(model?.qualityOptions);
}

/**
 * 该模型是否接受画质参数。
 *
 * 判据是目录里有没有配 `qualityOptions`，不是模型名长什么样 —— 后台新增一个
 * 支持画质的模型时，前端不需要跟着改代码。
 */
export function modelSupportsQuality(
  model: MediaModelCapabilities | null | undefined,
): boolean {
  return resolveModelQualityOptions(model).length > 0;
}

/**
 * 把已选值收敛到当前模型允许的取值范围内。
 *
 * 换模型后旧的选择可能已经不在新模型的档位里，此时退回首个可用档位，
 * 避免把模型不支持的值提交给后端。
 *
 * [aipdd fork delta] `options` 为空表示模型压根没声明这项能力（不是「一个档位都
 * 不支持」），此时没有可收敛的范围：原样返回已选值，而不是退到 `options[0]` 拿到
 * 一个 `undefined` / 空串发给后端。
 */
export function pickAllowedOption(
  current: string | null | undefined,
  options: readonly string[],
  fallback?: string,
): string {
  const normalized = String(current ?? '').trim();
  if (normalized && options.includes(normalized)) return normalized;
  if (options.length === 0) return normalized || fallback || '';
  const caseInsensitive = normalized
    ? options.find((option) => option.toLowerCase() === normalized.toLowerCase())
    : undefined;
  return caseInsensitive ?? options[0] ?? fallback ?? '';
}
