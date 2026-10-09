// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { type DragEvent } from "react";
import { type NodeProps } from "@xyflow/react";
import { Film, Images, Layers, Sparkles, type LucideIcon } from "lucide-react";
import type { TFunction } from "i18next";
import { isExportImageNode, isImageEditNode, isImageGenNode, isStoryboardGenNode, isUploadNode, type CanvasNode, type Seedance2SceneOptimize, type VideoGenMode, type VideoGenQuality, type VideoNodeData } from "@/features/canvas/domain/canvasNodes";
import { videoModelReferenceDisabledReason, type VideoEmptyStateCtaMode } from "@/features/canvas/nodes/shared/videoModelCapabilities";
import { FALLBACK_VIDEO_ASPECT_OPTIONS, FALLBACK_VIDEO_RESOLUTION_OPTIONS } from "@/features/canvas/domain/mediaModelOptions";
import { isVideoFile } from "@/features/canvas/application/videoFileTypes";
import { type FreezoneVideoAspectRatio, type FreezoneVideoResolution } from "@/api/ops";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

export type VideoNodeProps = NodeProps & {
  id: string;
  data: VideoNodeData;
  selected?: boolean;
};

export const DEFAULT_WIDTH = 580;
export const DEFAULT_HEIGHT = 380;
/**
 * 视频生成的计费 feature key。主体（错误态重试的计费探针）与操作面板（估价
 * 展示 + 提交置灰）共用，必须同一口径——放主体导出、面板 import。
 */
export const VIDEO_GENERATE_FEATURE_KEY = "freezone.video_generate";

export const MIN_WIDTH = 480;
export const MIN_HEIGHT = 280;
export const MAX_WIDTH = 1100;
export const MAX_HEIGHT = 1000;

// 图片节点的默认落位尺寸（与 ImageGenNode 的 DEFAULT_WIDTH/HEIGHT 对齐）。
// 「全能参考 / 图片参考」会在视频节点左侧新建一个图片节点，排版要按它的真实尺寸算。
export const IMAGE_GEN_NODE_WIDTH = 580;
export const IMAGE_GEN_NODE_HEIGHT = 360;

// 与图片节点同理：参考素材那一行落地后，提示词的可写高度被压得偏小，往上加一截。
export const OPERATIONS_PANEL_HEIGHT = 328;
export const OPERATIONS_PANEL_GAP = 12;
// Extend the ops panel beyond the node's left/right edges so the textarea +
// chips have more room than the video frame itself.
export const OPERATIONS_PANEL_OVERHANG = 120;

// 空态 CTA 的图标 + 文案：具体展示哪几个模式由 `videoEmptyStateCtaModes(modelId)`
// 按模型能力决定（见 shared/videoModelCapabilities.ts），这里只负责「模式 → 外观」。
export const VIDEO_EMPTY_STATE_CTA_META: Record<
  VideoEmptyStateCtaMode,
  { Icon: LucideIcon; labelKey: string }
> = {
  allReference: { Icon: Sparkles, labelKey: "node.videoNode.cta.allReference" },
  imageReference: { Icon: Images, labelKey: "node.videoNode.cta.imageReference" },
  firstFrame: { Icon: Film, labelKey: "node.videoNode.cta.firstFrame" },
  imageToVideo: { Icon: Film, labelKey: "node.videoNode.cta.imageToVideo" },
  firstLastFrame: { Icon: Layers, labelKey: "node.videoNode.cta.firstLastFrame" },
};

// 各 genMode 对上游引用数量的硬上限。UI 用这张表把后端字段约束（多图 / 多模态
// 场景下）显式表达出来：超额 chip 标灰 + 从 @ 候选剔除，避免「prompt 引用了
// @图片10 但提交时被静默丢掉」。
//
// 表里没出现的模式默认不限制（textToVideo 不消费上游），走原有路径。
//   - allReference (omni)  ：image 1-9 / video 0-3 / audio 0-3。音频另有两条厂商时长
//                            约束——**逐条** 1.8~15.2s 和**总和** ≤15.2s（后台可配
//                            referenceAudioTotalMaxSeconds）——都在提交前单独校验，
//                            见 audioReferenceDurationRejection。时长口径不进这张表：
//                            这里只表达条数。
//   - videoEdit            ：默认 1 个源视频 + 5 张参考图；独立音频默认关闭，媒体目录
//                            配置 referenceAudioMax > 0 后按该上限开放。
//   - firstLastFrame       ：仅图片 2 张（首帧 + 尾帧），不允许任何视频 / 音频。
//                            图片 >2 时另有自动切到 allReference 的兜底（见
//                            VideoNode 内部 effect）。
//   - firstFrame / imageToVideo：都只接 1 张图；前者锁定首帧，后者作为整体画面参考。
export const REFERENCE_CAPS_BY_MODE: Partial<
  Record<VideoGenMode, { image: number; video: number; audio: number }>
> = {
  firstFrame: { image: 1, video: 0, audio: 0 },
  imageToVideo: { image: 1, video: 0, audio: 0 },
  imageReference: { image: 9, video: 0, audio: 0 },
  videoEdit: { image: 5, video: 1, audio: 0 },
  videoExtend: { image: 0, video: 1, audio: 0 },
  allReference: { image: 9, video: 3, audio: 3 },
  firstLastFrame: { image: 2, video: 0, audio: 0 },
};

// 后台「媒体模型」未给该模型配置比例 / 分辨率时的兜底档位。正常路径下这两项
// 都来自目录条目的 ratioOptions / resolutionOptions。
export const ASPECT_RATIOS: ReadonlyArray<FreezoneVideoAspectRatio> =
  FALLBACK_VIDEO_ASPECT_OPTIONS;
export const QUALITIES: ReadonlyArray<VideoGenQuality> = FALLBACK_VIDEO_RESOLUTION_OPTIONS;
export const SCENE_OPTIMIZE_OPTIONS: ReadonlyArray<Seedance2SceneOptimize> = ["anime", "realistic"];
export const DEFAULT_DURATION_MIN = 5;
export const DEFAULT_DURATION_MAX = 15;

export function qualityToResolution(q: VideoGenQuality): FreezoneVideoResolution {
  return q;
}

export function videoQualityOptionsForModel(
  model: { resolutionOptions?: string[] } | null | undefined,
): readonly VideoGenQuality[] {
  const options = (model?.resolutionOptions ?? []).map((item) => item.trim()).filter(Boolean);
  return options.length > 0 ? options : QUALITIES;
}

export function normalizeVideoQuality(
  value: VideoGenQuality | undefined,
  options: readonly VideoGenQuality[],
): VideoGenQuality {
  const configured = value
    ? options.find((option) => option.toLowerCase() === value.toLowerCase())
    : undefined;
  const fallback =
    options.find((option) => option.toLowerCase() === "720p") ?? options[0] ?? "720p";
  return configured ?? fallback;
}

export function videoDurationBoundsForModel(
  model: { minDuration?: number | null; maxDuration?: number | null } | null | undefined,
): { min: number; max: number } {
  const min = Number(model?.minDuration);
  const max = Number(model?.maxDuration);
  const resolvedMin = Number.isFinite(min) && min > 0 ? min : DEFAULT_DURATION_MIN;
  const resolvedMax = Number.isFinite(max) && max >= resolvedMin ? max : DEFAULT_DURATION_MAX;
  return { min: resolvedMin, max: resolvedMax };
}

export function clampVideoDuration(value: number, bounds: { min: number; max: number }): number {
  return Math.min(Math.max(Math.round(value), bounds.min), bounds.max);
}

/**
 * 把 client（视口）指针坐标换算成「视频画面矩形内」的 0..1 归一化坐标，
 * 供智能去字幕的框选拖拽使用。
 *
 * 媒体容器嵌在 React Flow 的缩放视口里：`getBoundingClientRect()` 返回的是
 * **视觉像素**（跟随画布 zoom 缩放），而 ResizeObserver 量出的布局尺寸与
 * `displayed` 画面矩形都是**布局像素**（未缩放）。指针偏移必须先按实测的
 * 视觉/布局比例除回布局像素，再与 `displayed` 混算——否则画布 zoom ≠ 1 时
 * 框选框会与鼠标位置错位。
 */
export function clientPointToDisplayedNormalized(
  clientX: number,
  clientY: number,
  containerRect: { left: number; top: number; width: number; height: number },
  layoutSize: { w: number; h: number },
  displayed: { left: number; top: number; width: number; height: number },
): { nx: number; ny: number } {
  const scaleX =
    layoutSize.w > 0 && containerRect.width > 0 ? containerRect.width / layoutSize.w : 1;
  const scaleY =
    layoutSize.h > 0 && containerRect.height > 0 ? containerRect.height / layoutSize.h : 1;
  const localX = (clientX - containerRect.left) / scaleX - displayed.left;
  const localY = (clientY - containerRect.top) / scaleY - displayed.top;
  const nx = displayed.width > 0 ? localX / displayed.width : 0;
  const ny = displayed.height > 0 ? localY / displayed.height : 0;
  return {
    nx: Math.max(0, Math.min(1, nx)),
    ny: Math.max(0, Math.min(1, ny)),
  };
}

// 音频节点的 durationMs 是懒加载的（波形播放器挂载读元数据后才写入），刚上传、
// 从未渲染过的音频节点可能为 null。提交前用一个临时 <audio> 探测真实时长兜底，
// 探测失败（CORS/网络等）返回 null，不阻断提交，交由后端兜底。
export function probeMediaDurationMs(url: string, media: "audio" | "video"): Promise<number | null> {
  return new Promise((resolve) => {
    if (!url) {
      resolve(null);
      return;
    }
    const element = document.createElement(media);
    let settled = false;
    const finish = (ms: number | null) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      element.onloadedmetadata = null;
      element.onerror = null;
      element.removeAttribute("src");
      element.load();
      resolve(ms);
    };
    const timer = window.setTimeout(() => finish(null), 8000);
    element.preload = "metadata";
    element.onloadedmetadata = () => {
      const secs = element.duration;
      finish(Number.isFinite(secs) && secs > 0 ? Math.round(secs * 1000) : null);
    };
    element.onerror = () => finish(null);
    element.src = url;
  });
}

export function probeAudioDurationMs(url: string): Promise<number | null> {
  return probeMediaDurationMs(url, "audio");
}

export function probeVideoDurationMs(url: string): Promise<number | null> {
  return probeMediaDurationMs(url, "video");
}

export function isSeedance2ValueModel(modelId: string | null | undefined): boolean {
  const normalized = String(modelId ?? "").trim().toLowerCase();
  return normalized === "newapi_seedance-2.0-value" ||
    normalized === "newapi_seedance-2.0-fast-value" ||
    normalized === "huimeng_seedance-2.0-value" ||
    normalized === "huimeng_seedance-2.0-fast-value";
}

// 模型能力判定（isHappyHorseVideoModel / isSeedance1xVideoModel /
// isSeedance2VideoModel / isGrokVideoChannelModel / isVideoModeSupportedByModel）
// 统一收敛到 nodes/shared/videoModelCapabilities.ts，作为 CTA / tab / 提交校验的
// 单一事实来源；这里仅额外叠加媒体目录声明的逐模式素材上限。

export function selectedVideoModelReferenceDisabledReason(
  model: ModelOption | null | undefined,
  counts: { images: number; videos: number; audios: number },
  mode: VideoGenMode,
  t: TFunction,
): string | null {
  const capabilityReason = videoModelReferenceDisabledReason(model, counts);
  if (capabilityReason) return t(capabilityReason);
  const caps = referenceCapsForMode(model, mode);
  if (!caps) return null;
  if (counts.images > caps.image) {
    return t("node.videoModel.reason.maxImages", { count: caps.image });
  }
  if (counts.videos > caps.video) {
    return caps.video === 0
      ? t("node.videoModel.reason.videoUnsupported")
      : t("node.videoModel.reason.maxVideos", { count: caps.video });
  }
  if (counts.audios > caps.audio) {
    return caps.audio === 0
      ? t("node.videoModel.reason.audioUnsupported")
      : t("node.videoModel.reason.maxAudios", { count: caps.audio });
  }
  return null;
}

// 首帧与单图图生视频的 1 张图是**结构性**的，不是模型容量。
// 所以这条不接受媒体目录 referenceImageMax 的覆盖——那个字段表达的是「这个模型最多
// 能吃几张参考图」，管的是参考类模式；让它盖住这里等于允许配置把首帧悄悄变成参考。
export const FIXED_IMAGE_CAP_BY_MODE: Partial<Record<VideoGenMode, number>> = {
  firstFrame: 1,
  imageToVideo: 1,
};

export function referenceCapsForMode(
  model: ModelOption | null | undefined,
  mode: VideoGenMode,
): { image: number; video: number; audio: number } | null {
  const defaults = REFERENCE_CAPS_BY_MODE[mode];
  if (!defaults) return null;
  if (mode === "videoExtend") return defaults;
  return {
    image: FIXED_IMAGE_CAP_BY_MODE[mode] ?? model?.referenceImageMax ?? defaults.image,
    video: model?.referenceVideoMax ?? defaults.video,
    audio: model?.referenceAudioMax ?? defaults.audio,
  };
}

export function hasConfiguredReferenceCaps(model: ModelOption | null | undefined): boolean {
  return (
    model?.referenceImageMax != null ||
    model?.referenceVideoMax != null ||
    model?.referenceAudioMax != null
  );
}

export function referenceFileExtension(value: string): string {
  try {
    const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const pathname = new URL(value, origin).pathname;
    return pathname.split(".").pop()?.toLowerCase() ?? "";
  } catch {
    return "";
  }
}

export function isValidReferenceLink(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      Boolean(parsed.hostname) &&
      !parsed.username &&
      !parsed.password
    );
  } catch {
    return false;
  }
}

export function sceneOptimizeOptionsForModel(
  model: {
    id?: string;
    apiModel?: string;
    sceneOptimizeOptions?: Array<"anime" | "realistic">;
  } | null | undefined,
): readonly Seedance2SceneOptimize[] {
  if (model?.sceneOptimizeOptions?.length) {
    return model.sceneOptimizeOptions;
  }
  return isSeedance2ValueModel(model?.apiModel ?? model?.id) ? SCENE_OPTIMIZE_OPTIONS : [];
}

export function defaultSceneOptimizeForModel(
  model: {
    id?: string;
    apiModel?: string;
    defaultSceneOptimize?: "anime" | "realistic" | null;
  } | null | undefined,
): Seedance2SceneOptimize {
  if (model?.defaultSceneOptimize === "anime" || model?.defaultSceneOptimize === "realistic") {
    return model.defaultSceneOptimize;
  }
  const modelId = String(model?.apiModel ?? model?.id ?? "").toLowerCase();
  return modelId.includes("fast-value") ? "realistic" : "anime";
}

export function normalizeSceneOptimize(
  value: Seedance2SceneOptimize | undefined,
  options: readonly Seedance2SceneOptimize[],
  fallback: Seedance2SceneOptimize,
): Seedance2SceneOptimize | undefined {
  if (options.length === 0) return undefined;
  return value && options.includes(value) ? value : fallback;
}

export function referenceImageUrl(node: CanvasNode | undefined | null): string | null {
  if (!node) return null;
  if (isImageGenNode(node)) {
    const data = node.data;
    // imageGen 上传给生图用的「参考图」会写到 data.referenceImageUrl；
    // 在 imageGen 自身还没生成结果之前，它就是该节点对外呈现的图片，
    // 视频节点也应该把它当成上游图引用。
    const ref =
      typeof data.referenceImageUrl === "string" &&
      data.referenceImageUrl.length > 0
        ? data.referenceImageUrl
        : null;
    return data.previewImageUrl || data.imageUrl || ref;
  }
  if (
    isUploadNode(node) ||
    isImageEditNode(node) ||
    isExportImageNode(node) ||
    isStoryboardGenNode(node)
  ) {
    const data = node.data;
    return data.previewImageUrl || data.imageUrl || null;
  }
  return null;
}

// 上游「视频引用」：视频节点自带 videoUrl，但从资产库选入的视频是 upload 节点，
// 地址同样写在 data.videoUrl。所以「是不是视频上游」应按「存在非空 data.videoUrl」
// 判定，而非节点类型——否则资产库视频会被漏认（HappyHorse 不自动切 videoEdit、
// 提交找不到 videoUrl），还会被 referenceImageUrl / isUploadNode 误当图片。
export function referenceVideoUrl(node: CanvasNode | undefined | null): string | null {
  if (!node) return null;
  const url = (node.data as { videoUrl?: unknown }).videoUrl;
  return typeof url === "string" && url.length > 0 ? url : null;
}

export function submittableImageUrl(
  node: CanvasNode | undefined | null,
): string | null {
  if (!node) return null;
  if (isImageGenNode(node)) {
    const data = node.data;
    const ref =
      typeof data.referenceImageUrl === "string" &&
      data.referenceImageUrl.length > 0
        ? data.referenceImageUrl
        : null;
    return data.imageUrl || ref;
  }
  if (
    isUploadNode(node) ||
    isImageEditNode(node) ||
    isExportImageNode(node) ||
    isStoryboardGenNode(node)
  ) {
    return node.data.imageUrl || null;
  }
  return null;
}

export function resolveDroppedVideoFile(event: DragEvent<HTMLElement>): File | null {
  const directFile = event.dataTransfer.files?.[0];
  if (directFile && isVideoFile(directFile)) {
    return directFile;
  }
  // items[].type 同样对 .mxf 为空串，先按 MIME 粗筛拿到 File 再用扩展名兜底。
  const candidates = Array.from(event.dataTransfer.items || []).filter(
    (candidate) => candidate.kind === "file",
  );
  for (const candidate of candidates) {
    const file = candidate.getAsFile();
    if (file && isVideoFile(file)) return file;
  }
  return null;
}

export function resolveOutputUrl(
  result: Record<string, unknown> | null | undefined,
): string | null {
  if (!result) return null;
  for (const key of ["video_url", "output_url", "url"]) {
    const value = result[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}
