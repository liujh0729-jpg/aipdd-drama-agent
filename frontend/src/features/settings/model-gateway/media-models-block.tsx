// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：媒体模型块与目录编辑器（Catalog 字段组）。

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  useProviderCatalog,
  useSaveMediaModels,
  useSaveProviderChannels,
  type NewApiDatabaseConfigInput,
  type NewApiChannelType,
  type SavedMediaModelConfig,
  type SavedProviderChannelConfig,
} from "@/lib/queries/model-gateway";
import {
  useSettingsStore,
  normalizeMediaModelEntries,
  type FeatureModelProvider,
  type MediaModelEntry,
} from "@/stores/settingsStore";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  featureProviderLabel,
  MEDIA_ROW_GRID,
  MEDIA_SAVE_KINDS,
  isMediaSaveKind,
  CUSTOM_PROVIDER_CHANNEL_TYPES,
  readComfyUIWorkflows,
  readComfyUIModelName,
  normalizeProviderChannelSettings,
  COMFY_WORKFLOW_MANAGED_CONFIG_KEY,
  providersSupportingCapability,
} from "./shared";
import type { ModelKind } from "@/components/settings/model-classification";
import { FieldRow } from "./field-row";
import { catalogCandidates } from "./provider-catalog-block";

type MediaModelType = (typeof MEDIA_SAVE_KINDS)[number];

function defaultComfyMediaModelConfig(
  model: string,
  workflowIds: readonly string[] = [],
): MediaModelEntry {
  const routeTokens = new Set(
    (workflowIds.length > 0 ? workflowIds : [model]).flatMap((value) =>
      value.trim().toLowerCase().replace(/-/g, "_").split("_"),
    ),
  );
  const supportedModes: string[] = [];
  const referenceCapabilities: Record<string, number | boolean> = {};
  const isMiniMaxH3Local = model.trim().toLowerCase() === "minimax-h3-local";
  const resolutionOptions = isMiniMaxH3Local
    ? ["480p", "768p", "1080p"]
    : ["480p", "640p"];
  const ratioOptions = isMiniMaxH3Local
    ? ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
    : routeTokens.has("i2v")
      ? ["16:9", "1:1"]
      : ["1:1", "16:9"];
  if (
    routeTokens.has("t2v") ||
    (!routeTokens.has("i2v") && !routeTokens.has("r2v"))
  ) {
    supportedModes.push("text_to_video");
  }
  if (routeTokens.has("i2v")) {
    supportedModes.push(
      ...(isMiniMaxH3Local
        ? ["first_frame"]
        : ["image_to_video", "image_reference"]),
    );
    referenceCapabilities.referenceImageMax = 1;
  }
  if (routeTokens.has("r2v")) {
    supportedModes.push("all_reference");
    referenceCapabilities.referenceImageMax = 9;
    referenceCapabilities.referenceVideoMax = 3;
    referenceCapabilities.referenceAudioMax = 3;
  }
  return {
    provider: "comfyui",
    upstreamModel: model,
    mediaType: "video",
    label: model,
    enabled: true,
    sortOrder: 100,
    config: {
      request: { endpoint: "video/generations", parameters: [] },
      resolutionOptions,
      ratioOptions,
      minDuration: 4,
      maxDuration: 15,
      supportedModes,
      ...referenceCapabilities,
      [COMFY_WORKFLOW_MANAGED_CONFIG_KEY]: true,
    },
  };
}

function detachComfyWorkflowManagedConfig(
  entry: MediaModelEntry | undefined,
): MediaModelEntry | undefined {
  if (!entry?.config?.[COMFY_WORKFLOW_MANAGED_CONFIG_KEY]) return entry;
  const config = { ...entry.config };
  delete config[COMFY_WORKFLOW_MANAGED_CONFIG_KEY];
  return { ...entry, config };
}

function isBareComfyWorkflowMediaModel(entry: MediaModelEntry): boolean {
  if (entry.provider !== "comfyui") return false;
  const keys = Object.keys(entry.config ?? {});
  return keys.every(
    (key) => key === "request" || key === COMFY_WORKFLOW_MANAGED_CONFIG_KEY,
  );
}

function MediaModelsBlock({
  configuredProviders,
  channelTypeByProvider,
  newApiBaseUrl,
  database,
  savedChannelByProvider,
  savedMediaModels,
  comfyOnly = false,
  excludeComfyUI = false,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedMediaModels: Record<string, SavedMediaModelConfig> | undefined;
  comfyOnly?: boolean;
  excludeComfyUI?: boolean;
}) {
  const { t } = useTranslation();
  const localSavedMediaModels = useSettingsStore(
    (s) => s.featureModelConfig.mediaModels ?? {},
  );
  const providerChannels = useSettingsStore(
    (s) => s.featureModelConfig.providerChannels,
  );
  const setMediaModels = useSettingsStore((s) => s.setMediaModels);
  const saveProviderChannels = useSaveProviderChannels();
  const saveMediaModels = useSaveMediaModels();
  const [mediaModels, setLocalMediaModels] = useState(localSavedMediaModels);
  const [saveError, setSaveError] = useState("");
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [creatingModel, setCreatingModel] = useState(false);
  const savedMediaModelsKey = JSON.stringify(savedMediaModels);
  const lastSyncedBackendKey = useRef<string | null>(null);
  const comfyWorkflowModels = useMemo(() => {
    const model = readComfyUIModelName(
      providerChannels.comfyui?.settings ?? {},
    );
    return model ? [model] : [];
  }, [providerChannels.comfyui?.settings]);
  const comfyWorkflowIds = useMemo(
    () =>
      Object.keys(
        readComfyUIWorkflows(providerChannels.comfyui?.settings ?? {}),
      ).sort(),
    [providerChannels.comfyui?.settings],
  );
  // [aipdd fork delta req40] 行清单只来自用户已经保存的媒体映射（映射里的名字又是从
  // 渠道目录点选的）：前端不再内置厂商名，否则渠道下架某个模型时，这一行还会凭空出现，
  // 引导配出一个指向死名的默认值 —— 正是这次要根治的事故面。
  const mediaModelRows = useMemo(() => {
    const models = Object.keys(mediaModels).filter(
      (model) =>
        (comfyOnly
          ? mediaModels[model]?.provider === "comfyui"
          : !excludeComfyUI || mediaModels[model]?.provider !== "comfyui"),
    );
    return models
      .map((model) => {
        const entry = mediaModels[model];
        return {
          model,
          kind: entry?.mediaType ?? "video",
          officialOnly: false,
          sortOrder: entry?.sortOrder ?? 100,
        };
      })
      .sort(
        (left, right) =>
          left.sortOrder - right.sortOrder ||
          left.model.localeCompare(right.model),
      );
  }, [comfyOnly, excludeComfyUI, mediaModels]);

  useEffect(() => {
    if (savedMediaModels === undefined) return;
    if (lastSyncedBackendKey.current === savedMediaModelsKey) return;
    lastSyncedBackendKey.current = savedMediaModelsKey;
    const fromBackend = normalizeMediaModelEntries(
      Object.fromEntries(
        Object.entries(savedMediaModels ?? {}).map(([model, entry]) => [
          model,
          {
            provider: entry.provider as FeatureModelProvider,
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
    const next = fromBackend;
    const nextKey = JSON.stringify(next ?? {});
    setLocalMediaModels((current) =>
      JSON.stringify(current ?? {}) === nextKey ? current : (next ?? {}),
    );
  }, [savedMediaModels, savedMediaModelsKey]);

  useEffect(() => {
    setLocalMediaModels((current) => {
      let changed = false;
      const next = { ...current };
      const workflowModelSet = new Set(comfyWorkflowModels);
      for (const [model, entry] of Object.entries(next)) {
        if (
          entry.config?.[COMFY_WORKFLOW_MANAGED_CONFIG_KEY] === true &&
          !workflowModelSet.has(model)
        ) {
          delete next[model];
          changed = true;
        }
      }
      for (const model of comfyWorkflowModels) {
        const existing = next[model];
        if (!existing) {
          next[model] = defaultComfyMediaModelConfig(model, comfyWorkflowIds);
          changed = true;
        } else if (isBareComfyWorkflowMediaModel(existing)) {
          const defaults = defaultComfyMediaModelConfig(
            model,
            comfyWorkflowIds,
          );
          next[model] = {
            ...defaults,
            ...existing,
            config: { ...defaults.config, ...(existing.config ?? {}) },
          };
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [comfyWorkflowIds, comfyWorkflowModels]);

  const handleSave = async () => {
    setSaveError("");
    const next: typeof localSavedMediaModels = {};
    for (const row of mediaModelRows) {
      if (row.officialOnly) continue;
      const entry = mediaModels[row.model];
      if (entry?.provider && configuredProviders.includes(entry.provider)) {
        next[row.model] = {
          provider: entry.provider,
          upstreamModel: entry.upstreamModel.trim(),
          ...(entry.mediaType ? { mediaType: entry.mediaType } : {}),
          ...(entry.label ? { label: entry.label } : {}),
          enabled: entry.enabled !== false,
          sortOrder: entry.sortOrder ?? 100,
          config: entry.config ?? {},
        };
      }
    }
    if (!comfyOnly) {
      for (const [model, entry] of Object.entries(mediaModels)) {
        if (excludeComfyUI && entry.provider === "comfyui") {
          next[model] = entry;
        }
      }
    }
    if (Object.keys(next).length === 0) {
      toast.error(t("settings.modelConfig.mediaModels.noMappings"));
      return;
    }
    const missingProviders = Array.from(
      new Set(
        Object.values(next)
          .map((entry) => entry.provider)
          .filter(
            (provider) =>
              provider !== "comfyui" &&
              !(providerChannels[provider]?.upstreamKey ?? "").trim() &&
              !savedChannelByProvider.get(provider)?.configured,
          ),
      ),
    );
    if (missingProviders.length > 0) {
      const message = t("settings.modelConfig.featureModels.missingKeys", {
        providers: missingProviders
          .map((provider) => featureProviderLabel(provider))
          .join("、"),
      });
      setSaveError(message);
      toast.error(message);
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    try {
      if (comfyOnly) {
        const comfyChannel = providerChannels.comfyui;
        if (!comfyChannel) {
          toast.error(t("settings.modelConfig.featureModels.noChannels"));
          return;
        }
        const channelResult = await saveProviderChannels.mutateAsync({
          preserveUnmentioned: true,
          channels: [
            {
              provider: "comfyui",
              type:
                savedChannelByProvider.get("comfyui")?.type ??
                CUSTOM_PROVIDER_CHANNEL_TYPES.comfyui,
              baseUrl: comfyChannel.baseUrl.trim(),
              priority: comfyChannel.priority ?? 0,
              settings: normalizeProviderChannelSettings(
                "comfyui",
                comfyChannel.settings ?? {},
              ),
            },
          ],
        });
        if (channelResult.ok !== true) {
          const message = getResponseErrorMessage(
            channelResult,
            t("settings.modelConfig.requestFailed"),
          );
          setSaveError(message);
          toast.error(message);
          return;
        }
      }
      const res = await saveMediaModels.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        models: next,
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
      const { succeeded, failed, models, results } = res.data;
      if (failed > 0) {
        const firstError = results.find((item) => item.error)?.error;
        const message =
          firstError ||
          t("settings.modelConfig.featureModels.savedPartial", {
            succeeded,
            failed,
          });
        setSaveError(message);
        toast.warning(message);
        return;
      }
      const saved = Object.fromEntries(
        Object.entries(models).map(([model, entry]) => [
          model,
          {
            provider: entry.provider as FeatureModelProvider,
            upstreamModel: entry.upstreamModel,
            ...(entry.mediaType ? { mediaType: entry.mediaType } : {}),
            ...(entry.label ? { label: entry.label } : {}),
            enabled: entry.enabled !== false,
            sortOrder: entry.sortOrder ?? 100,
            config: entry.config ?? {},
          },
        ]),
      );
      setMediaModels(saved);
      setLocalMediaModels(saved);
      toast.success(t("settings.modelConfig.mediaModels.saved"));
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
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.mediaModels.title")}
          </h4>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.mediaModels.description")}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setCreatingModel(true)}
        >
          <Plus className="size-3.5" />
          {t("settings.modelConfig.mediaModels.addModel")}
        </Button>
      </div>

      <div
        className={cn(
          MEDIA_ROW_GRID,
          "mt-3 px-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.mediaModels.colType")}</span>
        <span>{t("settings.modelConfig.mediaModels.colModel")}</span>
        <span>{t("settings.modelConfig.mediaModels.colProvider")}</span>
        <span>{t("settings.modelConfig.mediaModels.colUpstreamModel")}</span>
        <span>{t("settings.modelConfig.mediaModels.colActions")}</span>
      </div>

      <div className="mt-2 rounded-md border border-border/70">
        {mediaModelRows.map((row, index) => {
          const entry = mediaModels[row.model];
          const value = entry?.provider ?? "";
          const capableProviders = providersSupportingCapability(
            configuredProviders,
            channelTypeByProvider,
            row.kind,
            entry?.provider,
          );
          return (
            <div
              key={row.model}
              className={cn(
                MEDIA_ROW_GRID,
                "px-3 py-2.5",
                index > 0 && "border-t border-border/70",
              )}
            >
              <span className="text-xs text-muted-foreground">
                {t(`settings.modelConfig.mediaModels.types.${row.kind}`)}
              </span>
              <code className="truncate rounded border border-border/60 bg-white/[0.03] px-2 py-1.5 text-[11px] text-muted-foreground">
                {row.model}
              </code>
              {row.officialOnly ? (
                <div className="h-8 rounded-md border border-border/60 bg-white/[0.03] px-3 py-1.5 text-xs text-muted-foreground">
                  {t("settings.modelConfig.mediaModels.officialOnly")}
                </div>
              ) : (
                <Select
                  value={value}
                  onValueChange={(provider) =>
                    setLocalMediaModels((prev) => ({
                      ...prev,
                      [row.model]: {
                        ...detachComfyWorkflowManagedConfig(prev[row.model]),
                        provider: provider as FeatureModelProvider,
                        upstreamModel: prev[row.model]?.upstreamModel ?? "",
                      },
                    }))
                  }
                  disabled={capableProviders.length === 0}
                >
                  <SelectTrigger size="sm" className="w-full">
                    <SelectValue
                      placeholder={t(
                        "settings.modelConfig.mediaModels.defaultProvider",
                      )}
                    >
                      {(provider: string) => featureProviderLabel(provider)}
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
              )}
              {row.officialOnly ? (
                <div className="h-8 rounded-md border border-border/60 bg-white/[0.03] px-3 py-1.5 text-xs text-muted-foreground">
                  {t("settings.modelConfig.mediaModels.officialOnly")}
                </div>
              ) : (
                <Input
                  value={entry?.upstreamModel ?? ""}
                  onChange={(event) =>
                    setLocalMediaModels((prev) => ({
                      ...prev,
                      [row.model]: {
                        ...detachComfyWorkflowManagedConfig(prev[row.model]),
                        provider:
                          prev[row.model]?.provider ??
                          capableProviders[0] ??
                          "ali",
                        upstreamModel: event.target.value,
                      },
                    }))
                  }
                  placeholder={t(
                    "settings.modelConfig.mediaModels.upstreamModelPlaceholder",
                    {
                      model: row.model,
                    },
                  )}
                  className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
                  disabled={capableProviders.length === 0}
                />
              )}
              <div className="flex items-center justify-end gap-1">
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  title={t(
                    "settings.modelConfig.mediaModels.editCapabilities",
                  )}
                  onClick={() => setEditingModel(row.model)}
                >
                  <Pencil className="size-3.5" />
                </Button>
                {!comfyOnly ? (
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    className="text-muted-foreground hover:text-destructive"
                    title={t("settings.modelConfig.mediaModels.removeModel")}
                    onClick={() =>
                      setLocalMediaModels((current) => {
                        const next = { ...current };
                        delete next[row.model];
                        return next;
                      })
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <Dialog
        open={creatingModel || editingModel !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            setCreatingModel(false);
            setEditingModel(null);
          }
        }}
      >
        <DialogContent className="max-h-[88vh] w-[calc(100vw-2rem)] max-w-[900px] overflow-hidden p-0 sm:max-w-[900px]">
          <DialogHeader className="sr-only">
            <DialogTitle>
              {t(
                editingModel
                  ? "settings.modelConfig.mediaModels.editModelTitle"
                  : "settings.modelConfig.mediaModels.addModelTitle",
              )}
            </DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[88vh]">
            <LocalMediaModelEditor
              key={editingModel ?? "__new_media_model__"}
              originalModel={editingModel}
              entry={editingModel ? mediaModels[editingModel] : undefined}
              configuredProviders={configuredProviders}
              channelTypeByProvider={channelTypeByProvider}
              comfyOnly={comfyOnly}
              onCancel={() => {
                setCreatingModel(false);
                setEditingModel(null);
              }}
              onSave={(model, entry) => {
                setLocalMediaModels((current) => {
                  const next = { ...current };
                  if (editingModel && editingModel !== model)
                    delete next[editingModel];
                  next[model] = entry;
                  return next;
                });
                setCreatingModel(false);
                setEditingModel(null);
              }}
            />
          </ScrollArea>
        </DialogContent>
      </Dialog>

      {saveError ? (
        <p className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">
          {saveError}
        </p>
      ) : null}

      <div className="mt-3 flex items-center justify-end gap-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {configuredProviders.length > 0
            ? t("settings.modelConfig.mediaModels.saveHint")
            : t("settings.modelConfig.mediaModels.noChannelsHint")}
        </p>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={
            configuredProviders.length === 0 ||
            saveProviderChannels.isPending ||
            saveMediaModels.isPending
          }
        >
          {saveProviderChannels.isPending || saveMediaModels.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}
          {t(
            comfyOnly
              ? "settings.modelConfig.mediaModels.saveVideo"
              : "settings.modelConfig.mediaModels.save",
          )}
        </Button>
      </div>
    </div>
  );
}

function LocalMediaModelEditor({
  originalModel,
  entry,
  configuredProviders,
  channelTypeByProvider,
  comfyOnly,
  // [aipdd fork delta] 从默认生成模型面板按行进入时，模型名就是该行的真实上游
  // 模型名：只读展示，避免改名把映射指向不存在的模型；同时不再要求渠道目录里
  // 有这条记录（行来自上游 /models 清单，比目录更权威）。
  modelReadOnly = false,
  hintKey = "settings.modelConfig.mediaModels.capabilitiesHint",
  submitLabelKey = "settings.modelConfig.mediaModels.applyModelEdit",
  onCancel,
  onSave,
}: {
  originalModel: string | null;
  entry?: MediaModelEntry;
  configuredProviders: readonly FeatureModelProvider[];
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>;
  comfyOnly: boolean;
  modelReadOnly?: boolean;
  hintKey?: string;
  submitLabelKey?: string;
  onCancel: () => void;
  onSave: (model: string, entry: MediaModelEntry) => void;
}) {
  const { t } = useTranslation();
  // [aipdd fork delta] 媒体类型不再只认图片/视频：默认生成模型面板按 TAB 传入
  // audio/music，编辑时必须原样保留，否则会把音频/音乐条目改写成视频模型。
  const initialType: MediaModelType = isMediaSaveKind(entry?.mediaType as ModelKind)
    ? (entry?.mediaType as MediaModelType)
    : "video";
  const [model, setModel] = useState(originalModel ?? "");
  const [label, setLabel] = useState(entry?.label ?? originalModel ?? "");
  const [mediaType, setMediaType] = useState<MediaModelType>(initialType);
  const initialProviders = providersSupportingCapability(
    configuredProviders,
    channelTypeByProvider,
    initialType,
    entry?.provider,
  );
  const [provider, setProvider] = useState<FeatureModelProvider>(
    entry?.provider ?? initialProviders[0] ?? "",
  );
  const [upstreamModel, setUpstreamModel] = useState(
    entry?.upstreamModel ?? originalModel ?? "",
  );
  const [enabled, setEnabled] = useState(entry?.enabled !== false);
  const [sortOrder, setSortOrder] = useState(entry?.sortOrder ?? 100);
  const isImageOrVideo = mediaType === "image" || mediaType === "video";
  // [aipdd fork delta] 音频 / 音乐不经网关，没有 request.endpoint 这一段；默认配置
  // 留空，避免把 images/video 的端点写进厂商直连协议。
  const defaultConfig = isImageOrVideo
    ? {
        request: {
          endpoint:
            mediaType === "image" ? "images/generations" : "video/generations",
          parameters: [],
        },
      }
    : {};
  const [configJson, setConfigJson] = useState(
    JSON.stringify(entry?.config ?? defaultConfig, null, 2),
  );
  const capableProviders = useMemo(
    () =>
      providersSupportingCapability(
        configuredProviders,
        channelTypeByProvider,
        mediaType,
        provider,
      ),
    [channelTypeByProvider, configuredProviders, mediaType, provider],
  );
  // [aipdd fork delta req40] 模型名只能从渠道目录点选：渠道下架一个模型，目录里就没有
  // 它了，配不出死映射。comfyui 用工作流名，不适用这一条。
  const providerCatalog = useProviderCatalog();
  const catalogEntries = useMemo(
    () =>
      catalogCandidates(
        providerCatalog.data?.data?.providers ?? [],
        provider,
        [mediaType],
      ),
    [mediaType, provider, providerCatalog.data],
  );
  const catalogDriven =
    !modelReadOnly && Boolean(provider) && provider !== "comfyui";
  const handleCatalogPick = (name: string | null) => {
    const clean = String(name || "").trim();
    if (!clean) return;
    setModel(clean);
    setUpstreamModel(clean);
    const currentLabel = label.trim();
    if (!currentLabel || currentLabel === model.trim() || currentLabel === model) {
      setLabel(clean);
    }
  };

  const parsedConfig = useMemo(() => {
    try {
      const parsed = JSON.parse(configJson) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }, [configJson]);
  const setCapability = (key: string, value: unknown) => {
    if (!parsedConfig) {
      toast.error(
        t("settings.modelConfig.mediaModels.invalidCapabilitiesJson"),
      );
      return;
    }
    const next = { ...parsedConfig };
    if (value === undefined || value === null || value === "") delete next[key];
    else next[key] = value;
    setConfigJson(JSON.stringify(next, null, 2));
  };
  const stringOptions = (key: string): string[] =>
    Array.isArray(parsedConfig?.[key])
      ? (parsedConfig[key] as unknown[]).map(String)
      : [];

  const handleMediaTypeChange = (nextType: MediaModelType) => {
    setMediaType(nextType);
    const nextProviders = providersSupportingCapability(
      configuredProviders,
      channelTypeByProvider,
      nextType,
    );
    if (!nextProviders.includes(provider)) {
      setProvider(nextProviders[0] ?? "");
    }
    // 音频 / 音乐没有网关端点概念：保留现有 config，不注入 images/video 端点。
    if (nextType !== "image" && nextType !== "video") return;
    try {
      const current = JSON.parse(configJson) as Record<string, unknown>;
      const request =
        current.request &&
        typeof current.request === "object" &&
        !Array.isArray(current.request)
          ? (current.request as Record<string, unknown>)
          : {};
      setConfigJson(
        JSON.stringify(
          {
            ...current,
            request: {
              ...request,
              endpoint:
                nextType === "image"
                  ? "images/generations"
                  : "video/generations",
              parameters: Array.isArray(request.parameters)
                ? request.parameters
                : [],
            },
          },
          null,
          2,
        ),
      );
    } catch {
      // Keep invalid draft text so the user can repair it before saving.
    }
  };

  const handleSave = () => {
    const cleanModel = model.trim();
    if (!cleanModel || !label.trim() || !provider) {
      toast.error(t("settings.modelConfig.mediaModels.missingModelFields"));
      return;
    }
    let config: Record<string, unknown>;
    try {
      const parsed = JSON.parse(configJson) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error();
      config = parsed as Record<string, unknown>;
      delete config[COMFY_WORKFLOW_MANAGED_CONFIG_KEY];
    } catch {
      toast.error(
        t("settings.modelConfig.mediaModels.invalidCapabilitiesJson"),
      );
      return;
    }
    if (
      catalogDriven &&
      !catalogEntries.some((entry) => String(entry.name || "").trim() === cleanModel)
    ) {
      toast.error(t("settings.modelConfig.providerCatalog.catalogPickRequired"));
      return;
    }
    onSave(cleanModel, {
      provider,
      upstreamModel: upstreamModel.trim() || cleanModel,
      mediaType,
      label: label.trim(),
      enabled,
      sortOrder,
      config,
    });
  };

  return (
    <div className="p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">
            {t(
              originalModel
                ? "settings.modelConfig.mediaModels.editModelTitle"
                : "settings.modelConfig.mediaModels.addModelTitle",
            )}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t(hintKey)}
          </p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        {modelReadOnly ? (
          <div className="grid grid-cols-[120px_1fr] items-center gap-3">
            <Label className="justify-start text-[11px] font-normal text-muted-foreground">
              {t("settings.modelConfig.mediaModels.modelId")}
            </Label>
            <Input
              value={model}
              readOnly
              className="h-8 text-xs"
              aria-label={t("settings.modelConfig.mediaModels.modelId")}
            />
          </div>
        ) : catalogDriven ? (
          <div className="grid grid-cols-[120px_1fr] items-center gap-3">
            <Label className="justify-start text-[11px] font-normal text-muted-foreground">
              {t("settings.modelConfig.mediaModels.modelId")}
            </Label>
            <Select
              value={model}
              onValueChange={handleCatalogPick}
              disabled={catalogEntries.length === 0}
            >
              <SelectTrigger size="sm">
                <SelectValue
                  placeholder={t("settings.modelConfig.providerCatalog.empty")}
                />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false}>
                {catalogEntries.map((entry) => (
                  <SelectItem key={entry.name} value={entry.name}>
                    {entry.name}
                    {entry.inferred ? " *" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <FieldRow
            label={t("settings.modelConfig.mediaModels.modelId")}
            value={model}
            onChange={setModel}
            placeholder={t("settings.modelConfig.mediaModels.modelIdPlaceholder")}
          />
        )}
        <FieldRow
          label={t("settings.modelConfig.mediaModels.displayName")}
          value={label}
          onChange={setLabel}
          placeholder="Seedance 2.0 Mini"
        />
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.mediaModels.mediaType")}
          </Label>
          <Select
            value={mediaType}
            onValueChange={(value) =>
              handleMediaTypeChange(value as MediaModelType)
            }
          >
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {MEDIA_SAVE_KINDS.map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {t(`settings.modelConfig.mediaModels.types.${kind}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.mediaModels.colProvider")}
          </Label>
          <Select
            value={provider}
            onValueChange={(value) =>
              setProvider(value as FeatureModelProvider)
            }
            disabled={comfyOnly || capableProviders.length === 0}
          >
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {capableProviders.map((item) => (
                <SelectItem key={item} value={item}>
                  {featureProviderLabel(item)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {modelReadOnly ? (
          <div className="col-span-2 grid grid-cols-[120px_1fr] items-center gap-3">
            <Label className="justify-start text-[11px] font-normal text-muted-foreground">
              {t("settings.modelConfig.mediaModels.colUpstreamModel")}
            </Label>
            <Input
              value={upstreamModel}
              readOnly
              className="h-8 text-xs"
              aria-label={t("settings.modelConfig.mediaModels.colUpstreamModel")}
            />
          </div>
        ) : catalogDriven ? (
          <div className="col-span-2 grid grid-cols-[120px_1fr] items-center gap-3">
            <Label className="justify-start text-[11px] font-normal text-muted-foreground">
              {t("settings.modelConfig.mediaModels.colUpstreamModel")}
            </Label>
            <Select
              value={upstreamModel}
              onValueChange={(value) => setUpstreamModel(value ?? "")}
              disabled={catalogEntries.length === 0}
            >
              <SelectTrigger size="sm">
                <SelectValue
                  placeholder={t("settings.modelConfig.providerCatalog.empty")}
                />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false}>
                {catalogEntries.map((entry) => (
                  <SelectItem key={entry.name} value={entry.name}>
                    {entry.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <div className="col-span-2">
            <FieldRow
              label={t("settings.modelConfig.mediaModels.colUpstreamModel")}
              value={upstreamModel}
              onChange={setUpstreamModel}
              placeholder={model || "upstream-model-name"}
            />
          </div>
        )}
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label className="justify-start text-[11px] font-normal text-muted-foreground">
            {t("settings.modelConfig.mediaModels.sortOrder")}
          </Label>
          <Input
            type="number"
            value={sortOrder}
            onChange={(event) => setSortOrder(Number(event.target.value) || 0)}
            className="h-8"
          />
        </div>
        <label className="flex items-center gap-2 text-xs text-foreground">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          {t("settings.modelConfig.mediaModels.enabled")}
        </label>
      </div>
      <div className="mt-3">
        {isImageOrVideo ? (
          <>
            <p className="text-[11px] font-medium text-foreground">
              {t("settings.modelConfig.mediaModels.commonCapabilities")}
            </p>
            <div className="mt-2 grid grid-cols-2 gap-3">
          <CatalogMultiSelectField
            label={t("settings.modelConfig.mediaModels.resolutionOptions")}
            value={stringOptions("resolutionOptions")}
            onChange={(value) => setCapability("resolutionOptions", value)}
            options={
              mediaType === "image"
                ? ["1K", "2K", "3K", "4K", "8K", "1024x1024", "2048x2048"]
                : ["480p", "720p", "1080p", "2K", "4K"]
            }
          />
          <CatalogMultiSelectField
            label={t("settings.modelConfig.mediaModels.ratioOptions")}
            value={stringOptions("ratioOptions")}
            onChange={(value) => setCapability("ratioOptions", value)}
            options={[
              "1:1",
              "16:9",
              "9:16",
              "4:3",
              "3:4",
              "3:2",
              "2:3",
              "21:9",
              "adaptive",
            ]}
          />
          {mediaType === "image" ? (
            <CatalogListField
              label={t("settings.modelConfig.mediaModels.qualityOptions")}
              value={stringOptions("qualityOptions")}
              onChange={(value) => setCapability("qualityOptions", value)}
              placeholder="low, medium, high"
            />
          ) : (
            <>
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.minDuration")}
                value={parsedConfig?.minDuration}
                onChange={(value) => setCapability("minDuration", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.maxDuration")}
                value={parsedConfig?.maxDuration}
                onChange={(value) => setCapability("maxDuration", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.referenceImageMax")}
                value={parsedConfig?.referenceImageMax}
                min={0}
                onChange={(value) => setCapability("referenceImageMax", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.referenceVideoMax")}
                value={parsedConfig?.referenceVideoMax}
                min={0}
                onChange={(value) => setCapability("referenceVideoMax", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.referenceAudioMax")}
                value={parsedConfig?.referenceAudioMax}
                min={0}
                onChange={(value) => setCapability("referenceAudioMax", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.referenceFileMax")}
                value={parsedConfig?.referenceFileMax}
                min={0}
                max={1}
                onChange={(value) => setCapability("referenceFileMax", value)}
              />
              <CatalogNumberField
                label={t("settings.modelConfig.mediaModels.referenceLinkMax")}
                value={parsedConfig?.referenceLinkMax}
                min={0}
                max={1}
                onChange={(value) => setCapability("referenceLinkMax", value)}
              />
              <CatalogListField
                label={t("settings.modelConfig.mediaModels.referenceFileTypes")}
                value={stringOptions("referenceFileTypes")}
                onChange={(value) => setCapability("referenceFileTypes", value)}
                placeholder="pdf, docx, xlsx, pptx, txt, md"
              />
              {[
                "referenceAudioMinSeconds",
                "referenceAudioMaxSeconds",
                "referenceAudioTotalMinSeconds",
                "referenceAudioTotalMaxSeconds",
                "referenceVideoMinSeconds",
                "referenceVideoMaxSeconds",
                "referenceVideoTotalMinSeconds",
                "referenceVideoTotalMaxSeconds",
              ].map((field) => (
                <CatalogNumberField
                  key={field}
                  label={t(`settings.modelConfig.mediaModels.${field}`)}
                  value={parsedConfig?.[field]}
                  min={0.1}
                  step={0.1}
                  onChange={(value) => setCapability(field, value)}
                />
              ))}
              <label className="flex items-center gap-2 text-xs text-foreground">
                <input
                  type="checkbox"
                  checked={parsedConfig?.humanReview === true}
                  onChange={(event) =>
                    setCapability("humanReview", event.target.checked)
                  }
                />
                {t("settings.modelConfig.mediaModels.humanReview")}
              </label>
              <div className="col-span-2">
                <p className="mb-2 text-[11px] text-muted-foreground">
                  {t("settings.modelConfig.mediaModels.supportedModes")}
                </p>
                <div className="grid grid-cols-3 gap-2">
                  {/* 左边是后端目录里的模式值，右边只是展示名，和视频节点的页签共用词条。 */}
                  {[
                    ["text_to_video", "node.videoNode.tabs.textToVideo"],
                    ["first_frame", "node.videoNode.tabs.firstFrame"],
                    ["first_last_frame", "node.videoNode.tabs.firstLastFrame"],
                    ["image_to_video", "node.videoNode.tabs.imageToVideo"],
                    ["image_reference", "node.videoNode.tabs.imageReference"],
                    ["all_reference", "node.videoNode.tabs.allReference"],
                    ["video_edit", "node.videoNode.tabs.videoEdit"],
                    ["video_extend", "node.videoNode.tabs.videoExtend"],
                  ].map(([value, labelKey]) => {
                    const selected = stringOptions("supportedModes");
                    return (
                      <label
                        key={value}
                        className="flex items-center gap-2 text-xs text-foreground"
                      >
                        <input
                          type="checkbox"
                          checked={selected.includes(value)}
                          onChange={(event) =>
                            setCapability(
                              "supportedModes",
                              event.target.checked
                                ? [...selected, value]
                                : selected.filter((item) => item !== value),
                            )
                          }
                        />
                        {t(labelKey)}
                      </label>
                    );
                  })}
                </div>
              </div>
            </>
          )}
            </div>
          </>
        ) : (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.mediaModels.jsonOnlyHint")}
          </p>
        )}
      </div>
      <div className="mt-3 border-t border-border/60 pt-3">
        <Label className="text-[11px] font-normal text-muted-foreground">
          {t("settings.modelConfig.mediaModels.capabilitiesJson")}
        </Label>
        <Textarea
          value={configJson}
          onChange={(event) => setConfigJson(event.target.value)}
          spellCheck={false}
          className="mt-1 h-56 resize-y font-mono text-[11px]"
        />
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t("common.cancel")}
        </Button>
        <Button type="button" size="sm" onClick={handleSave}>
          {t(submitLabelKey)}
        </Button>
      </div>
    </div>
  );
}

function CatalogListField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  placeholder: string;
}) {
  const normalized = value.join(", ");
  const [draft, setDraft] = useState(normalized);
  useEffect(() => setDraft(normalized), [normalized]);
  return (
    <div>
      <Label className="text-[11px] font-normal text-muted-foreground">
        {label}
      </Label>
      <Input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() =>
          onChange([
            ...new Set(
              draft
                .split(/[,，]/)
                .map((item) => item.trim())
                .filter(Boolean),
            ),
          ])
        }
        placeholder={placeholder}
        className="mt-1 h-8"
      />
    </div>
  );
}

function CatalogMultiSelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string[];
  options: readonly string[];
  onChange: (value: string[]) => void;
}) {
  const { t } = useTranslation();
  const [customValue, setCustomValue] = useState("");
  const choices = [
    ...options,
    ...value.filter((item) => !options.includes(item)),
  ];
  const toggle = (option: string, selected: boolean) => {
    onChange(
      selected
        ? [...new Set([...value, option])]
        : value.filter((item) => item !== option),
    );
  };
  const addCustomValues = () => {
    const additions = customValue
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (additions.length === 0) return;
    onChange([...new Set([...value, ...additions])]);
    setCustomValue("");
  };

  return (
    <div>
      <Label className="text-[11px] font-normal text-muted-foreground">
        {label}
      </Label>
      <div className="mt-1 rounded-md border border-input/80 bg-background/30 p-2">
        <div className="flex flex-wrap gap-x-3 gap-y-2">
          {choices.map((option) => (
            <label
              key={option}
              className="flex items-center gap-1.5 text-xs text-foreground"
            >
              <input
                type="checkbox"
                checked={value.includes(option)}
                onChange={(event) => toggle(option, event.target.checked)}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
        <div className="mt-2 flex gap-2 border-t border-border/60 pt-2">
          <Input
            value={customValue}
            onChange={(event) => setCustomValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addCustomValues();
              }
            }}
            placeholder={t(
              "settings.modelConfig.mediaModels.customOptionPlaceholder",
            )}
            className="h-7"
          />
          <Button
            type="button"
            size="icon-sm"
            variant="outline"
            title={t("settings.modelConfig.mediaModels.addCustomOption")}
            onClick={addCustomValues}
            disabled={!customValue.trim()}
          >
            <Plus className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function CatalogNumberField({
  label,
  value,
  min = 1,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: unknown;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number | undefined) => void;
}) {
  return (
    <div>
      <Label className="text-[11px] font-normal text-muted-foreground">
        {label}
      </Label>
      <Input
        type="number"
        min={min}
        max={max}
        step={step}
        value={typeof value === "number" ? value : ""}
        onChange={(event) =>
          onChange(event.target.value ? Number(event.target.value) : undefined)
        }
        className="mt-1 h-8"
      />
    </div>
  );
}

export {
  defaultComfyMediaModelConfig,
  detachComfyWorkflowManagedConfig,
  isBareComfyWorkflowMediaModel,
  MediaModelsBlock,
  LocalMediaModelEditor,
  CatalogListField,
  CatalogMultiSelectField,
  CatalogNumberField,
};
