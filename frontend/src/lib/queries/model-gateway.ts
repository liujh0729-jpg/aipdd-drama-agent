// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export type GatewayMode = "official" | "custom" | "hybrid";

/** 通用的「端点预览」：服务端只回 key 预览，绝不回完整 key。 */
export interface GatewayEndpointPreview {
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface OfficialGatewayConfig extends GatewayEndpointPreview {
  /** "database" | "env" 等，标识官方配置来源。 */
  source: string;
  /** .env 里的官方默认（回退用）。 */
  environment: GatewayEndpointPreview;
}

export interface CustomGatewayConfig extends GatewayEndpointPreview {
  adminBaseUrl: string;
  tokenName: string;
  tokenId: string;
}

export interface EffectiveGatewayConfig {
  /** "official" | "custom"。 */
  source: string;
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface NewApiDatabaseStatus {
  configured: boolean;
  available?: boolean;
  source: string;
  databaseType?: "sqlite" | "external";
}

export interface SavedProviderChannelConfig {
  provider: string;
  type?: number;
  configured: boolean;
  upstreamKeyPreview: string;
  baseUrl: string;
  priority?: number;
  settings?: Record<string, unknown>;
}

export interface NewApiChannelType {
  type: number;
  provider: string;
  name: string;
  description: string;
  icon: string;
  defaultBaseUrl: string;
  status: number;
  capabilities: string[];
  requiresBaseUrl: boolean;
  supportsBaseUrlOverride: boolean;
}

export interface SavedMediaModelConfig {
  provider: string;
  upstreamModel: string;
  // [aipdd fork delta req46] music：与后端 mediaModels 白名单同步（「默认生成模型」
  // 面板新增的音乐 TAB；调用不经网关，走厂商直连协议）。
  mediaType?: "image" | "video" | "audio" | "music";
  label?: string;
  enabled?: boolean;
  sortOrder?: number;
  config?: Record<string, unknown>;
}

export interface SavedEmbeddingModelConfig {
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
  sendDimensions?: boolean;
  internalModel?: string;
}

// [aipdd fork delta] 保存 VolcEngine 渠道后后端自动拉取 doubao-embedding 的回执。
// found=false 时不阻断渠道保存，仅引导面板手动拉取 + 设为默认。
export interface AutoEmbeddingResult {
  found: boolean;
  applied?: boolean;
  model?: string;
  dimension?: number;
  reason?: string;
  warning?: string;
}

// [aipdd fork delta] 默认模型组合回显（文本 LLM / 图像 / Embedding）。
export interface DefaultModelCombination {
  text: string;
  image: string;
  embedding: string;
}

export interface ModelGatewayProvisionerConfig {
  enabled: boolean;
  adminBaseUrl: string;
  dbConfigured: boolean;
  adminUsername: string;
  relayTokenName: string;
  relayBaseUrl: string;
  providers: Record<string, { label: string; type: number; base_url: string }>;
  providerChannels?: SavedProviderChannelConfig[];
  mediaModels?: Record<string, SavedMediaModelConfig>;
  embeddingModel?: SavedEmbeddingModelConfig;
  // [aipdd fork delta] WP-A: 别名映射单一真源在本项目，网关只是投影。
  aliasMappings?: Record<string, ModelAliasMapping>;
  aliasMappingSource?: "project";
  // [aipdd fork delta] WP-C: 每条别名的主 / 备渠道归属（真冗余，不再同渠道两行伪装）。
  aliasChannelRoles?: Record<string, ModelAliasChannelRole>;
  database?: NewApiDatabaseStatus;
}

// [aipdd fork delta] WP-B: 别名映射一致性自检（只告警不阻断）。
// unmapped=功能位没有别名映射；staleTargets=上游已下架；capabilityMismatch=能力错配。
export interface AliasMappingIssue {
  alias: string;
  provider: string;
  target: string;
  candidates?: string[];
}

export interface AliasMappingHealth {
  status: "ok" | "warning" | "degraded" | "pending" | "unavailable";
  checked: boolean;
  source?: string;
  total?: number;
  unmapped?: string[];
  staleTargets?: AliasMappingIssue[];
  capabilityMismatch?: AliasMappingIssue[];
  noChannel?: string[];
  degraded?: { provider: string; error: string }[];
  aliases?: Record<
    string,
    {
      status: "mapped" | "noChannel" | "stale" | "capabilityMismatch";
      provider: string;
      target: string;
      capability?: string;
    }
  >;
  generatedAt?: string;
}

export interface MediaRelayConfig {
  source: string;
  provider: string;
  ttlSeconds: number;
  endpoint: string;
  bucket: string;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
  cloudName?: string;
  cloudinaryApiKeyPreview?: string;
  cloudinaryApiSecretPreview?: string;
  apiFolder?: string;
  // [aipdd fork delta req44] AIPDD 参考媒体中转回显（「本地存储」与
  // 「AIPDD 数字资产云」两档共用同一份凭据）。
  aipddApiKeyPreview?: string;
  aipddBaseUrl?: string;
  aipddPrefix?: string;
  aipddRegisterAsset?: boolean;
  aipddChannelConfigured?: boolean;
  // [aipdd fork delta] AWS S3 档回显（密钥仅脱敏 preview）。
  s3Region?: string;
  s3Bucket?: string;
  s3EndpointUrl?: string;
  s3AccessKeyIdPreview?: string;
  s3AccessKeySecretPreview?: string;
  s3SessionTokenPreview?: string;
  awsConfigured?: boolean;
  configured: boolean;
}

// [aipdd fork delta] 火山 AI MediaKit 视频高清（生成式超分）配置回显。
export interface VideoEnhanceConfig {
  source: string;
  apiKeyPreview: string;
  configured: boolean;
  baseUrl: string;
  backend: string;
}

// [aipdd fork delta] 剪映草稿目录（VectCut 服务上的草稿存储根目录）配置回显。
// 未配置时 draftDir 为空串、configured=false，调用侧回退 VectCut 部署默认。
export interface VectcutDraftConfig {
  draftDir: string;
  configured: boolean;
}

// [aipdd fork delta] 抖音 Web 登录态 Cookie 状态。Cookie 是完整登录态，后端
// **只回非敏感线索**（条数 / Cookie 名清单 / 长度），永不回 value；提取链路
// 未配置时自动回落宿主 Chrome CDP，所以 configured=false 不是错误态。
// [aipdd fork delta req45] Chrome 兜底走通后会把那份登录态自动写回这里，设置页
// 也提供显式的「从 Chrome 抓取」。
export interface DouyinCookieConfig {
  configured: boolean;
  cookieCount: number;
  cookieNames: string[];
  length: number;
  // [aipdd fork delta req45] 仅「从 Chrome 抓取」接口的响应里出现：本次真的写入了
  // 一条新的登录态（状态接口本身不会有这个字段）。
  captured?: boolean;
}

// [aipdd fork delta] operator 口令（导出提示词等）状态：设置数据库优先，
// 其次 PROMPT_EXPORT_PASSWORD 环境变量兜底；source 仅用于展示来源。
export interface OperatorSecurityConfig {
  promptExportPasswordConfigured: boolean;
  promptExportPasswordPreview: string;
  source: string;
}

export interface ModelGatewayConfig {
  mode: GatewayMode;
  effective: EffectiveGatewayConfig;
  official: OfficialGatewayConfig;
  custom: CustomGatewayConfig;
  provisioner?: ModelGatewayProvisionerConfig;
  mediaRelay?: MediaRelayConfig;
  // [aipdd fork delta] 火山 AI MediaKit 视频高清配置。
  videoEnhance?: VideoEnhanceConfig;
  // [aipdd fork delta] 剪映草稿目录（设置 → 剪映草稿）。
  vectcut?: VectcutDraftConfig;
  // [aipdd fork delta] 抖音文案提取的纯 HTTP 主链路凭据（设置 → 平台 Cookie）。
  douyinCookie?: DouyinCookieConfig;
  // [aipdd fork delta] operator 口令（设置 → 账户安全）。
  operatorSecurity?: OperatorSecurityConfig;
  // [aipdd fork delta] 拆解一体化模型：当前生效的默认模型组合回显。
  defaultCombination?: DefaultModelCombination;
  // [aipdd fork delta] WP-B: 别名映射自检只读快照（后端不在此触发远程调用）。
  aliasMappingHealth?: AliasMappingHealth;
}

export interface OfficialMediaCatalogStatus {
  autoUpdate: boolean;
  source: "bundled" | "remote";
  schemaVersion: number;
  catalogVersion: string;
  modelCount: number;
  lastCheckedAt: string;
  sha256: string;
  revision: string;
  publishedAt: string;
  remoteUrl: string;
  lastError: string;
  updated?: boolean;
}

export interface SaveOfficialConfigInput {
  newApiApiKey: string;
}

export interface NewApiDatabaseConfigInput {
  sqlDsn?: string;
  sqlitePath?: string;
  adminUsername?: string;
}

export interface InitCustomNewApiInput {
  /** 可选；不传则后端用 LOCALAPI_BASE_URL 环境变量。 */
  newApiBaseUrl?: string;
  database?: NewApiDatabaseConfigInput;
  setupUsername?: string;
  setupPassword?: string;
  setupConfirmPassword?: string;
}

export interface NewApiSetupInitStatus {
  initialized: boolean;
  rootInitialized: boolean;
  databaseType: string;
  setupPerformed: boolean;
  alreadyInitialized: boolean;
}

export interface InitCustomNewApiResult {
  mode: "custom";
  newApiAdminBaseUrl: string;
  newApiBaseUrl: string;
  newApiSetup?: NewApiSetupInitStatus;
}

export interface FastApiErrorResponse {
  detail?: unknown;
  error?: unknown;
  message?: unknown;
  ok?: false;
}

/** 一个 NewAPI 渠道：provider + 上游 Key + DC 模型名→上游模型名映射。 */
export interface CustomChannelInput {
  provider: string;
  type?: number;
  /** 渠道名，可选；不填后端自动生成。 */
  name?: string;
  upstreamKey: string;
  /** DC 内部模型名 -> 真实上游模型名。 */
  modelMapping: Record<string, string>;
  group: string;
  priority: number;
  weight: number;
  /** 可选；仅自定义 provider 或覆盖默认地址时填。 */
  baseUrl: string;
  /** 可选；不填后端用 modelMapping 第一个 key。 */
  testModel: string;
  settings?: Record<string, unknown>;
}

export interface SaveProviderChannelsInput {
  preserveUnmentioned?: boolean;
  channels: Array<{
    provider: string;
    type?: number;
    upstreamKey?: string;
    baseUrl?: string;
    priority?: number;
    settings?: Record<string, unknown>;
  }>;
}

export interface SyncProviderChannelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamKey?: string;
  baseUrl?: string;
}

export interface SaveMediaModelsInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
}

export interface SaveMediaRelayConfigInput {
  // [aipdd fork delta] 允许把媒体存储切回本地档位（provider=local）。
  // [aipdd fork delta req44] 新增 aipdd_cloud：数字资产云档；两档都必填 AIPDD API Key
  // （留空时后端回落已连接 AIPDD 渠道自身的 Key）。
  provider: "aliyun_oss" | "aws_s3" | "cloudinary" | "local" | "aipdd_cloud";
  ttlSeconds: number;
  endpoint?: string;
  bucket?: string;
  accessKeyId?: string;
  accessKeySecret?: string;
  cloudName?: string;
  apiKey?: string;
  apiSecret?: string;
  apiFolder?: string;
  aipddApiKey?: string;
  aipddBaseUrl?: string;
  aipddPrefix?: string;
  registerAsset?: boolean;
  // [aipdd fork delta] AWS S3 档（与阿里云 OSS 并行）：留空=沿用已存值（后端合并语义）。
  s3Region?: string;
  s3Bucket?: string;
  s3AccessKeyId?: string;
  s3AccessKeySecret?: string;
  s3EndpointUrl?: string;
  s3SessionToken?: string;
}

// [aipdd fork delta] 保存火山 AI MediaKit 视频高清配置。
export interface SaveVideoEnhanceConfigInput {
  apiKey?: string;
  baseUrl?: string;
  backend?: "auto" | "mediakit" | "ffmpeg";
  // [aipdd fork delta] 渠道目录「移除」动作：显式清除 Key / Base URL。
  clear?: boolean;
}

// [aipdd fork delta] 保存剪映草稿目录；空串 = 取消配置（回 VectCut 部署默认）。
export interface SaveVectcutDraftDirInput {
  draftDir: string;
}

// [aipdd fork delta] 保存抖音 Web 登录态 Cookie；空串 / clear=true = 清除（提取
// 回落宿主 Chrome CDP）。格式不敏感：后端统一归一。
export interface SaveDouyinCookieInput {
  cookie: string;
  clear?: boolean;
}

export interface SaveCustomChannelsBatchInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  channels: CustomChannelInput[];
}

// [aipdd fork delta] WP-A: 模型别名映射真源条目。
export interface ModelAliasMapping {
  provider: string;
  target: string;
  /** [aipdd fork delta] WP-B: 该别名位需要的能力，视觉位必须声明以触发同类校验。 */
  capability?: "text" | "vision";
  fallbackProvider?: string;
  fallbackTarget?: string;
}

// [aipdd fork delta] WP-C: 后端按别名真源算出的主 / 备归属。
export interface ModelAliasChannelRole {
  provider: string;
  target: string;
  capability?: string;
  fallbackProvider?: string;
  fallbackTarget?: string;
  /** true = fallbackProvider 是另一个渠道，网关侧才真有有序回退。 */
  backup?: boolean;
}

export interface SaveAliasMappingsInput {
  newApiBaseUrl?: string;
  database?: NewApiDatabaseConfigInput;
  /** alias -> 条目；也兼容 alias -> 目标模型名的简写（需带 defaultProvider）。 */
  mappings: Record<string, ModelAliasMapping | string>;
  /** true=并入既有真源（默认），false=整体替换。 */
  merge?: boolean;
  defaultProvider?: string;
  /** [aipdd fork delta] WP-B: 整批别名条目的默认能力位（条目自带 capability 优先）。 */
  capability?: "text" | "vision";
  /** [aipdd fork delta] WP-B: 落库前按上游真实清单校验目标存在性/能力，默认 true。 */
  validate?: boolean;
  /** 落库后是否立即把真源投影到网关渠道，默认 true。 */
  sync?: boolean;
}

export interface AliasMappingProjectionResult {
  provider: string;
  ok: boolean;
  skipped?: string;
  models?: number;
  action?: string;
  channelId?: number | string;
  httpStatus?: number;
  error?: string;
}

export interface AliasMappingsResult {
  source: "project";
  mappings: Record<string, ModelAliasMapping>;
  projection?: Record<string, Record<string, string>> | AliasMappingProjectionResult[];
  projectionFailed?: AliasMappingProjectionResult[];
  health?: AliasMappingHealth;
}

export interface CustomChannelWriteResult {
  provider?: string;
  name?: string;
  ok?: boolean;
  channelId?: number | string;
  error?: string;
  /** 后端已 mask，不含完整 key。 */
  upstreamKey?: string;
  [key: string]: unknown;
}

export interface SaveCustomChannelsBatchResult {
  succeeded: number;
  failed: number;
  results: CustomChannelWriteResult[];
}

export interface SyncProviderChannelResult {
  provider: string;
  channelId?: number | string;
  httpStatus?: number;
  savedChannel?: SavedProviderChannelConfig | null;
  sentPayload?: unknown;
  newApiResponse?: unknown;
}

export interface SaveMediaModelsResult extends SaveCustomChannelsBatchResult {
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelResult {
  embeddingModel: SavedEmbeddingModelConfig;
  result: CustomChannelWriteResult;
}

export function useModelGatewayConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelGateway(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/config", { signal })
        .json<OkResponse<ModelGatewayConfig>>(),
    enabled,
  });
}

const officialMediaCatalogQueryKey = [
  ...queryKeys.modelGateway(),
  "official-media-catalog",
] as const;
const officialMediaCatalogStatusPollMs = 60_000;

function fetchOfficialMediaCatalogStatus(signal?: AbortSignal) {
  return api
    .get("api/v1/model-gateway/official/media-catalog", { signal })
    .json<OkResponse<OfficialMediaCatalogStatus>>();
}

export function useOfficialMediaCatalogStatus(enabled = true) {
  return useQuery({
    queryKey: officialMediaCatalogQueryKey,
    queryFn: ({ signal }) => fetchOfficialMediaCatalogStatus(signal),
    enabled,
  });
}

export function useOfficialMediaCatalogWatcher(enabled = true) {
  const previousSha256 = useRef<string | null>(null);
  const query = useQuery({
    queryKey: officialMediaCatalogQueryKey,
    queryFn: ({ signal }) => fetchOfficialMediaCatalogStatus(signal),
    enabled,
    refetchInterval: (current) =>
      current.state.data?.data.autoUpdate === false
        ? false
        : officialMediaCatalogStatusPollMs,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
  const sha256 = query.data?.data.sha256.trim() ?? "";

  useEffect(() => {
    if (!sha256) return;
    const previous = previousSha256.current;
    previousSha256.current = sha256;
    if (previous && previous !== sha256 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("media-model-catalog-updated"));
    }
  }, [sha256]);

  return query;
}

export function useSaveOfficialMediaCatalogPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (autoUpdate: boolean) =>
      api
        .post("api/v1/model-gateway/official/media-catalog/preferences", {
          json: { autoUpdate },
          throwHttpErrors: false,
        })
        .json<OkResponse<OfficialMediaCatalogStatus> | ErrorResponse>(),
    onSuccess: (response) => {
      if (response.ok) {
        qc.setQueryData(officialMediaCatalogQueryKey, response);
      } else {
        qc.invalidateQueries({ queryKey: officialMediaCatalogQueryKey });
      }
    },
  });
}

export function useCheckOfficialMediaCatalog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/official/media-catalog/check", {
          timeout: 20_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<OfficialMediaCatalogStatus> | ErrorResponse>(),
    onSuccess: (response) => {
      if (response.ok) {
        qc.setQueryData(officialMediaCatalogQueryKey, response);
      } else {
        qc.invalidateQueries({ queryKey: officialMediaCatalogQueryKey });
      }
    },
  });
}

export function useNewApiChannelTypes(enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.modelGateway(), "channel-types"],
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/custom/newapi/channel-types", {
          signal,
          throwHttpErrors: false,
        })
        .json<OkResponse<{ items: NewApiChannelType[] }> | ErrorResponse>(),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useSaveOfficialConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveOfficialConfigInput) =>
      api
        .post("api/v1/model-gateway/official/config", { json: input })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useEnableOfficial() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/official/enable")
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useEnableCustom() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/custom/enable", { throwHttpErrors: false })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useEnableHybrid() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/hybrid/enable", { throwHttpErrors: false })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useInitCustomNewApi() {
  const qc = useQueryClient();
  return useMutation({
    // 初始化要连 NewAPI、建 token、写库，耗时较长，放宽超时。
    // 后端 provisioner 的 wait_for_db / wait_for_newapi / wait_for_admin_user 是串联的，
    // 各自按 NEWAPI_PROVISIONER_INIT_TIMEOUT_MS（默认 120s）重试；前端预算必须盖住
    // 这个最坏窗口，否则网关冷启动时先在前端超时（ky 60s 会抛 Request timed out），
    // 而后端还在后台把 init 跑完，界面却停在失败态。
    mutationFn: (input: InitCustomNewApiInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/init", {
          json: input,
          timeout: 300_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<InitCustomNewApiResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}


/** 保存供应商渠道级配置。 */
export function useSaveProviderChannels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveProviderChannelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channels", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<
          | OkResponse<{
              channels: SavedProviderChannelConfig[];
              mediaModels: Record<string, SavedMediaModelConfig>;
              embeddingModel: SavedEmbeddingModelConfig | null;
              // [aipdd fork delta] 保存 VolcEngine 渠道后后端自动拉取并设默认
              // doubao-embedding 的结果；未拉取 / 未命中时为 null。失败不阻断渠道保存。
              autoEmbedding?: AutoEmbeddingResult | null;
            }>
          | ErrorResponse
          | FastApiErrorResponse
        >(),
    onSuccess: async (response) => {
      if (response.ok === true) {
        await qc.cancelQueries({ queryKey: queryKeys.modelGateway() });
        qc.setQueryData<OkResponse<ModelGatewayConfig>>(
          queryKeys.modelGateway(),
          (current) =>
            current
              ? {
                  ...current,
                  data: {
                    ...current.data,
                    provisioner: current.data.provisioner
                      ? {
                          ...current.data.provisioner,
                          providerChannels: response.data.channels,
                          mediaModels: response.data.mediaModels,
                          embeddingModel:
                            response.data.embeddingModel ?? undefined,
                        }
                      : current.data.provisioner,
                  },
                }
              : current,
        );
      }
      if (response.ok !== true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}

/** 删除 CE 本地及 NewAPI 中的 ComfyUI 渠道和媒体模型映射。 */
export function useClearComfyUIConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .delete("api/v1/model-gateway/custom/newapi/comfyui", {
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<unknown> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (response) => {
      if (response.ok !== true) return;
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      window.dispatchEvent(new Event("media-model-catalog-updated"));
    },
  });
}

/** 更新 NewAPI 中已存在的供应商渠道 key / Base URL，不改模型映射。 */
export function useSyncProviderChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SyncProviderChannelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channel/sync", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<SyncProviderChannelResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}

/** [aipdd fork delta] 拉取上游 OpenAI 兼容网关的模型清单（用已存的 base+key）。
 *  `kind` 给定时后端会拿实测缓存收敛候选（text 只摘 not-granted，vision 再加
 *  not-vision），拿不准的名字照旧保留。 */
export function useFetchUpstreamModels() {
  return useMutation({
    mutationFn: (input: {
      provider: string;
      baseUrl?: string;
      upstreamKey?: string;
      kind?: "text" | "vision";
    }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/upstream-models", {
          json: input,
          timeout: 40_000,
          throwHttpErrors: false,
        })
        .json<
          | OkResponse<{
              provider: string;
              baseUrl: string;
              models: string[];
              count: number;
              probeKind?: string;
              droppedNotGranted?: number;
            }>
          | ErrorResponse
          | FastApiErrorResponse
        >(),
  });
}

/** [aipdd fork delta req40] 渠道目录条目：渠道在 ``/v1/models`` 里自己声明的事实。
 *
 * ``inferred`` 为真表示这一条**没有**元数据，kind / 端点是按模型名推断出来的 —— UI 必须
 * 标出来，否则用户会把猜的当成渠道保证的。平台不再预先知道渠道里有什么模型。 */
export interface UpstreamCatalogEntry {
  name: string;
  displayName?: string;
  kind?: string;
  mediaType?: string;
  endpoints?: string[];
  protocols?: string[];
  operations?: string[];
  inputModalities?: string[];
  outputModalities?: string[];
  dimensions?: number | null;
  available?: boolean;
  inferred?: boolean;
  mediaMetadata?: Record<string, unknown>;
}

export interface ProviderCatalogBlock {
  provider: string;
  baseUrl: string;
  fetchedAt: string;
  entries: UpstreamCatalogEntry[];
}

export interface ProviderCatalogMeta {
  baseUrl?: string;
  fetchedAt?: string;
  count?: number;
}

export interface ProviderCatalogResult {
  providers: ProviderCatalogBlock[];
  meta: Record<string, ProviderCatalogMeta>;
}

/** 目录的只读视图：零远程调用（只读快照），「模型」Tab 首屏可以直接拉。 */
export function useProviderCatalog(enabled = true) {
  const catalogKey = [...queryKeys.modelGateway(), "provider-catalog"] as const;
  return useQuery({
    queryKey: catalogKey,
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/custom/newapi/provider-catalog", { signal })
        .json<OkResponse<ProviderCatalogResult>>(),
    enabled,
  });
}

export interface SyncProviderCatalogResult {
  provider: string;
  count: number;
  models: string[];
  catalog: UpstreamCatalogEntry[];
  /** ``{槽位: [被取消的模型名]}``：目录里没有的名字，其默认绑定已被自动取消。 */
  pruned: Record<string, string[]>;
  projection: Record<string, unknown>;
}

/** 「用户动设置 = 拉清单」的写入口：拉取 → 按渠道整体覆盖目录 → 剪掉指向已下架模型的
 *  默认绑定 → 重投影网关注册。失败时后端不动快照（一次抖动不该取消用户全部默认）。 */
export function useSyncProviderCatalog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { provider: string; force?: boolean }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-catalog/sync", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<
          OkResponse<SyncProviderCatalogResult> | ErrorResponse | FastApiErrorResponse
        >(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      qc.invalidateQueries({
        queryKey: [...queryKeys.modelGateway(), "provider-catalog"],
      });
    },
  });
}

/** [aipdd fork delta] 本地 ComfyUI：按「模型名 + 工作流名称」让后端去 ComfyUI
 * 拉取并转换为 API 格式。返回 workflowByModel（model -> API JSON）与逐项 errors。 */
export function useResolveComfyUIWorkflows() {
  return useMutation({
    mutationFn: (input: {
      baseUrl: string;
      items: Array<{ model: string; workflowName: string }>;
    }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/comfyui/resolve", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<
          | OkResponse<{
              baseUrl: string;
              workflowByModel: Record<string, unknown>;
              resolved: string[];
              errors: Array<{
                model: string;
                workflowName: string;
                error: string;
              }>;
            }>
          | ErrorResponse
          | FastApiErrorResponse
        >(),
  });
}

/** [aipdd fork delta] 「AI分析并自动配置」的返回：适配文件指针 + 能力声明 +
 *  可直接写入渠道的 workflowByModel / 工作流名 / 媒体类型。 */
export interface ComfyUIAdaptResult {
  baseUrl: string;
  /** 本次真正用的分析模型（卡片显式选的，或后端解析的文本功能位）。 */
  analyzerModel: string;
  workflowByModel: Record<string, unknown>;
  workflowNameByModel: Record<string, string>;
  typeByModel: Record<string, string>;
  adaptationFile: string;
  adaptationPointers: Record<string, unknown>;
  capabilitiesByModel: Record<string, Record<string, unknown>>;
  adapted: string[];
  errors: Array<{ model: string; workflowName: string; error: string }>;
}

/**
 * [aipdd fork delta] 本地 ComfyUI「AI分析并自动配置」：后端按名称取回工作流，交给
 * 已配置的文本模型分析出节点绑定与真实能力，落适配文件并回指针表。逐工作流一次真实
 * 调用，超时给足（后端单份上限 180s）。
 */
export function useAdaptComfyUIWorkflows() {
  return useMutation({
    mutationFn: (input: {
      baseUrl: string;
      items: Array<{ model: string; workflowName: string }>;
      textModel?: string;
    }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/comfyui/ai-adapt", {
          json: input,
          timeout: 600_000,
          throwHttpErrors: false,
        })
        .json<
          | OkResponse<ComfyUIAdaptResult>
          | ErrorResponse
          | FastApiErrorResponse
        >(),
  });
}

/** 写入单个 NewAPI 渠道。 */
export function useSaveCustomChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CustomChannelInput & { newApiBaseUrl: string; database?: NewApiDatabaseConfigInput }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<CustomChannelWriteResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存图片 / 视频固定模型映射。 */
export function useSaveMediaModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaModelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/media-models", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveMediaModelsResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** [aipdd fork delta] 仅写入本地媒体模型映射（mediaType/sortOrder/enabled），
 * 不重复 upsert NewAPI 渠道。供「默认生成模型」面板一键设默认使用，避免
 * /media-models 整批渠道 upsert 中任一失败就导致默认不生效。 */
export function useSaveMediaModelMappings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaModelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/media-model-mappings", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<SaveMediaModelsResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 Cognee embedding 模型映射；维度只保存到 CE 本地配置。 */
export function useSaveEmbeddingModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveEmbeddingModelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/embedding-model", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveEmbeddingModelResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 NewAPI 参考媒体 relay 配置。 */
export function useSaveMediaRelayConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaRelayConfigInput) =>
      api
        .post("api/v1/model-gateway/media-relay/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<MediaRelayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** [aipdd fork delta] 保存火山 AI MediaKit 视频高清（生成式超分）配置。 */
export function useSaveVideoEnhanceConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveVideoEnhanceConfigInput) =>
      api
        .post("api/v1/model-gateway/video-enhance/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<VideoEnhanceConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** [aipdd fork delta] 保存剪映草稿目录（设置 → 剪映草稿）。 */
export function useSaveVectcutDraftDir() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveVectcutDraftDirInput) =>
      api
        .post("api/v1/model-gateway/vectcut-draft/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<VectcutDraftConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** [aipdd fork delta] 保存 / 清除抖音 Web 登录态 Cookie（设置 → 平台 Cookie）。
 * 后端只回非敏感状态（条数 / Cookie 名），响应里不会有 Cookie 原文。 */
export function useSaveDouyinCookie() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveDouyinCookieInput) =>
      api
        .post("api/v1/model-gateway/douyin-cookie/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<DouyinCookieConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** [aipdd fork delta req45] 从已登录抖音的宿主 Chrome 抓取登录态并保存。
 * 抓不到时后端回 400 + 可操作成因（没开调试端口 / 那个 Chrome 未登录抖音），
 * 直接把 message 贴给用户就是最好的指引。成功同样只回脱敏状态。 */
export function useCaptureDouyinCookie() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/douyin-cookie/capture", {
          json: {},
          timeout: 90_000,
        })
        .json<OkResponse<DouyinCookieConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

// [aipdd fork delta] 保存 operator 口令到设置数据库；空串 = 清空（回 env 兜底），
// 传 undefined 视为不改动。
export interface SaveOperatorSecurityInput {
  promptExportPassword?: string;
}

export function useSaveOperatorSecurityConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveOperatorSecurityInput) =>
      api
        .post("api/v1/model-gateway/operator-security/config", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<OperatorSecurityConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 批量写入 NewAPI 渠道（功能模型映射保存）。后端支持部分成功。 */
export function useSaveCustomChannelsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveCustomChannelsBatchInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels/batch", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveCustomChannelsBatchResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

// [aipdd fork delta] WP-A: 读取别名映射单一真源（含当前投影）。
export function useModelAliasMappings(enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.modelGateway(), "alias-mappings"],
    queryFn: async () => {
      const res = await api
        .get("api/v1/model-gateway/custom/newapi/alias-mappings")
        .json<OkResponse<AliasMappingsResult> | ErrorResponse>();
      if ("detail" in res) throw new Error(String(res.detail));
      return ("data" in res ? res.data : undefined) as AliasMappingsResult | undefined;
    },
    enabled,
    staleTime: 30_000,
  });
}

/**
 * [aipdd fork delta] WP-A: 写入模型别名映射真源（文本/视觉「设为默认」的正路）。
 * 后端先落自有存储，再把真源编译成 model_mapping 投影到网关渠道。
 */
export function useSaveModelAliasMappings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveAliasMappingsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/alias-mappings", {
          json: { merge: true, sync: true, ...input },
          timeout: 120_000,
        })
        .json<OkResponse<AliasMappingsResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/**
 * [aipdd fork delta] WP-B: 手动跑一次别名映射一致性自检（会拉上游清单，故 60s 超时）。
 * 只告警不阻断：返回体里的三类问题用于面板横幅，不影响已落库的真源。
 */
export function useCheckAliasMappingHealth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await api
        .get("api/v1/model-gateway/custom/newapi/alias-mappings/health", {
          searchParams: { refresh: "true" },
          timeout: 60_000,
        })
        .json<OkResponse<AliasMappingHealth> | ErrorResponse>();
      if ("detail" in res) throw new Error(String(res.detail));
      return ("data" in res ? res.data : undefined) as
        | AliasMappingHealth
        | undefined;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

// --------------------------------------------------------------------------- //
// [aipdd fork delta] 一键配齐：在创建剧集处选一次模型，铺成整链要用的全部功能位绑定。
// --------------------------------------------------------------------------- //

/** 运行前收集的三个模型（全部可选；空 = 那一类不动）。 */
export interface ProvisionFeatureRoutesInput {
  /** 文本模型调用名（``{provider}::{model}``，候选来自 /model-gateway/text-models）。 */
  textModel?: string;
  /** 视觉理解模型（选填；留空由后端按“选的文本模型能读图就沿用 → 否则从目录挑”）。 */
  visionModel?: string;
  /** 图片模型（「默认生成模型」目录条目 id）。 */
  imageModel?: string;
  /** 视频模型（同上，mediaType=video）。 */
  videoModel?: string;
  sync?: boolean;
}

/** 一类功能位（或一个媒体默认）的配齐结果。 */
export interface ProvisionSlotRow {
  model: string;
  provider?: string;
  target?: string;
  /** 视觉位的来源：explicit（用户选的）/ same-as-text / catalog（系统替挑的）。 */
  source?: string;
  slots?: string[];
  bound: number;
  /** 只填缺口时保留下未动的个数（系统替挑的名字不抢用户手动绑好的位）。 */
  preserved?: number;
  hint?: string;
  ok?: boolean;
}

export interface ProvisionFeatureRoutesResult {
  text?: ProvisionSlotRow | null;
  vision?: ProvisionSlotRow | null;
  image?: ProvisionSlotRow | null;
  video?: ProvisionSlotRow | null;
  hints: string[];
  touched: string[];
  projectionFailed?: unknown[];
  projectionError?: string;
}

/**
 * [aipdd fork delta] 把一次选择写进两个真源（功能位绑定表 + 媒体默认），随后投影到网关。
 *
 * 为什么要前端主动调：出厂状态是 22 个文本位 + 4 个视觉位全空，运行期兜底能接住但用户
 * 看不见自己在用什么，也永远有人撞上「未绑定可用模型」。这一笔是幂等的（只写选中的那些）。
 */
export function useProvisionFeatureRoutes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ProvisionFeatureRoutesInput) => {
      const res = await api
        .post("api/v1/model-gateway/custom/newapi/feature-routes/provision", {
          json: { sync: true, ...input },
          timeout: 120_000,
        })
        .json<OkResponse<ProvisionFeatureRoutesResult> | ErrorResponse>();
      if ("detail" in res) throw new Error(String(res.detail));
      return ("data" in res ? res.data : undefined) as
        | ProvisionFeatureRoutesResult
        | undefined;
    },
    onSuccess: () => {
      // 绑定表与媒体目录都变了：广场 / 功能位 / 三个模型候选都得重取。
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      window.dispatchEvent(new CustomEvent("media-model-catalog-updated"));
    },
  });
}
