// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall, apiCallEnvelope, apiClient } from "./client";
import { readReferenceMediaLimits, type ReferenceMediaLimits } from "./referenceMediaLimits";

// Per-node generation history -------------------------------------------- //

/**
 * Optional canvas/node context the backend uses to record a per-node
 * generation history entry. Generation-style endpoints accept these; omitting
 * them is harmless (the backend simply skips history recording). Mixed into
 * each generation payload via {@link FreezoneNodeContext}.
 */
export interface FreezoneNodeContext {
  /** Current canvas id, usually "default". */
  canvasId?: string | null;
  /** Id of the node that triggered the generation. */
  nodeId?: string | null;
  modelParams?: Record<string, unknown>;
}

/**
 * Map the camelCase node context to the backend's snake_case body fields,
 * emitting keys only when present so legacy callers stay byte-identical.
 */
function nodeContextBody(ctx: FreezoneNodeContext): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (ctx.canvasId) out.canvas_id = ctx.canvasId;
  if (ctx.nodeId) out.node_id = ctx.nodeId;
  if (ctx.modelParams) out.model_params = ctx.modelParams;
  return out;
}

/**
 * [aipdd fork delta] 数字输入安全归一：0/NaN/∞ → fallback，越界夹回 [min, max]，
 * int 字段四舍五入。工具页数字输入框可被清空（Number("") = 0）或键入越界值，
 * 裸传给后端会被 Pydantic 422 拒收（如 freezone/video/idea 的 max_scenes）。
 */
function normalizeNumberInput(
  value: number | null | undefined,
  fallback: number,
  min: number,
  max: number,
  integer = false,
): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return fallback;
  return Math.min(max, Math.max(min, integer ? Math.round(n) : n));
}

/**
 * [aipdd fork delta] 区间夹取（0 为合法值的场景：音量 / 归一化框选区）；
 * NaN/∞ → fallback。与 {@link normalizeNumberInput} 的区别是不把 0 当空值。
 */
function clampNumberInput(
  value: number | null | undefined,
  fallback: number,
  min: number,
  max: number,
): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** One recorded generation attempt for a node (see generation-history GET). */
export interface FreezoneGenerationHistoryRecord {
  schema_version: number;
  canvas_id: string;
  node_id: string;
  /** ISO timestamp the backend recorded the attempt. */
  recorded_at: string;
  /** Stable record id, e.g. "freezone_gen:<job_id>". */
  id: string;
  task_type: string;
  task_key: string;
  job_id: string;
  /** "completed" | "failed" | other backend status strings. */
  status: string;
  /** "image" | "video" | "audio" | "text" | "3d" ... */
  media_type: string;
  /** Raw task result payload; shape varies by task_type. */
  result: Record<string, unknown>;
  /** 注册表模型 id（还原时回填 data.model）。旧记录无此字段。 */
  model?: string;
  /** 生成模式（视频 genMode / 图片 generationMode）。旧记录无此字段。 */
  gen_mode?: string;
}

/**
 * Read a node's recorded generation history (most recent first per the
 * backend). Returns `[]` when the node has no history yet. The endpoint is
 * independent of canvas JSON, so this never touches the saved-canvas flow.
 */
export async function fetchNodeGenerationHistory(
  project: string,
  canvasId: string,
  nodeId: string,
  limit = 100,
): Promise<FreezoneGenerationHistoryRecord[]> {
  const data = await apiCall<{ records?: FreezoneGenerationHistoryRecord[] }>(
    `projects/${encodeURIComponent(project)}/freezone/canvases/${encodeURIComponent(
      canvasId,
    )}/nodes/${encodeURIComponent(nodeId)}/generation-history?limit=${limit}`,
  );
  return data?.records ?? [];
}

/**
 * Read the whole canvas's generation history in one request (most recent
 * first). Unlike {@link fetchNodeGenerationHistory} this is not scoped to a
 * single node, and the backend aggregates across every node that ever recorded
 * history on this canvas — including nodes since deleted from the canvas — so
 * their past attempts stay visible in the history browser.
 */
export async function fetchCanvasGenerationHistory(
  project: string,
  canvasId: string,
  limit = 500,
): Promise<FreezoneGenerationHistoryRecord[]> {
  const data = await apiCall<{ records?: FreezoneGenerationHistoryRecord[] }>(
    `projects/${encodeURIComponent(project)}/freezone/canvases/${encodeURIComponent(
      canvasId,
    )}/generation-history?limit=${limit}`,
  );
  return data?.records ?? [];
}

/** Remove one entry from the history browser; the underlying media is retained. */
export async function deleteNodeGenerationHistoryRecord(
  project: string,
  canvasId: string,
  nodeId: string,
  recordId: string,
): Promise<{ deleted: boolean }> {
  return await apiCall<{ deleted: boolean }>(
    `projects/${encodeURIComponent(project)}/freezone/canvases/${encodeURIComponent(
      canvasId,
    )}/nodes/${encodeURIComponent(nodeId)}/generation-history?record_id=${encodeURIComponent(recordId)}`,
    { method: "DELETE" },
  );
}

// /freezone/gen ----------------------------------------------------------- //

export type FreezoneProvider =
  | "newapi"
  | "openrouter"
  | "huimeng"
  | "openai";

export interface FreezoneGenCamera {
  /** id from /freezone/image/camera-options.camera_bodies */
  cameraBodyId?: string | null;
  /** id from /freezone/image/camera-options.lenses */
  lensId?: string | null;
  focalLengthMm?: number | null;
  aperture?: string | null;
}

export interface FreezoneGenStyle {
  /** id from /freezone/image/style-templates */
  templateId?: string | null;
}

export interface FreezoneGenPayload extends FreezoneNodeContext {
  prompt: string;
  aspectRatio?: string;
  imageSize?: string;
  referenceUrls?: string[];
  camera?: FreezoneGenCamera | null;
  style?: FreezoneGenStyle | null;
  /** Override `NANOBANANA_PROVIDER` env default for the reference-image path. */
  provider?: FreezoneProvider | null;
  /** Override the provider's default model (e.g. "gpt-image-2"). */
  model?: string | null;
  /** 注册表模型 id（还原用；与 provider 拆分后的 model 串不同）。 */
  modelId?: string | null;
  /** 生成模式（还原用）：text_to_image / image_to_image / all_reference / image_reference。 */
  genMode?: string | null;
  /** Only honored by openai gpt-image-2 (low / medium / high / auto). */
  quality?: string | null;
}

export interface FreezoneJobRef {
  task_type:
    | "freezone_gen"
    | "freezone_edit"
    | "freezone_multi_view"
    | "freezone_relight"
    | "freezone_scene_360"
    | "freezone_template_edit"
    | "freezone_upscale"
    | "freezone_outpaint"
    | "freezone_redraw"
    | "freezone_video_gen"
    | "freezone_video_omni_gen"
    | "freezone_video_i2v"
    | "freezone_video_erase"
    | "freezone_video_compose"
    | "freezone_video_upscale"
    | "freezone_audio_separate"
    | "freezone_audio_speech"
    // [aipdd fork delta] 工具页「人声复刻 → 替换人声」。
    | "freezone_voice_replace"
    | "freezone_audio_eleven_music"
    // [aipdd fork delta req46] 工具页「音乐创作」（MiniMax Music 3.0 直连）。
    | "freezone_music_generate"
    | "freezone_image_reverse_prompt"
    | "freezone_text_generate"
    | "freezone_text_translate"
    | "freezone_story_script"
    | "freezone_video_story"
    | "freezone_video_clone"
    | "freezone_video_mix"
    | "stage_asset";
  job_id: string;
  task_key: string;
}

// /freezone/video/gen ----------------------------------------------------- //

export type FreezoneVideoAspectRatio = string;

export type FreezoneVideoResolution = string;

/** Local element marker on the source image, used to anchor subjects/objects. */
export interface FreezoneVideoMark {
  label: string;
  sourceUrl?: string;
  pointX?: number | null;
  pointY?: number | null;
  boxX?: number | null;
  boxY?: number | null;
  boxWidth?: number | null;
  boxHeight?: number | null;
  note?: string;
}

export interface FreezoneVideoGenPayload extends FreezoneNodeContext {
  prompt: string;
  /** e.g. locked_off / follow_tracking / orbit_up */
  cameraTemplateId?: string | null;
  characterIds?: string[];
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio;
  resolution?: FreezoneVideoResolution;
  /** seconds; spec only requires ≥1, the UI typically caps higher. */
  durationSeconds?: number;
  generateAudio?: boolean;
  /** Backend model id, e.g. huimeng_seedance20_fast / seedance_pro. */
  model?: string;
  /** 文生视频入口的固定业务模式。 */
  genMode: "textToVideo";
  /**
   * Real-person material review. Set `true` when the input contains real
   * human faces so the backend routes the job through the human-review path
   * (may take longer, approval not guaranteed). Omitted/false otherwise.
   */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
}

export async function submitFreezoneVideoGen(
  project: string,
  payload: FreezoneVideoGenPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/gen`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        camera_template_id: payload.cameraTemplateId ?? null,
        character_ids: payload.characterIds ?? [],
        marks: (payload.marks ?? []).map((m) => ({
          label: m.label,
          source_url: m.sourceUrl ?? "",
          point_x: m.pointX ?? null,
          point_y: m.pointY ?? null,
          box_x: m.boxX ?? null,
          box_y: m.boxY ?? null,
          box_width: m.boxWidth ?? null,
          box_height: m.boxHeight ?? null,
          note: m.note ?? "",
        })),
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        duration_seconds: Math.max(payload.durationSeconds ?? 5, 1),
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        scene_optimize: payload.sceneOptimize ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/upscale ------------------------------------------------- //

/** Target clarity tier. Scales by long edge: 1080p=1920, 2k=2560, 4k=3840. */
export type FreezoneVideoUpscaleResolution = "1080p" | "2k" | "4k";

/** Denoise strength. none=off, 1x=light, 2x=medium. */
export type FreezoneVideoUpscaleDenoise = "none" | "1x" | "2x";

export interface FreezoneVideoUpscalePayload extends FreezoneNodeContext {
  /** Static URL of the source video to upscale. */
  sourceUrl: string;
  resolution?: FreezoneVideoUpscaleResolution;
  /** Base version only supports "none" (frame rate unchanged). */
  frameInterpolation?: "none";
  denoiseStrength?: FreezoneVideoUpscaleDenoise;
}

export async function submitFreezoneVideoUpscale(
  project: string,
  payload: FreezoneVideoUpscalePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/upscale`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        resolution: payload.resolution ?? "1080p",
        frame_interpolation: payload.frameInterpolation ?? "none",
        denoise_strength: payload.denoiseStrength ?? "1x",
        ...nodeContextBody(payload),
      },
    },
  );
}

export interface FreezoneVideoKeyframesPayload extends FreezoneNodeContext {
  /** Static URL of the first frame. At least one of first/last must be set. */
  firstFrameUrl?: string | null;
  lastFrameUrl?: string | null;
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  model?: string;
  /** 关键帧业务模式。首尾帧可只提供首帧、只提供尾帧或同时提供。 */
  genMode: "firstFrame" | "firstLastFrame";
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
}

export async function submitFreezoneVideoKeyframes(
  project: string,
  payload: FreezoneVideoKeyframesPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/keyframes`,
    {
      method: "POST",
      json: {
        first_frame_url: payload.firstFrameUrl ?? null,
        last_frame_url: payload.lastFrameUrl ?? null,
        prompt: payload.prompt ?? "",
        camera_template_id: payload.cameraTemplateId ?? null,
        marks: (payload.marks ?? []).map((m) => ({
          label: m.label,
          source_url: m.sourceUrl ?? "",
          point_x: m.pointX ?? null,
          point_y: m.pointY ?? null,
          box_x: m.boxX ?? null,
          box_y: m.boxY ?? null,
          box_width: m.boxWidth ?? null,
          box_height: m.boxHeight ?? null,
          note: m.note ?? "",
        })),
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        duration_seconds: Math.max(payload.durationSeconds ?? 5, 1),
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        scene_optimize: payload.sceneOptimize ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/i2v ----------------------------------------------------- //
//
// Unified endpoint for single-image 图生视频 and multi-image 图片参考. Both
// use image_reference provider semantics and never occupy the first-frame slot.

export interface FreezoneVideoI2vPayload extends FreezoneNodeContext {
  /** Image reference URLs. imageToVideo requires one; imageReference uses the catalog cap. */
  imageUrls: string[];
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** default huimeng_seedance10_fast (matches keyframes); multi-image prefers seedance 2.0. */
  model?: string;
  /** Required product entry; both values execute as image_reference. */
  genMode: "imageToVideo" | "imageReference";
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
}

export async function submitFreezoneVideoI2v(
  project: string,
  payload: FreezoneVideoI2vPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/i2v`,
    {
      method: "POST",
      json: {
        image_urls: payload.imageUrls,
        prompt: payload.prompt ?? "",
        camera_template_id: payload.cameraTemplateId ?? null,
        marks: (payload.marks ?? []).map((m) => ({
          label: m.label,
          source_url: m.sourceUrl ?? "",
          point_x: m.pointX ?? null,
          point_y: m.pointY ?? null,
          box_x: m.boxX ?? null,
          box_y: m.boxY ?? null,
          box_width: m.boxWidth ?? null,
          box_height: m.boxHeight ?? null,
          note: m.note ?? "",
        })),
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        duration_seconds: Math.max(payload.durationSeconds ?? 5, 1),
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        scene_optimize: payload.sceneOptimize ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/video-edit ---------------------------------------------- //
//
// 视频编辑：1 个源视频，并按媒体目录能力附带参考图片 / 独立参考音频。

export interface FreezoneVideoEditPayload extends FreezoneNodeContext {
  /** 源视频静态地址，必填。 */
  videoUrl: string;
  /** 0-5 张参考图静态地址。 */
  imageUrls?: string[];
  /** 独立参考音频静态地址；数量和时长上限由媒体模型目录决定。 */
  audioUrls?: string[];
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  resolution?: FreezoneVideoResolution;
  /** 视频编辑音频策略：auto 自动 / origin 保留原声。 */
  audioSetting?: "auto" | "origin";
  generateAudio?: boolean;
  /** 视频模型或模型选项 id；须传 /freezone/video/models 目录条目之一。 */
  model?: string;
  /** 视频编辑入口的固定业务模式。 */
  genMode: "videoEdit";
  humanReview?: boolean;
  /**
   * [aipdd fork delta] 工具页批量任务专用：产物完成后由后端 runner 自动登记
   * 进资产库的「待分类资产」。画布等其它入口不传（默认 false）。
   */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoEdit(
  project: string,
  payload: FreezoneVideoEditPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/video-edit`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        image_urls: payload.imageUrls ?? [],
        audio_urls: payload.audioUrls ?? [],
        prompt: payload.prompt ?? "",
        camera_template_id: payload.cameraTemplateId ?? null,
        marks: (payload.marks ?? []).map((m) => ({
          label: m.label,
          source_url: m.sourceUrl ?? "",
          point_x: m.pointX ?? null,
          point_y: m.pointY ?? null,
          box_x: m.boxX ?? null,
          box_y: m.boxY ?? null,
          box_width: m.boxWidth ?? null,
          box_height: m.boxHeight ?? null,
          note: m.note ?? "",
        })),
        resolution: payload.resolution ?? "720p",
        audio_setting: payload.audioSetting ?? "auto",
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        ...nodeContextBody(payload),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/video/video-extend -------------------------------------------- //

export interface FreezoneVideoExtendPayload extends FreezoneNodeContext {
  /** 待延长的源视频静态地址，必填。 */
  videoUrl: string;
  /** 源视频结束后要继续生成的内容。 */
  prompt: string;
  cameraTemplateId?: string | null;
  resolution?: FreezoneVideoResolution;
  /** 新生成延长片段的时长。 */
  durationSeconds?: number;
  generateAudio?: boolean;
  model?: string;
  genMode: "videoExtend";
  humanReview?: boolean;
}

export async function submitFreezoneVideoExtend(
  project: string,
  payload: FreezoneVideoExtendPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/video-extend`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        prompt: payload.prompt,
        camera_template_id: payload.cameraTemplateId ?? null,
        resolution: payload.resolution ?? "720p",
        duration_seconds: Math.max(payload.durationSeconds ?? 5, 1),
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/omni-gen ------------------------------------------------ //

export type FreezoneVideoReferenceType = "image" | "video" | "audio" | "file" | "link";

export interface FreezoneVideoReferenceItem {
  type: FreezoneVideoReferenceType;
  url: string;
  role?: string;
  label?: string;
}

export interface FreezoneVideoOmniGenPayload extends FreezoneNodeContext {
  prompt: string;
  theme?: string;
  cameraTemplateId?: string | null;
  /** Mixed media references; file/link support and limits come from the model catalog. */
  references?: FreezoneVideoReferenceItem[];
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** default huimeng_seedance20_fast per backend default. */
  model?: string;
  /** 全能参考入口的固定业务模式。 */
  genMode: "allReference";
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /**
   * [aipdd fork delta] 工具页批量任务专用：产物完成后由后端 runner 自动登记
   * 进资产库的「待分类资产」。画布等其它入口不传（默认 false）。
   */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoOmniGen(
  project: string,
  payload: FreezoneVideoOmniGenPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/omni-gen`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        theme: payload.theme ?? "",
        camera_template_id: payload.cameraTemplateId ?? null,
        references: (payload.references ?? []).map((r) => ({
          type: r.type,
          url: r.url,
          role: r.role ?? "",
          label: r.label ?? "",
        })),
        marks: (payload.marks ?? []).map((m) => ({
          label: m.label,
          source_url: m.sourceUrl ?? "",
          point_x: m.pointX ?? null,
          point_y: m.pointY ?? null,
          box_x: m.boxX ?? null,
          box_y: m.boxY ?? null,
          box_width: m.boxWidth ?? null,
          box_height: m.boxHeight ?? null,
          note: m.note ?? "",
        })),
        // [aipdd fork delta req42] 全能参考（换人 / 换产品 / 对口型）的输入就是现成的
        // 一条视频：画幅默认跟随源素材（后端按本地文件探测 + 目录档位落档）。旧的
        // ?? "16:9" 会让没显式传画幅的调用方（如画布智能剪辑节点）把竖屏素材出成横屏。
        aspect_ratio: payload.aspectRatio ?? "auto",
        resolution: payload.resolution ?? "720p",
        duration_seconds: Math.max(payload.durationSeconds ?? 5, 1),
        generate_audio: payload.generateAudio ?? false,
        ...(payload.model ? { model: payload.model, model_id: payload.model } : {}),
        gen_mode: payload.genMode,
        human_review: payload.humanReview ?? false,
        scene_optimize: payload.sceneOptimize ?? null,
        ...nodeContextBody(payload),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/video/clone ---------------------------------------------------- //

/**
 * [aipdd fork delta] 视频复刻：参考视频 → 抽帧解读 → 逐镜规划 → 逐镜生成 →
 * 自动拼接成片。工具页批量任务专用入口。
 */
export interface FreezoneVideoClonePayload {
  /** 参考视频静态地址（当前项目下真实存在的 /static/... 视频 URL）。 */
  videoUrl: string;
  /** 可选复刻附加要求；留空完全按参考视频复刻。 */
  prompt?: string;
  /**
   * [aipdd fork delta] 可选「参考内容」图片（≤4 张，资产库/上传入口的 /static/...
   * URL）：角色/场景/风格参照，与提示词一起作用于对原视频的复刻处理；
   * 需模型支持全能参考能力。
   */
  referenceUrls?: string[];
  /** 视频模型 id（/freezone/video/models 返回值之一）。 */
  model?: string;
  /**
   * [aipdd fork delta] 文本（LLM）模型 id（/model-gateway/text-models 返回值
   * 之一），用于抽帧解读后的逐镜复刻规划；留空走系统默认文本模型链。
   */
  modelText?: string;
  /** frames=原视频关键帧作逐镜参考图（默认）；text=纯文本重制。 */
  referenceMode?: "frames" | "text";
  /**
   * [aipdd fork delta] 带参考内容时的复刻优先级（默认 subject）：
   * subject=主体替换优先，只提交参考内容、不提交原视频关键帧；
   * composition=忠实构图优先，关键帧与参考内容一并提交（实测部分模型会把
   * 参考内容压掉、整体还原关键帧里的原主体）。未提供参考内容时两者行为相同。
   */
  referencePriority?: "subject" | "composition";
  /** generate=模型生成音（默认）；mute=静音；source=保留参考视频原声。 */
  audioMode?: "generate" | "mute" | "source";
  aspectRatio?: string;
  resolution?: FreezoneVideoResolution;
  /** 最多抽取多少关键帧（3-50），建议 12-20。 */
  maxFrames?: number;
  /** ffmpeg 场景切换阈值 0-1。 */
  sceneThreshold?: number;
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoClone(
  project: string,
  payload: FreezoneVideoClonePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/clone`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        prompt: payload.prompt ?? "",
        ...(payload.referenceUrls?.length
          ? { reference_urls: payload.referenceUrls }
          : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        reference_mode: payload.referenceMode ?? "frames",
        reference_priority: payload.referencePriority ?? "subject",
        audio_mode: payload.audioMode ?? "generate",
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        max_frames: payload.maxFrames ?? 12,
        scene_threshold: payload.sceneThreshold ?? 0.3,
        human_review: payload.humanReview ?? false,
        scene_optimize: payload.sceneOptimize ?? null,
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/video/mix ------------------------------------------------------ //

/**
 * [aipdd fork delta] 混剪短视频：素材池（图片/视频）→ 主题或手写文案 → AI 配音
 * → 烧录字幕 → 顺序循环混剪成片（本地 ffmpeg，素材原声丢弃）。
 * 工具页一次提交产出一条成片（素材池 ≠ 批量任务）。
 */
export interface FreezoneVideoMixPayload {
  /** 素材池静态地址列表（按顺序循环铺满全片；1-60 项）。 */
  materialUrls: string[];
  /** 视频主题；generate 模式与 script 至少填一项。 */
  subject?: string;
  /** 手写口播文案；填写后优先于主题生成。 */
  script?: string;
  /** generate=AI 配音（默认，时长驱动剪辑）；mute=静音按素材/目标时长成片。 */
  audioMode?: "generate" | "mute";
  /** 是否烧录字幕（仅配音模式生效；默认不烧录，用户勾选才烧）。 */
  subtitleEnabled?: boolean;
  /** 字幕字号档位 small/medium/large（仅烧录字幕时生效；默认 medium）。 */
  subtitleFontSize?: "small" | "medium" | "large";
  /** 背景音乐静态地址（可选）。 */
  bgmUrl?: string;
  /** BGM 音量 0-1；null/undefined = 自动（配音混音 0.2 / 静音唯一音轨 1.0）。 */
  bgmVolume?: number | null;
  aspectRatio?: string;
  resolution?: FreezoneVideoResolution;
  /** 每段素材时长（秒），1-10，默认 3。 */
  clipDuration?: number;
  /** fade=淡入淡出（默认）；none=硬切。 */
  transition?: "fade" | "none";
  /** cover=裁切铺满（默认）；contain=完整留边。 */
  fitMode?: "cover" | "contain";
  /** 静音模式目标时长（秒，5-600）；留空按素材总长单遍。 */
  maxDurationSeconds?: number | null;
  /** 配音情绪提示词（可选）。 */
  emotionPrompt?: string;
  /** 配音声线引用（可选；留空用项目默认解说声线）。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** 音频模型 apiModel（后台 audio 目录）；留空走后端默认引擎。 */
  model?: string;
  /**
   * [aipdd fork delta] 文本（LLM）模型 id（/model-gateway/text-models 返回值
   * 之一），用于由主题自动生成口播文案（手写 script 时不用）；留空走系统
   * 默认文本模型链。
   */
  modelText?: string;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoMix(
  project: string,
  payload: FreezoneVideoMixPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/mix`,
    {
      method: "POST",
      json: {
        material_urls: payload.materialUrls,
        subject: payload.subject ?? "",
        script: payload.script ?? "",
        audio_mode: payload.audioMode ?? "generate",
        subtitle_enabled: payload.subtitleEnabled ?? false,
        ...(payload.subtitleFontSize ? { subtitle_font_size: payload.subtitleFontSize } : {}),
        bgm_url: payload.bgmUrl ?? "",
        // 空 = 自动音量（后端按有无配音定 0.2 / 1.0）；不能发 0 覆盖默认语义。
        ...(payload.bgmVolume == null
          ? {}
          : { bgm_volume: clampNumberInput(payload.bgmVolume, 0.2, 0, 1) }),
        aspect_ratio: payload.aspectRatio ?? "9:16",
        resolution: payload.resolution ?? "720p",
        clip_duration: normalizeNumberInput(payload.clipDuration, 3, 1, 10),
        transition: payload.transition ?? "fade",
        fit_mode: payload.fitMode ?? "cover",
        max_duration_seconds:
          payload.maxDurationSeconds == null
            ? null
            : clampNumberInput(payload.maxDurationSeconds, 5, 5, 600),
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/whiteboard-animation ------------------------------------------- //

/**
 * [aipdd fork delta] 白板动画视频：口播文案 → AI 分镜 → 逐幕暖米黄线稿 →
 * 逐笔揭示手绘动画（本地 OpenCV + ffmpeg）→ 拼接 + 配音（+ 可选字幕/BGM）成片。
 * 文本/图像/配音三类模型均可自选，留空走系统默认链；一次提交产出一条成片。
 */
export interface FreezoneWhiteboardAnimationPayload {
  /** 口播文案（白板动画旁白全文，必填，1-10000 字）。 */
  script: string;
  /** 分镜规划文本（LLM）模型 id；留空走系统默认文本模型链。 */
  modelText?: string;
  /** 线稿出图模型目录 id（后台 image 目录）；留空用默认图像模型。 */
  modelImage?: string;
  /** 配音音频模型目录 id（后台 audio 目录）；留空走默认语音引擎。 */
  model?: string;
  aspectRatio?: string;
  resolution?: FreezoneVideoResolution;
  /** 分镜幕数上限 1-12，默认 8。 */
  maxScenes?: number;
  /** 是否在逐笔揭示前沿叠加笔尖光标（默认开）。 */
  penCursor?: boolean;
  /** 是否烧录字幕（默认不烧录）。 */
  subtitleEnabled?: boolean;
  /** 字幕字号档位 small/medium/large（仅烧录字幕时生效）。 */
  subtitleFontSize?: "small" | "medium" | "large";
  /** 配音情绪提示词（可选）。 */
  emotionPrompt?: string;
  /** 配音声线引用（可选；留空用项目默认解说声线）。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** 背景音乐静态地址（可选）。 */
  bgmUrl?: string;
  /** BGM 音量 0-1；留空自动（与配音混音 0.2）。 */
  bgmVolume?: number | null;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneWhiteboardAnimation(
  project: string,
  payload: FreezoneWhiteboardAnimationPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/whiteboard-animation`,
    {
      method: "POST",
      json: {
        script: payload.script,
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.modelImage ? { model_image: payload.modelImage } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        max_scenes: normalizeNumberInput(payload.maxScenes, 8, 1, 12, true),
        pen_cursor: payload.penCursor ?? true,
        subtitle_enabled: payload.subtitleEnabled ?? false,
        ...(payload.subtitleFontSize
          ? { subtitle_font_size: payload.subtitleFontSize }
          : {}),
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        bgm_url: payload.bgmUrl ?? "",
        // 空 = 自动音量（后端按有无配音定 0.2）；0 是显式合法值，不能被当空丢弃。
        ...(payload.bgmVolume == null
          ? {}
          : { bgm_volume: clampNumberInput(payload.bgmVolume, 0.2, 0, 1) }),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/explainer-video ------------------------------------------------ //

/**
 * [aipdd fork delta] 科普讲解视频：参考资料 → 分章解说词 → 分镜 DSL → 逐帧
 * 代码绘制 MG 动画（本地 Pillow + ffmpeg 流式编码，不依赖浏览器）→ 拼接 +
 * 配音（+ 可选 BGM）。画面不调图像/视频模型，只用网关的文本与音频两类能力；
 * 成片固定 16:9 横屏，时长 90-330 秒；一次提交只产出一条成片。
 */
export interface FreezoneExplainerVideoPayload {
  /** 参考资料（主题说明 / 文章 / 文档正文，必填，1-12000 字）。 */
  referenceContent: string;
  /** 片名提示（可选）；留空由模型自拟。 */
  title?: string;
  /** 目标成片时长（秒），自动夹到 90-330；默认 180。 */
  durationS?: number;
  /** 解说词与分镜共用的文本（LLM）模型 id；留空走系统默认文本链。 */
  modelText?: string;
  /** 配音音频模型目录 id（后台 audio 目录）；留空走默认语音引擎。 */
  model?: string;
  /** 幕底样式 dots 点阵波（默认）/ stars 星点。 */
  background?: "dots" | "stars";
  resolution?: FreezoneVideoResolution;
  /** [aipdd fork delta] HyperFrames 渲染设计预设（配色/字体/底纹）；仅 hyperframes 引擎生效。 */
  stylePreset?: "ink" | "ocean" | "sunset" | "paper" | "mono";
  /** [aipdd fork delta] 成片画幅（hyperframes 引擎）：landscape 横屏 / portrait 竖屏 / square 方形。 */
  format?: "landscape" | "portrait" | "square";
  /** [aipdd fork delta] 是否叠加同步字幕带；默认开。 */
  captions?: boolean;
  /** 配音情绪提示词（可选）。 */
  emotionPrompt?: string;
  /** 配音声线引用（可选；留空用项目默认解说声线）。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** 背景音乐静态地址（可选）。 */
  bgmUrl?: string;
  /** BGM 音量 0-1；留空自动（与配音混音 0.2）。 */
  bgmVolume?: number | null;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneExplainerVideo(
  project: string,
  payload: FreezoneExplainerVideoPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/explainer-video`,
    {
      method: "POST",
      json: {
        reference_content: payload.referenceContent,
        title: payload.title ?? "",
        duration_s: normalizeNumberInput(payload.durationS, 180, 90, 330, true),
        background: payload.background ?? "dots",
        resolution: payload.resolution ?? "720p",
        style_preset: payload.stylePreset ?? "ink",
        format: payload.format ?? "landscape",
        captions: payload.captions ?? true,
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        bgm_url: payload.bgmUrl ?? "",
        // 空 = 自动音量（后端混音 0.2）；0 是显式合法值，不能被当空丢弃。
        ...(payload.bgmVolume == null
          ? {}
          : { bgm_volume: clampNumberInput(payload.bgmVolume, 0.2, 0, 1) }),
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/video/idea ----------------------------------------------------- //

/**
 * [aipdd fork delta] 公众号文章编写与排版（内容创作族）：主题/素材 → 风格化
 * Markdown → 本地确定性排版套用主题组件生成公众号合规 HTML（每个中文文本节点包
 * span leaf）→ validate 强门禁（ERROR=0 才算成功）。写作只走网关文本模型一处出网；
 * 排版与校验纯本地。产物为结果 JSON + 一份预览 HTML；一次提交产出一篇文章。
 */
export interface FreezoneArticleWritePayload {
  /** 文章主题（如「RAG 检索增强生成」）；与素材至少填一项。 */
  topic?: string;
  /** 参考素材（文章 / 文档正文，1-12000 字）；写作只在素材范围内取材。 */
  material?: string;
  /** 写作风格：viewpoint 观点深度（默认）/ tutorial / emotional / data / story / rational。 */
  style?: "viewpoint" | "tutorial" | "emotional" | "data" | "story" | "rational";
  /** 排版主题：moyu-green 摸鱼绿（默认）/ graphite-minimal / red-white / amber-orange / terminal-geek / zen-whitespace。 */
  theme?: "moyu-green" | "graphite-minimal" | "red-white" | "amber-orange" | "terminal-geek" | "zen-whitespace";
  /** 正文目标字数，自动夹到 300-6000；默认 1500。 */
  targetWords?: number;
  /** 目标读者画像（可选）。 */
  audience?: string;
  /** 语气提示（可选）。 */
  tone?: string;
  /** 开头引言金句（可选）；留空省略引言卡。 */
  introQuote?: string;
  /** 作者署名（可选）。 */
  author?: string;
  /** 文本（LLM）模型 id；留空走系统默认文本链。 */
  modelText?: string;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）。 */
  sourceTool?: string;
}

export async function submitFreezoneArticleWrite(
  project: string,
  payload: FreezoneArticleWritePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/article/write`,
    {
      method: "POST",
      json: {
        topic: payload.topic ?? "",
        material: payload.material ?? "",
        style: payload.style ?? "viewpoint",
        theme: payload.theme ?? "moyu-green",
        target_words: normalizeNumberInput(payload.targetWords, 1500, 300, 6000, true),
        audience: payload.audience ?? "",
        tone: payload.tone ?? "",
        intro_quote: payload.introQuote ?? "",
        author: payload.author ?? "",
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

/**
 * [aipdd fork delta] 渠道短视频（内容创作族 Phase 2B）：文章/素材 → 走讲解视频同一
 * HyperFrames 渲染管线，但按渠道（douyin/kuaishou/xiaohongshu/shipinhao/bilibili/tiktok）
 * 定制成片规格（画幅/时长上下限/字幕/封面）。源可为参考正文（referenceContent）或直接指向
 * 一篇已排版的公众号文章（articleJobId，后端读其 Markdown）。产出该渠道规格 MP4 + 封面 +
 * 文案建议；**不自动上传**。竖屏/方形无 Pillow 回退，需后端配置渲染服务。
 */
export interface FreezoneChannelShortVideoPayload {
  /** 目标渠道 key（默认 douyin）；画幅/时长上下限据此而定。 */
  channel?: string;
  /** 参考资料（与 articleJobId 至少一项，1-12000 字）。 */
  referenceContent?: string;
  /** 或直接指定来源 freezone_article_write 任务的 job_id（后端读其 Markdown 正文）。 */
  articleJobId?: string;
  /** 片名提示（可选）；留空由模型自拟。 */
  title?: string;
  /** 目标成片时长（秒）；0/留空用渠道默认档，非零自动夹到该渠道上下限。 */
  durationS?: number;
  /** 解说词与分镜共用的文本（LLM）模型 id；留空走系统默认文本链。 */
  modelText?: string;
  /** 配音音频模型目录 id（后台 audio 目录）；留空走默认语音引擎。 */
  model?: string;
  /** 配音情绪提示词（可选）。 */
  emotionPrompt?: string;
  /** HyperFrames 渲染设计预设；仅 hyperframes 引擎生效，未知值后端归一化为 ink。 */
  stylePreset?: "ink" | "ocean" | "sunset" | "paper" | "mono";
  /** 是否叠加同步字幕带；undefined = 用渠道默认（均为开），显式 true/false 覆盖。 */
  captions?: boolean;
  resolution?: FreezoneVideoResolution;
  /** 配音声线引用（可选；留空用项目默认解说声线）。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** 背景音乐静态地址（可选）。 */
  bgmUrl?: string;
  /** BGM 音量 0-1；留空自动（与配音混音 0.2）。 */
  bgmVolume?: number | null;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneChannelShortVideo(
  project: string,
  payload: FreezoneChannelShortVideoPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/channel-short-video`,
    {
      method: "POST",
      json: {
        channel: payload.channel ?? "douyin",
        reference_content: payload.referenceContent ?? "",
        article_job_id: payload.articleJobId ?? "",
        title: payload.title ?? "",
        // 0 = 渠道默认档；上下限由后端 resolve_duration 按渠道夹，前端不预设固定范围。
        duration_s: Number.isFinite(payload.durationS) ? Math.trunc(payload.durationS ?? 0) : 0,
        resolution: payload.resolution ?? "720p",
        style_preset: payload.stylePreset ?? "ink",
        // captions 缺省传 null，交给后端取渠道默认（不提前真值化）。
        captions: payload.captions ?? null,
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        bgm_url: payload.bgmUrl ?? "",
        // 空 = 自动音量（后端混音 0.2）；0 是显式合法值，不能被当空丢弃。
        ...(payload.bgmVolume == null
          ? {}
          : { bgm_volume: clampNumberInput(payload.bgmVolume, 0.2, 0, 1) }),
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

/**
 * [aipdd fork delta] 口播智能剪辑：一条口播视频→本地静音剪 + 网关 ASR/文本
 * 瑕疵剪与分镜/B-roll 判定 + 生图/生乐→直出 MP4 或 VectCut 剪映草稿（或两者）。
 */
export interface FreezoneNarrationSmartEditPayload {
  /** 待剪源的同源引用（上传路径或 /static 资产）；必填。 */
  videoUrl: string;
  /** 输出通道：draft / video / both（默认 both）。 */
  outputMode?: "draft" | "video" | "both";
  /** 成片画幅：9:16 / 16:9 / 1:1（默认 9:16）。 */
  aspect?: string;
  /** 是否剪静音/停顿（默认 true）。 */
  cutSilence?: boolean;
  /** 是否剪口误/废话/重复（默认 true）。 */
  cutFillers?: boolean;
  /** 是否分镜/B-roll 判定与生成（默认 true）。 */
  planBroll?: boolean;
  /** 是否网关生成 BGM（默认 false）。 */
  genBgm?: boolean;
  /** 草稿是否导入剪后转录字幕（默认 true）。 */
  captions?: boolean;
  /** silencedetect 静音阈值 dB（默认 -35）。 */
  noiseDb?: number | null;
  /** 判为静音的最短时长秒（默认 0.4）。 */
  minSilenceS?: number | null;
  /** 单次 B-roll 生图槽位上限（默认 8）。 */
  brollMax?: number;
  /** 转写 ASR 模型候选 id（空串走自动）。 */
  asrModel?: string;
  /** 文本模型 id（瑕疵剪与分镜共用）。 */
  textModel?: string;
  /** B-roll 生图模型 id。 */
  imageModel?: string;
  /** 音乐生成模型 id（BGM）。 */
  musicModel?: string;
  /** BGM 提示词。 */
  bgmPrompt?: string;
  /** BGM 垫底音量 0-1（默认 0.2）。 */
  bgmGain?: number | null;
  /** 可选草稿/成片标题。 */
  title?: string;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）。 */
  sourceTool?: string;
}

export async function submitFreezoneNarrationSmartEdit(
  project: string,
  payload: FreezoneNarrationSmartEditPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/narration-smart-edit`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        output_mode: payload.outputMode ?? "both",
        aspect: payload.aspect ?? "9:16",
        cut_silence: payload.cutSilence ?? true,
        cut_fillers: payload.cutFillers ?? true,
        plan_broll: payload.planBroll ?? true,
        gen_bgm: payload.genBgm ?? false,
        captions: payload.captions ?? true,
        noise_db: payload.noiseDb ?? -35,
        min_silence_s: clampNumberInput(payload.minSilenceS ?? 0.4, 0.4, 0, 5),
        broll_max: payload.brollMax ?? 8,
        asr_model: payload.asrModel ?? "",
        text_model: payload.textModel ?? "",
        image_model: payload.imageModel ?? "",
        music_model: payload.musicModel ?? "",
        bgm_prompt: payload.bgmPrompt ?? "",
        bgm_gain: clampNumberInput(payload.bgmGain ?? 0.2, 0.2, 0, 1),
        title: payload.title ?? "",
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

/**
 * [aipdd fork delta] 公众号发布（同步）：把已排版的文章发到草稿箱 / 可选直发。
 * 凭证缺失不阻塞——后端返回 status=needs_config 与配置指引（在面板内联展示）。
 */
export interface FreezoneArticlePublishPayload {
  /** 来源 freezone_article_write 任务 job_id（与 htmlUrl 二选一）。 */
  articleJobId?: string;
  /** 或直接指定排版 HTML 静态地址；优先于 articleJobId。 */
  htmlUrl?: string;
  title?: string;
  author?: string;
  digest?: string;
  /** 封面图静态地址（图文草稿必填）。 */
  coverUrl?: string;
  /** 建草稿后是否直接发布。 */
  publishNow?: boolean;
}

export interface FreezoneArticlePublishResult {
  /** 发布结果：published / draft / needs_config / dry_run / error。 */
  status: string;
  /** 是否真正产出可交付（published/draft/dry_run）；needs_config/error 为 false。 */
  published_ok: boolean;
  message: string;
  draft_media_id?: string | null;
  publish_id?: string | null;
  article_url?: string | null;
  guidance?: string | null;
  error_code?: number | string | null;
}

export async function submitFreezoneArticlePublish(
  project: string,
  payload: FreezoneArticlePublishPayload,
): Promise<FreezoneArticlePublishResult> {
  return await apiCall<FreezoneArticlePublishResult>(
    `projects/${encodeURIComponent(project)}/freezone/article/publish`,
    {
      method: "POST",
      json: {
        article_job_id: payload.articleJobId ?? "",
        html_url: payload.htmlUrl ?? "",
        title: payload.title ?? "",
        author: payload.author ?? "",
        digest: payload.digest ?? "",
        cover_url: payload.coverUrl ?? "",
        publish_now: payload.publishNow ?? false,
      },
    },
  );
}

/** freezone_article_write 产物结果（任务完成后回读，用于预览与发布）。 */
export interface FreezoneArticleResult {
  title: string;
  style: string;
  theme: string;
  model: string;
  markdown: string;
  html_url: string;
  span_leaf_count: number;
  validation_warnings: string[];
}

export async function fetchFreezoneArticleResult(
  project: string,
  jobId: string,
): Promise<FreezoneArticleResult> {
  return await apiCall<FreezoneArticleResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_article_write/${encodeURIComponent(jobId)}/result`,
  );
}

/**
 * [aipdd fork delta] 一句话生成短视频（ViMax idea2video 口径）：一句话创意 →
 * 故事扩写 → 角色提取与三视图 → 分场景脚本 → 分镜/机位树/首尾帧 → 逐镜视频
 * → 拼接成片。肖像与帧读图片模型目录、逐镜读视频模型目录；一次提交产出一条
 * 完整成片（不是逐素材批量）。
 */
export interface FreezoneVideoIdeaPayload {
  /** 一句话创意（必填，≤2000 字）。 */
  idea: string;
  /** 视觉风格，贯穿角色肖像与逐镜画面的生成。 */
  style?: string;
  /** 可选附加要求（受众、类型、节奏、结局等）。 */
  userRequirement?: string;
  /**
   * 成片目标总时长（秒）5-300，默认 60：分场与分镜（场景数、每场景镜头数、
   * 单镜时长）由后端 AI 依总时长自主规划。
   */
  totalDuration?: number;
  /** three=正/侧/背三视图（默认，一致性更好）；front=仅正面。 */
  portraitMode?: "three" | "front";
  /**
   * 文本（LLM）模型（故事扩写/角色提取/脚本/分镜等文本步骤）。目录 id
   * （/model-gateway/text-models 返回）；留空走系统默认文本模型链。
   */
  modelText?: string;
  /** 图片模型（角色肖像与逐镜帧；image 目录，catalogId / id 均可）。 */
  modelImage?: string;
  /** 视频模型（逐镜生成；video 目录，catalogId / id 均可）。 */
  modelVideo?: string;
  aspectRatio?: string;
  resolution?: FreezoneVideoResolution;
  /** 逐镜视频是否由模型生成音频（对白/音效）；模型不支持时静默关闭。 */
  generateAudio?: boolean;
  /** 产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoIdea(
  project: string,
  payload: FreezoneVideoIdeaPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/idea`,
    {
      method: "POST",
      json: {
        idea: payload.idea,
        style: payload.style ?? "Cinematic, realistic, film-like.",
        user_requirement: payload.userRequirement ?? "",
        // 总时长口径：只传目标总时长（归一 5-300，默认 60）；分场/分镜/
        // 单镜时长交给后端 AI 规划。
        total_duration: normalizeNumberInput(payload.totalDuration, 60, 5, 300),
        portrait_mode: payload.portraitMode ?? "three",
        ...(payload.modelText ? { model_text: payload.modelText } : {}),
        ...(payload.modelImage ? { model_image: payload.modelImage } : {}),
        ...(payload.modelVideo ? { model_video: payload.modelVideo } : {}),
        aspect_ratio: payload.aspectRatio ?? "16:9",
        resolution: payload.resolution ?? "720p",
        generate_audio: payload.generateAudio ?? true,
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/douyin-copywrite ------------------------------------------------- //

/**
 * [aipdd fork delta] 抖音文案提取的 ASR 模型候选（基于网关拉取的模型 list
 * 过滤 ASR 同类模型，外加内置 FunASR 通道）；用户显式指定，不按固定路由
 * 顺序选择。
 */
export interface FreezoneDouyinAsrModelOption {
  /** 提交时回传的稳定标识：`gateway:{provider}:{model}` / `funasr:aipdd:{task}` / `doubao_asr:openspeech:{variant}`。 */
  id: string;
  /** gateway=网关多媒体转写（本地音频直接上传）；funasr=内置 FunASR 异步通道（需公网音频直链）；doubao_asr=豆包语音 openspeech 直连（需独立 API Key，与网关 API Key 不通用）。 */
  kind: "gateway" | "funasr" | "doubao_asr";
  provider: string;
  /**
   * [aipdd fork delta] 展示用渠道（工具页在模型名旁渲染彩色渠道胶囊，
   * 空串 = 明确不标注）；与 provider 相同语义但展示层独立演进。
   */
  channel?: string;
  model: string;
  /** 展示名（如 `doubao-seed-asr`）；渠道由 channel 字段单独渲染。 */
  label: string;
  /**
   * [aipdd fork delta] 该候选依赖的渠道/凭证是否就绪（gateway 恒真；
   * funasr / doubao_asr 为后端只读探测）。false 时工具页在提交按钮附近
   * 提醒去设置页配置（VolcEngine 语音识别 API Key 与网关
   * API Key 不通用）。
   */
  ready?: boolean;
  /**
   * [aipdd fork delta req40] 该候选能不能收**本地上传**的音频：后端按渠道目录声明的
   * ``audio/transcriptions`` 端点回答（内置 FunASR 通道固定 false，只吃公网直链）；
   * 老响应缺失时按 true 处理。工具页不再拿 ``kind`` 反推输入形态。
   */
  acceptsLocalAudio?: boolean;
}

export async function fetchFreezoneDouyinCopywriteModels(
  project: string,
): Promise<FreezoneDouyinAsrModelOption[]> {
  return await apiCall<FreezoneDouyinAsrModelOption[]>(
    `projects/${encodeURIComponent(project)}/freezone/douyin-copywrite/models`,
  );
}

/**
 * [aipdd fork delta] 抖音文案提取：粘贴分享链接 → 后端解析视频详情（宿主
 * Chrome CDP 拦截 aweme detail）→ 按用户选定 ASR 模型转写口播文案。产物 txt 存项目
 * outputs，文案全文同时随任务结果返回；输入是链接不是素材，一次提交一条。
 */
export interface FreezoneDouyinCopywritePayload {
  /** 抖音分享链接或整段分享文案（含链接即可）。 */
  video: string;
  /** ASR 候选 id（/freezone/douyin-copywrite/models 返回值之一）；留空默认 FunASR。 */
  model?: string;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneDouyinCopywrite(
  project: string,
  payload: FreezoneDouyinCopywritePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/douyin-copywrite`,
    {
      method: "POST",
      json: {
        video: payload.video,
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/media-copywrite ------------------------------------------------ //

// [aipdd fork delta] req21：把「抖音文案提取」泛化为「音视频提取文案」，ASR
// 候选源与抖音同（网关 /audio/transcriptions + 内置 FunASR + 豆包 openspeech）。
/** ASR 候选形状与抖音文案提取一致（gateway / funasr），复用同一展示语义。 */
export type FreezoneMediaAsrModelOption = FreezoneDouyinAsrModelOption;

export async function fetchFreezoneMediaCopywriteModels(
  project: string,
): Promise<FreezoneMediaAsrModelOption[]> {
  return await apiCall<FreezoneMediaAsrModelOption[]>(
    `projects/${encodeURIComponent(project)}/freezone/media-copywrite/models`,
  );
}

/**
 * [aipdd fork delta] 音视频提取文案：三来源（link / upload_video / upload_audio）
 * 统一提交。`link` 走 yt-dlp 下载（抖音 yt-dlp↔宿主 Chrome CDP 双通路），`upload_*`
 * 的 `url` 是前端先传 `/freezone/upload` 返回的同源静态地址。产物 txt 存
 * outputs，文案全文随任务结果返回；一次提交解析一条媒体。
 */
export interface FreezoneMediaCopywritePayload {
  /** 来源类型："link" | "upload_video" | "upload_audio"。 */
  sourceType: "link" | "upload_video" | "upload_audio";
  /** 链接原文（link）或上传返回的静态地址（upload_*）。 */
  url: string;
  /** 链接平台提示（douyin/bilibili/kuaishou/xiaohongshu/tiktok/youtube/other），可空。 */
  platform?: string;
  /** ASR 候选 id（/freezone/media-copywrite/models 返回值之一）。 */
  model?: string;
  /** 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneMediaCopywrite(
  project: string,
  payload: FreezoneMediaCopywritePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/media-copywrite`,
    {
      method: "POST",
      json: {
        source_type: payload.sourceType,
        url: payload.url,
        ...(payload.platform ? { platform: payload.platform } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/video-script-reverse --------------------------------------------- //

// [aipdd fork delta] 工具页「视频反推剧本」：上传视频（/freezone/upload 回传的
// 静态 URL）→ 抽帧 → 用户可选视觉模型反推纯文本剧本。产物 txt，不覆盖故事
// 脚本 JSON；`model` 留空走后端默认视觉链。
export interface FreezoneVideoScriptReversePayload {
  /** 上传视频的静态 URL（后端 resolve_static_url_to_path 反解）。 */
  videoUrl: string;
  /** 用户补充要求，随任务书交给视觉模型，可空。 */
  prompt?: string;
  /** 选定的视觉模型调用名（/model-gateway/vision-models 的 id）。 */
  model?: string;
  /** 视频总时长（秒），可空。 */
  durationSec?: number | null;
  maxFrames?: number;
  sceneThreshold?: number;
}

// [aipdd fork delta] 工具页「视频反推剧本」独立任务类型（产物 txt）。ref 类型
// 单独声明、不进 FreezoneJobRef 联合：该任务不走 /freezone/jobs/{type}/{id}/result
// 端点（后端 Literal 白名单不含它），画布 overlay 会把整个联合传给
// fetchFreezoneJobResult，混进来会直接破坏类型子集关系。
export interface FreezoneVideoScriptReverseJobRef
  extends Omit<FreezoneJobRef, "task_type"> {
  task_type: "freezone_video_script_reverse";
}

export async function submitFreezoneVideoScriptReverse(
  project: string,
  payload: FreezoneVideoScriptReversePayload,
): Promise<FreezoneVideoScriptReverseJobRef> {
  const body: Record<string, unknown> = { video_url: payload.videoUrl };
  if (payload.prompt) body.prompt = payload.prompt;
  if (payload.model) body.model = payload.model;
  if (payload.durationSec != null) body.duration_sec = payload.durationSec;
  if (payload.maxFrames != null) body.max_frames = payload.maxFrames;
  if (payload.sceneThreshold != null) body.scene_threshold = payload.sceneThreshold;
  return await apiCall<FreezoneVideoScriptReverseJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video-script-reverse`,
    { method: "POST", json: body },
  );
}

export async function submitFreezoneGen(
  project: string,
  payload: FreezoneGenPayload,
): Promise<FreezoneJobRef> {
  const camera = payload.camera
    ? {
        camera_body: payload.camera.cameraBodyId ?? "",
        lens: payload.camera.lensId ?? "",
        focal_length_mm: payload.camera.focalLengthMm ?? 0,
        aperture: payload.camera.aperture ?? "",
      }
    : null;
  const style = payload.style?.templateId
    ? { template_id: payload.style.templateId }
    : null;
  // 后端只能按静态路径打开引用图：任何 `data:` base64 先上传换成上传 URL，
  // 其余 http(s)/static 原样保留（仅剥掉 `?v=` 缓存串）。统一在此兜底，
  // 避免各调用方漏做净化把 base64 塞进 reference_urls。
  const referenceUrls = await ensureBackendImageUrls(
    project,
    payload.referenceUrls,
  );
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/gen`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        aspect_ratio: payload.aspectRatio ?? "1:1",
        image_size: payload.imageSize ?? "2K",
        reference_urls: referenceUrls,
        camera,
        style,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        ...(payload.modelId ? { model_id: payload.modelId } : {}),
        ...(payload.genMode ? { gen_mode: payload.genMode } : {}),
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/erase --------------------------------------------------- //

/**
 * Per `FreezoneVideoEraseRequest` in openapi.json:
 * - `smart_subtitle`: backend auto-estimates the bottom subtitle band.
 * - `box`: front-end supplies a normalized box (0..1 against source frame).
 */
export type FreezoneVideoEraseMode = "smart_subtitle" | "box";

export interface FreezoneVideoEraseBox {
  /** Top-left x, normalized 0..1 against the source video frame. */
  x: number;
  /** Top-left y, normalized 0..1 against the source video frame. */
  y: number;
  /** Width, normalized (0, 1]. */
  width: number;
  /** Height, normalized (0, 1]. */
  height: number;
}

export interface FreezoneVideoErasePayload {
  sourceUrl: string;
  mode: FreezoneVideoEraseMode;
  /**
   * [aipdd fork delta] 工具页引擎三选一的擦除侧（见 VideoWatermarkPanel）：
   * - `local`：强制本机 ffmpeg 擦除（不出网）；
   * - `mediakit`：优先火山 AI MediaKit 云端擦除（未配置 / 失败自动回退本地）。
   * 不传（画布等既有入口）= 后端 `auto`（有 Key 走 MediaKit）。
   */
  engine?: "local" | "mediakit";
  /** Required when `mode === "box"`; ignored otherwise. */
  box?: FreezoneVideoEraseBox | null;
  /**
   * [aipdd fork delta] 工具页批量任务专用：产物完成后由后端 runner 自动登记
   * 进资产库的「待分类资产」。画布等其它入口不传（默认 false）。
   */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneVideoErase(
  project: string,
  payload: FreezoneVideoErasePayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    source_url: payload.sourceUrl,
    mode: payload.mode,
  };
  if (payload.engine) {
    body.engine = payload.engine;
  }
  if (payload.mode === "box" && payload.box) {
    // [aipdd fork delta] 框选区归一：0-1，宽高 gt=0——清空输入（Number("")=0）
    // 会让宽/高变 0 被 422 拒收，保底最小正值。
    body.box_x = clampNumberInput(payload.box.x, 0, 0, 1);
    body.box_y = clampNumberInput(payload.box.y, 0, 0, 1);
    body.box_width = clampNumberInput(payload.box.width, 0.5, 0.01, 1);
    body.box_height = clampNumberInput(payload.box.height, 0.1, 0.01, 1);
  }
  if (payload.archiveToLibrary) {
    body.archive_to_library = true;
  }
  if (payload.sourceTool) {
    body.source_tool = payload.sourceTool;
  }
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/erase`,
    { method: "POST", json: body },
  );
}

// /freezone/video/compose ------------------------------------------------- //

export type FreezoneVideoComposeResolution = "720p" | "1080p";
export type FreezoneVideoComposeTrackKind = "video" | "audio";

export interface FreezoneVideoComposeItemPayload {
  itemId: string;
  sourceUrl: string;
  /** Position on the output timeline, in seconds. */
  timelineStart?: number;
  /** Trim start within source media, in seconds. */
  sourceStart?: number;
  /** Trim end within source media, in seconds. Must be > sourceStart. */
  sourceEnd: number;
  volume?: number;
  muted?: boolean;
  /**
   * Playback rate (变速). 1 = original speed. ⚠️ Only honored if the BE compose
   * endpoint implements it; otherwise the field is ignored (export stays 1×).
   */
  speed?: number;
}

export interface FreezoneVideoComposeTrackPayload {
  trackId: string;
  kind: FreezoneVideoComposeTrackKind;
  items: FreezoneVideoComposeItemPayload[];
}

export interface FreezoneVideoComposePayload {
  title?: string;
  canvasId?: string;
  resolution?: FreezoneVideoComposeResolution;
  fps?: number;
  backgroundColor?: string;
  keepOriginalAudio?: boolean;
  /**
   * 封面图 URL（已上传到后端的稳定地址）。后端支持时会把它挂成 MP4 的封面流
   * （attached_pic，不改正片时长）；不支持时忽略，前端缩略图仍用同一个 url。
   */
  coverUrl?: string | null;
  tracks: FreezoneVideoComposeTrackPayload[];
}

export async function submitFreezoneVideoCompose(
  project: string,
  payload: FreezoneVideoComposePayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    title: payload.title ?? "",
    canvas_id: payload.canvasId ?? "",
    resolution: payload.resolution ?? "1080p",
    fps: payload.fps ?? 30,
    background_color: payload.backgroundColor ?? "#000000",
    keep_original_audio: payload.keepOriginalAudio ?? true,
    cover_url: payload.coverUrl ?? "",
    tracks: payload.tracks.map((track) => ({
      track_id: track.trackId,
      kind: track.kind,
      items: track.items.map((item) => ({
        item_id: item.itemId,
        source_url: item.sourceUrl,
        timeline_start: item.timelineStart ?? 0,
        source_start: item.sourceStart ?? 0,
        source_end: item.sourceEnd,
        volume: item.volume ?? 1,
        muted: item.muted ?? false,
        speed: item.speed ?? 1,
      })),
    })),
  };
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/compose`,
    { method: "POST", json: body },
  );
}

// /freezone/video/audio-separate ----------------------------------------- //

/**
 * Per `FreezoneAudioSeparateRequest` in openapi.json the only required field is
 * `source_url`. Backend returns two artifacts via the task SSE: the extracted
 * audio track and a silent (muted) version of the original video.
 */
export interface FreezoneAudioSeparatePayload {
  sourceUrl: string;
  /** 目标 beat;给定后分离出的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
}

export async function submitFreezoneAudioSeparate(
  project: string,
  payload: FreezoneAudioSeparatePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/audio-separate`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
      },
    },
  );
}

/**
 * Result shape for `freezone_audio_separate` isn't typed in openapi.json — the
 * endpoint declares `{}` (free-form). The light-version backend ships two
 * artifacts (audio track + silent video); callers walk the tree to find the
 * two URLs by extension.
 */
export async function fetchFreezoneAudioSeparateResult(
  project: string,
  jobId: string,
): Promise<Record<string, unknown>> {
  return await apiCall<Record<string, unknown>>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_audio_separate/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/image/reverse-prompt ----------------------------------------- //

/**
 * `source_url` is required; `instruction` steers the returned prompt.
 */
export interface FreezoneReversePromptPayload extends FreezoneNodeContext {
  sourceUrl: string;
  instruction?: string;
}

export async function submitFreezoneReversePrompt(
  project: string,
  payload: FreezoneReversePromptPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/image/reverse-prompt`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        instruction: payload.instruction ?? "",
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/image/style-templates ---------------------------------------- //

export interface FreezoneStyleTemplate {
  id: string;
  label: string;
  /** 题材分类:古装 / 都市 / 年代 / 生活 / 科幻 / 类型 / 写意,图墙顶部按它分组。 */
  category: string;
  /** 封面图相对路径,需经 resolveStyleAssetUrl 解析。 */
  cover: string;
  /** 女 / 少 / 男 / 老 四张示例图的相对路径。 */
  samples: string[];
  /** 中文风格提示词全文,原样拼进生成 prompt。 */
  style_prompt: string;
}

export interface FreezoneStyleTemplateList {
  assetBase: string;
  /** 后端风格清单的版本号,图片和提示词对不上时用来定位是哪一份清单在生效。 */
  version: string;
  templates: FreezoneStyleTemplate[];
}

/**
 * 把后端吐回来的东西掰成风格模板数组。
 *
 * 这个端点的形状在两个仓库之间反复横跳过：早期 `data` 是裸列表，后来被换成
 * `{asset_base, version, templates}` 的对象（于是没跟进的前端 `templates.find(...)`
 * 当场抛 `is not a function`，整页白屏），现在又改回裸列表、元信息挂到信封同级。
 * 所以这里不信任何一种形状，四种常见包装都认，认不出就退空列表 —— 图墙空着是能
 * 看懂的降级，白屏不是。同一族的 {@link coerceCameraTemplateList} 就是这么做的。
 */
function coerceStyleTemplateList(payload: unknown): FreezoneStyleTemplate[] {
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.templates)) candidate = wrapper.templates;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else if (Array.isArray(wrapper.style_templates)) candidate = wrapper.style_templates;
  }
  if (!Array.isArray(candidate)) return [];
  const result: FreezoneStyleTemplate[] = [];
  for (const item of candidate) {
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    // 没 id 就没法回写到节点上，这条直接丢掉；其余字段缺了还能降级显示。
    const id = pickString(entry, "id", "template_id", "templateId", "key");
    if (!id) continue;
    result.push({
      id,
      label: pickString(entry, "label", "display_name", "displayName", "title", "name") ?? id,
      category: pickString(entry, "category", "group", "kind") ?? "",
      cover: pickString(entry, "cover", "cover_url", "coverUrl", "thumbnail") ?? "",
      samples: pickStringArray(entry, "samples", "sample_urls", "sampleUrls"),
      style_prompt:
        pickString(entry, "style_prompt", "stylePrompt", "prompt", "prompt_fragment") ?? "",
    });
  }
  return result;
}

export async function listFreezoneStyleTemplates(
  project: string,
): Promise<FreezoneStyleTemplateList> {
  // 走 apiCallEnvelope 而不是 apiCall：清单元信息挂在 `data` 同级（`data` 得留给
  // 裸列表，见后端路由注释）。元信息在 data 里的旧形状也照样能认出来。
  const envelope = await apiCallEnvelope<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/image/style-templates`,
  );
  const nested =
    envelope.data && typeof envelope.data === "object" && !Array.isArray(envelope.data)
      ? (envelope.data as Record<string, unknown>)
      : {};
  const meta = (key: string) =>
    pickString(envelope, key) ?? pickString(nested, key) ?? "";
  return {
    assetBase: meta("asset_base"),
    version: meta("version"),
    templates: coerceStyleTemplateList(envelope.data),
  };
}

// /freezone/image/camera-options ----------------------------------------- //

export interface FreezoneCameraIdLabel {
  id: string;
  label: string;
}

export interface FreezoneCameraOptions {
  camera_bodies: FreezoneCameraIdLabel[];
  lenses: FreezoneCameraIdLabel[];
  focal_lengths_mm: number[];
  apertures: string[];
}

export async function fetchFreezoneCameraOptions(
  project: string,
): Promise<FreezoneCameraOptions> {
  return await apiCall<FreezoneCameraOptions>(
    `projects/${encodeURIComponent(project)}/freezone/image/camera-options`,
  );
}

// /freezone/image/models -------------------------------------------------- //

export type MediaModelParameterControl = "select" | "number" | "switch" | "text" | "multiselect";
export interface MediaModelParameterDefinition {
  key: string;
  label: string;
  control: MediaModelParameterControl;
  requestPath: string;
  options?: Array<string | number | boolean>;
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  required?: boolean;
  modes?: string[];
}
export interface MediaModelRequestSchema {
  endpoint: "images/generations" | "video/generations" | "audio/speech";
  parameters?: MediaModelParameterDefinition[];
  omitPaths?: string[];
}

export interface FreezoneImageModelInfo extends ReferenceMediaLimits {
  /** Opaque database identity used by new billing and task records. */
  catalogId?: string;
  /** Stable picker id, e.g. `"huimeng/gpt-image-2"`. */
  id: string;
  /** Provider tab id (`huimeng` / `openrouter` / `openai`). */
  providerId: FreezoneProvider;
  /**
   * [aipdd fork delta] 展示用渠道（aipdd / newapi / volcengine ...）：后台
   * 「默认生成模型」里这条模型所属的渠道。providerId 是执行身份（媒体调用统一
   * 经 newapi 渠道），画布模型选择器读 channel 在模型旁做彩色渠道标注。
   */
  channel?: string;
  /** Value sent to backend `model` field. */
  apiModel: string;
  /** Display label in the model chip. */
  label: string;
  resolutionOptions?: string[];
  qualityOptions?: string[];
  ratioOptions?: string[];
  referenceImageMax?: number | null;
  request?: MediaModelRequestSchema;
}

// Provider inference for raw model strings the backend may return without
// metadata (e.g. just a flat string list). Order matters — first match wins.
const MODEL_PROVIDER_HINTS: Array<{
  match: (raw: string) => boolean;
  providerId: FreezoneProvider;
}> = [
  { match: (s) => s.toLowerCase().startsWith("huimeng"), providerId: "huimeng" },
  { match: (s) => s.toLowerCase().includes("/gemini"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("google/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("anthropic/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("openrouter/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("gpt-image"), providerId: "openai" },
  { match: (s) => s.toLowerCase().startsWith("dall-e"), providerId: "openai" },
];

function inferProvider(raw: string): FreezoneProvider {
  for (const hint of MODEL_PROVIDER_HINTS) {
    if (hint.match(raw)) return hint.providerId;
  }
  return "huimeng";
}

function pickString(record: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function pickNumber(record: Record<string, unknown>, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim().length > 0) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function pickBoolean(record: Record<string, unknown>, ...keys: string[]): boolean | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function pickStringArray(record: Record<string, unknown>, ...keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === "string" && item.length > 0);
    }
  }
  return [];
}

function pickMediaRequestSchema(value: unknown): MediaModelRequestSchema | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const schema = value as Record<string, unknown>;
  if (typeof schema.endpoint !== "string") return undefined;
  return schema as unknown as MediaModelRequestSchema;
}

function normalizeProviderId(raw: string | null): FreezoneProvider | null {
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  if (
    lowered === "newapi" ||
    lowered === "huimeng" ||
    lowered === "openrouter" ||
    lowered === "openai"
  ) {
    return lowered;
  }
  return null;
}

function modelEntryFromObject(entry: Record<string, unknown>): FreezoneImageModelInfo | null {
  const apiModel = pickString(entry, "model", "apiModel", "api_model", "name");
  if (!apiModel) return null;
  const providerId =
    normalizeProviderId(pickString(entry, "providerId", "provider_id", "provider")) ??
    inferProvider(apiModel);
  const id = pickString(entry, "id") ?? `${providerId}/${apiModel}`;
  const label = pickString(entry, "label", "displayName", "display_name") ?? apiModel;
  const catalogId = pickString(entry, "catalogId", "catalog_id");
  // [aipdd fork delta] 展示用渠道；未声明时保持 undefined，由消费方回退 providerId。
  const channel = pickString(entry, "channel", "channelProvider", "channel_provider");
  return {
    ...(catalogId ? { catalogId } : {}),
    id,
    providerId,
    ...(channel ? { channel } : {}),
    apiModel,
    label,
    resolutionOptions: pickStringArray(entry, "resolutionOptions", "resolution_options"),
    qualityOptions: pickStringArray(entry, "qualityOptions", "quality_options"),
    ratioOptions: pickStringArray(entry, "ratioOptions", "ratio_options"),
    ...readReferenceMediaLimits(entry),
    referenceImageMax: pickNumber(entry, "referenceImageMax", "reference_image_max"),
    request: pickMediaRequestSchema(entry.request),
  };
}

function modelEntryFromString(raw: string): FreezoneImageModelInfo {
  const providerId = inferProvider(raw);
  return {
    id: `${providerId}/${raw}`,
    providerId,
    apiModel: raw,
    label: raw,
  };
}

// [aipdd fork delta] 防御式目录解析导出：风格封面模型端点
// （/style-gallery/image-models）与 /freezone/image/models 返回同源条目，
// 复用同一套归一化，避免两处形状理解漂移。
export function coerceModelList(payload: unknown): FreezoneImageModelInfo[] {
  // Accept several shapes the backend might return — schema is empty in
  // openapi.json so we normalize defensively rather than guess one shape.
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.models)) candidate = wrapper.models;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else {
      // provider→models[] map: { huimeng: [...], openrouter: [...] }
      const flattened: FreezoneImageModelInfo[] = [];
      for (const [providerRaw, value] of Object.entries(wrapper)) {
        const providerId = normalizeProviderId(providerRaw);
        if (!providerId || !Array.isArray(value)) continue;
        for (const item of value) {
          if (typeof item === "string") {
            flattened.push({
              id: `${providerId}/${item}`,
              providerId,
              apiModel: item,
              label: item,
            });
          } else if (item && typeof item === "object") {
            const entry = modelEntryFromObject(item as Record<string, unknown>);
            if (entry) flattened.push({ ...entry, providerId });
          }
        }
      }
      if (flattened.length > 0) return flattened;
    }
  }

  if (!Array.isArray(candidate)) return [];
  const result: FreezoneImageModelInfo[] = [];
  for (const item of candidate) {
    if (typeof item === "string") {
      result.push(modelEntryFromString(item));
    } else if (item && typeof item === "object") {
      const entry = modelEntryFromObject(item as Record<string, unknown>);
      if (entry) result.push(entry);
    }
  }
  return result;
}

export async function fetchFreezoneImageModels(
  project: string,
): Promise<FreezoneImageModelInfo[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/image/models`,
  );
  return coerceModelList(payload);
}

// /freezone/audio/models -------------------------------------------------- //
// [aipdd fork delta] 画布音频节点的模型候选：后台「默认生成模型」里
// mediaType=audio 的条目。形状与 image 目录一致，复用同一套防御式解析。
// 空列表是合法状态（语音默认引擎不依赖目录），所以这里不提供硬编码默认模型。
// 拉取失败时同样回落空列表，不伪装成有模型可用。

export type FreezoneAudioModelInfo = FreezoneImageModelInfo;

export async function fetchFreezoneAudioModels(
  project: string,
): Promise<FreezoneAudioModelInfo[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/audio/models`,
  );
  return coerceModelList(payload);
}

// /freezone/music/models -------------------------------------------------- //
// [aipdd fork delta req46] 工具页「音乐创作」的模型候选：后台「默认生成模型」里
// mediaType=music 的条目（music-3.0 / music-cover 等）。条目形状与 image / audio
// 目录同源，复用同一套防御式解析；没有硬编码兜底列表——管理员没配就是不配，
// 面板按「空目录 = 去设置页配置」解释，绝不写死厂商模型名伪装可用（req40 棘轮）。

export type FreezoneMusicModelInfo = FreezoneImageModelInfo;

export async function fetchFreezoneMusicModels(
  project: string,
): Promise<FreezoneMusicModelInfo[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/music/models`,
  );
  return coerceModelList(payload);
}

// /freezone/audio/music-create + /freezone/audio/music-cover-preprocess ---- //
// [aipdd fork delta req46] MiniMax Music 3.0 的两种直连链路：
// - music-create 建异步任务（task_type=freezone_music_generate），产物 mp3/wav
//   落项目输出目录并入库「待分类资产」；
// - music-cover-preprocess 同步返回特征 id + ASR 抽取的歌词，供哼唱模式让用户
//   先改词再提交（上游按 MD5 去重，重复预处理拿到的是同一个 id）。
// 模式 → 上游字段的收口在后端 minimax_music.build_music_payload，前端只表达
// 「用户选了什么」，不自己拼协议字段，避免两处口径漂移。

/** 音乐创作模式（与后端 Literal 一一对应）。 */
export type FreezoneMusicMode =
  | "one_sentence"
  | "lyrics"
  | "instrumental"
  | "cover"
  | "humming";

export interface FreezoneMusicCreatePayload {
  /** 创作模式。 */
  mode: FreezoneMusicMode;
  /** 音乐描述；一句话/纯音乐必填，翻唱/哼唱是目标风格描述（10-300 字）。 */
  prompt?: string;
  /** 歌词；歌词模式必填（1-3500 字），翻唱选填。 */
  lyrics?: string;
  /** 音乐模型目录条目 id；留空 = 后端用设置页的「音乐默认模型」。 */
  model?: string;
  /** 站点渠道（minimax / minimax_cn）；留空 = 取模型条目所属渠道。 */
  provider?: string;
  /** 参考音频：同源静态地址 / 项目内相对路径 / 公网直链（6 秒-6 分钟、≤50MB）。 */
  referenceAudio?: string;
  /** 两步翻唱的特征 id（预处理返回）；带了它就不要再传 referenceAudio。 */
  coverFeatureId?: string;
  /** 采样率：16000 / 24000 / 32000 / 44100。 */
  sampleRate?: number;
  /** 码率：32000 / 64000 / 128000 / 256000。 */
  bitrate?: number;
  /** 产物格式：mp3 / wav / pcm。 */
  audioFormat?: "mp3" | "wav" | "pcm";
  /** 归档条目名前缀（来源素材名），可空。 */
  sourceName?: string;
  /** 工具页批量任务标记：完成后登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** 任务归属标识（工具 kind），浮现到 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneMusicCreate(
  project: string,
  payload: FreezoneMusicCreatePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/music-create`,
    {
      method: "POST",
      json: {
        mode: payload.mode,
        ...(payload.prompt ? { prompt: payload.prompt } : {}),
        ...(payload.lyrics ? { lyrics: payload.lyrics } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.provider ? { provider: payload.provider } : {}),
        ...(payload.referenceAudio
          ? { reference_audio: payload.referenceAudio }
          : {}),
        ...(payload.coverFeatureId
          ? { cover_feature_id: payload.coverFeatureId }
          : {}),
        ...(payload.sampleRate !== undefined
          ? { sample_rate: payload.sampleRate }
          : {}),
        ...(payload.bitrate !== undefined ? { bitrate: payload.bitrate } : {}),
        ...(payload.audioFormat ? { audio_format: payload.audioFormat } : {}),
        ...(payload.sourceName ? { source_name: payload.sourceName } : {}),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

/** 两步翻唱第一步的返回：特征 id（24 小时有效）+ ASR 抽取的结构化歌词。 */
export interface FreezoneMusicCoverPreprocessResult {
  cover_feature_id: string;
  formatted_lyrics: string;
  audio_duration?: number | null;
  model?: string;
  provider?: string;
}

export async function submitFreezoneMusicCoverPreprocess(
  project: string,
  payload: {
    model?: string;
    provider?: string;
    referenceAudio: string;
  },
): Promise<FreezoneMusicCoverPreprocessResult> {
  const response = await apiCall<{ data?: FreezoneMusicCoverPreprocessResult }>(
    `projects/${encodeURIComponent(project)}/freezone/audio/music-cover-preprocess`,
    {
      method: "POST",
      json: {
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.provider ? { provider: payload.provider } : {}),
        reference_audio: payload.referenceAudio,
      },
    },
  );
  // 后端把结果包在 { ok, data } 里；防御式取值，缺 data 时按空结果处理，
  // 让面板显示「没有抽到词」而不是崩在一次 undefined 解构上。
  return response?.data ?? { cover_feature_id: "", formatted_lyrics: "" };
}

// /model-gateway/text-models ---------------------------------------------- //
// [aipdd fork delta] 画布脚本/文本节点的模型候选：后端聚合 NewAPI 渠道
// model_mapping 里的文本模型名（排除媒体/embedding），默认文本模型排首项。
// 不挂项目作用域，是全局端点。

export interface FreezoneTextModelInfo {
  /** 网关内部模型名，同时作为 picker id 与提交给后端的 model 值。 */
  id: string;
  providerId: string;
  /** [aipdd fork delta] 展示用渠道：别名真源里这条模型实际所属的渠道。 */
  channel?: string;
  /** [aipdd fork delta] 系统默认文本模型标记（后端 text-models 聚合时唯一标 true）。 */
  isDefault?: boolean;
  apiModel: string;
  label: string;
}

export async function fetchFreezoneTextModels(): Promise<FreezoneTextModelInfo[]> {
  const payload = await apiCall<unknown>("model-gateway/text-models");
  return normalizeGatewayModelInfos(payload);
}

// [aipdd fork delta] 网关文本/视觉候选端点（text-models / vision-models）同构的
// 响应解析真源：包装体 {data:[...]} / 裸数组 / 字符串条目统一成 FreezoneTextModelInfo。
function normalizeGatewayModelInfos(payload: unknown): FreezoneTextModelInfo[] {
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.models)) candidate = wrapper.models;
  }
  if (!Array.isArray(candidate)) return [];
  const result: FreezoneTextModelInfo[] = [];
  for (const item of candidate) {
    if (typeof item === "string" && item.trim()) {
      result.push({ id: item, providerId: "newapi", apiModel: item, label: item });
      continue;
    }
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    const id = pickString(entry, "id", "model", "apiModel", "api_model");
    if (!id) continue;
    // [aipdd fork delta] 展示用渠道；未声明时保持 undefined，由消费方回退 providerId。
    const channel = pickString(entry, "channel", "channelProvider", "channel_provider");
    const isDefault = pickBoolean(entry, "isDefault", "is_default");
    result.push({
      id,
      providerId: pickString(entry, "providerId", "provider") ?? "newapi",
      ...(channel ? { channel } : {}),
      ...(isDefault ? { isDefault: true } : {}),
      apiModel: pickString(entry, "apiModel", "api_model") ?? id,
      label: pickString(entry, "label", "displayName", "display_name") ?? id,
    });
  }
  return result;
}

// /model-gateway/vision-models --------------------------------------------- //

// [aipdd fork delta] 「视频反推剧本」工具页的视觉模型候选：与 text-models 同构，
// 后端只列具备视觉理解能力的模型。空列表不阻断提交：留空 = 后端默认视觉链
// （FREEZONE_VISION_MODEL → VISION_MODEL_FALLBACK）。
export type FreezoneVisionModelInfo = FreezoneTextModelInfo;

export async function fetchFreezoneVisionModels(): Promise<FreezoneVisionModelInfo[]> {
  const payload = await apiCall<unknown>("model-gateway/vision-models");
  return normalizeGatewayModelInfos(payload);
}

// /freezone/video/models -------------------------------------------------- //

/** Provider tab id for video generation models. */
export type FreezoneVideoProvider = "newapi" | "seedance" | "huimeng";

export interface FreezoneVideoModelInfo extends ReferenceMediaLimits {
  /** Opaque database identity used by new billing and task records. */
  catalogId?: string;
  /** Stable picker id, e.g. `"seedance_2"` (backend currently keys by api id). */
  id: string;
  /** Provider tab id (`seedance` / `huimeng`). */
  providerId: FreezoneVideoProvider;
  /**
   * [aipdd fork delta] 展示用渠道（aipdd / newapi / volcengine ...）：后台
   * 「默认生成模型」里这条模型所属的渠道；未声明时由消费方回退 providerId。
   */
  channel?: string;
  /** Value sent to backend `/freezone/video/gen` `model` field. */
  apiModel: string;
  /** Display label in the model chip. */
  label: string;
  /** Supported output resolution values for this model, when advertised by backend. */
  resolutionOptions?: FreezoneVideoResolution[];
  humanReview?: boolean;
  /** Whether this model can produce native synchronized audio. Missing means legacy-supported. */
  supportsGenerateAudio?: boolean;
  /** Smallest supported duration in seconds, when advertised by backend. */
  minDuration?: number | null;
  /** Largest supported duration in seconds, when advertised by backend. */
  maxDuration?: number | null;
  /** Supported Seedance 2.0 Value style hints, when advertised by backend. */
  sceneOptimizeOptions?: Array<"anime" | "realistic">;
  /** Default Seedance 2.0 Value style hint, when advertised by backend. */
  defaultSceneOptimize?: "anime" | "realistic" | null;
  ratioOptions?: string[];
  supportedModes?: string[];
  /**
   * [aipdd fork delta req44] 渠道在线绑定里确实备好的提交模式（`first_last_frame` 等）。
   *
   * 这是**只加不减的咨询证据**：只用来放开被「按模型名猜」藏掉的入口（H3 的首尾帧），
   * 不能反过来当成能力上限 —— 渠道没暴露能力接口 / 快照还没落时它就是空数组，读不出
   * 「不支持」。与 `supportedModes`（显式声明，参与严校验）刻意分成两个字段。
   */
  bindingModes?: string[];
  referenceImageMax?: number | null;
  referenceVideoMax?: number | null;
  referenceAudioMax?: number | null;
  referenceFileMax?: number | null;
  referenceLinkMax?: number | null;
  referenceFileTypes?: string[];
  referenceAudioMinSeconds?: number | null;
  referenceAudioMaxSeconds?: number | null;
  referenceAudioTotalMinSeconds?: number | null;
  /**
   * 参考音频**总时长**上限（秒）。厂商 r2v 除了逐条 1.8~15.2s，还另有一条总和限制
   * （见 videoModelCapabilities 里的说明），这项就是它的后台可配值；没配时前端按
   * 15.2s 兜底。允许小数，别当成整数字段处理。
   */
  referenceAudioTotalMaxSeconds?: number | null;
  referenceVideoMinSeconds?: number | null;
  referenceVideoMaxSeconds?: number | null;
  referenceVideoTotalMinSeconds?: number | null;
  referenceVideoTotalMaxSeconds?: number | null;
  request?: MediaModelRequestSchema;
}

// Provider inference for raw model ids the backend may return without
// metadata. Order matters — first match wins. Anything we don't recognize
// falls back to `seedance` (the primary provider).
const VIDEO_MODEL_PROVIDER_HINTS: Array<{
  match: (raw: string) => boolean;
  providerId: FreezoneVideoProvider;
}> = [
  { match: (s) => s.toLowerCase().startsWith("huimeng"), providerId: "huimeng" },
  { match: (s) => s.toLowerCase().startsWith("seedance"), providerId: "seedance" },
];

function inferVideoProvider(raw: string): FreezoneVideoProvider {
  for (const hint of VIDEO_MODEL_PROVIDER_HINTS) {
    if (hint.match(raw)) return hint.providerId;
  }
  return "seedance";
}

function normalizeVideoProviderId(raw: string | null): FreezoneVideoProvider | null {
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  if (lowered === "newapi" || lowered === "seedance" || lowered === "huimeng") return lowered;
  return null;
}

function videoModelEntryFromObject(
  entry: Record<string, unknown>,
): FreezoneVideoModelInfo | null {
  const apiModel = pickString(entry, "model", "apiModel", "api_model", "name");
  if (!apiModel) return null;
  const providerId =
    normalizeVideoProviderId(pickString(entry, "providerId", "provider_id", "provider")) ??
    inferVideoProvider(apiModel);
  const id = pickString(entry, "id") ?? apiModel;
  const label = pickString(entry, "label", "displayName", "display_name") ?? apiModel;
  const catalogId = pickString(entry, "catalogId", "catalog_id");
  // [aipdd fork delta] 展示用渠道；未声明时保持 undefined，由消费方回退 providerId。
  const channel = pickString(entry, "channel", "channelProvider", "channel_provider");
  const resolutionOptions = pickStringArray(entry, "resolutionOptions", "resolution_options");
  const sceneOptimizeOptions = pickStringArray(entry, "sceneOptimizeOptions", "scene_optimize_options")
    .map((value) => value.toLowerCase())
    .filter((value): value is "anime" | "realistic" =>
      value === "anime" || value === "realistic"
    );
  const defaultSceneOptimizeRaw = pickString(entry, "defaultSceneOptimize", "default_scene_optimize")
    ?.toLowerCase();
  const defaultSceneOptimize =
    defaultSceneOptimizeRaw === "anime" || defaultSceneOptimizeRaw === "realistic"
      ? defaultSceneOptimizeRaw
      : null;
  return {
    ...(catalogId ? { catalogId } : {}),
    id,
    providerId,
    ...(channel ? { channel } : {}),
    apiModel,
    label,
    ...(resolutionOptions.length > 0 ? { resolutionOptions } : {}),
    humanReview: pickBoolean(entry, "humanReview", "human_review"),
    supportsGenerateAudio: pickBoolean(
      entry,
      "supportsGenerateAudio",
      "supports_generate_audio",
    ),
    minDuration: pickNumber(entry, "minDuration", "min_duration"),
    maxDuration: pickNumber(entry, "maxDuration", "max_duration"),
    ...(sceneOptimizeOptions.length > 0 ? { sceneOptimizeOptions } : {}),
    defaultSceneOptimize,
    ratioOptions: pickStringArray(entry, "ratioOptions", "ratio_options"),
    supportedModes: pickStringArray(entry, "supportedModes", "supported_modes"),
    bindingModes: pickStringArray(entry, "bindingModes", "binding_modes"),
    referenceImageMax: pickNumber(entry, "referenceImageMax", "reference_image_max"),
    ...readReferenceMediaLimits(entry),
    referenceVideoMax: pickNumber(entry, "referenceVideoMax", "reference_video_max"),
    referenceAudioMax: pickNumber(entry, "referenceAudioMax", "reference_audio_max"),
    referenceFileMax: pickNumber(entry, "referenceFileMax", "reference_file_max"),
    referenceLinkMax: pickNumber(entry, "referenceLinkMax", "reference_link_max"),
    referenceFileTypes: pickStringArray(entry, "referenceFileTypes", "reference_file_types"),
    referenceAudioMinSeconds: pickNumber(
      entry,
      "referenceAudioMinSeconds",
      "reference_audio_min_seconds",
    ),
    referenceAudioMaxSeconds: pickNumber(
      entry,
      "referenceAudioMaxSeconds",
      "reference_audio_max_seconds",
    ),
    referenceAudioTotalMinSeconds: pickNumber(
      entry,
      "referenceAudioTotalMinSeconds",
      "reference_audio_total_min_seconds",
    ),
    referenceAudioTotalMaxSeconds: pickNumber(
      entry,
      "referenceAudioTotalMaxSeconds",
      "reference_audio_total_max_seconds",
    ),
    referenceVideoMinSeconds: pickNumber(
      entry,
      "referenceVideoMinSeconds",
      "reference_video_min_seconds",
    ),
    referenceVideoMaxSeconds: pickNumber(
      entry,
      "referenceVideoMaxSeconds",
      "reference_video_max_seconds",
    ),
    referenceVideoTotalMinSeconds: pickNumber(
      entry,
      "referenceVideoTotalMinSeconds",
      "reference_video_total_min_seconds",
    ),
    referenceVideoTotalMaxSeconds: pickNumber(
      entry,
      "referenceVideoTotalMaxSeconds",
      "reference_video_total_max_seconds",
    ),
    request: pickMediaRequestSchema(entry.request),
  };
}

function videoModelEntryFromString(raw: string): FreezoneVideoModelInfo {
  const providerId = inferVideoProvider(raw);
  return {
    id: raw,
    providerId,
    apiModel: raw,
    label: raw,
  };
}

function coerceVideoModelList(payload: unknown): FreezoneVideoModelInfo[] {
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.models)) candidate = wrapper.models;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else {
      // provider→models[] map: { seedance: [...], huimeng: [...] }
      const flattened: FreezoneVideoModelInfo[] = [];
      for (const [providerRaw, value] of Object.entries(wrapper)) {
        const providerId = normalizeVideoProviderId(providerRaw);
        if (!providerId || !Array.isArray(value)) continue;
        for (const item of value) {
          if (typeof item === "string") {
            flattened.push({
              id: item,
              providerId,
              apiModel: item,
              label: item,
            });
          } else if (item && typeof item === "object") {
            const entry = videoModelEntryFromObject(item as Record<string, unknown>);
            if (entry) flattened.push({ ...entry, providerId });
          }
        }
      }
      if (flattened.length > 0) return flattened;
    }
  }

  if (!Array.isArray(candidate)) return [];
  const result: FreezoneVideoModelInfo[] = [];
  for (const item of candidate) {
    if (typeof item === "string") {
      result.push(videoModelEntryFromString(item));
    } else if (item && typeof item === "object") {
      const entry = videoModelEntryFromObject(item as Record<string, unknown>);
      if (entry) result.push(entry);
    }
  }
  return result;
}

export async function fetchFreezoneVideoModels(
  project: string,
): Promise<FreezoneVideoModelInfo[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/models`,
  );
  return coerceVideoModelList(payload);
}

// /freezone/video/camera-templates --------------------------------------- //

export interface FreezoneVideoCameraTemplate {
  /** Stable id used by the picker + sent to backend as `camera_template_id`. */
  id: string;
  /** Display label (e.g. "镜头下降"). */
  label: string;
  /** Prompt fragment prepended to the user's prompt when this template is active. */
  promptFragment: string;
  /** Optional preview video URL. Falls back to `/video/camera-presets/<id>.mp4`. */
  videoUrl: string | null;
}

function coerceCameraTemplateList(payload: unknown): FreezoneVideoCameraTemplate[] {
  // openapi.json schema is empty `{}` — backend shape isn't documented.
  // Accept several common envelopes defensively.
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.templates)) candidate = wrapper.templates;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else if (Array.isArray(wrapper.camera_templates)) candidate = wrapper.camera_templates;
  }
  if (!Array.isArray(candidate)) return [];
  const result: FreezoneVideoCameraTemplate[] = [];
  for (const item of candidate) {
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    const id = pickString(entry, "id", "template_id", "templateId", "name", "key");
    if (!id) continue;
    const label =
      pickString(entry, "label", "display_name", "displayName", "title", "name") ?? id;
    const promptFragment =
      pickString(
        entry,
        "promptFragment",
        "prompt_fragment",
        "prompt",
        "fragment",
        "description",
      ) ?? label;
    const videoUrl = pickString(
      entry,
      "videoUrl",
      "video_url",
      "previewUrl",
      "preview_url",
      "thumbnail",
      "thumbnail_url",
    );
    result.push({ id, label, promptFragment, videoUrl });
  }
  return result;
}

export async function fetchFreezoneVideoCameraTemplates(
  project: string,
): Promise<FreezoneVideoCameraTemplate[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/camera-templates`,
  );
  return coerceCameraTemplateList(payload);
}

// /freezone/edit ---------------------------------------------------------- //

export interface FreezoneEditPayload extends FreezoneNodeContext {
  prompt: string;
  baseUrl: string;
  extraReferenceUrls?: string[];
  aspectRatio?: string;
  imageSize?: string;
  provider?: FreezoneProvider | null;
  model?: string | null;
  /** 注册表模型 id（还原用；与 provider 拆分后的 model 串不同）。 */
  modelId?: string | null;
  /** 生成模式（还原用）：text_to_image / image_to_image / all_reference / image_reference。 */
  genMode?: string | null;
  quality?: string | null;
  /**
   * [aipdd fork delta] 工具页批量任务专用：产物完成后由后端 runner 自动登记
   * 进资产库的「待分类资产」。画布等其它入口不传（默认 false）。
   */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneEdit(
  project: string,
  payload: FreezoneEditPayload,
): Promise<FreezoneJobRef> {
  // 基准图与额外引用图同样必须是后端可解析的静态 URL，base64 先上传。
  const baseUrl = await ensureBackendImageUrl(project, payload.baseUrl);
  const extraReferenceUrls = await ensureBackendImageUrls(
    project,
    payload.extraReferenceUrls,
  );
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/edit`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        base_url: baseUrl,
        extra_reference_urls: extraReferenceUrls,
        aspect_ratio: payload.aspectRatio ?? "2:3",
        image_size: payload.imageSize ?? "2K",
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        ...(payload.modelId ? { model_id: payload.modelId } : {}),
        ...(payload.genMode ? { gen_mode: payload.genMode } : {}),
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/sketch-from-context ------------------------------------------- //

export type FreezoneSketchContextSource =
  | "beat"
  | "selected_background"
  | "director_combined"
  | "background_candidate";

export interface FreezoneSketchFromContextPayload extends FreezoneNodeContext {
  episode: number;
  beat: number;
  sourceKind?: FreezoneSketchContextSource;
  sourceUrl?: string | null;
  provider?: FreezoneProvider | null;
  model?: string | null;
  quality?: string | null;
}

export async function submitFreezoneSketchFromContext(
  project: string,
  payload: FreezoneSketchFromContextPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/sketch-from-context`,
    {
      method: "POST",
      json: {
        episode: payload.episode,
        beat: payload.beat,
        source_kind: payload.sourceKind ?? "beat",
        source_url: payload.sourceUrl ?? null,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/frame-from-context -------------------------------------------- //

export interface FreezoneFrameFromContextPayload extends FreezoneNodeContext {
  episode: number;
  beat: number;
  sketchUrl: string;
  backgroundUrl?: string | null;
  provider?: FreezoneProvider | null;
  model?: string | null;
  quality?: string | null;
}

export async function submitFreezoneFrameFromContext(
  project: string,
  payload: FreezoneFrameFromContextPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/frame-from-context`,
    {
      method: "POST",
      json: {
        episode: payload.episode,
        beat: payload.beat,
        sketch_url: payload.sketchUrl,
        background_url: payload.backgroundUrl ?? null,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/image-to-3gs --------------------------------------------------- //

/** SHARP / 3GS 来源类型；master/reverse 单面 SOG，pano 用 360 全景生成。 */
export type FreezoneImageTo3GSKind = "master" | "reverse" | "pano";

export interface FreezoneImageTo3GSPayload extends FreezoneNodeContext {
  /** 源图静态地址，通常来自 Freezone 图片节点。 */
  sourceUrl: string;
  /** 默认 master；当前 3D 世界节点仅使用 master。 */
  sourceKind?: FreezoneImageTo3GSKind;
}

export async function submitFreezoneImageTo3GS(
  project: string,
  payload: FreezoneImageTo3GSPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/image-to-3gs`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        source_kind: payload.sourceKind ?? "master",
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/extract-frames ------------------------------------------------ //

export interface FreezoneExtractPayload {
  videoUrl: string;
  maxFrames?: number;
  sceneThreshold?: number;
}

export async function submitFreezoneExtract(
  project: string,
  payload: FreezoneExtractPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/extract-frames`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        max_frames: payload.maxFrames ?? 20,
        scene_threshold: payload.sceneThreshold ?? 0.3,
      },
    },
  );
}

// /freezone/analyze-shots ------------------------------------------------- //

export interface FreezoneAnalyzePayload {
  frameUrls: string[];
  /** Analysis is a backend capability; Freezone UI sends OpenRouter by default. */
  provider?: "openrouter" | null;
  /** Optional backend model override for internal/debug use. */
  model?: string | null;
}

export async function submitFreezoneAnalyze(
  project: string,
  payload: FreezoneAnalyzePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-shots`,
    {
      method: "POST",
      json: {
        frame_urls: payload.frameUrls,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
      },
    },
  );
}

// /freezone/redraw -------------------------------------------------------- //

export type FreezoneRedrawAspectRatio =
  | "original"
  | "1:1"
  | "4:3"
  | "3:4"
  | "16:9"
  | "9:16";

/**
 * 重绘接口自身接受的比例取值（与后端 `FreezoneRedrawRequest.aspect_ratio` 的
 * Literal 保持一致）。
 *
 * 这不是某个模型的能力，而是接口契约：即便后台给模型配了别的比例档位，重绘也
 * 只能提交这几个值，所以前端取「模型配置 ∩ 接口契约」。
 */
export const FREEZONE_REDRAW_ASPECT_RATIOS: readonly FreezoneRedrawAspectRatio[] = [
  "original",
  "1:1",
  "4:3",
  "3:4",
  "16:9",
  "9:16",
];

export interface FreezoneRedrawPayload {
  sourceUrl: string;
  /** Optional mask static URL. Transparent pixels = editable region (局部重绘). */
  maskUrl?: string | null;
  prompt?: string;
  aspectRatio?: FreezoneRedrawAspectRatio;
  numImages?: number;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneRedraw(
  project: string,
  payload: FreezoneRedrawPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/redraw`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        mask_url: payload.maskUrl ?? null,
        prompt: payload.prompt ?? "",
        aspect_ratio: payload.aspectRatio ?? "original",
        num_images: payload.numImages ?? 1,
        image_size: payload.imageSize ?? "2K",
        ...(payload.model ? { model: payload.model } : {}),
      },
    },
  );
}

// /freezone/upscale ------------------------------------------------------- //

export type FreezoneUpscaleScaleFactor = 2 | 4 | 6;

export interface FreezoneUpscalePayload {
  sourceUrl: string;
  scaleFactor?: FreezoneUpscaleScaleFactor;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneUpscale(
  project: string,
  payload: FreezoneUpscalePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/upscale`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        scale_factor: payload.scaleFactor ?? 2,
        image_size: payload.imageSize ?? "2K",
        ...(payload.model ? { model: payload.model } : {}),
      },
    },
  );
}

// /freezone/outpaint ------------------------------------------------------ //

export type FreezoneOutpaintAspectRatio =
  | "original"
  | "1:1"
  | "4:3"
  | "3:4"
  | "16:9"
  | "9:16";

export interface FreezoneOutpaintPayload {
  sourceUrl: string;
  targetAspectRatio?: FreezoneOutpaintAspectRatio;
  numImages?: number;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneOutpaint(
  project: string,
  payload: FreezoneOutpaintPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/outpaint`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        target_aspect_ratio: payload.targetAspectRatio ?? "original",
        num_images: payload.numImages ?? 1,
        image_size: payload.imageSize ?? "2K",
        ...(payload.model ? { model: payload.model } : {}),
      },
    },
  );
}

// /freezone/multi-view ---------------------------------------------------- //

export type FreezoneMultiViewPreset =
  | "custom"
  | "fisheye"
  | "oblique"
  | "front"
  | "front_up"
  | "full_body"
  | "back";

export type FreezoneMultiViewShotSize =
  | "extreme_close_up"
  | "close_up"
  | "medium_close"
  | "medium"
  | "full_body"
  | "wide"
  | "extreme_wide";

export interface FreezoneMultiViewPayload {
  sourceUrl: string;
  preset?: FreezoneMultiViewPreset;
  yawDegrees?: number;
  pitchDegrees?: number;
  shotSize?: FreezoneMultiViewShotSize;
  prompt?: string;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneMultiView(
  project: string,
  payload: FreezoneMultiViewPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/multi-view`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        preset: payload.preset ?? "custom",
        yaw_degrees: payload.yawDegrees ?? 0,
        pitch_degrees: payload.pitchDegrees ?? 0,
        shot_size: payload.shotSize ?? "medium",
        prompt: payload.prompt ?? "",
        image_size: payload.imageSize ?? "2K",
        ...(payload.model ? { model: payload.model } : {}),
      },
    },
  );
}

// /freezone/relight ------------------------------------------------------- //

export type FreezoneRelightScope = "global" | "local";

export type FreezoneRelightKeyLightDirection =
  | "left"
  | "top"
  | "right"
  | "front"
  | "bottom"
  | "back";

export interface FreezoneRelightPayload {
  sourceUrl: string;
  lightingReferenceUrl?: string | null;
  scope?: FreezoneRelightScope;
  smartMode?: boolean;
  brightness?: number;
  colorHex?: string;
  /** 色温（开尔文）。后端 `color_temperature_kelvin: int | null`，范围 1500-10000。 */
  colorTemperatureKelvin?: number | null;
  keyLightDirection?: FreezoneRelightKeyLightDirection;
  rimLight?: boolean;
  prompt?: string;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneRelight(
  project: string,
  payload: FreezoneRelightPayload,
): Promise<FreezoneJobRef> {
  // 源图与光照参考图都要走静态 URL，base64 先上传。
  const sourceUrl = await ensureBackendImageUrl(project, payload.sourceUrl);
  const lightingReferenceUrl = payload.lightingReferenceUrl
    ? await ensureBackendImageUrl(project, payload.lightingReferenceUrl)
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/relight`,
    {
      method: "POST",
      json: {
        source_url: sourceUrl,
        lighting_reference_url: lightingReferenceUrl,
        scope: payload.scope ?? "global",
        smart_mode: payload.smartMode ?? true,
        brightness: payload.brightness ?? 50,
        color_hex: payload.colorHex ?? "#ffffff",
        color_temperature_kelvin: payload.colorTemperatureKelvin ?? null,
        key_light_direction: payload.keyLightDirection ?? "front",
        rim_light: payload.rimLight ?? false,
        prompt: payload.prompt ?? "",
        image_size: payload.imageSize ?? "2K",
        ...(payload.model ? { model: payload.model } : {}),
      },
    },
  );
}

// /freezone/scene-360 ----------------------------------------------------- //

export interface FreezoneScene360Payload {
  referenceUrl: string;
  imageSize?: string;
  model?: string;
  /** 媒体模型目录身份，后端据此按目录定价规则计费（与前端报价同口径）。 */
  catalogId?: string;
  quality?: string;
  mode?: "candidate" | "commit";
}

/**
 * **单图 360 (simple pipeline)** — 老路径,只把一张图当 reference 走通用
 * 图编辑生成 panorama。不做 master/reverse overlap 分析、空间合约、缝合,
 * 适合 freezone 自由画布上 \"拿任一张图试试 360\" 的快速生成。
 * Asset-scoped scene 360 generation uses the scenes asset API (复杂工作流),不是这条。
 */
export async function submitFreezoneScene360(
  project: string,
  payload: FreezoneScene360Payload,
): Promise<FreezoneJobRef> {
  // 参考图必须是后端可解析的静态 URL，base64 先上传。
  const referenceUrl = await ensureBackendImageUrl(project, payload.referenceUrl);
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/scene-360`,
    {
      method: "POST",
      json: {
        reference_url: referenceUrl,
        image_size: payload.imageSize ?? "2K",
        mode: payload.mode ?? "candidate",
        // 360 的最终产物合同固定为 2:1；上游比例适配属于后端实现细节。
        // 面板上没有模型选择器，报价和执行必须使用同一个固定模型。
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.catalogId ? { catalog_id: payload.catalogId } : {}),
        ...(payload.quality ? { quality: payload.quality } : {}),
      },
    },
  );
}

// /freezone/template-edit ------------------------------------------------- //

export type FreezoneTemplateEditMode =
  | "multi_camera_nine_grid"
  | "story_pitch_four_grid"
  | "character_face_three_view"
  | "product_three_view"
  | "storyboard_25_grid"
  | "cinematic_light_correction"
  | "character_three_view_generation"
  | "image_projection_after_3s"
  | "image_projection_before_5s";

export interface FreezoneTemplateEditPayload {
  sourceUrl: string;
  mode: FreezoneTemplateEditMode;
  prompt?: string;
  imageSize?: string;
  model?: string;
  /** 媒体模型目录身份，后端据此按目录定价规则计费（与前端报价同口径）。 */
  catalogId?: string;
  quality?: string;
}

export async function submitFreezoneTemplateEdit(
  project: string,
  payload: FreezoneTemplateEditPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/template-edit`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        mode: payload.mode,
        prompt: payload.prompt ?? "",
        image_size: payload.imageSize ?? "2K",
        // 同 scene-360：报价按目录首模型算，执行也必须用同一个模型/画质。
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.catalogId ? { catalog_id: payload.catalogId } : {}),
        ...(payload.quality ? { quality: payload.quality } : {}),
      },
    },
  );
}

// /freezone/jobs/{type}/{id}/result --------------------------------------- //

export interface FreezoneJobResult {
  url: string;
  size: number;
}

export async function fetchFreezoneJobResult(
  project: string,
  taskType:
    | "freezone_gen"
    | "freezone_edit"
    | "freezone_upscale"
    | "freezone_extract"
    | "freezone_analyze"
    | "freezone_multi_view"
    | "freezone_relight"
    | "freezone_scene_360"
    | "freezone_template_edit"
    | "freezone_outpaint"
    | "freezone_redraw"
    | "freezone_video_gen"
    | "freezone_video_omni_gen"
    | "freezone_video_i2v"
    | "freezone_video_erase"
    | "freezone_video_compose"
    | "freezone_video_upscale"
    | "freezone_audio_separate"
    | "freezone_audio_speech"
    // [aipdd fork delta] 工具页「人声复刻 → 替换人声」。
    | "freezone_voice_replace"
    | "freezone_audio_eleven_music"
    // [aipdd fork delta req46] 工具页「音乐创作」（MiniMax Music 3.0 直连）。
    | "freezone_music_generate"
    | "freezone_image_reverse_prompt"
    | "freezone_text_generate"
    | "freezone_text_translate"
    | "freezone_story_script"
    | "freezone_video_story"
    | "freezone_video_clone"
    | "freezone_video_mix"
    | "stage_asset",
  jobId: string,
): Promise<FreezoneJobResult> {
  return await apiCall<FreezoneJobResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/${encodeURIComponent(taskType)}/${encodeURIComponent(jobId)}/result`,
  );
}

/**
 * `freezone_image_reverse_prompt` results aren't files — the dedicated job
 * result endpoint returns `{ prompt: "..." }` directly. SSE `task.result` only
 * carries `{ output_format: "json" }`, so the text must be fetched here.
 */
export interface FreezoneReversePromptResult {
  prompt: string;
}

export async function fetchFreezoneReversePromptResult(
  project: string,
  jobId: string,
): Promise<FreezoneReversePromptResult> {
  return await apiCall<FreezoneReversePromptResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_image_reverse_prompt/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/audio/references + /freezone/audio/speech -------------------- //

/**
 * 后端定义的声线 scope。
 * - project_narrator: 项目解说人
 * - character_default: 角色默认声线
 * - character_age_group: 角色年龄段声线（需要 slot）
 * - identity: 身份自己的声线（需要 identity_id）
 * - identity_resolved: 按 身份→年龄段→角色默认 兜底解析后的实际声线
 */
export type FreezoneAudioVoiceRefScope =
  | "project_narrator"
  | "user_custom"
  | "character_default"
  | "character_age_group"
  | "identity"
  | "identity_resolved";

export interface FreezoneAudioVoiceRef {
  scope: FreezoneAudioVoiceRefScope;
  /** scope=character_* / identity* 时必填，匹配项目角色名。 */
  characterName?: string;
  /** scope=identity / identity_resolved 时必填。 */
  identityId?: string;
  /** scope=character_age_group 时必填：child/youth/middle/elder。 */
  slot?: string;
  /** scope=user_custom 时必填：来自 GET/POST /freezone/audio/voices。 */
  voiceId?: string;
}

/**
 * `GET /freezone/audio/references` 返回的单条记录。openapi 没给 schema（{}），
 * 这里按后端 description 推测，UI 防御性读取——遇到陌生字段就忽略。
 */
export interface FreezoneAudioReferenceItem {
  scope: FreezoneAudioVoiceRefScope;
  /** scope=character_* 或 identity* 时有值。 */
  character_name?: string | null;
  /** scope=identity / identity_resolved 时有值。 */
  identity_id?: string | null;
  /** scope=character_age_group 时有值：child/youth/middle/elder。 */
  slot?: string | null;
  /** scope=user_custom 时有值：账号级音色 ID（来自 POST /freezone/audio/voices）。 */
  voice_id?: string | null;
  /** 展示名（后端给的，若没给前端兜底拼）。 */
  label?: string | null;
  /** 语言/腔调说明。 */
  language?: string | null;
  /** 性别（如果后端给）。 */
  gender?: string | null;
  /** 预览音频静态 URL（如果后端给）。 */
  preview_url?: string | null;
  [key: string]: unknown;
}

export interface FreezoneAudioReferencesResult {
  /** 项目内可用的声线引用列表。 */
  available: FreezoneAudioReferenceItem[];
  [key: string]: unknown;
}

export async function fetchFreezoneAudioReferences(
  project: string,
): Promise<FreezoneAudioReferencesResult> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/audio/references`,
  );
  // 容错：后端可能直接返回数组、或 { available: [...] }，或 { items: [...] }。
  if (Array.isArray(payload)) {
    return { available: payload as FreezoneAudioReferenceItem[] };
  }
  if (payload && typeof payload === "object") {
    const wrapper = payload as Record<string, unknown>;
    if (Array.isArray(wrapper.available)) {
      return wrapper as unknown as FreezoneAudioReferencesResult;
    }
    if (Array.isArray(wrapper.items)) {
      return { available: wrapper.items as FreezoneAudioReferenceItem[] };
    }
    if (Array.isArray(wrapper.data)) {
      return { available: wrapper.data as FreezoneAudioReferenceItem[] };
    }
  }
  return { available: [] };
}

export interface FreezoneAudioSpeechPayload {
  /** 要合成的台词 / 旁白文本。 */
  text: string;
  /** 情绪提示词，留空则用项目解说风格。例："紧张、压低声音、带一点恐惧感"。 */
  emotionPrompt?: string;
  /** 声线引用；不传则用项目默认解说人。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** [aipdd fork delta] 选定的音频模型（后台 audio 目录的 apiModel）；留空走默认引擎。 */
  model?: string;
  /** 目标 beat;给定后生成的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
  /**
   * [aipdd fork delta] 工具页批量任务专用：产物完成后由后端 runner 自动登记
   * 进资产库的「待分类资产」。画布等其它入口不传（默认 false）。
   */
  archiveToLibrary?: boolean;
  /** [aipdd fork delta] 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

export async function submitFreezoneAudioSpeech(
  project: string,
  payload: FreezoneAudioSpeechPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/speech`,
    {
      method: "POST",
      json: {
        text: payload.text,
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        // [aipdd fork delta] undefined 会被 JSON 序列化丢弃，后端 pydantic
        // 默认值兼得住；不能发空串覆盖默认引擎语义以外的路径。
        ...(payload.model ? { model: payload.model } : {}),
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
        // [aipdd fork delta] 工具页批量任务标记：产物完成后后端自动登记进
        // 资产库「待分类资产」。
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/audio/voice-replace ----------------------------------------- //

/**
 * [aipdd fork delta] 工具页「人声复刻 → 替换人声」请求。
 *
 * sourceUrl / referenceAudioUrl 都必须是本会话上传得到的项目内 /static/... 地址。
 * 后端把参考音频注册成账号级 user_custom 声线后走 IndexTTS2 零样本合成目标音色新声。
 */
export interface FreezoneVoiceReplacePayload {
  /** 原始音频/视频静态地址（从中提取待替换台词）。 */
  sourceUrl: string;
  /** 参考音频（目标音色）静态地址。 */
  referenceAudioUrl: string;
  /** 可选：注册音色时的展示名；为空则用上传文件名。 */
  referenceName?: string;
  /** 可选音频模型目录 id；留空走默认 IndexTTS2 零样本链路。 */
  model?: string;
  /**
   * 台词识别 ASR 候选 id（/freezone/media-copywrite/models 之一）；留空则执行时
   * [aipdd fork delta] req32 自动挑选能接收本地音频的已就绪候选（豆包语音 →
   * 网关；FunASR 需公网直链，永不参与自动选择）。
   */
  asrModel?: string;
  /** 目标 beat；给定后结果带 beat_audio slot_target。 */
  targetEpisode?: number;
  targetBeat?: number;
  /** 工具页批量任务标记：产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

/**
 * [aipdd fork delta] 提交替换人声任务。返回异步任务句柄，结果用
 * fetchFreezoneJobResult(project, 'freezone_voice_replace', jobId) 取。
 */
export async function submitFreezoneVoiceReplace(
  project: string,
  payload: FreezoneVoiceReplacePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/voice-replace`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        reference_audio_url: payload.referenceAudioUrl,
        ...(payload.referenceName ? { reference_name: payload.referenceName } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.asrModel ? { asr_model: payload.asrModel } : {}),
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/audio/video-dubbing ----------------------------------------- //

/**
 * [aipdd fork delta] 工具页「视频配音」请求。
 *
 * sourceUrl / referenceAudioUrl 都必须是本会话上传得到的项目内 /static/... 地址。
 * 后端抽取原台词（不翻译）、把参考音频注册成账号级 user_custom 声线零样本原语言
 * 重声，最后用新声替换原视频整条音轨输出配好音的 mp4。language 仅发音提示。
 */
export interface FreezoneVideoDubbingPayload {
  /** 原始视频静态地址（提取台词 + 保留画面）。 */
  sourceUrl: string;
  /** 参考音频（目标音色）静态地址。 */
  referenceAudioUrl: string;
  /** 可选：注册音色时的展示名；为空则用上传文件名。 */
  referenceName?: string;
  /** [aipdd fork delta] 语种/发音提示，透传合成上游；台词不翻译，为空则不带。 */
  language?: string;
  /** 可选音频模型目录 id；留空走默认 IndexTTS2 零样本链路。 */
  model?: string;
  /**
   * 台词识别 ASR 候选 id（/freezone/media-copywrite/models 之一）；留空则执行时
   * 自动挑选能接收本地音频的已就绪候选（豆包语音 → 网关；FunASR 永不参与）。
   */
  asrModel?: string;
  /** 目标 beat；给定后结果带 beat_audio slot_target。 */
  targetEpisode?: number;
  targetBeat?: number;
  /** 工具页批量任务标记：产物完成后自动登记进资产库「待分类资产」。 */
  archiveToLibrary?: boolean;
  /** 工具页任务归属标识（取 def.kind）；写入 metadata.source_tool。 */
  sourceTool?: string;
}

/**
 * [aipdd fork delta] 提交视频配音任务。返回异步任务句柄，结果用
 * fetchFreezoneJobResult(project, 'freezone_video_dubbing', jobId) 取。
 */
export async function submitFreezoneVideoDubbing(
  project: string,
  payload: FreezoneVideoDubbingPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/video-dubbing`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        reference_audio_url: payload.referenceAudioUrl,
        ...(payload.referenceName ? { reference_name: payload.referenceName } : {}),
        ...(payload.language ? { language: payload.language } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.asrModel ? { asr_model: payload.asrModel } : {}),
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
        ...(payload.archiveToLibrary ? { archive_to_library: true } : {}),
        ...(payload.sourceTool ? { source_tool: payload.sourceTool } : {}),
      },
    },
  );
}

// /freezone/audio/eleven-music ------------------------------------------- //

/**
 * 文本生成音乐请求。除 input 外全部可选，不传走后端默认。
 * response_format / output_format 不需要前端传（走后端默认 mp3 / mp3_44100_128），
 * 故不在此暴露；model 只在用户显式选了模型时才带上，留空由后端解析默认音频位。
 */
export interface FreezoneAudioMusicPayload {
  /** 音乐描述 prompt（风格、乐器、氛围等）；映射到后端 `input`。 */
  prompt: string;
  /** [aipdd fork delta] 选定的音乐模型；留空 = 后端解析「设置 → 模型与渠道」的默认音频模型。 */
  model?: string;
  /** 生成长度（毫秒），范围 3000–600000，留空走后端默认 30000。 */
  musicLengthMs?: number;
  /** 是否强制纯音乐，留空走后端默认 true。 */
  forceInstrumental?: boolean;
  /** 是否严格遵守音乐段落时长策略，留空走后端默认 true。 */
  respectSectionsDurations?: boolean;
  /** 目标 beat;给定后生成的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
}

/**
 * 文本生成音乐。返回异步任务句柄，结果用
 * fetchFreezoneJobResult('freezone_audio_eleven_music') 取。
 */
export async function submitFreezoneAudioMusic(
  project: string,
  payload: FreezoneAudioMusicPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/eleven-music`,
    {
      method: "POST",
      json: {
        input: payload.prompt,
        // [aipdd fork delta] 后端全程透传 body.model；不选时省略 key，让后端去解析
        // 「设置 → 模型与渠道」里配好的默认音频模型，没配就在提交前拦住。
        ...(payload.model ? { model: payload.model } : {}),
        music_length_ms: payload.musicLengthMs,
        force_instrumental: payload.forceInstrumental,
        respect_sections_durations: payload.respectSectionsDurations,
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
      },
    },
  );
}

// /freezone/audio/voices ------------------------------------------------- //
//
// 「我的音色」(scope=user_custom) 的列表统一通过 /freezone/audio/references
// 拿（混在 available[] 里，按 scope 过滤）。这里只保留 POST：上传一段音频克
// 隆出账号级音色。

/**
 * POST /freezone/audio/voices 的响应。openapi 是空对象，按推测字段定义。
 * 调用方一般只需要 `voice_id`，其它仅用于上传后即时展示。
 */
export interface FreezoneAudioVoiceItem {
  voice_id: string;
  name?: string | null;
  language?: string | null;
  gender?: string | null;
  preview_url?: string | null;
  [key: string]: unknown;
}

export interface CreateFreezoneAudioVoiceOptions {
  /** 同 uploadFreezoneImage：上传大文件时用 false 关闭 ky 默认 30s 超时。 */
  timeoutMs?: number | false;
}

export async function createFreezoneAudioVoice(
  project: string,
  file: File | Blob,
  name?: string,
  options?: CreateFreezoneAudioVoiceOptions,
): Promise<FreezoneAudioVoiceItem> {
  const fd = new FormData();
  fd.append(
    "file",
    file,
    file instanceof File ? file.name : "voice.wav",
  );
  if (name && name.trim()) fd.append("name", name.trim());
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/audio/voices`,
    {
      method: "POST",
      body: fd,
      timeout: options?.timeoutMs ?? false,
    },
  ).json<{ ok: boolean; data?: FreezoneAudioVoiceItem; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "voice upload failed");
  }
  return resp.data;
}

// /freezone/text/translate ------------------------------------------------ //

export interface FreezoneTextGeneratePayload extends FreezoneNodeContext {
  prompt: string;
}

export async function submitFreezoneTextGenerate(
  project: string,
  payload: FreezoneTextGeneratePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/generate`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        ...nodeContextBody(payload),
      },
    },
  );
}

export interface FreezoneTextGenerateResult {
  generated_text: string;
  model: string;
}

export async function fetchFreezoneTextGenerateResult(
  project: string,
  jobId: string,
): Promise<FreezoneTextGenerateResult> {
  return await apiCall<FreezoneTextGenerateResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_text_generate/${encodeURIComponent(jobId)}/result`,
  );
}

export type FreezoneTextTranslateNodeType =
  | "generic"
  | "image"
  | "video"
  | "audio"
  | "text";

export interface FreezoneTextTranslatePayload extends FreezoneNodeContext {
  text: string;
  /** Hints the translator at the calling node's tone. Defaults to "generic". */
  nodeType?: FreezoneTextTranslateNodeType;
}

export async function submitFreezoneTextTranslate(
  project: string,
  payload: FreezoneTextTranslatePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/translate`,
    {
      method: "POST",
      json: {
        text: payload.text,
        node_type: payload.nodeType ?? "generic",
        ...nodeContextBody(payload),
      },
    },
  );
}

export interface FreezoneTextTranslateResult {
  translated_text: string;
  source_language: "zh" | "en";
  target_language: "zh" | "en";
  node_type: FreezoneTextTranslateNodeType;
}

export async function fetchFreezoneTextTranslateResult(
  project: string,
  jobId: string,
): Promise<FreezoneTextTranslateResult> {
  return await apiCall<FreezoneTextTranslateResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_text_translate/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/text/story-script -------------------------------------------- //

/** 角色参考输入项（角色生成脚本用）。 */
export interface FreezoneStoryScriptCharacterRef {
  /** 角色名或角色标签。 */
  name?: string;
  /** 可选：已有角色描述。 */
  description?: string;
  /** 角色参考图静态 URL（/static/...）。 */
  imageUrl?: string;
  /** 可选：角色定位或叙事功能（如「女主」）。 */
  role?: string;
}

export interface FreezoneStoryScriptPayload extends FreezoneNodeContext {
  /**
   * 文本输入：剧本 / 情节文本。后端要求 source_text / source_url / video_url /
   * character_refs 至少给一个。
   */
  sourceText?: string;
  /** 文本资源静态 URL，后端拉取后走生成流程。 */
  sourceUrl?: string;
  /** 视频输入：已上传视频的静态 URL，后端会先解析视频再生成同样的故事脚本表。 */
  videoUrl?: string;
  /** 视频总时长（秒），让脚本表的时间戳更准。 */
  durationSec?: number;
  /** 角色参考输入：无文本 / 视频时，基于角色图和描述生成故事脚本表。 */
  characterRefs?: FreezoneStoryScriptCharacterRef[];
  /** 用户额外补充的提示词（steering），例如风格、镜头偏好等。 */
  prompt?: string;
  /** [aipdd fork delta] 选定的文本模型（/model-gateway/text-models 的 id）；留空走后端默认。 */
  model?: string;
}

export async function submitFreezoneStoryScript(
  project: string,
  payload: FreezoneStoryScriptPayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = { ...nodeContextBody(payload) };
  if (payload.sourceText != null) body.source_text = payload.sourceText;
  if (payload.sourceUrl != null) body.source_url = payload.sourceUrl;
  if (payload.videoUrl != null) body.video_url = payload.videoUrl;
  if (payload.durationSec != null) body.duration_sec = payload.durationSec;
  if (payload.prompt != null) body.prompt = payload.prompt;
  // [aipdd fork delta] 脚本节点按用户自选模型生成；未选时省略 key 走后端默认。
  if (payload.model) body.model = payload.model;
  if (payload.characterRefs && payload.characterRefs.length > 0) {
    body.character_refs = payload.characterRefs.map((ref) => {
      const item: Record<string, unknown> = {};
      if (ref.name != null) item.name = ref.name;
      if (ref.description != null) item.description = ref.description;
      if (ref.imageUrl != null) item.image_url = ref.imageUrl;
      if (ref.role != null) item.role = ref.role;
      return item;
    });
  }
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/story-script`,
    { method: "POST", json: body },
  );
}

/**
 * 故事脚本表的一行。字段名必须和后端 `FreezoneStoryScriptRow`
 * （`src/novelvideo/api/schemas.py`）逐字对齐 —— 早期前端用的是
 * `character` / `action` 这类简写，后端从来没发过这些 key，表格里就只显示
 * 「-」（issue #207）。
 */
export interface FreezoneStoryScriptRow {
  shot_no?: string | number | null;
  duration?: string | number | null;
  visual_description?: string | null;
  character_1?: string | null;
  character_description_1?: string | null;
  /** 角色图1 URL，由后端按角色名匹配 character_refs 回填。 */
  character_image_1?: string | null;
  character_2?: string | null;
  character_description_2?: string | null;
  /** 角色图2 URL，由后端按角色名匹配 character_refs 回填。 */
  character_image_2?: string | null;
  /** 参考图 URL，视频参考模式下由后端按 keyframe_index 回填对应关键帧。 */
  reference?: string | null;
  /** 视频参考模式下这一镜对应的输入关键帧序号（1-based，0 表示无）。 */
  keyframe_index?: number | null;
  shot?: string | null;
  character_action?: string | null;
  emotion?: string | null;
  scene_tags?: string | null;
  lighting_mood?: string | null;
  sound?: string | null;
  dialogue?: string | null;
  shot_prompt?: string | null;
  video_motion_prompt?: string | null;
  [key: string]: unknown;
}

export interface FreezoneStoryScriptResult {
  title?: string | null;
  rows: FreezoneStoryScriptRow[];
  /** 视频参考模式下抽出的关键帧静态 URL，按顺序对应 keyframe_index。 */
  frame_urls?: string[] | null;
}

export async function fetchFreezoneStoryScriptResult(
  project: string,
  jobId: string,
): Promise<FreezoneStoryScriptResult> {
  return await apiCall<FreezoneStoryScriptResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_story_script/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/upload (multipart) -------------------------------------------- //

export interface FreezoneUploadResult {
  url: string;
  filename: string;
  size: number;
}

export interface FreezoneUploadOptions {
  /**
   * Override the upload timeout. Defaults to `false` (no timeout) — a clock on
   * an upload races the user's uplink rather than the server, and aborting
   * mid-body shows up as HTTP 499 at the edge with the file never delivered
   * (see uploadApi in lib/api.ts). Pass a number only to bound a specific call.
   */
  timeoutMs?: number | false;
}

export async function uploadFreezoneImage(
  project: string,
  file: File | Blob,
  filename?: string,
  options?: FreezoneUploadOptions,
): Promise<FreezoneUploadResult> {
  const fd = new FormData();
  fd.append("file", file, filename ?? (file instanceof File ? file.name : "upload.png"));
  // ky's `body: FormData` skips the json envelope helper, so we go direct.
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/upload`,
    {
      method: "POST",
      body: fd,
      timeout: options?.timeoutMs ?? false,
    },
  ).json<{ ok: boolean; data?: FreezoneUploadResult; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "upload failed");
  }
  return resp.data;
}

export async function uploadFreezoneReferenceFile(
  project: string,
  file: File,
): Promise<FreezoneUploadResult> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/reference-file-upload`,
    {
      method: "POST",
      body: fd,
      timeout: false,
    },
  ).json<{ ok: boolean; data?: FreezoneUploadResult; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "upload failed");
  }
  return resp.data;
}

// /freezone/assets/copy ---------------------------------------------------- //

export interface FreezoneAssetCopyFailure {
  source: string;
  /** invalid_source / not_found / forbidden / unavailable / copy_failed */
  reason: string;
}

export interface FreezoneAssetCopyResult {
  /** 源 URL（原字符串）→ 目标项目里的新 URL。 */
  mapping: Record<string, string>;
  failed: FreezoneAssetCopyFailure[];
}

/**
 * 跨项目粘贴：让后端把 `sources`（源项目的同源素材 URL）拷进 `project`。
 * 字节不经过浏览器；OSS 部署下是服务端 CopyObject。单条失败只体现在 `failed` 里。
 */
export async function copyFreezoneAssets(
  project: string,
  sources: string[],
): Promise<FreezoneAssetCopyResult> {
  return apiCall<FreezoneAssetCopyResult>(
    `projects/${encodeURIComponent(project)}/freezone/assets/copy`,
    { method: "POST", json: { sources }, timeout: false },
  );
}

/** Same endpoint as image upload — backend treats the upload as a generic blob. */
export async function uploadFreezoneVideo(
  project: string,
  file: File | Blob,
  filename?: string,
): Promise<FreezoneUploadResult> {
  // Timeouts are off by default for uploads (see FreezoneUploadOptions):
  // video files routinely run into the tens of MB and the body is streamed, so
  // any clock cancels the request before the server sees the end of it.
  return await uploadFreezoneImage(project, file, filename);
}

// [aipdd fork delta] Blender MCP 白模视频联合（B）：从本机 Blender 拉取 / 手动入库，
// 后端把白模视频入库成本项目静态直链后写回指定画布节点。超时关：Blender 渲染可能几分钟。
export interface WhiteModelBlenderResult {
  /** pulled=Blender 导出成功；unreachable=连不上 Blender（前端据此弹兜底）；ingested=手动入库成功。 */
  status: "pulled" | "unreachable" | "ingested";
  url?: string;
  filename?: string;
  message?: string;
}

export async function pullWhiteModelFromBlender(
  project: string,
  opts: { canvasId: string; nodeId: string; commitSlot?: boolean },
): Promise<WhiteModelBlenderResult> {
  return apiCall<WhiteModelBlenderResult>(
    `projects/${encodeURIComponent(project)}/freezone/blender/pull-white-model-video`,
    {
      method: "POST",
      json: {
        canvas_id: opts.canvasId,
        node_id: opts.nodeId,
        commit_slot: opts.commitSlot ?? false,
      },
      timeout: false,
    },
  );
}

export async function ingestWhiteModelVideo(
  project: string,
  opts: { canvasId: string; nodeId: string; url: string; commitSlot?: boolean },
): Promise<WhiteModelBlenderResult> {
  return apiCall<WhiteModelBlenderResult>(
    `projects/${encodeURIComponent(project)}/freezone/blender/ingest-white-model-video`,
    {
      method: "POST",
      json: {
        canvas_id: opts.canvasId,
        node_id: opts.nodeId,
        url: opts.url,
        commit_slot: opts.commitSlot ?? false,
      },
      timeout: false,
    },
  );
}

function dataUrlToBlob(dataUrl: string): Blob {
  const [meta, payload = ""] = dataUrl.split(",", 2);
  const mimeMatch = /data:([^;]+)/.exec(meta);
  const mime = mimeMatch?.[1] ?? "image/png";
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function extensionForMime(mime: string): string {
  if (mime === "image/png") return "png";
  if (mime === "image/jpeg" || mime === "image/jpg") return "jpg";
  if (mime === "image/webp") return "webp";
  if (mime === "image/gif") return "gif";
  return "png";
}

/**
 * Coerce an arbitrary image reference into a backend-resolvable static path.
 *
 * - `data:` URLs are uploaded via `/freezone/upload` first; the response URL
 *   is what backends like `/freezone/image/reverse-prompt` can actually open.
 * - Anything else (already a `/static/...` path, http(s) URL, blob:, etc.) is
 *   returned as-is, minus the `?v=<ts>` cache buster that breaks backend path
 *   lookup (same strip pattern as `RedrawOverlay`).
 */
export async function ensureBackendImageUrl(
  project: string,
  rawUrl: string,
): Promise<string> {
  if (rawUrl.startsWith("data:")) {
    const blob = dataUrlToBlob(rawUrl);
    const ext = extensionForMime(blob.type);
    const uploaded = await uploadFreezoneImage(
      project,
      blob,
      `paste-${Date.now()}.${ext}`,
    );
    return uploaded.url.split("?")[0];
  }
  return rawUrl.split("?")[0];
}

/**
 * Batch variant of {@link ensureBackendImageUrl}: coerce a list of image
 * references into backend-resolvable static URLs. Empty / blank entries are
 * dropped; original order is preserved; uploads run in parallel.
 */
export async function ensureBackendImageUrls(
  project: string,
  rawUrls: readonly string[] | null | undefined,
): Promise<string[]> {
  if (!rawUrls || rawUrls.length === 0) return [];
  const cleaned = rawUrls.filter(
    (url): url is string => typeof url === "string" && url.trim().length > 0,
  );
  return await Promise.all(
    cleaned.map((url) => ensureBackendImageUrl(project, url)),
  );
}

// /freezone/extract-frames + analyze-shots -------------------------------- //

export interface FreezoneExtractFramesPayload {
  videoUrl: string;
  maxFrames?: number;
  sceneThreshold?: number;
}

export async function submitFreezoneExtractFrames(
  project: string,
  payload: FreezoneExtractFramesPayload,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/extract-frames`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        max_frames: payload.maxFrames ?? 20,
        scene_threshold: payload.sceneThreshold ?? 0.3,
      },
    },
  );
}

export interface FreezoneAnalyzeShotsPayload {
  frameUrls: string[];
}

export async function submitFreezoneAnalyzeShots(
  project: string,
  payload: FreezoneAnalyzeShotsPayload,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-shots`,
    {
      method: "POST",
      json: {
        frame_urls: payload.frameUrls,
      },
    },
  );
}

// /freezone/analyze-video-story ------------------------------------------ //

export interface FreezoneAnalyzeVideoStoryPayload {
  /** /static/... URL inside the project. */
  videoUrl: string;
  /** 3..50, defaults to 20 backend-side. */
  maxFrames?: number;
  /** 0..1 ffmpeg scene threshold; defaults to 0.3 backend-side. */
  sceneThreshold?: number;
  /** Optional total duration (seconds) — improves table timestamp accuracy. */
  durationSec?: number;
}

export async function submitFreezoneAnalyzeVideoStory(
  project: string,
  payload: FreezoneAnalyzeVideoStoryPayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    video_url: payload.videoUrl,
  };
  if (payload.maxFrames != null) body.max_frames = payload.maxFrames;
  if (payload.sceneThreshold != null) body.scene_threshold = payload.sceneThreshold;
  if (payload.durationSec != null) body.duration_sec = payload.durationSec;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-video-story`,
    { method: "POST", json: body },
  );
}

// /freezone/video/character-library -------------------------------------- //

/**
 * 素材媒介。前三类是可直接预览的媒体文件；"draft" 是 VectCut 跑出的剪映草稿——
 * 它不是单个文件而是一个草稿包，条目只承载 draft_id，下载走草稿包代理端点。
 */
export type FreezoneAssetLibraryMedia = "image" | "video" | "audio" | "draft";
/** 可作为上传媒介的类型：剪映草稿只能由 VectCut 生成，不支持用户上传。 */
export type FreezoneUploadableMedia = Exclude<FreezoneAssetLibraryMedia, "draft">;
export type FreezoneAssetLibrarySource =
  | "upload"
  | "character"
  | "scene"
  | "prop";

/** 用途类目。老条目没有该字段，读取侧按 source/media 兜底推导。 */
export type FreezoneAssetLibraryCategory =
  | "other"
  | "character"
  | "scene"
  | "prop"
  | "sketch"
  | "style"
  | "audio"
  | "draft"
  | "topdown"
  | "storyVersion";

/**
 * 用户自建的资产库文件夹。系统文件夹（主线 / 待分类资产 / 各类目同名目录）不落盘，
 * 由前端按保留 key 生成，只有这里的自建文件夹会从后端读回来。
 */
export interface FreezoneAssetLibraryFolder {
  id: string;
  name: string;
  /** 封面图 URL，取自文件夹内某个素材；没设过时为空,前端画默认文件夹图标。 */
  cover?: string | null;
  created_at?: string;
}

export interface FreezoneVideoCharacterLibraryItem {
  id?: string;
  name: string;
  media?: FreezoneAssetLibraryMedia;
  source?: FreezoneAssetLibrarySource;
  category?: FreezoneAssetLibraryCategory;
  /** 保存位置：系统文件夹 key 或自建文件夹 id。老条目没有，读取侧按类目兜底。 */
  folder?: string;
  image_urls?: string[];
  video_url?: string | null;
  audio_url?: string | null;
  cover_url?: string | null;
  /** 剪映草稿条目在 VectCut 服务里的草稿标识（仅 media="draft" 的条目有）。 */
  draft_id?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface FreezoneAddVideoCharacterLibraryItemPayload {
  name: string;
  media?: FreezoneUploadableMedia;
  imageUrls?: string[];
  videoUrl?: string;
  audioUrl?: string;
  category?: FreezoneAssetLibraryCategory;
  folder?: string;
  /** [aipdd fork delta] 俯视图草稿（category=topdown）内联的可编辑场景 JSON。 */
  scene?: unknown;
}

export async function fetchFreezoneVideoCharacterLibrary(
  project: string,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library`,
  );
}

export async function submitFreezoneAddVideoCharacterLibraryItem(
  project: string,
  payload: FreezoneAddVideoCharacterLibraryItemPayload,
): Promise<unknown> {
  const body: Record<string, unknown> = {
    name: payload.name,
    media: payload.media ?? "image",
  };
  if (payload.imageUrls && payload.imageUrls.length > 0) {
    body.image_urls = payload.imageUrls;
  }
  if (payload.videoUrl) body.video_url = payload.videoUrl;
  if (payload.audioUrl) body.audio_url = payload.audioUrl;
  if (payload.category) body.category = payload.category;
  if (payload.folder) body.folder = payload.folder;
  if (payload.scene !== undefined) body.scene = payload.scene;
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library`,
    { method: "POST", json: body },
  );
}

export async function fetchFreezoneAssetLibraryFolders(
  project: string,
): Promise<FreezoneAssetLibraryFolder[]> {
  return await apiCall<FreezoneAssetLibraryFolder[]>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/folders`,
  );
}

export async function createFreezoneAssetLibraryFolder(
  project: string,
  name: string,
): Promise<FreezoneAssetLibraryFolder> {
  return await apiCall<FreezoneAssetLibraryFolder>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/folders`,
    { method: "POST", json: { name } },
  );
}

/** 改名 / 换封面。只传要改的字段,另一个原样保留。 */
export async function updateFreezoneAssetLibraryFolder(
  project: string,
  folderId: string,
  patch: { name?: string; cover?: string },
): Promise<FreezoneAssetLibraryFolder> {
  return await apiCall<FreezoneAssetLibraryFolder>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/folders/${encodeURIComponent(folderId)}`,
    { method: "PATCH", json: patch },
  );
}

/** 整柜清空:删掉文件夹连同里面的素材。返回被删掉的素材条数。 */
export async function deleteFreezoneAssetLibraryFolder(
  project: string,
  folderId: string,
): Promise<{ deleted_items: number }> {
  return await apiCall<{ deleted_items: number }>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/folders/${encodeURIComponent(folderId)}`,
    { method: "DELETE" },
  );
}

/**
 * 把主线的人物/场景/道具参考图与人物语音幂等同步进资产库。后端按稳定合成 id
 * upsert,重复同步只更新不重复。返回同步后的完整库(apiCall 会解包 data)。
 */
export async function syncFreezoneAssetLibraryFromMainline(
  project: string,
): Promise<FreezoneVideoCharacterLibraryItem[]> {
  return await apiCall<FreezoneVideoCharacterLibraryItem[]>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/sync-from-mainline`,
    { method: "POST" },
  );
}

/**
 * 给资产库条目改名。只动展示名，素材地址不变——画布上已经引用它的节点不受影响。
 *
 * 主线同步来的条目改了名会在下次「从主线同步」时被覆盖回去，所以调用侧只对本地
 * 上传的条目开放这个入口。
 */
export async function renameFreezoneVideoCharacterLibraryItem(
  project: string,
  itemId: string,
  name: string,
): Promise<FreezoneVideoCharacterLibraryItem> {
  return await apiCall<FreezoneVideoCharacterLibraryItem>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library/${encodeURIComponent(itemId)}`,
    { method: "PATCH", json: { name } },
  );
}

export async function deleteFreezoneVideoCharacterLibraryItem(
  project: string,
  itemId: string,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
}

/**
 * 剪映草稿条目的草稿包（zip）下载地址：资产库「剪映草稿」的下载按钮用它。
 *
 * 草稿文件在 VectCut 服务自己的目录下，api 只做转发（见 routes/freezone/
 * library.py 的 drafts/{id}/download），所以这里拼的是 api 代理地址。
 */
export function freezoneVectcutDraftDownloadUrl(
  project: string,
  draftId: string,
): string {
  return `/api/v1/projects/${encodeURIComponent(project)}/freezone/video/drafts/${encodeURIComponent(
    draftId,
  )}/download`;
}

/**
 * [aipdd fork delta] 上传一整个剪映草稿文件夹：一个文件夹 = 一份草稿。
 *
 * 浏览器把文件夹里的文件平铺传入（`path` 是相对路径，已去掉最外层文件夹名），
 * api 生成安全 draft_id 后转发 VectCut 落盘，并登记进资产库「剪映草稿」。
 */
export async function uploadFreezoneVectcutDraft(
  project: string,
  name: string,
  files: Array<{ path: string; file: File }>,
): Promise<unknown> {
  const fd = new FormData();
  fd.append("name", name);
  files.forEach(({ path, file }) => {
    fd.append("files", file, file.name);
    fd.append("relative_paths", path);
  });
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/video/drafts/upload`,
    { method: "POST", body: fd, timeout: false },
  ).json<{ ok: boolean; data?: unknown; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "draft upload failed");
  }
  return resp.data;
}

/**
 * [aipdd fork delta] 把 VectCut 草稿目录里还没登记的草稿补登记进资产库。
 * 目录是真源：只补新增、不清理，已登记条目（含用户改过的名字）保持原样。
 */
export async function syncFreezoneVectcutDrafts(
  project: string,
): Promise<{ added: number; total: number; items: unknown[] }> {
  return await apiCall<{ added: number; total: number; items: unknown[] }>(
    `projects/${encodeURIComponent(project)}/freezone/video/drafts/sync`,
    { method: "POST" },
  );
}

// /freezone/init ---------------------------------------------------------- //

export async function initFreezone(project: string): Promise<{ freezone_dir: string }> {
  return await apiCall<{ freezone_dir: string }>(
    `projects/${encodeURIComponent(project)}/freezone/init`,
    { method: "POST" },
  );
}
