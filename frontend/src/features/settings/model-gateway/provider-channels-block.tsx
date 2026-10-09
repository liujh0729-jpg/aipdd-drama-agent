// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：自建渠道块（渠道行、能力筛选弹窗、ComfyUI 工作流编辑器）。

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { confirmDialog } from "@/components/confirm-dialog-host";
import {
  AlertTriangle,
  Eye,
  EyeOff,
  Loader2,
  Maximize2,
  Minimize2,
  Plus,
  RotateCw,
  Search,
  Trash2,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
// [aipdd fork delta] 渠道目录里 fork 注入的品牌行（火山 AI MediaKit）名/描述都是
// 后端写死的中文：按界面语言覆盖（en・vi → Volcengine AI MediaKit），其余行仍用
// 后端原值。
import {
  localizedBrandDescription,
  localizedBrandName,
} from "@/lib/provider-labels";
import {
  useSaveProviderChannels,
  useClearComfyUIConfig,
  useSyncProviderChannel,
  type NewApiDatabaseConfigInput,
  type NewApiChannelType,
  type SavedProviderChannelConfig,
} from "@/lib/queries/model-gateway";
import {
  useSettingsStore,
  type FeatureModelProvider,
} from "@/stores/settingsStore";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  featureProviderLabel,
  customProviderChannelType,
  readComfyUIWorkflows,
  readComfyUIModelName,
  buildComfyUIWorkflowRoutes,
  normalizeProviderChannelSettings,
  type ChannelCapability,
} from "./shared";

/**
 * [aipdd fork delta] 不当作供应商渠道出现的 provider：火山 AI MediaKit 本就跟随
 * VolcEngine 存在（凭证真源是 video-enhance 配置，不进渠道表），在渠道列表与
 * 「渠道管理」目录里都不该占一行——用户填的是 VolcEngine 卡内的子区块。
 * 豆包语音识别凭证同理：它是 volcengine 渠道的 settings.asr，从来不是独立渠道。
 */
const HIDDEN_CHANNEL_PROVIDERS: ReadonlySet<string> = new Set(["mediakit"]);

function ProviderChannelsBlock({
  savedProviderChannels,
  newApiBaseUrl,
  database,
  channelTypes,
  channelTypesLoading,
  channelTypesLoadFailed,
  onRetryChannelTypes,
  allowedProviders,
  excludedProviders,
  defaultComfyWorkflows,
}: {
  savedProviderChannels: SavedProviderChannelConfig[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  channelTypes: NewApiChannelType[];
  channelTypesLoading: boolean;
  channelTypesLoadFailed: boolean;
  onRetryChannelTypes: () => void;
  allowedProviders?: readonly string[];
  excludedProviders?: readonly string[];
  defaultComfyWorkflows?: {
    model: string;
    workflows: Record<string, Record<string, unknown>>;
  };
}) {
  const { t } = useTranslation();
  const providerChannels = useSettingsStore(
    (s) => s.featureModelConfig.providerChannels,
  );
  const addFeatureProviderChannel = useSettingsStore(
    (s) => s.addFeatureProviderChannel,
  );
  const updateFeatureProviderChannel = useSettingsStore(
    (s) => s.updateFeatureProviderChannel,
  );
  const removeFeatureProviderChannel = useSettingsStore(
    (s) => s.removeFeatureProviderChannel,
  );
  const saveProviderChannels = useSaveProviderChannels();
  const clearComfyUIConfig = useClearComfyUIConfig();
  const [channelCatalogOpen, setChannelCatalogOpen] = useState(false);
  const [removingProvider, setRemovingProvider] = useState<string | null>(null);
  const [latestAddedProvider, setLatestAddedProvider] = useState<string | null>(
    null,
  );
  const channelTypeByProvider = useMemo(
    () => new Map(channelTypes.map((item) => [item.provider, item])),
    [channelTypes],
  );

  const allowedProviderSet = useMemo(
    () => (allowedProviders ? new Set(allowedProviders) : null),
    [allowedProviders],
  );
  const excludedProviderSet = useMemo(
    () => new Set(excludedProviders ?? []),
    [excludedProviders],
  );
  // [aipdd fork delta] 主列表渲染口径与弹窗 configuredProviderSet 对齐：本地添加行
  // ∩ 后端真源已配置，保证「渠道管理」弹窗里已是「删除」态的渠道在外部列表也
  // 一一出现（用户添加了渠道却看不见的问题）。
  const configuredProviders = useMemo(() => {
    const names = new Set(Object.keys(providerChannels));
    for (const channel of savedProviderChannels) {
      if (!channel.configured) continue;
      if (HIDDEN_CHANNEL_PROVIDERS.has(channel.provider)) continue;
      if (allowedProviderSet && !allowedProviderSet.has(channel.provider)) {
        continue;
      }
      if (excludedProviderSet.has(channel.provider)) continue;
      names.add(channel.provider);
    }
    return Array.from(names).sort((left, right) => {
      if (left === latestAddedProvider) return -1;
      if (right === latestAddedProvider) return 1;
      return left.localeCompare(right);
    });
  }, [
    allowedProviderSet,
    excludedProviderSet,
    latestAddedProvider,
    providerChannels,
    savedProviderChannels,
  ]);
  // [aipdd fork delta] 目录「已配置」判定 = 本地渠道行 ∩ 后端真源已配置：渠道连接
  // 卡保存的渠道即使本地行尚未 hydrate / 被清，也显示为已配置，不再出现
  // 「NewAPI / ComfyUI 已连好却显示待添加」。
  const configuredProviderSet = useMemo(() => {
    const set = new Set(configuredProviders);
    for (const channel of savedProviderChannels) {
      if (!channel.configured) continue;
      if (HIDDEN_CHANNEL_PROVIDERS.has(channel.provider)) continue;
      if (allowedProviderSet && !allowedProviderSet.has(channel.provider)) {
        continue;
      }
      if (excludedProviderSet.has(channel.provider)) continue;
      set.add(channel.provider);
    }
    return set;
  }, [
    allowedProviderSet,
    configuredProviders,
    excludedProviderSet,
    savedProviderChannels,
  ]);
  const visibleChannelTypes = channelTypes.filter(
    (item) =>
      (!allowedProviderSet || allowedProviderSet.has(item.provider)) &&
      !excludedProviderSet.has(item.provider) &&
      !HIDDEN_CHANNEL_PROVIDERS.has(item.provider),
  );
  const savedChannelByProvider = useMemo(() => {
    return new Map(
      savedProviderChannels.map((channel) => [channel.provider, channel]),
    );
  }, [savedProviderChannels]);
  const addProvider = (provider: FeatureModelProvider) => {
    if (providerChannels[provider]) return;
    const channelType = channelTypeByProvider.get(provider);
    if (channelType && channelType.status !== 1) return;
    addFeatureProviderChannel(provider);
    if (channelType?.requiresBaseUrl && channelType.defaultBaseUrl) {
      updateFeatureProviderChannel(provider, {
        baseUrl: channelType.defaultBaseUrl,
      });
    }
    setLatestAddedProvider(provider);
  };

  const providerChannelsForSave = (
    providers: readonly FeatureModelProvider[],
  ) =>
    providers
      .filter((provider) => {
        if (provider === "comfyui") return true;
        if ((providerChannels[provider]?.upstreamKey ?? "").trim()) return true;
        return Boolean(savedChannelByProvider.get(provider)?.configured);
      })
      .map((provider) => ({
        provider,
        type:
          channelTypeByProvider.get(provider)?.type ||
          savedChannelByProvider.get(provider)?.type ||
          customProviderChannelType(provider),
        upstreamKey:
          (providerChannels[provider]?.upstreamKey ?? "").trim() || undefined,
        // [aipdd fork delta] 本地无行（后端真源专属）时回填真源值，避免「移除」
        // 其他渠道时误清它的 baseUrl / settings。
        baseUrl:
          (providerChannels[provider]?.baseUrl ?? "").trim() ||
          savedChannelByProvider.get(provider)?.baseUrl ||
          "",
        priority:
          providerChannels[provider]?.priority ??
          savedChannelByProvider.get(provider)?.priority ??
          0,
        ...(providerChannels[provider]
          ? {
              settings: normalizeProviderChannelSettings(
                provider,
                providerChannels[provider]?.settings ?? {},
              ),
            }
          : {}),
      }));

  const removeProvider = async (provider: FeatureModelProvider) => {
    const hasLocalRow = Boolean(providerChannels[provider]);
    if (!hasLocalRow && !savedChannelByProvider.has(provider)) return;
    const hasSavedComfyUIConfig =
      provider === "comfyui" &&
      savedChannelByProvider.get("comfyui")?.configured === true;
    if (!hasSavedComfyUIConfig) {
      const currentConfig = useSettingsStore.getState().featureModelConfig;
      const featureCount = Object.values(currentConfig.featureModels).filter(
        (entry) => entry.provider === provider,
      ).length;
      const mediaCount = Object.values(currentConfig.mediaModels ?? {}).filter(
        (entry) => entry.provider === provider,
      ).length;
      const embeddingCount =
        currentConfig.embeddingModel?.provider === provider ? 1 : 0;
      const confirmed = await confirmDialog({
        title: t("settings.modelConfig.featureModels.removeChannelTitle"),
        description: t(
          "settings.modelConfig.featureModels.removeChannelConfirm",
          {
            provider: featureProviderLabel(provider, channelTypeByProvider),
            featureCount,
            mediaCount,
            embeddingCount,
          },
        ),
        confirmText: t("settings.modelConfig.featureModels.removeChannelShort"),
        confirmVariant: "destructive",
      });
      if (!confirmed) return;

      const removeLocally = () => {
        removeFeatureProviderChannel(provider);
        setLatestAddedProvider((current) =>
          current === provider ? null : current,
        );
      };
      if (!savedChannelByProvider.has(provider)) {
        removeLocally();
        return;
      }

      setRemovingProvider(provider);
      try {
        // [aipdd fork delta] 保留集合 = 本地行 ∪ 后端真源已配置（并集再去掉被删者、
        // 按本区块 allowed / excluded 过滤）：本地行尚未 hydrate 的真源渠道不会被
        // 「全量替换」误删。
        const keepProviders = Array.from(
          new Set<FeatureModelProvider>([
            ...configuredProviders,
            ...savedProviderChannels
              .filter((channel) => channel.configured)
              .map((channel) => channel.provider),
          ]),
        )
          .filter((item) => !excludedProviderSet.has(item))
          .filter(
            (item) => !allowedProviderSet || allowedProviderSet.has(item),
          )
          .filter((item) => item !== provider);
        const response = await saveProviderChannels.mutateAsync({
          preserveUnmentioned: Boolean(allowedProviderSet),
          channels: providerChannelsForSave(keepProviders),
        });
        if (response.ok !== true) {
          toast.error(
            getResponseErrorMessage(
              response,
              t("settings.modelConfig.requestFailed"),
            ),
          );
          return;
        }
        removeLocally();
        toast.success(t("settings.modelConfig.featureModels.channelsSaved"));
      } catch (error) {
        toast.error(
          await getRequestErrorMessage(
            error,
            t("settings.modelConfig.requestFailed"),
          ),
        );
      } finally {
        setRemovingProvider(null);
      }
      return;
    }

    const confirmed = await confirmDialog({
      title: t("settings.modelConfig.featureModels.clearComfyTitle"),
      description: t("settings.modelConfig.featureModels.clearComfyConfirm"),
      confirmText: t("settings.modelConfig.featureModels.clearComfy"),
      confirmVariant: "destructive",
    });
    if (!confirmed) return;

    setRemovingProvider(provider);
    try {
      const response = await clearComfyUIConfig.mutateAsync();
      if (response.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            response,
            t("settings.modelConfig.requestFailed"),
          ),
        );
        return;
      }
      removeFeatureProviderChannel(provider);
      setLatestAddedProvider((current) =>
        current === provider ? null : current,
      );
      const currentMediaModels =
        useSettingsStore.getState().featureModelConfig.mediaModels ?? {};
      useSettingsStore
        .getState()
        .setMediaModels(
          Object.fromEntries(
            Object.entries(currentMediaModels).filter(
              ([, entry]) => entry.provider !== "comfyui",
            ),
          ),
        );
      toast.success(t("settings.modelConfig.featureModels.comfyCleared"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.requestFailed"),
        ),
      );
    } finally {
      setRemovingProvider(null);
    }
  };

  const handleSaveChannels = () => {
    if (configuredProviders.length === 0) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    const channelsToSave = providerChannelsForSave(configuredProviders);
    // [aipdd fork delta] 部分渠道未填上游 Key 会被保存过滤静默丢弃（用户「添加了
    // 5 个只剩 3 个」的根因之一）：这里显式列出被跳过的渠道，而不是无提示地只保存一部分。
    const skippedProviders = configuredProviders.filter(
      (provider) => !channelsToSave.some((channel) => channel.provider === provider),
    );
    if (channelsToSave.length === 0) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: configuredProviders
            .map((provider) =>
              featureProviderLabel(provider, channelTypeByProvider),
            )
            .join("、"),
        }),
      );
      return;
    }
    if (skippedProviders.length > 0) {
      toast.warning(
        t("settings.modelConfig.featureModels.channelsSkippedMissingKeys", {
          providers: skippedProviders
            .map((provider) =>
              featureProviderLabel(provider, channelTypeByProvider),
            )
            .join("、"),
        }),
      );
    }
    saveProviderChannels.mutate(
      {
        preserveUnmentioned: Boolean(allowedProviderSet),
        channels: channelsToSave,
      },
      {
        onSuccess: (res) => {
          if (!res.ok) {
            toast.error(
              getResponseErrorMessage(
                res,
                t("settings.modelConfig.requestFailed"),
              ),
            );
            return;
          }
          for (const { provider } of channelsToSave) {
            if ((providerChannels[provider]?.upstreamKey ?? "").trim()) {
              useSettingsStore
                .getState()
                .clearFeatureProviderUpstreamKey(provider);
            }
          }
          toast.success(t("settings.modelConfig.featureModels.channelsSaved"));
        },
        onError: () => {
          toast.error(t("settings.modelConfig.requestFailed"));
        },
      },
    );
  };

  return (
    <div className="mt-5 rounded-md border border-border/70 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.featureModels.channelsTitle")}
          </h4>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.featureModels.channelsDescription")}
          </p>
        </div>
        <div className="flex items-center justify-end">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => setChannelCatalogOpen(true)}
          >
            <Plus className="size-3.5" />
            {t("settings.modelConfig.featureModels.manageChannels")}
          </Button>
        </div>
      </div>

      <ChannelCapabilitiesDialog
        open={channelCatalogOpen}
        onOpenChange={setChannelCatalogOpen}
        channelTypes={visibleChannelTypes}
        configuredProviders={configuredProviderSet}
        loading={channelTypesLoading}
        loadError={channelTypesLoadFailed}
        onRetry={onRetryChannelTypes}
        onAdd={addProvider}
        onRemove={(provider) => void removeProvider(provider)}
        removingProvider={removingProvider}
      />

      {configuredProviders.length > 0 ? (
        <>
          <div className="mt-3 space-y-2.5">
            {configuredProviders.map((provider) => (
              <ProviderChannelRow
                key={provider}
                provider={provider}
                channelType={channelTypeByProvider.get(provider)}
                savedChannel={savedChannelByProvider.get(provider)}
                newApiBaseUrl={newApiBaseUrl}
                database={database}
                defaultComfyWorkflows={defaultComfyWorkflows}
              />
            ))}
          </div>
          <div className="mt-3 flex items-center justify-end gap-3">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t("settings.modelConfig.featureModels.channelsSaveHint")}
            </p>
            <Button
              type="button"
              size="sm"
              onClick={handleSaveChannels}
              disabled={saveProviderChannels.isPending}
            >
              {saveProviderChannels.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              {t("settings.modelConfig.featureModels.saveChannels")}
            </Button>
          </div>
        </>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-border/70 px-3 py-2 text-[11px] text-muted-foreground">
          {t("settings.modelConfig.featureModels.noChannelsHint")}
        </p>
      )}
    </div>
  );
}

const CHANNEL_CAPABILITY_FILTERS: readonly ChannelCapability[] = [
  "text",
  "vision",
  "embedding",
  "rerank",
  "image",
  "video",
  "audio",
  // [aipdd fork delta req46] 音乐能力位：MiniMax 两条渠道都声明了它，不登记到
  // 筛选面里就找不到「能生音乐」的渠道。
  "music",
];

function ChannelCapabilitiesDialog({
  open,
  onOpenChange,
  channelTypes,
  configuredProviders,
  loading,
  loadError,
  onRetry,
  onAdd,
  onRemove,
  removingProvider,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channelTypes: readonly NewApiChannelType[];
  configuredProviders: ReadonlySet<string>;
  loading: boolean;
  loadError: boolean;
  onRetry: () => void;
  onAdd: (provider: FeatureModelProvider) => void;
  onRemove: (provider: FeatureModelProvider) => void;
  removingProvider: string | null;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [capability, setCapability] = useState<ChannelCapability | "all">(
    "all",
  );
  const filteredChannelTypes = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return channelTypes.filter((item) => {
      if (capability !== "all" && !item.capabilities.includes(capability)) {
        return false;
      }
      if (!normalizedQuery) return true;
      return [
        localizedBrandName(item.provider) || item.name,
        item.provider,
        localizedBrandDescription(item.provider) || item.description,
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    });
  }, [capability, channelTypes, query]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[78vh] w-[calc(100vw-2rem)] max-w-[880px] flex-col gap-0 overflow-hidden rounded-lg border border-border bg-popover p-0 sm:max-w-[880px]">
        <DialogHeader className="border-b border-border px-5 py-4 pr-12">
          <DialogTitle>
            {t("settings.modelConfig.featureModels.channelCatalogTitle")}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t("settings.modelConfig.featureModels.channelCatalogDescription")}
          </DialogDescription>
        </DialogHeader>

        {!loadError ? (
          <div className="grid grid-cols-1 gap-2 border-b border-border px-5 py-3 sm:grid-cols-[minmax(0,1fr)_180px]">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={t(
                  "settings.modelConfig.featureModels.channelCatalogSearch",
                )}
                placeholder={t(
                  "settings.modelConfig.featureModels.channelCatalogSearch",
                )}
                className="h-8 pl-8"
              />
            </div>
            <Select
              value={capability}
              onValueChange={(value) =>
                setCapability(value as ChannelCapability | "all")
              }
            >
              <SelectTrigger
                size="sm"
                className="w-full"
                aria-label={t(
                  "settings.modelConfig.featureModels.channelCapabilityFilter",
                )}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false}>
                <SelectItem value="all">
                  {t("settings.modelConfig.featureModels.capabilities.all")}
                </SelectItem>
                {CHANNEL_CAPABILITY_FILTERS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {t(
                      `settings.modelConfig.featureModels.capabilities.${item}`,
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto">
          {loadError ? (
            <div className="flex min-h-52 flex-col items-center justify-center gap-3 px-5 py-10 text-center">
              <AlertTriangle className="size-5 text-destructive" />
              <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
                {t("settings.modelConfig.featureModels.channelTypesLoadFailed")}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={onRetry}
                disabled={loading}
              >
                <RotateCw
                  className={cn("size-3.5", loading && "animate-spin")}
                />
                {t("settings.modelConfig.featureModels.retryChannelTypes")}
              </Button>
            </div>
          ) : loading ? (
            <div className="flex min-h-52 items-center justify-center gap-2 px-5 py-10 text-xs text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("settings.modelConfig.featureModels.channelTypesLoading")}
            </div>
          ) : (
            <div className="min-w-[720px]">
              <div className="grid grid-cols-[160px_minmax(210px,1fr)_minmax(220px,1.2fr)_90px] gap-3 border-b border-border px-5 py-2 text-[11px] font-medium text-muted-foreground">
                <span>
                  {t("settings.modelConfig.featureModels.channelProvider")}
                </span>
                <span>
                  {t("settings.modelConfig.featureModels.channelCapabilities")}
                </span>
                <span>
                  {t("settings.modelConfig.featureModels.channelDescription")}
                </span>
                <span className="text-right">
                  {t("settings.modelConfig.featureModels.channelAction")}
                </span>
              </div>
              {filteredChannelTypes.length > 0 ? (
                filteredChannelTypes.map((item) => {
                  const configured = configuredProviders.has(item.provider);
                  return (
                    <div
                      key={item.provider}
                      className="grid grid-cols-[160px_minmax(210px,1fr)_minmax(220px,1.2fr)_90px] items-center gap-3 border-b border-border/70 px-5 py-3 last:border-b-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium text-foreground">
                          {localizedBrandName(item.provider) ||
                            item.name ||
                            featureProviderLabel(item.provider)}
                        </p>
                        <code className="block truncate text-[10px] text-muted-foreground">
                          {item.provider}
                        </code>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {item.capabilities.length > 0 ? (
                          [...new Set(item.capabilities)].map(
                            (itemCapability) => (
                              <span
                                key={itemCapability}
                                className="rounded-sm border border-border/70 bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-muted-foreground"
                              >
                                {t(
                                  `settings.modelConfig.featureModels.capabilities.${itemCapability}`,
                                  { defaultValue: itemCapability },
                                )}
                              </span>
                            ),
                          )
                        ) : (
                          <span className="text-[11px] text-muted-foreground">
                            {t(
                              "settings.modelConfig.featureModels.noCapabilityData",
                            )}
                          </span>
                        )}
                      </div>
                      <p className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                        {localizedBrandDescription(item.provider) ||
                          item.description ||
                          "-"}
                      </p>
                      <div className="flex justify-end">
                        {configured ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                            onClick={() =>
                              onRemove(item.provider as FeatureModelProvider)
                            }
                            disabled={removingProvider !== null}
                          >
                            {removingProvider === item.provider ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="size-3.5" />
                            )}
                            {t(
                              "settings.modelConfig.featureModels.removeChannelShort",
                            )}
                          </Button>
                        ) : (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              onAdd(item.provider as FeatureModelProvider)
                            }
                            disabled={item.status !== 1}
                          >
                            {item.status === 1
                              ? t(
                                  "settings.modelConfig.featureModels.addChannelShort",
                                )
                              : t(
                                  "settings.modelConfig.featureModels.channelUnavailable",
                                )}
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })
              ) : (
                <p className="px-5 py-10 text-center text-xs text-muted-foreground">
                  {t("settings.modelConfig.featureModels.noMatchingChannels")}
                </p>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ProviderChannelRow({
  provider,
  channelType,
  savedChannel,
  newApiBaseUrl,
  database,
  defaultComfyWorkflows,
}: {
  provider: FeatureModelProvider;
  channelType: NewApiChannelType | undefined;
  savedChannel: SavedProviderChannelConfig | undefined;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  defaultComfyWorkflows?: {
    model: string;
    workflows: Record<string, Record<string, unknown>>;
  };
}) {
  const { t } = useTranslation();
  // [aipdd fork delta] MediaKit 不在这张列表里占行（见 HIDDEN_CHANNEL_PROVIDERS）：
  // 它的凭证子区块在上方「渠道连接」的 VolcEngine 卡内，清除也在那里。
  const channel = useSettingsStore(
    (s) => s.featureModelConfig.providerChannels[provider],
  );
  const updateFeatureProviderChannel = useSettingsStore(
    (s) => s.updateFeatureProviderChannel,
  );
  const removeFeatureProviderChannel = useSettingsStore(
    (s) => s.removeFeatureProviderChannel,
  );
  const clearFeatureProviderUpstreamKey = useSettingsStore(
    (s) => s.clearFeatureProviderUpstreamKey,
  );
  const syncProviderChannel = useSyncProviderChannel();
  const clearComfyUIConfig = useClearComfyUIConfig();
  const [revealed, setRevealed] = useState(false);
  const upstreamKeyValue = channel?.upstreamKey ?? "";
  const savedKeyPreview = savedChannel?.configured
    ? savedChannel.upstreamKeyPreview
    : "";
  const upstreamPlaceholder = savedKeyPreview || "sk-...";
  const isComfyUI = provider === "comfyui";
  const hasComfyUIConfig = isComfyUI && savedChannel?.configured === true;
  useEffect(() => {
    if (!upstreamKeyValue) setRevealed(false);
  }, [upstreamKeyValue]);
  const handleSync = async () => {
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const upstreamKey = upstreamKeyValue.trim();
    if (!isComfyUI && !upstreamKey && !savedChannel?.configured) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: channelType?.name || featureProviderLabel(provider),
        }),
      );
      return;
    }
    try {
      const res = await syncProviderChannel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        ...(upstreamKey ? { upstreamKey } : {}),
        baseUrl: (channel?.baseUrl ?? "").trim(),
      });
      if (res.ok !== true) {
        toast.error(
          getResponseErrorMessage(res, t("settings.modelConfig.requestFailed")),
        );
        return;
      }
      if (upstreamKey) {
        clearFeatureProviderUpstreamKey(provider);
        setRevealed(false);
      }
      toast.success(t("settings.modelConfig.featureModels.channelSynced"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.requestFailed"),
        ),
      );
    }
  };
  const handleClearComfyUI = async () => {
    const confirmed = await confirmDialog({
      title: t("settings.modelConfig.featureModels.clearComfyTitle"),
      description: t("settings.modelConfig.featureModels.clearComfyConfirm"),
      confirmText: t("settings.modelConfig.featureModels.clearComfy"),
      confirmVariant: "destructive",
    });
    if (!confirmed) return;
    try {
      const response = await clearComfyUIConfig.mutateAsync();
      if (response.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            response,
            t("settings.modelConfig.requestFailed"),
          ),
        );
        return;
      }
      removeFeatureProviderChannel("comfyui");
      const currentMediaModels =
        useSettingsStore.getState().featureModelConfig.mediaModels ?? {};
      useSettingsStore
        .getState()
        .setMediaModels(
          Object.fromEntries(
            Object.entries(currentMediaModels).filter(
              ([, entry]) => entry.provider !== "comfyui",
            ),
          ),
        );
      toast.success(t("settings.modelConfig.featureModels.comfyCleared"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.requestFailed"),
        ),
      );
    }
  };
  const handleLoadDefaultComfyWorkflows = async () => {
    if (!isComfyUI || !defaultComfyWorkflows) return;
    const initialSettings = channel?.settings ?? {};
    const currentModel = readComfyUIModelName(initialSettings);
    if (currentModel && currentModel !== defaultComfyWorkflows.model) {
      const confirmed = await confirmDialog({
        title: t("settings.modelConfig.featureModels.replaceComfyModelTitle"),
        description: t(
          "settings.modelConfig.featureModels.replaceComfyModelConfirm",
          {
            currentModel,
            nextModel: defaultComfyWorkflows.model,
          },
        ),
        confirmText: t("settings.modelConfig.featureModels.replaceComfyModel"),
        confirmVariant: "destructive",
      });
      if (!confirmed) return;
    }
    const latestChannel =
      useSettingsStore.getState().featureModelConfig.providerChannels[provider];
    const currentSettings = latestChannel?.settings ?? {};
    const currentComfyUI =
      currentSettings.comfyui &&
      typeof currentSettings.comfyui === "object" &&
      !Array.isArray(currentSettings.comfyui)
        ? (currentSettings.comfyui as Record<string, unknown>)
        : {};
    const nextComfyUI = { ...currentComfyUI };
    delete nextComfyUI.workflow_by_model;
    delete nextComfyUI.workflow;
    updateFeatureProviderChannel(provider, {
      // [aipdd fork delta req41] 不兜底 ``http://127.0.0.1:8188``：ComfyUI 地址是部署方
      // 在渠道里填的数据，没渠道就不给这个字段（下游自己报未配置）。
      baseUrl: latestChannel?.baseUrl || "",
      settings: {
        ...currentSettings,
        comfyui: {
          ...nextComfyUI,
          model_name: defaultComfyWorkflows.model,
          workflow_routes: buildComfyUIWorkflowRoutes({
            ...defaultComfyWorkflows.workflows,
            ...readComfyUIWorkflows(currentSettings),
          }),
        },
      },
    });
  };

  return (
    <div className="rounded-md border border-border/60 p-2.5">
      <div className="grid gap-2 sm:grid-cols-[130px_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
        <div>
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.featureModels.channelProvider")}
          </Label>
          <div className="mt-1.5 h-9 rounded-md border border-border/70 bg-white/[0.03] px-3 py-2 text-xs text-foreground">
            {channelType?.name || featureProviderLabel(provider)}
          </div>
        </div>
        <div>
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.featureModels.upstreamKey")}
          </Label>
          <div className="relative mt-1.5">
            <Input
              name={`provider-${provider}-upstream-api-key`}
              autoComplete="new-password"
              data-1p-ignore="true"
              data-lpignore="true"
              type={revealed ? "text" : "password"}
              value={upstreamKeyValue}
              onChange={(e) =>
                updateFeatureProviderChannel(provider, {
                  upstreamKey: e.target.value,
                })
              }
              placeholder={
                savedKeyPreview
                  ? t("settings.secretSavedPlaceholder", {
                      preview: savedKeyPreview,
                    })
                  : upstreamPlaceholder
              }
              autoCapitalize="none"
              spellCheck={false}
              className={cn(
                "h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30",
                upstreamKeyValue ? "pr-9" : savedKeyPreview ? "pr-16" : "",
              )}
            />
            {upstreamKeyValue ? (
              <button
                type="button"
                onClick={() => setRevealed((r) => !r)}
                aria-label={
                  revealed
                    ? t("settings.mediaStorage.hideSecret")
                    : t("settings.mediaStorage.showSecret")
                }
                className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
              >
                {revealed ? (
                  <EyeOff className="size-4" />
                ) : (
                  <Eye className="size-4" />
                )}
              </button>
            ) : savedKeyPreview ? (
              <span className="absolute top-1/2 right-2 -translate-y-1/2 rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
                {t("settings.secretSavedBadge")}
              </span>
            ) : null}
          </div>
        </div>
        <div>
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.featureModels.baseUrlOverride")}
          </Label>
          <Input
            value={channel?.baseUrl ?? ""}
            onChange={(e) => {
              updateFeatureProviderChannel(provider, {
                baseUrl: e.target.value,
              });
            }}
            placeholder={
              channelType?.defaultBaseUrl ||
              t("settings.modelConfig.featureModels.baseUrlPlaceholder")
            }
            disabled={channelType?.supportsBaseUrlOverride === false}
            className="mt-1.5 h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
          />
        </div>
        <div className="flex items-center justify-end gap-1.5 sm:self-end">
          {!isComfyUI ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-9 whitespace-nowrap px-2 text-[11px]"
              onClick={handleSync}
              disabled={syncProviderChannel.isPending}
              title={t("settings.modelConfig.featureModels.syncChannelHint")}
            >
              {syncProviderChannel.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RotateCw className="size-3.5" />
              )}
              {t("settings.modelConfig.featureModels.syncChannel")}
            </Button>
          ) : null}
          {hasComfyUIConfig ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 whitespace-nowrap text-destructive hover:text-destructive"
              onClick={() => void handleClearComfyUI()}
              disabled={clearComfyUIConfig.isPending}
            >
              {clearComfyUIConfig.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Trash2 className="size-3.5" />
              )}
              {t("settings.modelConfig.featureModels.clearComfy")}
            </Button>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => removeFeatureProviderChannel(provider)}
              title={t("settings.modelConfig.featureModels.removeChannel")}
            >
              <Trash2 className="size-4" />
            </Button>
          )}
        </div>
      </div>
      {isComfyUI ? (
        <ComfyUIWorkflowsEditor
          settings={channel?.settings ?? {}}
          onLoadDefaultTemplate={
            defaultComfyWorkflows ? handleLoadDefaultComfyWorkflows : undefined
          }
          onChange={(settings) =>
            updateFeatureProviderChannel(provider, { settings })
          }
        />
      ) : null}
    </div>
  );
}

function ComfyUIWorkflowsEditor({
  settings,
  onChange,
  onLoadDefaultTemplate,
}: {
  settings: Record<string, unknown>;
  onChange: (settings: Record<string, unknown>) => void;
  onLoadDefaultTemplate?: () => void;
}) {
  const { t } = useTranslation();
  const workflows = readComfyUIWorkflows(settings);
  const model = readComfyUIModelName(settings);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [expandedWorkflows, setExpandedWorkflows] = useState<Set<string>>(
    () => new Set(),
  );

  const writeConfig = (
    nextModel: string,
    nextWorkflows: Record<string, Record<string, unknown>>,
  ) => {
    const existingComfyUI =
      settings.comfyui &&
      typeof settings.comfyui === "object" &&
      !Array.isArray(settings.comfyui)
        ? (settings.comfyui as Record<string, unknown>)
        : {};
    const nextComfyUI = { ...existingComfyUI };
    delete nextComfyUI.workflow_by_model;
    delete nextComfyUI.workflow;
    nextComfyUI.model_name = nextModel;
    nextComfyUI.workflow_routes = buildComfyUIWorkflowRoutes(nextWorkflows);
    onChange({
      ...settings,
      comfyui: nextComfyUI,
    });
  };

  const addWorkflow = () => {
    let index = Object.keys(workflows).length + 1;
    let workflowId = `workflow-${index}`;
    while (workflows[workflowId]) workflowId = `workflow-${++index}`;
    writeConfig(model, { ...workflows, [workflowId]: {} });
    setDrafts((current) => ({ ...current, [workflowId]: "{}" }));
  };

  const renameWorkflow = (previous: string, nextValue: string) => {
    const next = nextValue.trim();
    if (!next || next === previous) return;
    if (workflows[next]) {
      toast.error(
        t("settings.modelConfig.featureModels.comfyDuplicateWorkflow"),
      );
      return;
    }
    const renamed: Record<string, Record<string, unknown>> = {};
    for (const [model, workflow] of Object.entries(workflows)) {
      renamed[model === previous ? next : model] = workflow;
    }
    writeConfig(model, renamed);
    setDrafts((current) => {
      const updated = {
        ...current,
        [next]:
          current[previous] ?? JSON.stringify(workflows[previous], null, 2),
      };
      delete updated[previous];
      return updated;
    });
  };

  const commitWorkflow = (workflowId: string, text: string) => {
    try {
      const parsed = JSON.parse(text) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error();
      writeConfig(model, {
        ...workflows,
        [workflowId]: parsed as Record<string, unknown>,
      });
    } catch {
      toast.error(t("settings.modelConfig.featureModels.comfyInvalidWorkflow"));
    }
  };

  return (
    <div className="mt-3 border-t border-border/60 pt-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.featureModels.comfyWorkflows")}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings.modelConfig.featureModels.comfyWorkflowHint")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {onLoadDefaultTemplate ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void onLoadDefaultTemplate()}
            >
              <Plus className="size-3.5" />
              {t("settings.modelConfig.quick.loadComfyTemplate")}
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={addWorkflow}
          >
            <Plus className="size-3.5" />
            {t("settings.modelConfig.featureModels.comfyAddWorkflow")}
          </Button>
        </div>
      </div>
      <div className="mt-3">
        <Label className="text-xs text-muted-foreground">
          {t("settings.modelConfig.featureModels.comfyModelName")}
        </Label>
        <Input
          value={model}
          onChange={(event) => writeConfig(event.target.value, workflows)}
          placeholder={t(
            "settings.modelConfig.featureModels.comfyModelPlaceholder",
          )}
          className="mt-1 h-8"
        />
      </div>
      <div className="mt-3 space-y-3">
        {Object.entries(workflows).map(([workflowId, workflow]) => {
          const text = drafts[workflowId] ?? JSON.stringify(workflow, null, 2);
          const isExpanded = expandedWorkflows.has(workflowId);
          return (
            <div
              key={workflowId}
              className="rounded-md border border-border/60 p-2.5"
            >
              <Label className="text-xs text-muted-foreground">
                {t("settings.modelConfig.featureModels.comfyWorkflowId")}
              </Label>
              <div className="flex items-center gap-2">
                <Input
                  defaultValue={workflowId}
                  onBlur={(event) =>
                    renameWorkflow(workflowId, event.target.value)
                  }
                  placeholder={t(
                    "settings.modelConfig.featureModels.comfyWorkflowIdPlaceholder",
                  )}
                  className="mt-1 h-8"
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => {
                    const next = { ...workflows };
                    delete next[workflowId];
                    writeConfig(model, next);
                  }}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
              <div className="mt-2 flex items-center justify-between gap-2">
                <Label className="text-xs text-muted-foreground">
                  {t("settings.modelConfig.featureModels.comfyWorkflowJson")}
                </Label>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2 text-[10px] text-muted-foreground"
                  onClick={() =>
                    setExpandedWorkflows((current) => {
                      const next = new Set(current);
                      if (next.has(workflowId)) next.delete(workflowId);
                      else next.add(workflowId);
                      return next;
                    })
                  }
                >
                  {isExpanded ? (
                    <Minimize2 className="size-3.5" />
                  ) : (
                    <Maximize2 className="size-3.5" />
                  )}
                  {t(
                    isExpanded
                      ? "settings.modelConfig.featureModels.comfyCollapseWorkflow"
                      : "settings.modelConfig.featureModels.comfyExpandWorkflow",
                  )}
                </Button>
              </div>
              <Textarea
                value={text}
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [workflowId]: event.target.value,
                  }))
                }
                onBlur={(event) =>
                  commitWorkflow(workflowId, event.target.value)
                }
                spellCheck={false}
                className={cn(
                  "mt-1 resize-none font-mono text-xs transition-[height] duration-200",
                  isExpanded ? "h-[60vh] min-h-80" : "h-44",
                )}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export {
  ProviderChannelsBlock,
  CHANNEL_CAPABILITY_FILTERS,
  ChannelCapabilitiesDialog,
  ProviderChannelRow,
  ComfyUIWorkflowsEditor,
};
