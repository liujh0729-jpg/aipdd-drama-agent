// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 唯一的客户端流水线循环：把「决策」留在服务端计划器
// （@/api/pipeline#planPipeline），这里只负责「发射 + 等待 + 再取下一步」。
//
// 单一职责，供三类调用方共用：
// - PipelineControlBar「一键运行」：ceiling="full"，三档模式（手动/检查点/全自动），
//   planEpisodeAssets=true（资产规划与故事版都算前提）。
// - 分集列表「自动脚本」：episode 锁定 + ceiling="script" + planEpisodeAssets=true。
// - 分集列表「自动分镜图」：episode 锁定 + ceiling="sketch"（只勾草图）或 "full"
//   （勾了渲染图），只补「分镜脚本 + 故事版」，不动场景/道具规划（planEpisodeAssets 不传）。
//
// 逐集阶段顺序由服务端计划器给出：身份规划 → 分镜脚本 → 场景/道具规划 → 故事版
// → 草图配色+生成 → 镜头首帧。
//
// 只推进到镜头首帧，绝不提交视频；服务端计划器已保证这一点，客户端不会越界。
import i18n from "i18next";

import {
  fetchFreezoneJobResult,
  submitFreezoneAddVideoCharacterLibraryItem,
  submitFreezoneGen,
} from "@/api/ops";
import { awaitTaskCompletion, listTasks } from "@/api/tasks";
import {
  planPipeline,
  type PipelineMode,
  type PlanAction,
  type PlanRequestBody,
  type PlanStep,
} from "@/api/pipeline";
import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import type { ErrorResponse, TaskResponse } from "@/types/api";

export type { PipelineMode } from "@/api/pipeline";

/** 推进上限：到分镜脚本 / 到分镜草图 / 到镜头首帧（渲染图，默认）。 */
export type StageCeiling = "script" | "sketch" | "full";

/** 用户主动停止（在可中断点抛出）：调用方据此提示「已停止」而非「失败」。 */
export class PipelineStoppedError extends Error {
  constructor() {
    super("pipeline stopped by user");
    this.name = "PipelineStoppedError";
  }
}

export type PipelineStepLabel =
  | "identity"
  | "scenePlan"
  | "propPlan"
  | "script"
  | "storyVersion"
  | "colors"
  | "sketch"
  | "frame";

export interface StoryVersionConfig {
  /** freezone.story_version.system 的基础提示词（含 {user_requirement} 占位）。 */
  basePrompt: string;
  /** 资产库「故事版」类目展示名，用于命名入库条目。 */
  categoryLabel: string;
  /** 该集创作要求（摘要/原文截断），注入基础提示词。 */
  requirementFor(episode: number): string;
}

export interface PipelineGenerationSettings {
  aspectRatio?: string;
  model?: string;
  style?: string;
  targetBeats?: number;
  storyStyle?: string;
  /** 目标集数；留空则由 totalSeconds/secondsPerEpisode 推导（后端默认 10）。 */
  episodeCount?: number;
  /** 总时长（秒）；与 secondsPerEpisode 一起推导集数。 */
  totalSeconds?: number;
  /** 单集时长（秒）；同时推导目标镜数。 */
  secondsPerEpisode?: number;
}

/**
 * runPipelineDriven 的正常收敛结果（异常路径仍以抛错表达：failed→Error、stop→PipelineStoppedError）。
 * 调用方据此区分「真的跑完」与「缺输入/达上限/单步」，避免把 requires_input 误报成「已完成」。
 */
export type PipelineRunResult =
  | { kind: "completed"; stage: string; message: string }
  | { kind: "requires-input"; stage: string; message: string }
  | { kind: "ceiling"; stage: string }
  | { kind: "manual-step"; stage: string; message: string };

export interface PipelineDriverOptions {
  project: string;
  mode: PipelineMode;
  episode?: number;
  ceiling?: StageCeiling;
  includeStoryVersion?: boolean;
  storyVersion?: StoryVersionConfig;
  /**
   * [aipdd fork delta] 把该集「场景规划 / 道具规划」也当前置自动补齐（纯 LLM 文本
   * 规划，不出资产图）。「自动草图」入口不传，按用户口径草图只补到脚本+故事版。
   */
  planEpisodeAssets?: boolean;
  settings?: PipelineGenerationSettings;
  /** 返回 true 时在下一个可中断点抛 {@link PipelineStoppedError}。 */
  isStopped?(): boolean;
  /** 每个「下一步」计划到达时回调（进度 / 阶段展示）。 */
  onStage?(step: PlanStep): void;
  /** 单个动作发起前回调其步骤标签，供 UI 精确展示。 */
  onStep?(episode: number | null, step: PipelineStepLabel): void;
  /** checkpoint 档：动作发射前的确认闸；返回 false 则停止本轮。 */
  approveGate?(step: PlanStep): Promise<boolean>;
  /** 某一集的一段完成后回调，供调用方失效对应查询缓存。 */
  onInvalidate?(episode: number | null): void;
  /** 覆盖默认的故事正文（仅在全流程首步、项目无故事源时使用）。 */
  storyText?: string;
}

const ACTIVE_STATUSES = new Set(["submitting", "queued", "running"]);
const IDLE_POLL_MS = 3000;
const MAX_STEPS = 200;

// 阶段 → 序号：项目级/剧本/故事等前置阶段一律 0（任何上限都放行），
// 逐集阶段按其在工作流中的次序编号，done/idle 记为终态 99。
// 身份规划是脚本前前置，记 1；场景/道具规划跟分镜脚本同档（服务端在脚本之后才排
// 这两步，规划器要吃剧本场景头）；故事版紧跟其后，都记 2。
const EPISODE_STAGE_RANK: Record<string, number> = {
  identities_planning: 1,
  scenes_planning: 2,
  props_planning: 2,
  script_generating: 2,
  story_version_pending: 2,
  sketches_generating: 3,
  frames_generating: 4,
};
const CEILING_RANK: Record<StageCeiling, number> = {
  script: 2,
  sketch: 3,
  full: 4,
};

function stageRank(stage: string): number {
  if (!stage || stage === "done") return 99;
  const match = /^episode_\d+_(.+)$/.exec(stage);
  if (match) return EPISODE_STAGE_RANK[match[1]] ?? 99;
  if (stage.endsWith("_done")) return 99;
  return 0;
}

function actionStepLabel(action: PlanAction): PipelineStepLabel | null {
  if (action.kind === "story_version") return "storyVersion";
  const path = action.path ?? "";
  if (path.includes("/identities/plan")) return "identity";
  if (path.includes("/scenes/plan")) return "scenePlan";
  if (path.includes("/props/plan")) return "propPlan";
  if (path.includes("/story/to-script")) return "script";
  if (path.includes("/sketches/assign-colors")) return "colors";
  if (path.includes("/sketches/generate")) return "sketch";
  if (path.includes("/beats/regenerate")) return "frame";
  return null;
}

function checkStopped(isStopped?: () => boolean): void {
  if (isStopped?.()) throw new PipelineStoppedError();
}

function buildRequestBody(opts: PipelineDriverOptions): PlanRequestBody {
  const body: PlanRequestBody = {
    mode: opts.mode,
    execute: false,
  };
  if (opts.episode != null) body.episode = opts.episode;
  if (opts.includeStoryVersion) body.include_story_version = true;
  if (opts.planEpisodeAssets) body.plan_episode_assets = true;
  if (opts.storyText) body.story_text = opts.storyText;
  const s = opts.settings;
  if (s) {
    if (s.aspectRatio) body.aspect_ratio = s.aspectRatio;
    if (s.model) body.model = s.model;
    if (s.style) body.style = s.style;
    if (s.targetBeats != null) body.target_beats = s.targetBeats;
    if (s.storyStyle != null) body.story_style = s.storyStyle;
    if (s.episodeCount != null) body.episode_count = s.episodeCount;
    if (s.totalSeconds != null) body.total_seconds = s.totalSeconds;
    if (s.secondsPerEpisode != null) body.seconds_per_episode = s.secondsPerEpisode;
  }
  return body;
}

/** 轮询项目任务直到无活跃任务；每轮检查停止标志。 */
async function waitUntilIdle(project: string, isStopped?: () => boolean): Promise<void> {
  // eslint-disable-next-line no-constant-condition
  while (true) {
    checkStopped(isStopped);
    let active = false;
    try {
      const tasks = await listTasks(project);
      active = tasks.some((t) => ACTIVE_STATUSES.has(String(t.status)));
    } catch {
      active = false;
    }
    if (!active) return;
    await new Promise((resolve) => setTimeout(resolve, IDLE_POLL_MS));
  }
}

async function executeStoryVersion(
  project: string,
  episode: number,
  config: StoryVersionConfig,
  isStopped?: () => boolean,
): Promise<void> {
  const token = "{user_requirement}";
  const requirement = config.requirementFor(episode);
  const prompt = config.basePrompt.includes(token)
    ? config.basePrompt.split(token).join(requirement)
    : `${config.basePrompt}\n\n${requirement}`;
  const ref = await submitFreezoneGen(project, {
    prompt,
    aspectRatio: "16:9",
    imageSize: "2K",
  });
  await awaitTaskCompletion(ref.task_key, project, { taskType: ref.task_type });
  checkStopped(isStopped);
  const result = await fetchFreezoneJobResult(project, "freezone_gen", ref.job_id);
  if (!result.url) throw new Error("story-version: gen completed without url");
  await submitFreezoneAddVideoCharacterLibraryItem(project, {
    name: `EP${episode} ${config.categoryLabel}`,
    media: "image",
    imageUrls: [result.url],
    category: "storyVersion",
  });
}

/** 发射一个细粒度 REST 动作（await_task 时阻塞到任务完成）。 */
async function executeRestAction(
  project: string,
  action: PlanAction,
): Promise<void> {
  const method = (action.method ?? "POST").toLowerCase() as
    | "post"
    | "put"
    | "patch"
    | "get";
  // 计划器给出的是绝对路径 "/api/v1/..."；ky 的 prefix 是 "/"，去掉前导斜杠对齐既有调用。
  const url = (action.path ?? "").replace(/^\//, "");
  const res = await jsonWithBackendError<TaskResponse | ErrorResponse>(
    api[method](url, {
      searchParams: (action.query ?? undefined) as Record<string, string> | undefined,
      json: action.body ?? undefined,
      throwHttpErrors: false,
    }),
  );
  if (res.ok === false) throw new Error(res.error);
  if (action.await_task && res.ok && res.task_key) {
    await awaitTaskCompletion(res.task_key, project, { taskType: res.task_type });
  }
}

async function executeActions(
  opts: PipelineDriverOptions,
  step: PlanStep,
): Promise<void> {
  const episode = step.episode ?? opts.episode ?? null;
  for (const action of step.actions) {
    checkStopped(opts.isStopped);
    const label = actionStepLabel(action);
    if (label) opts.onStep?.(episode, label);
    if (action.kind === "story_version") {
      // 类型化步只有拿到故事版配置（含提示词）时才执行；否则跳过（前端专属能力）。
      if (opts.storyVersion && episode != null && opts.storyVersion.basePrompt.trim()) {
        await executeStoryVersion(
          opts.project,
          episode,
          opts.storyVersion,
          opts.isStopped,
        );
      }
      continue;
    }
    await executeRestAction(opts.project, action);
  }
}

/**
 * 客户端唯一流水线循环：反复向服务端计划器取「下一步」→（检查点确认）→ 发射
 * → 等待空闲 → 再取下一步，直到达到 ceiling、项目 done、失败或用户停止。
 */
export async function runPipelineDriven(
  opts: PipelineDriverOptions,
): Promise<PipelineRunResult> {
  const ceiling = opts.ceiling ?? "full";
  const body = buildRequestBody(opts);
  let guard = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    checkStopped(opts.isStopped);
    guard += 1;
    if (guard > MAX_STEPS) return { kind: "ceiling", stage: "" };

    const step = await planPipeline(opts.project, body);
    opts.onStage?.(step);

    if (step.status === "failed") {
      throw new Error(step.error || step.message || `pipeline failed at ${step.stage}`);
    }
    if (step.status === "done" || step.status === "idle") {
      return { kind: "completed", stage: step.stage, message: step.message || "" };
    }
    if (step.status === "requires_input") {
      // 项目还没有故事源等前置输入：不是「完成」，交调用方引导补齐（不误报完成）。
      return {
        kind: "requires-input",
        stage: step.stage,
        // 服务端没给原因时的兜底文案（按界面语言取词，不写死中文）。
        message: step.message || i18n.t("pipeline.requiresStoryInput"),
      };
    }

    // 运行中：只需等待该阶段空闲，不涉及上限——上限只约束「将要发射的 planned 动作」，
    // 空闲后重新取到的下一个 planned 阶段再由下面的 ceiling 判定是否越界。
    if (step.status === "running") {
      await waitUntilIdle(opts.project, opts.isStopped);
      continue;
    }

    // 到这里必是 planned：已达上限则不发射，直接结束本轮。
    if (stageRank(step.stage) > CEILING_RANK[ceiling]) {
      return { kind: "ceiling", stage: step.stage };
    }

    if (opts.mode === "checkpoint" && opts.approveGate) {
      const approved = await opts.approveGate(step);
      checkStopped(opts.isStopped);
      if (!approved) return { kind: "ceiling", stage: step.stage };
    }

    await executeActions(opts, step);
    opts.onInvalidate?.(step.episode ?? opts.episode ?? null);

    if (opts.mode === "manual") {
      return { kind: "manual-step", stage: step.stage, message: step.message || "" };
    }

    await waitUntilIdle(opts.project, opts.isStopped);
  }
}
