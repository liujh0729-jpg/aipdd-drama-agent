// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：功能模型映射块（含 Cognee / Embedding / 能力分组行）。

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ChevronDown, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  FEATURE_MODEL_GROUPS,
  FEATURE_MODEL_PRODUCT_GROUPS,
  type FeatureModelDef,
  type FeatureModelGroup,
} from "@/lib/feature-models";
import {
  useNewApiChannelTypes,
  useSaveCustomChannel,
  useSaveCustomChannelsBatch,
  useSaveEmbeddingModel,
  useSaveModelAliasMappings,
  useFetchUpstreamModels,
  type CustomChannelInput,
  type ModelAliasMapping,
  type NewApiDatabaseConfigInput,
  type NewApiChannelType,
  type SavedEmbeddingModelConfig,
  type SavedMediaModelConfig,
  type SavedProviderChannelConfig,
} from "@/lib/queries/model-gateway";
import {
  useSettingsStore,
  DEFAULT_FEATURE_MODEL_PROVIDER,
  normalizeMediaModelEntries,
  type EmbeddingModelEntry,
  type FeatureModelProvider,
} from "@/stores/settingsStore";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  featureProviderLabel,
  FEATURE_ROW_GRID,
  TEXT_DEFAULT_KEY,
  VISION_DEFAULT_KEY,
  splitFeatureModelGroups,
  DEFAULT_EMBEDDING_DIMENSION,
  DEFAULT_EMBEDDING_BATCH_SIZE,
  customProviderChannelType,
  featureModelBackendSnapshotKey,
  normalizeProviderChannelSettings,
  providersSupportingCapability,
} from "./shared";
import { MediaModelsBlock } from "./media-models-block";
import { ProviderChannelsBlock } from "./provider-channels-block";

function FeatureModelsBlock({
  newApiBaseUrl,
  database,
  savedProviderChannels,
  savedEmbeddingModel,
  savedMediaModels,
  backendSnapshotLoaded,
  mediaOnly = false,
  comfyOnly = false,
  excludeComfyUI = false,
  defaultComfyWorkflows,
  channelTypesEnabled = true,
}: {
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedProviderChannels: SavedProviderChannelConfig[];
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
  savedMediaModels: Record<string, SavedMediaModelConfig> | undefined;
  backendSnapshotLoaded: boolean;
  mediaOnly?: boolean;
  comfyOnly?: boolean;
  excludeComfyUI?: boolean;
  defaultComfyWorkflows?: {
    model: string;
    workflows: Record<string, Record<string, unknown>>;
  };
  channelTypesEnabled?: boolean;
}) {
  const { t } = useTranslation();
  const featureModels = useSettingsStore(
    (s) => s.featureModelConfig.featureModels,
  );
  const providerChannels = useSettingsStore(
    (s) => s.featureModelConfig.providerChannels,
  );
  const channelTypesQuery = useNewApiChannelTypes(channelTypesEnabled);
  const channelTypesLoadFailed =
    channelTypesQuery.isError ||
    (channelTypesQuery.data !== undefined &&
      channelTypesQuery.data.ok !== true);
  const channelTypeByProvider = useMemo(
    () =>
      new Map(
        channelTypesQuery.data?.ok
          ? channelTypesQuery.data.data.items.map(
              (item) => [item.provider, item] as const,
            )
          : [],
      ),
    [channelTypesQuery.data],
  );
  const saveBatch = useSaveCustomChannelsBatch();
  const saveAliases = useSaveModelAliasMappings();
  const hydrateFeatureModelConfigFromBackend = useSettingsStore(
    (s) => s.hydrateFeatureModelConfigFromBackend,
  );
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);

  const configuredProviders = useMemo(
    () =>
      Object.keys(providerChannels)
        .filter(
          (provider) =>
            (!comfyOnly || provider === "comfyui") &&
            (!excludeComfyUI || provider !== "comfyui"),
        )
        .sort(),
    [comfyOnly, excludeComfyUI, providerChannels],
  );
  const textFeatureGroups = useMemo(
    () =>
      splitFeatureModelGroups(
        FEATURE_MODEL_PRODUCT_GROUPS,
        (feature) => !feature.requiresVision && feature.id !== "COGNEE",
      ),
    [],
  );
  const visionFeatureGroups = useMemo(
    () =>
      splitFeatureModelGroups(FEATURE_MODEL_PRODUCT_GROUPS, (feature) =>
        Boolean(feature.requiresVision),
      ),
    [],
  );
  const savedChannelByProvider = useMemo(() => {
    return new Map(
      savedProviderChannels.map((channel) => [channel.provider, channel]),
    );
  }, [savedProviderChannels]);

  const backendSnapshotKey = featureModelBackendSnapshotKey(
    savedProviderChannels,
    savedMediaModels ?? {},
    savedEmbeddingModel,
  );
  const lastHydratedBackendSnapshotKey = useRef("");
  useEffect(() => {
    if (!backendSnapshotLoaded) return;
    if (lastHydratedBackendSnapshotKey.current === backendSnapshotKey) return;
    lastHydratedBackendSnapshotKey.current = backendSnapshotKey;

    const current = useSettingsStore.getState().featureModelConfig;
    const providerChannels = Object.fromEntries(
      savedProviderChannels.map((channel) => {
        const provider = channel.provider.trim().toLowerCase();
        const currentChannel = current.providerChannels[provider];
        return [
          provider,
          {
            provider,
            upstreamKey: currentChannel?.upstreamKey ?? "",
            baseUrl: channel.baseUrl ?? "",
            priority: channel.priority ?? 0,
            settings: normalizeProviderChannelSettings(
              provider,
              channel.settings ?? {},
            ),
          },
        ];
      }),
    );
    const providerKeys = Object.fromEntries(
      Object.entries(providerChannels).flatMap(([provider, channel]) =>
        channel.upstreamKey ? [[provider, channel.upstreamKey]] : [],
      ),
    );
    const mediaModels = normalizeMediaModelEntries(
      Object.fromEntries(
        Object.entries(savedMediaModels ?? {}).map(([model, entry]) => [
          model,
          {
            provider: entry.provider,
            upstreamModel: entry.upstreamModel,
            ...(entry.mediaType ? { mediaType: entry.mediaType } : {}),
            ...(entry.label ? { label: entry.label } : {}),
            enabled: entry.enabled !== false,
            sortOrder: entry.sortOrder ?? 100,
            config: entry.config ?? {},
          },
        ]),
      ),
    );
    const savedProvider = savedEmbeddingModel?.provider?.trim() ?? "";
    const savedUpstreamModel = savedEmbeddingModel?.upstreamModel?.trim() ?? "";
    const savedDimension = Number(savedEmbeddingModel?.dimension);
    const embeddingModel =
      savedProvider &&
      savedUpstreamModel &&
      Number.isFinite(savedDimension) &&
      savedDimension > 0
        ? {
            provider: savedProvider,
            upstreamModel: savedUpstreamModel,
            dimension: savedDimension,
            batchSize: savedEmbeddingModel?.batchSize,
          }
        : undefined;
    hydrateFeatureModelConfigFromBackend(
      { providerChannels, providerKeys, mediaModels, embeddingModel },
      backendSnapshotKey,
    );
  }, [
    backendSnapshotKey,
    backendSnapshotLoaded,
    hydrateFeatureModelConfigFromBackend,
    savedEmbeddingModel,
    savedMediaModels,
    savedProviderChannels,
  ]);

  // [aipdd fork delta] 区默认（文本/视觉）的功能覆盖面：与「默认生成模型」面板
  // 一致——文本默认覆盖全部非视觉功能（含 COGNEE），视觉默认只覆盖 requiresVision。
  const defaultFeatureSections = useMemo(
    () => [
      {
        key: TEXT_DEFAULT_KEY,
        capability: "text" as const,
        features: FEATURE_MODEL_GROUPS.flatMap((group) => group.features).filter(
          (feature) => !feature.requiresVision,
        ),
      },
      {
        key: VISION_DEFAULT_KEY,
        capability: "vision" as const,
        features: FEATURE_MODEL_GROUPS.flatMap((group) => group.features).filter(
          (feature) => Boolean(feature.requiresVision),
        ),
      },
    ],
    [],
  );
  // 「应用到全部 / 历史收编」作用的是区块内可见的行（文本区不含 COGNEE 行）。
  const sectionRows = useMemo(
    () => [
      {
        key: TEXT_DEFAULT_KEY,
        features: textFeatureGroups.flatMap((group) => group.features),
      },
      {
        key: VISION_DEFAULT_KEY,
        features: visionFeatureGroups.flatMap((group) => group.features),
      },
    ],
    [textFeatureGroups, visionFeatureGroups],
  );

  // [aipdd fork delta] 历史数据收编：旧版「应用到全部」把同一模型写死进每个功能
  // 行，重开后各行仍是「已单独配置」而清不掉。若某区可见行都指向同一模型且该区
  // 尚无默认标记，折算为该区默认并清空各行（幂等：有标记即跳过）。
  useEffect(() => {
    if (mediaOnly) return;
    for (const section of sectionRows) {
      if (featureModels[section.key]) continue;
      const first = featureModels[section.features[0]?.id ?? ""];
      if (!first || !first.model.trim()) continue;
      const shared = { provider: first.provider, model: first.model.trim() };
      const uniform = section.features.every((feature) => {
        const entry = featureModels[feature.id];
        return (
          entry !== undefined &&
          entry.provider === shared.provider &&
          entry.model.trim() === shared.model
        );
      });
      if (!uniform) continue;
      const clearIds = section.features.map((feature) => feature.id);
      // COGNEE 行独立成块但同属文本理解：指向同一模型时一并折算进文本默认。
      if (section.key === TEXT_DEFAULT_KEY) {
        const cognee = featureModels["COGNEE"];
        if (
          cognee !== undefined &&
          cognee.provider === shared.provider &&
          cognee.model.trim() === shared.model
        ) {
          clearIds.push("COGNEE");
        }
      }
      for (const featureId of clearIds) {
        updateFeatureModel(featureId, {
          provider: DEFAULT_FEATURE_MODEL_PROVIDER,
          model: "",
        });
      }
      updateFeatureModel(section.key, shared);
    }
  }, [featureModels, mediaOnly, sectionRows, updateFeatureModel]);

  // 把功能行按 provider 分组拼成渠道：modelMapping = { DC内部模型名: 上游模型名 }。
  const buildChannels = (): CustomChannelInput[] => {
    const byProvider = new Map<FeatureModelProvider, Record<string, string>>();
    for (const group of FEATURE_MODEL_GROUPS) {
      for (const feature of group.features) {
        const entry = featureModels[feature.id];
        if (!entry || !entry.model.trim() || !providerChannels[entry.provider])
          continue;
        const mapping = byProvider.get(entry.provider) ?? {};
        mapping[feature.defaultModel] = entry.model.trim();
        byProvider.set(entry.provider, mapping);
      }
    }
    return [...byProvider.entries()].map(([provider, modelMapping]) => {
      const channel = providerChannels[provider];
      return {
        provider,
        type:
          channelTypeByProvider.get(provider)?.type ||
          savedChannelByProvider.get(provider)?.type ||
          customProviderChannelType(provider),
        upstreamKey: (channel?.upstreamKey ?? "").trim(),
        modelMapping,
        group: "default",
        priority: channel?.priority ?? 0,
        weight: 0,
        baseUrl: (channel?.baseUrl ?? "").trim(),
        testModel: "",
        settings: normalizeProviderChannelSettings(
          provider,
          channel?.settings ?? {},
        ),
      };
    });
  };

  const handleSave = async () => {
    if (configuredProviders.length === 0) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    const channels = buildChannels();
    // [aipdd fork delta] 区默认折算成别名真源条目：只为「未单独配置」的功能写入
    // 默认目标；有本地草稿的功能由渠道批量写入覆盖，避免互相盖写。
    const defaultAliases: Record<string, ModelAliasMapping> = {};
    for (const section of defaultFeatureSections) {
      const marker = featureModels[section.key];
      const provider = marker?.provider?.trim() ?? "";
      const model = marker?.model?.trim() ?? "";
      if (!provider || !model || !providerChannels[provider]) continue;
      for (const feature of section.features) {
        const entry = featureModels[feature.id];
        if (entry?.model.trim()) continue;
        defaultAliases[feature.defaultModel] = {
          provider,
          target: model,
          capability: section.capability,
        };
      }
    }
    const hasDefaults = Object.keys(defaultAliases).length > 0;
    if (channels.length === 0 && !hasDefaults) {
      toast.error(t("settings.modelConfig.featureModels.noMappings"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const missing = channels
      .filter((c) => {
        if (c.provider === "comfyui") return false;
        if (c.upstreamKey) return false;
        return !savedChannelByProvider.get(c.provider)?.configured;
      })
      .map((c) => featureProviderLabel(c.provider, channelTypeByProvider));
    if (missing.length > 0) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: missing.join("、"),
        }),
      );
      return;
    }
    try {
      if (hasDefaults) {
        // 先落别名真源（各区默认），后端落库后重投影到对应渠道。
        const aliasRes = await saveAliases.mutateAsync({
          newApiBaseUrl: newApiBaseUrl.trim(),
          ...(database ? { database } : {}),
          mappings: defaultAliases,
        });
        if (aliasRes.ok !== true) {
          toast.error(
            getResponseErrorMessage(
              aliasRes,
              t("settings.modelConfig.requestFailed"),
            ),
          );
          return;
        }
      }
      if (channels.length === 0) {
        toast.success(t("settings.modelConfig.featureModels.defaultsSaved"));
        return;
      }
      const res = await saveBatch.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        channels,
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      const { succeeded, failed } = res.data;
      if (failed > 0) {
        toast.warning(
          t("settings.modelConfig.featureModels.savedPartial", {
            succeeded,
            failed,
          }),
        );
      } else {
        toast.success(
          t("settings.modelConfig.featureModels.saved", { count: succeeded }),
        );
      }
    } catch {
      toast.error(t("settings.modelConfig.requestFailed"));
    }
  };

  return (
    <>
      <ProviderChannelsBlock
        savedProviderChannels={savedProviderChannels}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        channelTypes={
          channelTypesQuery.data?.ok ? channelTypesQuery.data.data.items : []
        }
        channelTypesLoading={channelTypesQuery.isFetching}
        channelTypesLoadFailed={channelTypesLoadFailed}
        onRetryChannelTypes={() => void channelTypesQuery.refetch()}
        allowedProviders={comfyOnly ? ["comfyui"] : undefined}
        excludedProviders={excludeComfyUI ? ["comfyui"] : undefined}
        defaultComfyWorkflows={defaultComfyWorkflows}
      />

      {!mediaOnly ? (
        <CogneeModelsBlock
          configuredProviders={configuredProviders}
          channelTypeByProvider={channelTypeByProvider}
          newApiBaseUrl={newApiBaseUrl}
          database={database}
          providerChannels={providerChannels}
          savedChannelByProvider={savedChannelByProvider}
          savedEmbeddingModel={savedEmbeddingModel}
          backendSnapshotLoaded={backendSnapshotLoaded}
        />
      ) : null}

      {/* 功能模型映射 */}
      {!mediaOnly ? (
        <>
          <h4 className="mt-5 text-xs font-medium text-foreground">
            {t("settings.modelConfig.featureModels.title")}
          </h4>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.featureModels.description")}
          </p>

          <FeatureModelCapabilitySection
            title={t("settings.modelConfig.featureModels.textModelsTitle")}
            groups={textFeatureGroups}
            newApiBaseUrl={newApiBaseUrl}
            database={database}
            configuredProviders={configuredProviders}
            channelTypeByProvider={channelTypeByProvider}
            capability="text"
            providerChannels={providerChannels}
            savedChannelByProvider={savedChannelByProvider}
          />

          <FeatureModelCapabilitySection
            title={t(
              "settings.modelConfig.featureModels.multimodalModelsTitle",
            )}
            hint={t("settings.modelConfig.featureModels.visionRequiredHint")}
            groups={visionFeatureGroups}
            newApiBaseUrl={newApiBaseUrl}
            database={database}
            configuredProviders={configuredProviders}
            channelTypeByProvider={channelTypeByProvider}
            capability="vision"
            providerChannels={providerChannels}
            savedChannelByProvider={savedChannelByProvider}
          />

          <div className="mt-3 flex items-center justify-end gap-3">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t("settings.modelConfig.featureModels.saveHint")}
            </p>
            <Button
              type="button"
              size="sm"
              className="shrink-0"
              onClick={handleSave}
              disabled={saveBatch.isPending || saveAliases.isPending}
            >
              {saveBatch.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              {t("settings.modelConfig.featureModels.save")}
            </Button>
          </div>
        </>
      ) : null}

      {/* [aipdd fork delta] 自定义模式下「图片/视频/音频生成模型」表与上方「默认生成模型」
          面板重复，已隐藏；仅混合/仅媒体（mediaOnly，如 ComfyUI 工作流）场景保留。 */}
      {mediaOnly ? (
        <MediaModelsBlock
          configuredProviders={configuredProviders}
          channelTypeByProvider={channelTypeByProvider}
          newApiBaseUrl={newApiBaseUrl}
          database={database}
          savedChannelByProvider={savedChannelByProvider}
          savedMediaModels={savedMediaModels}
          comfyOnly={comfyOnly}
          excludeComfyUI={excludeComfyUI}
        />
      ) : null}
    </>
  );
}

function CogneeModelsBlock({
  configuredProviders,
  channelTypeByProvider,
  newApiBaseUrl,
  database,
  providerChannels,
  savedChannelByProvider,
  savedEmbeddingModel,
  backendSnapshotLoaded,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
  backendSnapshotLoaded: boolean;
}) {
  const { t } = useTranslation();

  return (
    <div className="mt-6 rounded-md border border-border/70 px-3 py-3">
      <h4 className="text-xs font-medium text-foreground">
        {t("settings.modelConfig.featureModels.groups.novelImport")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.featureModels.cogneeDescription")}
      </p>

      <div
        className={cn(
          FEATURE_ROW_GRID,
          "mt-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.featureModels.colFeature")}</span>
        <span>{t("settings.modelConfig.featureModels.colProvider")}</span>
        <span>{t("settings.modelConfig.featureModels.colModel")}</span>
      </div>
      <div className="mt-2">
        <FeatureModelRow
          featureId="COGNEE"
          defaultModel="DC-cognee-LLM"
          requiresVision={false}
          newApiBaseUrl={newApiBaseUrl}
          database={database}
          configuredProviders={configuredProviders}
          channelTypeByProvider={channelTypeByProvider}
          capability="text"
          providerChannels={providerChannels}
          savedChannelByProvider={savedChannelByProvider}
        />
      </div>

      <EmbeddingModelBlock
        configuredProviders={configuredProviders}
        channelTypeByProvider={channelTypeByProvider}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        savedChannelByProvider={savedChannelByProvider}
        savedEmbeddingModel={savedEmbeddingModel}
        backendSnapshotLoaded={backendSnapshotLoaded}
      />
    </div>
  );
}

function EmbeddingModelBlock({
  configuredProviders,
  channelTypeByProvider,
  newApiBaseUrl,
  database,
  savedChannelByProvider,
  savedEmbeddingModel,
  backendSnapshotLoaded,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
  backendSnapshotLoaded: boolean;
}) {
  const { t } = useTranslation();
  const localSavedEmbeddingModel = useSettingsStore(
    (s) => s.featureModelConfig.embeddingModel,
  );
  const setEmbeddingModel = useSettingsStore((s) => s.setEmbeddingModel);
  const saveEmbeddingModel = useSaveEmbeddingModel();
  const [localModel, setLocalModel] = useState<EmbeddingModelEntry | undefined>(
    localSavedEmbeddingModel,
  );
  const savedKey = JSON.stringify(savedEmbeddingModel ?? null);
  const lastSyncedBackendKey = useRef<string | null>(null);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (!backendSnapshotLoaded) return;
    if (lastSyncedBackendKey.current === savedKey) return;
    lastSyncedBackendKey.current = savedKey;
    const savedProvider =
      typeof savedEmbeddingModel?.provider === "string"
        ? savedEmbeddingModel.provider.trim()
        : "";
    const savedUpstreamModel =
      typeof savedEmbeddingModel?.upstreamModel === "string"
        ? savedEmbeddingModel.upstreamModel.trim()
        : "";
    const savedDimension = Number(savedEmbeddingModel?.dimension);
    const fromBackend =
      savedProvider &&
      savedUpstreamModel &&
      Number.isFinite(savedDimension) &&
      savedDimension > 0
        ? {
            provider: savedProvider as FeatureModelProvider,
            upstreamModel: savedUpstreamModel,
            dimension: savedDimension,
            batchSize: savedEmbeddingModel?.batchSize,
          }
        : undefined;
    // The API returns {} when no embedding model has been configured. Treat
    // that loaded empty snapshot as authoritative instead of reviving a stale
    // browser draft.
    const next = fromBackend;
    const nextKey = JSON.stringify(next ?? null);
    setLocalModel((current) =>
      JSON.stringify(current ?? null) === nextKey ? current : next,
    );
  }, [backendSnapshotLoaded, savedEmbeddingModel, savedKey]);

  const selectedProvider = localModel?.provider ?? "";
  const upstreamModel = localModel?.upstreamModel ?? "";
  const dimension =
    localModel === undefined
      ? DEFAULT_EMBEDDING_DIMENSION
      : localModel.dimension;
  const batchSize =
    localModel === undefined
      ? DEFAULT_EMBEDDING_BATCH_SIZE
      : localModel.batchSize;
  const embeddingProviders = useMemo(
    () =>
      providersSupportingCapability(
        configuredProviders,
        channelTypeByProvider,
        "embedding",
        selectedProvider,
      ),
    [channelTypeByProvider, configuredProviders, selectedProvider],
  );

  // [aipdd fork delta] 上游模型名改为下拉选择：根据选中的 provider 拉取其 embedding 模型。
  const fetchUpstream = useFetchUpstreamModels();
  const [availableEmbeddingModels, setAvailableEmbeddingModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  useEffect(() => {
    if (!selectedProvider) {
      setAvailableEmbeddingModels([]);
      return;
    }
    const channel = savedChannelByProvider.get(selectedProvider);
    if (!channel?.configured) {
      setAvailableEmbeddingModels([]);
      return;
    }
    let cancelled = false;
    setModelsLoading(true);
    fetchUpstream
      .mutateAsync({ provider: selectedProvider })
      .then((res) => {
        if (cancelled) return;
        if (res.ok === true) {
          const filtered = (res.data.models ?? []).filter((m) =>
            /(embedding|embed|bge[-_]|m3e|gte[-_]?embed|jina[-_]?embed|voyage|text[-_]embedding|oe[-_]embedding)/i.test(m),
          );
          setAvailableEmbeddingModels(filtered);
        }
      })
      .catch(() => {
        /* 拉取失败保持空列表，不阻断用户已存的值 */
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProvider, savedChannelByProvider]);

  const updateLocal = (patch: Partial<EmbeddingModelEntry>) => {
    setLocalModel((prev) => ({
      provider:
        patch.provider ?? prev?.provider ?? embeddingProviders[0] ?? "ali",
      upstreamModel: patch.upstreamModel ?? prev?.upstreamModel ?? "",
      dimension:
        patch.dimension ?? prev?.dimension ?? DEFAULT_EMBEDDING_DIMENSION,
      batchSize:
        "batchSize" in patch
          ? patch.batchSize
          : (prev?.batchSize ?? DEFAULT_EMBEDDING_BATCH_SIZE),
    }));
  };

  const handleSave = async () => {
    setSaveError("");
    if (embeddingProviders.length === 0) {
      toast.error(t("settings.modelConfig.embeddingModel.noChannels"));
      return;
    }
    const provider = localModel?.provider;
    if (!provider || !embeddingProviders.includes(provider)) {
      toast.error(t("settings.modelConfig.embeddingModel.missingProvider"));
      return;
    }
    if (!savedChannelByProvider.get(provider)?.configured) {
      const message = t("settings.modelConfig.featureModels.missingKeys", {
        providers: featureProviderLabel(provider),
      });
      setSaveError(message);
      toast.error(message);
      return;
    }
    const model = upstreamModel.trim();
    if (!model) {
      toast.error(t("settings.modelConfig.embeddingModel.missingModel"));
      return;
    }
    const normalizedDimension = Number(dimension);
    if (!Number.isInteger(normalizedDimension) || normalizedDimension <= 0) {
      toast.error(t("settings.modelConfig.embeddingModel.invalidDimension"));
      return;
    }
    const normalizedBatchSize =
      batchSize == null || String(batchSize).trim() === ""
        ? undefined
        : Math.round(Number(batchSize));
    if (
      normalizedBatchSize !== undefined &&
      (!Number.isFinite(normalizedBatchSize) || normalizedBatchSize <= 0)
    ) {
      toast.error(t("settings.modelConfig.embeddingModel.invalidBatchSize"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    try {
      const res = await saveEmbeddingModel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        upstreamModel: model,
        dimension: normalizedDimension,
        ...(normalizedBatchSize ? { batchSize: normalizedBatchSize } : {}),
      });
      if (!res.ok) {
        const message = getResponseErrorMessage(
          res,
          t("settings.modelConfig.requestFailed"),
        );
        setSaveError(message);
        toast.error(message);
        return;
      }
      const saved = {
        provider: res.data.embeddingModel.provider as FeatureModelProvider,
        upstreamModel: res.data.embeddingModel.upstreamModel,
        dimension: res.data.embeddingModel.dimension,
        batchSize: res.data.embeddingModel.batchSize,
      };
      setEmbeddingModel(saved);
      setLocalModel(saved);
      toast.success(t("settings.modelConfig.embeddingModel.saved"));
    } catch (error) {
      const message = await getRequestErrorMessage(
        error,
        t("settings.modelConfig.requestFailed"),
      );
      setSaveError(message);
      toast.error(message);
    }
  };

  return (
    <div className="mt-6">
      <h4 className="text-xs font-medium text-foreground">
        {t("settings.modelConfig.embeddingModel.title")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.embeddingModel.description")}
      </p>

      <div className="mt-3 rounded-md border border-border/70 px-3 py-3">
        <div className="grid grid-cols-[140px_minmax(0,1fr)_100px_110px] items-center gap-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          <span>{t("settings.modelConfig.embeddingModel.colProvider")}</span>
          <span>
            {t("settings.modelConfig.embeddingModel.colUpstreamModel")}
          </span>
          <span>{t("settings.modelConfig.embeddingModel.colDimension")}</span>
          <span>{t("settings.modelConfig.embeddingModel.colBatchSize")}</span>
        </div>
        <div className="mt-2 grid grid-cols-[140px_minmax(0,1fr)_100px_110px] items-center gap-3">
          <Select
            value={selectedProvider}
            onValueChange={(provider) =>
              updateLocal({ provider: provider as FeatureModelProvider })
            }
            disabled={embeddingProviders.length === 0}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue
                placeholder={t(
                  "settings.modelConfig.embeddingModel.defaultProvider",
                )}
              >
                {(provider: string) => featureProviderLabel(provider)}
              </SelectValue>
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {embeddingProviders.map((provider) => (
                <SelectItem key={provider} value={provider}>
                  {featureProviderLabel(provider)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={upstreamModel || undefined}
            onValueChange={(model) =>
              updateLocal({ upstreamModel: model ?? undefined })
            }
            disabled={embeddingProviders.length === 0 || modelsLoading}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue
                placeholder={
                  modelsLoading
                    ? t("common.loading")
                    : t("settings.modelConfig.embeddingModel.upstreamModelPlaceholder")
                }
              />
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {upstreamModel && !availableEmbeddingModels.includes(upstreamModel) ? (
                <SelectItem key={upstreamModel} value={upstreamModel}>
                  {t("settings.modelConfig.embeddingModel.savedSuffix", {
                    model: upstreamModel,
                  })}
                </SelectItem>
              ) : null}
              {availableEmbeddingModels.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={String(dimension)}
            onChange={(event) => {
              if (event.target.value.trim()) {
                updateLocal({
                  dimension: Number(event.target.value),
                });
              }
            }}
            inputMode="numeric"
            min={1}
            step={1}
            type="number"
            className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
            disabled={embeddingProviders.length === 0}
          />
          <Input
            value={batchSize == null ? "" : String(batchSize)}
            onChange={(event) =>
              updateLocal({
                batchSize: event.target.value.trim()
                  ? Number(event.target.value)
                  : undefined,
              })
            }
            inputMode="numeric"
            min={1}
            step={1}
            type="number"
            placeholder={t(
              "settings.modelConfig.embeddingModel.batchSizePlaceholder",
            )}
            className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
            disabled={embeddingProviders.length === 0}
          />
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-amber-300/80">
          {t("settings.modelConfig.embeddingModel.dimensionWarning")}
        </p>
      </div>

      {saveError ? (
        <p className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">
          {saveError}
        </p>
      ) : null}

      <div className="mt-3 flex items-center justify-end gap-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {embeddingProviders.length > 0
            ? t("settings.modelConfig.embeddingModel.saveHint")
            : t("settings.modelConfig.embeddingModel.noChannelsHint")}
        </p>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={
            embeddingProviders.length === 0 || saveEmbeddingModel.isPending
          }
        >
          {saveEmbeddingModel.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}
          {t("settings.modelConfig.embeddingModel.save")}
        </Button>
      </div>
    </div>
  );
}

function FeatureModelCapabilitySection({
  title,
  hint,
  groups,
  newApiBaseUrl,
  database,
  configuredProviders,
  channelTypeByProvider,
  capability,
  providerChannels,
  savedChannelByProvider,
}: {
  title: string;
  hint?: string;
  groups: readonly FeatureModelGroup[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  capability: "text" | "vision";
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const capableProviders = useMemo(
    () =>
      providersSupportingCapability(
        configuredProviders,
        channelTypeByProvider,
        capability,
      ),
    [capability, channelTypeByProvider, configuredProviders],
  );
  const [bulkProvider, setBulkProvider] = useState<FeatureModelProvider | "">(
    capableProviders[0] ?? "",
  );
  const [bulkModel, setBulkModel] = useState("");
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);
  // [aipdd fork delta] 该区当前的默认模型（全局兜底），用作行内「默认（xxx）」占位提示。
  const sectionDefaultModel = useSettingsStore(
    (s) =>
      s.featureModelConfig.featureModels[
        capability === "vision" ? VISION_DEFAULT_KEY : TEXT_DEFAULT_KEY
      ]?.model.trim() ?? "",
  );
  // [aipdd fork delta] 批量应用的模型也改为从所选渠道 /models 列表下拉选择。
  const fetchModels = useFetchUpstreamModels();
  const [bulkModels, setBulkModels] = useState<string[]>([]);
  const [bulkLoading, setBulkLoading] = useState(false);
  const loadBulkModels = useCallback(
    async (p?: string) => {
      if (!p) {
        setBulkModels([]);
        return;
      }
      setBulkLoading(true);
      try {
        const res = await fetchModels.mutateAsync({ provider: p, kind: capability });
        setBulkModels(res.ok === true ? res.data.models ?? [] : []);
      } catch {
        setBulkModels([]);
      } finally {
        setBulkLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bulkProvider],
  );
  useEffect(() => {
    void loadBulkModels(bulkProvider || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkProvider]);
  const featureCount = useMemo(
    () => groups.reduce((total, group) => total + group.features.length, 0),
    [groups],
  );

  useEffect(() => {
    if (!bulkProvider || !capableProviders.includes(bulkProvider)) {
      setBulkProvider(capableProviders[0] ?? "");
    }
  }, [bulkProvider, capableProviders]);

  const handleApplyBulk = () => {
    const provider = bulkProvider;
    const model = bulkModel.trim();
    if (!provider) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    if (!model) {
      toast.error(t("settings.modelConfig.featureModels.bulkMissingModel"));
      return;
    }
    // [aipdd fork delta]「应用到全部」= 设为该区默认（全局兜底）并清空各行单独
    // 配置：所有功能回到「未单独配置」态，渲染时回落到本默认；这样重开显示的是
    // 清空态（占位符提示默认模型），后续改默认也继续生效。
    for (const group of groups) {
      for (const feature of group.features) {
        updateFeatureModel(feature.id, {
          provider: DEFAULT_FEATURE_MODEL_PROVIDER,
          model: "",
        });
      }
    }
    updateFeatureModel(
      capability === "vision" ? VISION_DEFAULT_KEY : TEXT_DEFAULT_KEY,
      { provider, model },
    );
    toast.success(
      t("settings.modelConfig.featureModels.bulkApplied", {
        count: featureCount,
        model,
      }),
    );
  };

  return (
    <div className="mt-4">
      <h5 className="text-[11px] font-medium text-foreground">{title}</h5>
      {hint ? (
        <p className="mt-1 text-[11px] leading-relaxed text-amber-300/80">
          {hint}
        </p>
      ) : null}
      <div className="mt-2 grid grid-cols-[150px_minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-border/70 px-3 py-2">
        <Select
          value={bulkProvider}
          onValueChange={(value) =>
            setBulkProvider(value as FeatureModelProvider)
          }
          disabled={capableProviders.length === 0}
        >
          <SelectTrigger
            size="sm"
            className="w-full"
            aria-label={`${title} · ${t(
              "settings.modelConfig.featureModels.colProvider",
            )}`}
          >
            <SelectValue
              placeholder={t(
                "settings.modelConfig.featureModels.noChannelsShort",
              )}
            >
              {(value: string) => featureProviderLabel(value)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            {capableProviders.map((provider) => (
              <SelectItem key={provider} value={provider}>
                {featureProviderLabel(provider)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={bulkModel}
          onValueChange={(value) => setBulkModel(value ?? "")}
          disabled={!bulkProvider || capableProviders.length === 0}
        >
          <SelectTrigger
            size="sm"
            aria-label={`${title} · ${t(
              "settings.modelConfig.featureModels.colModel",
            )}`}
            className="h-8 w-full rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
          >
            <SelectValue
              placeholder={
                bulkLoading
                  ? t("settings.modelConfig.modelPicker.loading")
                  : t("settings.modelConfig.modelPicker.selectModel")
              }
            />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            {bulkModels.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
                {t("settings.modelConfig.modelPicker.empty")}
              </div>
            ) : (
              bulkModels.map((m) => (
                <SelectItem key={m} value={m} className="text-xs">
                  {m}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleApplyBulk}
          disabled={capableProviders.length === 0}
        >
          {t("settings.modelConfig.featureModels.applyToAll")}
        </Button>
      </div>

      {/* 表头：功能 / 供应商 / 上游模型名（与下方行栅格对齐） */}
      <div
        className={cn(
          FEATURE_ROW_GRID,
          "mt-2 px-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.featureModels.colFeature")}</span>
        <span>{t("settings.modelConfig.featureModels.colProvider")}</span>
        <span>{t("settings.modelConfig.featureModels.colModel")}</span>
      </div>

      <div className="mt-2 space-y-2">
        {groups.map((group) => (
          <FeatureModelGroupBlock
            key={group.key}
            groupKey={group.key}
            features={group.features}
            newApiBaseUrl={newApiBaseUrl}
            database={database}
            configuredProviders={configuredProviders}
            channelTypeByProvider={channelTypeByProvider}
            capability={capability}
            sectionDefaultModel={sectionDefaultModel}
            providerChannels={providerChannels}
            savedChannelByProvider={savedChannelByProvider}
          />
        ))}
      </div>
    </div>
  );
}

function FeatureModelGroupBlock({
  groupKey,
  features,
  newApiBaseUrl,
  database,
  configuredProviders,
  channelTypeByProvider,
  capability,
  sectionDefaultModel,
  providerChannels,
  savedChannelByProvider,
}: {
  groupKey: string;
  features: readonly FeatureModelDef[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  capability: "text" | "vision";
  sectionDefaultModel?: string;
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-md border border-border/70">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="text-xs font-medium text-foreground">
          {t(`settings.modelConfig.featureModels.groups.${groupKey}`)}
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded ? (
        <div className="space-y-2.5 border-t border-border/70 px-3 py-3">
          {features.map((feature) => (
            <FeatureModelRow
              key={feature.id}
              featureId={feature.id}
              defaultModel={feature.defaultModel}
              requiresVision={Boolean(feature.requiresVision)}
              newApiBaseUrl={newApiBaseUrl}
              database={database}
              configuredProviders={configuredProviders}
              channelTypeByProvider={channelTypeByProvider}
              capability={capability}
              sectionDefaultModel={sectionDefaultModel}
              providerChannels={providerChannels}
              savedChannelByProvider={savedChannelByProvider}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FeatureModelRow({
  featureId,
  defaultModel,
  requiresVision,
  newApiBaseUrl,
  database,
  configuredProviders,
  channelTypeByProvider,
  capability,
  sectionDefaultModel,
  providerChannels,
  savedChannelByProvider,
}: {
  featureId: string;
  defaultModel: string;
  requiresVision: boolean;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  capability: "text" | "vision";
  /** 该区默认模型（全局兜底）；行内未单独配置时以占位符提示。 */
  sectionDefaultModel?: string;
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const entry = useSettingsStore(
    (s) => s.featureModelConfig.featureModels[featureId],
  );
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);
  const saveChannel = useSaveCustomChannel();
  const capableProviders = useMemo(
    () =>
      providersSupportingCapability(
        configuredProviders,
        channelTypeByProvider,
        capability,
        entry?.provider,
      ),
    [capability, channelTypeByProvider, configuredProviders, entry?.provider],
  );
  const configuredSet = useMemo(
    () => new Set(capableProviders),
    [capableProviders],
  );
  const fallbackProvider = capableProviders[0];
  const provider =
    entry?.provider && configuredSet.has(entry.provider)
      ? entry.provider
      : fallbackProvider;
  const model = entry?.model ?? "";

  // [aipdd fork delta] 选定已配置渠道后，模型改为「从该渠道拉取的列表中选择」，
  // 不再让用户手输上游模型名（避免拼写错误 / 不存在的模型）。随 provider 变化自动重拉。
  const fetchModels = useFetchUpstreamModels();
  const [channelModels, setChannelModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const loadRowModels = useCallback(
    async (p?: string) => {
      if (!p) {
        setChannelModels([]);
        return;
      }
      setModelsLoading(true);
      try {
        const res = await fetchModels.mutateAsync({ provider: p, kind: capability });
        setChannelModels(res.ok === true ? res.data.models ?? [] : []);
      } catch {
        setChannelModels([]);
      } finally {
        setModelsLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [provider],
  );
  useEffect(() => {
    void loadRowModels(provider ?? undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);
  // 已保存但不在当前列表中的模型也并入，避免回显丢失。
  const modelOptions = useMemo(() => {
    const set = new Set(channelModels);
    if (model) set.add(model);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [channelModels, model]);

  // 单条保存：仅把该功能拼成一个渠道（modelMapping 只含这一条）写入。
  const handleSaveRow = async () => {
    const m = model.trim();
    if (!m) {
      toast.error(t("settings.modelConfig.featureModels.noMappings"));
      return;
    }
    if (!provider) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const channel = providerChannels[provider];
    const upstreamKey = (channel?.upstreamKey ?? "").trim();
    if (!upstreamKey && !savedChannelByProvider.get(provider)?.configured) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: featureProviderLabel(provider),
        }),
      );
      return;
    }
    try {
      const res = await saveChannel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        upstreamKey,
        modelMapping: { [defaultModel]: m },
        group: "default",
        priority: 0,
        weight: 0,
        baseUrl: (channel?.baseUrl ?? "").trim(),
        testModel: "",
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      toast.success(t("settings.modelConfig.featureModels.savedOne"));
    } catch {
      toast.error(t("settings.modelConfig.requestFailed"));
    }
  };

  return (
    <div className={FEATURE_ROW_GRID}>
      <span className="flex flex-wrap items-center gap-1.5 text-xs text-foreground">
        <span>
          {t(`settings.modelConfig.featureModels.features.${featureId}`)}
        </span>
        {requiresVision ? (
          <span className="rounded border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
            {t("settings.modelConfig.featureModels.multimodalRequiredBadge")}
          </span>
        ) : null}
      </span>
      <Select
        value={provider ?? ""}
        onValueChange={(value) =>
          updateFeatureModel(featureId, {
            provider: value as FeatureModelProvider,
          })
        }
        disabled={capableProviders.length === 0}
      >
        <SelectTrigger size="sm" className="w-full">
          <SelectValue
            placeholder={t(
              "settings.modelConfig.featureModels.noChannelsShort",
            )}
          >
            {(value: string) => featureProviderLabel(value)}
          </SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {capableProviders.map((p) => (
            <SelectItem key={p} value={p}>
              {featureProviderLabel(p)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex items-center gap-2">
        {/* [aipdd fork delta] 上游模型改为下拉选择（来自所选渠道的 /models 列表）。 */}
        <Select
          value={model}
          onValueChange={(value) =>
            updateFeatureModel(featureId, {
              provider: provider ?? fallbackProvider,
              model: value ?? undefined,
            })
          }
          disabled={!provider || capableProviders.length === 0}
        >
          <SelectTrigger
            size="sm"
            className="h-9 flex-1 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
          >
            <SelectValue
              placeholder={
                !provider
                  ? t("settings.modelConfig.featureModels.noChannelsShort")
                  : !model && sectionDefaultModel
                    ? t(
                        "settings.modelConfig.featureModels.followDefault",
                        { model: sectionDefaultModel },
                      )
                    : modelsLoading
                      ? t("settings.modelConfig.modelPicker.loading")
                      : t("settings.modelConfig.modelPicker.selectModel")
              }
            />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            {modelOptions.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
                {t("settings.modelConfig.modelPicker.empty")}
              </div>
            ) : (
              modelOptions.map((m) => (
                <SelectItem key={m} value={m} className="text-xs">
                  {m}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-8 shrink-0"
          onClick={() => void loadRowModels(provider ?? undefined)}
          disabled={!provider || modelsLoading}
          title={t("settings.modelConfig.modelPicker.reload")}
        >
          {modelsLoading ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCw className="size-3.5" />
          )}
        </Button>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSaveRow}
          disabled={saveChannel.isPending || capableProviders.length === 0}
          title={t("settings.modelConfig.featureModels.saveRow")}
        >
          {saveChannel.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            t("settings.modelConfig.featureModels.saveRow")
          )}
        </Button>
      </div>
    </div>
  );
}

export {
  FeatureModelsBlock,
  CogneeModelsBlock,
  EmbeddingModelBlock,
  FeatureModelCapabilitySection,
  FeatureModelGroupBlock,
  FeatureModelRow,
};
