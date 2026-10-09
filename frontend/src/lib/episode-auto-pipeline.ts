// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分集列表页「自动化生成分镜脚本 / 自动化生成分镜图」编排器。
//
// 统一后它只是 @/lib/pipeline-driver（唯一客户端循环）的一层薄壳：
// - 决策全部交给服务端权威计划器（POST /pipeline/plan），这里不再内联端点序列；
// - 「自动脚本」= 逐集推进到分镜脚本上限（身份规划 → 脚本 → 场景/道具规划 → 故事版）；
// - 「自动分镜图」= 逐集推进到用户勾选的产物（草图 / 渲染图），缺先补齐（脚本 → 故事版），
//   再配色 → 草图 → 渲染图；两个都勾时先草图再据草图出渲染图（阶段顺序由计划器保证），
//   按用户口径不替用户跑场景/道具规划；
// - 端点统一到 plan-async / to-script（与 MCP、REST 同源）。
// 保留原有导出签名与错误类型；运行态编排集中在 use-episode-auto-pipeline。
import type { QueryClient } from "@tanstack/react-query";

import {
  runPipelineDriven,
  PipelineStoppedError,
  type PipelineRunResult,
  type PipelineStepLabel,
  type StageCeiling,
  type StoryVersionConfig,
} from "@/lib/pipeline-driver";
import { queryKeys } from "@/lib/query-keys";

export type AutoPipelineKind = "script" | "storyboard";

/** 「自动化生成分镜图」的产物勾选：分镜草图 / 渲染图（镜头首帧）。 */
export type StoryboardTarget = "sketch" | "render";

/** 脚本管线的步骤；分镜图管线的步骤（与 driver 步骤标签对齐）。 */
export type AutoScriptStep =
  | "identity"
  | "scenePlan"
  | "propPlan"
  | "script"
  | "storyVersion";
export type AutoStoryboardStep = "colors" | "sketch" | "frame";
export type AutoStep = AutoScriptStep | AutoStoryboardStep;

export interface AutoEpisodeInput {
  number: number;
  summary?: string;
  rawContent?: string;
  beatSourceText?: string;
}

export interface AutoPipelineCallbacks {
  onEpisodeStart?(episode: number): void;
  onStep?(episode: number, step: AutoStep): void;
  /** 返回 true 时编排器在下一个可中断点抛出 {@link AutoPipelineStoppedError}。 */
  isStopped?(): boolean;
}

/** 用户主动停止：调用方据此提示「已停止」而非「失败」。 */
export class AutoPipelineStoppedError extends Error {
  constructor() {
    super("auto-pipeline stopped by user");
    this.name = "AutoPipelineStoppedError";
  }
}

/** 某集某步失败：携带集数 / 步骤 / 原始错误，供 UI 定位与文案透传。 */
export class AutoPipelineFailedError extends Error {
  readonly episode: number;
  readonly step: AutoStep;
  readonly cause?: unknown;
  constructor(episode: number, step: AutoStep, message: string, cause?: unknown) {
    super(message);
    this.name = "AutoPipelineFailedError";
    this.episode = episode;
    this.step = step;
    this.cause = cause;
  }
}

/**
 * 服务端计划器判定「缺前置输入」（如项目还没有故事源）而停住。
 * 既不是失败也不是完成：一条动作都没发射，必须让 UI 明确提示去补齐，
 * 绝不能落进「自动化已完成」——那正是用户看到「提示要补齐四步、实际什么都没做」的入口。
 */
export class AutoPipelineNeedsInputError extends Error {
  readonly episode: number;
  readonly stage: string;
  constructor(episode: number, stage: string, message: string) {
    super(message);
    this.name = "AutoPipelineNeedsInputError";
    this.episode = episode;
    this.stage = stage;
  }
}

// 与 story-version-panel.tsx、后端 prompt_library_defaults.py 保持一致。
export const STORY_VERSION_PROMPT_KEY = "freezone.story_version.system";
// 故事版创作要求来自该集摘要 / 原文，截断避免把整集正文塞进图像 prompt。
const STORY_VERSION_REQUIREMENT_MAX_CHARS = 800;

const AUTO_STEPS = new Set<AutoStep>([
  "identity",
  "scenePlan",
  "propPlan",
  "script",
  "storyVersion",
  "colors",
  "sketch",
  "frame",
]);

function storyVersionRequirement(ep: AutoEpisodeInput): string {
  const text = (ep.summary || ep.beatSourceText || ep.rawContent || "")
    .replace(/\s+/g, " ")
    .trim();
  return text.slice(0, STORY_VERSION_REQUIREMENT_MAX_CHARS);
}

/**
 * 故事版配置：两条自动化管线共用。关键不变量——只有拿到了提示词才
 * 会向计划器请求 include_story_version（服务端会回一个只有客户端能跑的
 * 类型化步，否则那个阶段会反复返回而不是前进）。
 */
function buildStoryVersionConfig(
  ep: AutoEpisodeInput,
  basePrompt: string,
  categoryLabel: string,
): StoryVersionConfig | undefined {
  return basePrompt.trim()
    ? {
        basePrompt,
        categoryLabel,
        requirementFor: () => storyVersionRequirement(ep),
      }
    : undefined;
}

/** driver 抛出的停止 / 失败错误翻译成编排器的既有错误类型（携带集数与最后步骤）。 */
function translateError(err: unknown, episode: number, lastStep: AutoStep): never {
  if (
    err instanceof AutoPipelineStoppedError ||
    err instanceof AutoPipelineFailedError ||
    err instanceof AutoPipelineNeedsInputError
  ) {
    // 编排器自己的类型化错误原样上抛：降级成 FailedError 会把「缺输入」误报成「某步失败」。
    throw err;
  }
  if (err instanceof PipelineStoppedError) {
    throw new AutoPipelineStoppedError();
  }
  const message = err instanceof Error ? err.message : String(err);
  throw new AutoPipelineFailedError(episode, lastStep, message, err);
}

export interface RunAutoPipelineParams {
  project: string;
  episodes: AutoEpisodeInput[];
  queryClient: QueryClient;
  callbacks?: AutoPipelineCallbacks;
}

export interface RunScriptPipelineParams extends RunAutoPipelineParams {
  /** 故事版基础提示词（freezone.story_version.system 的 value/default）；为空则跳过故事版。 */
  storyVersionBasePrompt: string;
  /** 资产库「故事版」类目展示名，用于命名条目。 */
  storyVersionCategoryLabel: string;
}

/** 分镜图自动化与脚本自动化共用故事版配置（出图前该集没故事版时先补一张）。 */
export interface RunStoryboardPipelineParams extends RunScriptPipelineParams {
  /**
   * 用户勾选的产物。只要勾了渲染图就把上限放到首帧：计划器本身就按「草图 → 首帧」
   * 出步，所以两个都勾时天然是先草图、再据草图出渲染图；只勾渲染图而草图缺失时，
   * 草图作为前置也会被补齐（首帧吃的就是草图）。
   */
  targets: StoryboardTarget[];
}

function invalidateAfterScript(queryClient: QueryClient, project: string, ep: number) {
  queryClient.invalidateQueries({ queryKey: queryKeys.episodes(project) });
  queryClient.invalidateQueries({ queryKey: queryKeys.pipelineStatus(project) });
  queryClient.invalidateQueries({ queryKey: queryKeys.characters(project) });
  queryClient.invalidateQueries({ queryKey: queryKeys.script(project, ep) });
  queryClient.invalidateQueries({ queryKey: queryKeys.beats(project, ep) });
  queryClient.invalidateQueries({ queryKey: queryKeys.episodeDetail(project, ep) });
}

function invalidateAfterSketch(queryClient: QueryClient, project: string, ep: number) {
  queryClient.invalidateQueries({ queryKey: queryKeys.grids(project, ep) });
  queryClient.invalidateQueries({ queryKey: queryKeys.beats(project, ep) });
  queryClient.invalidateQueries({ queryKey: queryKeys.script(project, ep) });
  queryClient.invalidateQueries({ queryKey: queryKeys.episodes(project) });
  queryClient.invalidateQueries({ queryKey: queryKeys.pipelineStatus(project) });
}

/**
 * driver 的返回值不能被丢弃：requires-input 意味着这一轮**一个动作都没发射**，
 * 只有 ceiling / completed 才是「推进到目标后正常收敛」。
 */
function assertAdvanced(result: PipelineRunResult, episode: number): void {
  if (result.kind === "requires-input") {
    throw new AutoPipelineNeedsInputError(episode, result.stage, result.message);
  }
}

/** 把 driver 步骤标签接到编排器回调，并记录「最后步骤」用于错误归属。 */
function makeStepAdapter(
  callbacks: AutoPipelineCallbacks | undefined,
  tracker: { lastStep: AutoStep },
): (episode: number | null, step: PipelineStepLabel) => void {
  return (episode, step) => {
    if (episode == null || !AUTO_STEPS.has(step as AutoStep)) return;
    tracker.lastStep = step as AutoStep;
    callbacks?.onStep?.(episode, step as AutoStep);
  };
}

/**
 * 脚本自动化：对选中多集逐集推进到「分镜脚本」上限（身份规划 → 脚本 →
 * 场景/道具规划 → 故事版，顺序由服务端计划器按真实依赖给出），委托
 * 服务端计划器 + 客户端 driver 循环。任一集失败即中止整轮
 * （抛 AutoPipelineFailedError）；用户停止抛 AutoPipelineStoppedError。
 */
export async function runScriptPipeline(
  params: RunScriptPipelineParams,
): Promise<void> {
  const {
    project,
    episodes,
    queryClient,
    callbacks,
    storyVersionBasePrompt,
    storyVersionCategoryLabel,
  } = params;
  for (const ep of episodes) {
    if (callbacks?.isStopped?.()) throw new AutoPipelineStoppedError();
    callbacks?.onEpisodeStart?.(ep.number);
    const tracker = { lastStep: "identity" as AutoStep };
    const storyVersion = buildStoryVersionConfig(
      ep,
      storyVersionBasePrompt,
      storyVersionCategoryLabel,
    );
    try {
      const result = await runPipelineDriven({
        project,
        // auto：循环推进锁定集直至 ceiling 上限（资产规划→脚本→故事版），
        // 而非 manual 的发一步即返回（那样对需先做前置规划的集永远到不了脚本）。
        mode: "auto",
        episode: ep.number,
        ceiling: "script",
        // 「资产规划」三件里的场景/道具规划也算前提：交服务端计划器按缺什么补什么。
        planEpisodeAssets: true,
        includeStoryVersion: Boolean(storyVersion),
        storyVersion,
        isStopped: callbacks?.isStopped,
        onStep: makeStepAdapter(callbacks, tracker),
        onInvalidate: (episode) =>
          episode != null && invalidateAfterScript(queryClient, project, episode),
      });
      assertAdvanced(result, ep.number);
    } catch (err) {
      translateError(err, ep.number, tracker.lastStep);
    }
  }
}

/**
 * 分镜图自动化：对选中多集逐集推进到用户勾选的产物（草图 → 渲染图）。
 * 前置按用户口径只补「分镜脚本 + 故事版」（该集缺哪样补哪样），不替用户跑
 * 场景/道具规划；故这里也需带着故事版配置进入。
 */
export async function runStoryboardPipeline(
  params: RunStoryboardPipelineParams,
): Promise<void> {
  const {
    project,
    episodes,
    queryClient,
    callbacks,
    storyVersionBasePrompt,
    storyVersionCategoryLabel,
    targets,
  } = params;
  const ceiling: StageCeiling = targets.includes("render") ? "full" : "sketch";
  for (const ep of episodes) {
    if (callbacks?.isStopped?.()) throw new AutoPipelineStoppedError();
    callbacks?.onEpisodeStart?.(ep.number);
    const tracker = { lastStep: "colors" as AutoStep };
    const storyVersion = buildStoryVersionConfig(
      ep,
      storyVersionBasePrompt,
      storyVersionCategoryLabel,
    );
    try {
      const result = await runPipelineDriven({
        project,
        // auto：循环推进至勾选产物的上限（缺先补脚本/故事版，再配色→草图→渲染图）。
        mode: "auto",
        episode: ep.number,
        ceiling,
        includeStoryVersion: Boolean(storyVersion),
        storyVersion,
        isStopped: callbacks?.isStopped,
        onStep: makeStepAdapter(callbacks, tracker),
        // 补齐前置时也会跑脚本阶段，故两类缓存一起失效，页面不会停在旧数据上。
        onInvalidate: (episode) => {
          if (episode == null) return;
          invalidateAfterScript(queryClient, project, episode);
          invalidateAfterSketch(queryClient, project, episode);
        },
      });
      assertAdvanced(result, ep.number);
    } catch (err) {
      translateError(err, ep.number, tracker.lastStep);
    }
  }
}
