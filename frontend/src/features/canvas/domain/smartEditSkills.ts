// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 智能剪辑节点的技能目录 —— 画布侧「工具」栏目的轻量镜像：
// 按上游素材内容类型列举可用技能，每个技能带自己的参数面板。全部复用既有
// freezone 提交函数，不新增后端 task_type。这里是纯元数据 + 类型识别，运行/
// 结果回落在 SmartEditNode 组件里做（需要 store / submit句柄）。
import {
  CANVAS_NODE_TYPES,
  isAudioNode,
  isVideoNode,
  resolveNodeSourceImageUrl,
  type CanvasNode,
  type SmartEditInputKind,
} from './canvasNodes';

/** 模型候选来源：asr/vision = 转写/视觉理解；image/video/text = 生成类目录。 */
export type SmartEditModelCatalog = "asr" | "vision" | "image" | "video" | "text";

/** 视频模型能力过滤：omni = 仅支持「全能参考」引用输入的模型。 */
export type SmartEditModelFilter = "omni";

export type SmartEditParam =
  | {
      key: string;
      type: "modelSelect";
      catalog: SmartEditModelCatalog;
      optional?: boolean;
      filter?: SmartEditModelFilter;
    }
  | {
      key: string;
      type: "select";
      options: { value: string; labelKey: string }[];
      default: string;
    }
  | { key: string; type: "textarea"; optional?: boolean };

/** 结果落地形态，决定组件创建哪种下游节点。 */
export type SmartEditResultKind =
  | "text"
  | "video"
  | "audio"
  | "image"
  /** 人声/背景音分离：一次产出背景音音频节点 + 无声视频节点两条边。 */
  | "video+audio";

export interface SmartEditSkill {
  id: string;
  inputKind: SmartEditInputKind;
  titleKey: string;
  descKey: string;
  /** lucide 图标 key，由 SmartEditNode 映射成组件。 */
  icon: string;
  resultKind: SmartEditResultKind;
  params: SmartEditParam[];
  /**
   * [aipdd fork delta] 多输入技能的第二路素材：从智能剪辑节点的其它上游按类型
   * 自动取。required=true 且缺该类型上游时禁止运行并提示 hintKey。
   */
  extraInputs?: {
    kind: SmartEditInputKind;
    required: boolean;
    max?: number;
    hintKey: string;
  }[];
}

// 技能矩阵（见方案表）。顺序 = 节点里卡片的展示顺序。
export const SMART_EDIT_SKILLS: SmartEditSkill[] = [
  {
    id: "extract-copy",
    inputKind: "video",
    titleKey: "smartEdit.skills.extractCopy.title",
    descKey: "smartEdit.skills.extractCopy.desc",
    icon: "mic",
    resultKind: "text",
    params: [{ key: "model", type: "modelSelect", catalog: "asr" }],
  },
  {
    id: "reverse-script",
    inputKind: "video",
    titleKey: "smartEdit.skills.reverseScript.title",
    descKey: "smartEdit.skills.reverseScript.desc",
    icon: "scroll",
    resultKind: "text",
    params: [
      { key: "model", type: "modelSelect", catalog: "vision", optional: true },
      { key: "prompt", type: "textarea", optional: true },
    ],
  },
  {
    id: "audio-separate",
    inputKind: "video",
    titleKey: "smartEdit.skills.audioSeparate.title",
    descKey: "smartEdit.skills.audioSeparate.desc",
    icon: "split",
    resultKind: "video+audio",
    params: [],
  },
  {
    id: "video-upscale",
    inputKind: "video",
    titleKey: "smartEdit.skills.videoUpscale.title",
    descKey: "smartEdit.skills.videoUpscale.desc",
    icon: "clarity",
    resultKind: "video",
    params: [
      {
        key: "resolution",
        type: "select",
        default: "1080p",
        options: [
          { value: "1080p", labelKey: "smartEdit.params.res1080p" },
          { value: "2k", labelKey: "smartEdit.params.res2k" },
          { value: "4k", labelKey: "smartEdit.params.res4k" },
        ],
      },
      {
        key: "denoise",
        type: "select",
        default: "1x",
        options: [
          { value: "none", labelKey: "smartEdit.params.denoiseNone" },
          { value: "1x", labelKey: "smartEdit.params.denoise1x" },
          { value: "2x", labelKey: "smartEdit.params.denoise2x" },
        ],
      },
    ],
  },
  {
    id: "lip-sync",
    inputKind: "video",
    titleKey: "smartEdit.skills.lipSync.title",
    descKey: "smartEdit.skills.lipSync.desc",
    icon: "mic",
    resultKind: "video",
    extraInputs: [
      {
        kind: "audio",
        required: true,
        max: 1,
        hintKey: "smartEdit.inputs.needDriverAudio",
      },
    ],
    params: [
      { key: "model", type: "modelSelect", catalog: "video", filter: "omni" },
    ],
  },
  {
    id: "replace-person",
    inputKind: "video",
    titleKey: "smartEdit.skills.replacePerson.title",
    descKey: "smartEdit.skills.replacePerson.desc",
    icon: "wand",
    resultKind: "video",
    extraInputs: [
      {
        kind: "image",
        required: true,
        max: 1,
        hintKey: "smartEdit.inputs.needRefImage",
      },
    ],
    params: [
      { key: "model", type: "modelSelect", catalog: "video", filter: "omni" },
    ],
  },
  {
    id: "replace-product",
    inputKind: "video",
    titleKey: "smartEdit.skills.replaceProduct.title",
    descKey: "smartEdit.skills.replaceProduct.desc",
    icon: "wand",
    resultKind: "video",
    extraInputs: [
      {
        kind: "image",
        required: true,
        max: 1,
        hintKey: "smartEdit.inputs.needRefImage",
      },
    ],
    params: [
      { key: "model", type: "modelSelect", catalog: "video", filter: "omni" },
    ],
  },
  {
    id: "video-clone",
    inputKind: "video",
    titleKey: "smartEdit.skills.videoClone.title",
    descKey: "smartEdit.skills.videoClone.desc",
    icon: "clapper",
    resultKind: "video",
    extraInputs: [
      {
        kind: "image",
        required: false,
        max: 4,
        hintKey: "smartEdit.inputs.cloneRefs",
      },
    ],
    params: [
      { key: "prompt", type: "textarea", optional: true },
      { key: "model", type: "modelSelect", catalog: "video" },
      { key: "modelText", type: "modelSelect", catalog: "text", optional: true },
      {
        key: "referenceMode",
        type: "select",
        default: "frames",
        options: [
          { value: "frames", labelKey: "smartEdit.params.refFrames" },
          { value: "text", labelKey: "smartEdit.params.refText" },
        ],
      },
      {
        key: "audioMode",
        type: "select",
        default: "generate",
        options: [
          { value: "generate", labelKey: "smartEdit.params.audioGenerate" },
          { value: "mute", labelKey: "smartEdit.params.audioMute" },
          { value: "source", labelKey: "smartEdit.params.audioSource" },
        ],
      },
    ],
  },
  {
    id: "audio-extract",
    inputKind: "audio",
    titleKey: "smartEdit.skills.audioExtract.title",
    descKey: "smartEdit.skills.audioExtract.desc",
    icon: "mic",
    resultKind: "text",
    params: [{ key: "model", type: "modelSelect", catalog: "asr" }],
  },
  {
    id: "image-upscale",
    inputKind: "image",
    titleKey: "smartEdit.skills.imageUpscale.title",
    descKey: "smartEdit.skills.imageUpscale.desc",
    icon: "clarity",
    resultKind: "image",
    params: [
      {
        key: "scaleFactor",
        type: "select",
        default: "2",
        options: [
          { value: "2", labelKey: "smartEdit.params.scale2" },
          { value: "4", labelKey: "smartEdit.params.scale4" },
          { value: "6", labelKey: "smartEdit.params.scale6" },
        ],
      },
    ],
  },
  {
    id: "reverse-prompt",
    inputKind: "image",
    titleKey: "smartEdit.skills.reversePrompt.title",
    descKey: "smartEdit.skills.reversePrompt.desc",
    icon: "text",
    resultKind: "text",
    params: [{ key: "instruction", type: "textarea", optional: true }],
  },
  {
    id: "image-watermark",
    inputKind: "image",
    titleKey: "smartEdit.skills.imageWatermark.title",
    descKey: "smartEdit.skills.imageWatermark.desc",
    icon: "eraser",
    resultKind: "image",
    params: [
      { key: "model", type: "modelSelect", catalog: "image", optional: true },
      { key: "prompt", type: "textarea", optional: true },
    ],
  },
];

export function skillsForKind(kind: SmartEditInputKind): SmartEditSkill[] {
  return SMART_EDIT_SKILLS.filter((skill) => skill.inputKind === kind);
}

/**
 * 取某节点承载的媒体 URL（按内容类型）。视频看 videoUrl、音频看 audioUrl、
 * 图片族（upload / imageEdit / imageGen / exportImage）走 resolveNodeSourceImageUrl。
 */
export function resolveNodeMediaUrl(
  node: CanvasNode | null | undefined,
): { kind: SmartEditInputKind; url: string } | null {
  if (!node) return null;
  if (isVideoNode(node)) {
    const url = typeof node.data.videoUrl === "string" ? node.data.videoUrl : "";
    return url ? { kind: "video", url } : null;
  }
  if (isAudioNode(node)) {
    const url = typeof node.data.audioUrl === "string" ? node.data.audioUrl : "";
    return url ? { kind: "audio", url } : null;
  }
  const imageUrl = resolveNodeSourceImageUrl(node);
  if (imageUrl) return { kind: "image", url: imageUrl };
  return null;
}

/**
 * [aipdd fork delta] 按内容类型收集全部有 URL 的上游素材，供多输入技能取第二路
 * （对口型的驱动音频、换人/换品的参考图、复刻的参考图集）。保序。
 */
export function collectUpstreamMediaByKind(
  upstreamNodes: readonly CanvasNode[],
): Record<SmartEditInputKind, string[]> {
  const acc: Record<SmartEditInputKind, string[]> = {
    video: [],
    image: [],
    audio: [],
  };
  for (const node of upstreamNodes) {
    const media = resolveNodeMediaUrl(node);
    if (media) acc[media.kind].push(media.url);
  }
  return acc;
}

/**
 * 从上游集合识别主素材内容类型（一次处理一路）：视频 > 图片 > 音频。
 * 无任何带 URL 的上游媒体时返回 null（节点显示「请先连入素材」）。
 */
export function detectInputKind(
  upstreamNodes: readonly CanvasNode[],
): SmartEditInputKind | null {
  let hasImage = false;
  let hasAudio = false;
  for (const node of upstreamNodes) {
    const media = resolveNodeMediaUrl(node);
    if (!media) continue;
    if (media.kind === "video") return "video";
    if (media.kind === "image") hasImage = true;
    else if (media.kind === "audio") hasAudio = true;
  }
  if (hasImage) return "image";
  if (hasAudio) return "audio";
  return null;
}

/**
 * 为技能找到匹配其输入类型的上游素材，返回该素材的原始 URL（可能是 blob/data，
 * 由调用方经 ensureBackendImageUrl 换成后端可静态打开的地址）。找不到返回 null。
 */
export function resolveSkillSource(
  skillKind: SmartEditInputKind,
  upstreamNodes: readonly CanvasNode[],
): string | null {
  for (const node of upstreamNodes) {
    const media = resolveNodeMediaUrl(node);
    if (media && media.kind === skillKind) return media.url;
  }
  return null;
}

// 领域节点类型常量再导出一次，方便组件按 CANVAS_NODE_TYPES.smartEdit 取用而不
// 必额外 import（保持与 nodeRegistry / index.ts 同一份来源）。
export const SMART_EDIT_NODE_TYPE = CANVAS_NODE_TYPES.smartEdit;
