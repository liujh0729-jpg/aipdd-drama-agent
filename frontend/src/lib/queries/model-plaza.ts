// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] WP-E: 模型广场——只读目录 + 探活 + 调用示例。
// 这里没有任何计费/额度/价格字段（CE 的额度体系已在 WP-D 整体拆除）。
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

// [aipdd fork delta] 原 audio（语音合成）与 official-catalog（官方目录）两个来源已整体移除。
export type PlazaConnector =
  | "gateway"
  | "comfyui"
  | "mediakit"
  | "env-override";

export type PlazaCapability =
  | "text"
  | "vision"
  | "embedding"
  | "image"
  | "video"
  | "audio"
  // [aipdd fork delta req46] 音乐能力位（后端 PLAZA_CAPABILITIES 同步）。
  | "music"
  | "enhance";

export type PlazaInvokeKind =
  | "openai-chat"
  | "openai-embeddings"
  | "openai-images"
  | "async-video-task"
  | "comfyui-workflow"
  | "mediakit-task"
  | "tts-voice"
  // [aipdd fork delta req46] 音乐不经网关：卡上得如实写成「直连音乐生成」。
  | "direct-music";

export type PlazaStatus =
  | "mapped"
  | "unmapped"
  | "noChannel"
  | "stale"
  | "capabilityMismatch";

export interface PlazaExamples {
  curl?: string;
  python?: string;
  node?: string;
  poll?: string;
  /** [aipdd fork delta] req41：渠道目录声明了 ``images/edits`` 时的图生图 / 编辑示例。 */
  edit?: string;
  /** 非 OpenAI 兼容链路的调用形态提示码（由前端按码翻译，不在后端写死文案）。 */
  note?: string;
}

export interface PlazaModel {
  id: string;
  displayName: string;
  connector: PlazaConnector;
  invokeKind: PlazaInvokeKind;
  capabilities: PlazaCapability[];
  /** 请求里真正要写的模型名（别名或映射键）。 */
  modelName: string;
  provider: string;
  channelName: string;
  /** 网关映射后的真实上游模型。 */
  upstreamModel: string;
  vendor: string;
  baseUrl: string;
  endpoint: string;
  status: PlazaStatus;
  inUseBy: string[];
  sortOrder: number;
  note: string;
  apiKeyPreview: string;
  examples: PlazaExamples;
  testable: boolean;
  /** [aipdd fork delta] req33 §6.3：后台实测缓存里的结论（ok / not-granted / not-vision / unknown / unprobed）。
   *  这一页不会为它发请求，媒体协议一律 unprobed——没测过不等于不行。 */
  probeState?: string;
  probeUnusable?: boolean;
  fallbackProvider?: string;
  fallbackModel?: string;
  /** [aipdd fork delta] env-override 条目的「功能位」业务标识（如 SCREENPLAY_NORMALIZER_MODEL）；
   * 卡片标题/调用名展示的是模型名，env 仅作功能位说明。 */
  env?: string;
  mediaType?: string;
  dimension?: number | string;
  batchSize?: number | string;
  backend?: string;
  configSource?: string;
  requiresKey?: boolean;
}

export interface PlazaConnectorSummary {
  id: PlazaConnector;
  modelCount: number;
  availableCount: number;
}

export interface PlazaAliasHealth {
  status?: string;
  checked?: boolean;
  unmapped?: string[];
  staleTargets?: unknown[];
  capabilityMismatch?: unknown[];
  noChannel?: string[];
  degraded?: unknown[];
  generatedAt?: string;
}

export interface PlazaData {
  connectors: PlazaConnectorSummary[];
  capabilities: PlazaCapability[];
  models: PlazaModel[];
  gateway: {
    mode: string;
    publicBaseUrl: string;
    apiKeyPreview: string;
    configured: boolean;
  };
  aliasHealth: PlazaAliasHealth;
}

export interface PlazaProbeResult {
  modelId: string;
  ok: boolean;
  skipped: boolean;
  latencyMs: number;
  httpStatus: number;
  error: string;
  reason?: string;
}

export function useModelPlaza(connector?: PlazaConnector) {
  return useQuery({
    queryKey: queryKeys.modelPlaza(connector),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/model-plaza", {
          signal,
          searchParams: connector ? { connector } : {},
          retry: 0,
        })
        .json<OkResponse<PlazaData>>(),
    // 目录是本地聚合，不做远程调用；一分钟新鲜度足够，避免切标签反复重算。
    staleTime: 60_000,
    retry: false,
  });
}

/** 单次最小探活：只有 OpenAI 兼容同步端点会真发请求，其余一律 skipped。 */
export function useTestPlazaModel() {
  return useMutation({
    mutationFn: async (modelId: string) =>
      (
        await api
          .post(`api/v1/model-gateway/model-plaza/${encodeURIComponent(modelId)}/test`, {
            retry: 0,
          })
          .json<OkResponse<PlazaProbeResult>>()
      ).data,
  });
}
