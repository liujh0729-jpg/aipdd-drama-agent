// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 统一编排的前端 API 入口：只对接服务端权威计划器
// POST /api/v1/projects/{project}/pipeline/plan。决策在服务器、循环在客户端
// （见 @/lib/pipeline-driver），这里不复制任何阶段状态机。
import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";

export type PipelineMode = "manual" | "checkpoint" | "auto";

export type PlanStatus =
  | "planned"
  | "running"
  | "failed"
  | "done"
  | "requires_input"
  | "idle";

/** 计划器产出的一个动作：细粒度 REST（含 path）或类型化步（含 kind，如 story_version）。 */
export interface PlanAction {
  method?: string;
  path?: string;
  query?: Record<string, unknown> | null;
  body?: Record<string, unknown> | null;
  await_task?: boolean;
  kind?: string;
  episode?: number;
}

/** 服务端 plan_next 的返回契约（单一逻辑源）。 */
export interface PlanStep {
  ok: boolean;
  stage: string;
  status: PlanStatus;
  actions: PlanAction[];
  message: string;
  next_hint: string;
  episode?: number;
  task?: Record<string, unknown>;
  error?: string;
  progress_counts?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PlanRequestBody {
  mode?: PipelineMode;
  execute?: boolean;
  include_story_version?: boolean;
  /** 把该集「场景规划 / 道具规划」也当前置自动补齐（草图入口不传）。 */
  plan_episode_assets?: boolean;
  episode?: number;
  story_text?: string;
  aspect_ratio?: string;
  model?: string;
  style?: string;
  episode_count?: number;
  target_beats?: number;
  seconds_per_episode?: number;
  total_seconds?: number;
  story_style?: string;
}

interface PlanEnvelope {
  ok: boolean;
  data: PlanStep;
}

// 计划器只 GET 探测、产出动作，响应很快；给足超时仅防御冷启动。
const PLAN_TIMEOUT_MS = 120_000;

/**
 * 请求服务端「下一个未完成阶段」的计划（execute=false 时零副作用）。
 * 循环推进由调用方驱动，本函数每次只取一步。
 */
export async function planPipeline(
  project: string,
  body: PlanRequestBody,
): Promise<PlanStep> {
  const res = await jsonWithBackendError<PlanEnvelope>(
    api.post(p`api/v1/projects/${project}/pipeline/plan`, {
      json: body,
      throwHttpErrors: false,
      timeout: PLAN_TIMEOUT_MS,
    }),
  );
  return res.data;
}
