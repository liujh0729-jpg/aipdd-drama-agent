// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：上游渠道连接卡（AIPDD/OpenAI 兼容/ComfyUI，MediaKit 作为 VolcEngine 子块）。

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Eye,
  EyeOff,
  Loader2,
  Plus,
  RotateCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/components/confirm-dialog-host";
import {
  useSaveProviderChannels,
  useFetchUpstreamModels,
  useResolveComfyUIWorkflows,
  useAdaptComfyUIWorkflows,
  useSaveVideoEnhanceConfig,
  type ModelGatewayConfig,
  type SavedProviderChannelConfig,
  type VideoEnhanceConfig,
} from "@/lib/queries/model-gateway";
import { useModelPlaza } from "@/lib/queries/model-plaza";
import { useSettingsStore } from "@/stores/settingsStore";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  AIPDD_BASE_URL,
  ARK_BASE_URL,
  MEDIA_DEFAULT_TABS,
  MEDIAKIT_BASE_URL,
  MINIMAX_BASE_URL,
  MINIMAX_CN_BASE_URL,
  OPENAI_OFFICIAL_BASE_URL,
  ANTHROPIC_BASE_URL,
  GEMINI_BASE_URL,
} from "./shared";

// [aipdd fork delta] 简洁的“渠道连接”卡片：把 AIPDD / 外部 NewAPI（OpenAI 兼容）
// 的 Base URL + Key 直接摆到自定义页最前面，点“保存并同步”即写入渠道真源（settings 库）。
// 渠道 provider key 为四大渠道：aipdd / newapi / volcengine / comfyui。
// [aipdd fork delta] AIPDD / VolcEngine 的 Base URL 是本产品的固定端点（不随部署环境变，
// 用户填错只会把已跑通的渠道拉坏），所以只读展示不录入；NewAPI / ComfyUI 地址
// 因用户而异，仍然是输入框。
// 火山 AI MediaKit 不再占一张独立卡：它本质属于 VolcEngine 渠道，因此作为 VolcEngine
// 卡内的子区块（extra）交付；它的 Base URL 同样是固定端点，只读展示。
// 底层复用已验证的 useSaveProviderChannels，ComfyUI 与逐模型映射仍在下方高级表格。
function ProviderConnectCard({
  title,
  description,
  provider,
  presetBaseUrl,
  fixedBaseUrl = false,
  saved,
  asrCredentials = false,
  minimaxVoiceCredentials = false,
  extra,
}: {
  title: string;
  description: string;
  provider: string;
  presetBaseUrl: string;
  /**
   * [aipdd fork delta] Base URL 是本产品的固定端点（AIPDD / VolcEngine），不给填。
   * 真源写死在 `presetBaseUrl`，保存时一律用它——即使用户早先存过一个错的 Base，
   * 再点一次保存就被改回固定值（渠道因此跑不通是常见事故，不是兼容面）。
   */
  fixedBaseUrl?: boolean;
  saved?: SavedProviderChannelConfig;
  /** [aipdd fork delta] VolcEngine 卡专属：附带豆包语音 ASR 凭证（存渠道 settings.asr）。 */
  asrCredentials?: boolean;
  /**
   * [aipdd fork delta req46] MiniMax 卡专属：Speech 2.8 直连合成的默认系统音色
   * （存渠道 settings.minimax.voiceId，与 volcengine 的 settings.asr 同形态）。
   */
  minimaxVoiceCredentials?: boolean;
  /**
   * [aipdd fork delta] 卡内子区块：归属于本渠道、但凭证真源独立的配置（如 VolcEngine
   * 下的火山 AI MediaKit）。自成一块并自带保存入口，排在渠道主体与上游模型列表之后。
   */
  extra?: ReactNode;
}) {
  const save = useSaveProviderChannels();
  const fetchModels = useFetchUpstreamModels();
  const { t } = useTranslation();
  // [aipdd fork delta] 渠道连接卡保存后与「高级配置 → 供应商渠道」保持对应。
  const addFeatureProviderChannel = useSettingsStore(
    (s) => s.addFeatureProviderChannel,
  );
  const updateFeatureProviderChannel = useSettingsStore(
    (s) => s.updateFeatureProviderChannel,
  );
  const [baseUrl, setBaseUrl] = useState(
    fixedBaseUrl ? presetBaseUrl : saved?.baseUrl || presetBaseUrl,
  );
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  // [aipdd fork delta] 豆包语音 ASR 凭证（仅 VolcEngine 卡渲染）：单个 API Key
  // 存 volcengine 渠道 settings.asr.apiKey，供抖音文案提取的豆包语音直连使用
  //（官方：任意接口带 X-Api-Key header 即可、无需 appid）。
  // 已保存值不回填明文，输入即覆盖（合并进原 settings，不丢其它扩展键）。
  const savedSettings = (saved?.settings ?? {}) as Record<string, unknown>;
  const savedAsr = (savedSettings.asr ?? {}) as Record<string, unknown>;
  const [asrApiKey, setAsrApiKey] = useState("");
  // [aipdd fork delta req46] MiniMax 默认音色（仅 MiniMax 卡渲染）：存渠道 settings.minimax.voiceId，
  // Speech 2.8 直连合成在请求未指定音色时用它兜底。它不是凭证而是配置项，因此回显已保存值；
  // 只在改动时才携带 settings（合并进原 settings，不丢其它扩展键）。
  const savedMinimax = (
    (savedSettings.minimax ?? {}) as Record<string, unknown>
  ).voiceId;
  const savedMinimaxVoiceId =
    typeof savedMinimax === "string" ? savedMinimax.trim() : "";
  const [minimaxVoiceId, setMinimaxVoiceId] = useState(savedMinimaxVoiceId);
  useEffect(() => {
    setMinimaxVoiceId(savedMinimaxVoiceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedMinimaxVoiceId]);
  useEffect(() => {
    // 固定端点不接受真源回显：直接按常量写，顺手把历史上存歪的 Base URL 纠正回来。
    if (fixedBaseUrl) {
      setBaseUrl(presetBaseUrl);
      return;
    }
    if (saved?.baseUrl) setBaseUrl(saved.baseUrl);
  }, [fixedBaseUrl, presetBaseUrl, saved?.baseUrl]);
  const loadModels = useCallback(async () => {
    try {
      const res = await fetchModels.mutateAsync({ provider });
      if (res.ok === true) {
        setModels(res.data.models ?? []);
        if ((res.data.count ?? 0) === 0) {
          toast.info(t("settings.modelConfig.cards.noModelList", { title }));
        }
      } else {
        toast.error(
          getResponseErrorMessage(res, t("settings.modelConfig.cards.fetchFailed")),
        );
      }
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.cards.fetchFailed"),
        ),
      );
    }
  }, [fetchModels, provider, title]);
  useEffect(() => {
    if (saved?.configured) void loadModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved?.configured]);
  // [aipdd fork delta] 卡片只负责「连接渠道 + 拉取上游模型」；默认模型统一在下方
  // 「默认生成模型」面板设置，避免同一处出现两套默认入口导致互相覆盖、界面无回显。
  const busy = save.isPending;
  const handleSave = async () => {
    const trimmedKey = key.trim();
    if (!trimmedKey && !saved?.configured) {
      toast.error(t("settings.modelConfig.cards.needApiKey"));
      return;
    }
    if (!baseUrl.trim()) {
      toast.error(t("settings.modelConfig.cards.needBaseUrl"));
      return;
    }
    try {
      // [aipdd fork delta] 填了 ASR 凭证才携带 settings（合并原 settings，不丢扩展
      // 键）；未填写时一律不传，后端保留渠道原有 settings。
      // [aipdd fork delta req46] MiniMax 默认音色同理：只在相对已保存值发生变化时
      // 写 settings.minimax，没改就不碰渠道现有配置。
      const asrDirty = Boolean(asrApiKey.trim());
      const nextMinimaxVoiceId = minimaxVoiceId.trim();
      const minimaxDirty = nextMinimaxVoiceId !== savedMinimaxVoiceId;
      const nextSettings: Record<string, unknown> | undefined =
        asrDirty || minimaxDirty
          ? {
              ...savedSettings,
              ...(asrDirty
                ? {
                    asr: {
                      ...savedAsr,
                      apiKey: asrApiKey.trim(),
                    },
                  }
                : {}),
              ...(minimaxDirty
                ? {
                    minimax: {
                      ...(savedSettings.minimax ?? {}),
                      voiceId: nextMinimaxVoiceId,
                    },
                  }
                : {}),
            }
          : undefined;
      const res = await save.mutateAsync({
        preserveUnmentioned: true,
        channels: [
          {
            provider,
            ...(trimmedKey ? { upstreamKey: trimmedKey } : {}),
            baseUrl: baseUrl.trim(),
            ...(nextSettings ? { settings: nextSettings } : {}),
          },
        ],
      });
      if (res.ok !== true) {
        toast.error(
          getResponseErrorMessage(res, t("settings.modelConfig.cards.saveFailed")),
        );
        return;
      }
      setKey("");
      setAsrApiKey("");
      // [aipdd fork delta] 与「高级配置 → 供应商渠道」对应：保存成功即同步本地
      // 渠道行（幂等），高级配置无需再手工添加该渠道。
      addFeatureProviderChannel(provider);
      updateFeatureProviderChannel(provider, { baseUrl: baseUrl.trim() });
      toast.success(t("settings.modelConfig.cards.savedAndSynced", { title }));
      // [aipdd fork delta req40] 某些渠道保存后后端会自动拉取向量模型并设默认；把
      // 结果播报出来，命中/告警/未命中分别引导，失败也不影响渠道保存。名字一律用后端
      // 回传的目录条目，前端不再写死厂商模型名做兜底文案。
      const autoEmbedding = res.data.autoEmbedding;
      if (autoEmbedding) {
        if (autoEmbedding.found && autoEmbedding.applied) {
          toast.success(
            t("settings.embedding.autoPulled", {
              model: autoEmbedding.model || "",
            }),
          );
          if (autoEmbedding.warning) {
            toast.warning(autoEmbedding.warning);
          }
        } else if (!autoEmbedding.found) {
          toast.info(
            t("settings.embedding.autoPullMiss", {
              reason: autoEmbedding.reason || "",
            }),
          );
        }
      }
      void loadModels();
      window.dispatchEvent(new Event("media-model-catalog-updated"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.cards.saveFailed"),
        ),
      );
    }
  };

  return (
    <div className="rounded-md border border-border/70 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">{title}</p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 text-[11px]",
            saved?.configured ? "text-emerald-400" : "text-amber-300",
          )}
        >
          {saved?.configured
            ? t("settings.modelConfig.cards.connectedPreview", {
                preview: saved.upstreamKeyPreview ?? "",
              })
            : t("settings.modelConfig.cards.notConnected")}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {fixedBaseUrl ? (
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">
              {t("settings.modelConfig.cards.baseUrlFixedLabel")}
            </Label>
            <p
              title={presetBaseUrl}
              className="truncate rounded-md border border-border/60 bg-white/[0.03] px-2 py-1.5 font-mono text-[11px] text-muted-foreground"
            >
              {presetBaseUrl}
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">Base URL</Label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={presetBaseUrl}
              className="h-8 text-xs"
              autoComplete="off"
            />
          </div>
        )}
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">API Key</Label>
          <div className="flex items-center gap-2">
            <Input
              type={reveal ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                saved?.configured
                  ? t("settings.modelConfig.cards.keyPlaceholderKeep")
                  : t("settings.modelConfig.cards.keyPlaceholderPaste")
              }
              className="h-8 text-xs"
              autoComplete="off"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              onClick={() => setReveal((v) => !v)}
            >
              {reveal ? (
                <EyeOff className="size-3.5" />
              ) : (
                <Eye className="size-3.5" />
              )}
            </Button>
          </div>
        </div>
        {/*
          [aipdd fork delta] 区块次序：可编辑项在上、只读信息在中、只留一枚保存按钮在下。
          渠道主体 → 上游模型（只读）→ 可选凭证（豆包语音）→「保存并同步」：
          可选凭证不再插在主 Key 与模型列表之间，但它仍在按钮上方，填完不必回头找保存。
        */}
        {saved?.configured ? (
          <div className="space-y-2 border-t border-border/60 pt-2">
            <div className="flex items-center justify-between">
              <Label className="text-[11px] text-muted-foreground">
                {t("settings.modelConfig.cards.upstreamModels", {
                  status: fetchModels.isPending
                    ? t("settings.modelConfig.cards.fetching")
                    : models.length,
                })}
              </Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-[11px]"
                onClick={() => void loadModels()}
                disabled={fetchModels.isPending}
              >
                {fetchModels.isPending ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <RotateCw className="size-3" />
                )}
                {t("settings.modelConfig.cards.fetchModels")}
              </Button>
            </div>
            {models.length > 0 ? (
              <div className="max-h-32 space-y-1.5 overflow-y-auto pr-1">
                <div className="flex flex-wrap gap-1.5">
                  {models.map((m) => (
                    <span
                      key={m}
                      className="rounded border border-border/60 bg-white/[0.03] px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {m}
                    </span>
                  ))}
                </div>
                <p className="text-[11px] text-muted-foreground">
                  {t("settings.modelConfig.cards.defaultModelHint")}
                </p>
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                {t("settings.modelConfig.cards.noModelsHint")}
              </p>
            )}
          </div>
        ) : null}
        {asrCredentials ? (
          <div className="space-y-2 rounded-md border border-border/60 bg-white/[0.02] p-2.5">
            <p className="text-[11px] font-medium text-foreground">
              {t("settings.modelConfig.cards.asrTitle")}
            </p>
            <div className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">
                {t("settings.modelConfig.cards.asrApiKey")}
              </Label>
              <Input
                type="password"
                value={asrApiKey}
                onChange={(e) => setAsrApiKey(e.target.value)}
                placeholder={
                  typeof savedAsr.apiKey === "string" && savedAsr.apiKey
                    ? t("settings.modelConfig.cards.asrKeepSaved")
                    : t("settings.modelConfig.cards.asrApiKeyPlaceholder")
                }
                className="h-8 text-xs"
                autoComplete="off"
              />
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              {t("settings.modelConfig.cards.asrHint")}
            </p>
          </div>
        ) : null}
        {minimaxVoiceCredentials ? (
          <div className="space-y-2 rounded-md border border-border/60 bg-white/[0.02] p-2.5">
            <p className="text-[11px] font-medium text-foreground">
              {t("settings.modelConfig.cards.minimaxVoiceTitle")}
            </p>
            <div className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">
                {t("settings.modelConfig.cards.minimaxVoiceId")}
              </Label>
              <Input
                value={minimaxVoiceId}
                onChange={(e) => setMinimaxVoiceId(e.target.value)}
                placeholder={t(
                  "settings.modelConfig.cards.minimaxVoiceIdPlaceholder",
                )}
                className="h-8 text-xs"
                autoComplete="off"
              />
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              {t("settings.modelConfig.cards.minimaxVoiceHint")}
            </p>
          </div>
        ) : null}
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={handleSave} disabled={busy}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("settings.modelConfig.cards.saveAndSync")}
          </Button>
        </div>
      </div>
      {extra ? (
        <div className="mt-3 border-t border-border/60 pt-3">{extra}</div>
      ) : null}
    </div>
  );
}

// [aipdd fork delta] 火山 AI MediaKit 配置子区块：视频「高清」+「智能去字幕」共用
// 同一份 API Key（Base URL 为固定端点，不录入）。本质属于 VolcEngine 渠道，
// 因此不再与 AIPDD / NewAPI / ComfyUI 并排占一张顶层卡，而是嵌在 VolcEngine 卡内（extra）。
// 保存仍走 video-enhance/config（凭证真源独立于 NewAPI 渠道表），保存后 invalidate
// 网关配置查询自动回显。
function MediaKitConnectCard({ saved }: { saved?: VideoEnhanceConfig }) {
  const { t } = useTranslation();
  const saveVideoEnhance = useSaveVideoEnhanceConfig();
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [clearing, setClearing] = useState(false);

  const savedKey = JSON.stringify(saved ?? {});
  useEffect(() => {
    if (!saved) return;
    // 已保存密钥只回显脱敏预览，不回填明文；用户重新输入才覆盖。
    if (saved.configured) setKey("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);

  const configured = Boolean(saved?.configured);
  const handleSave = async () => {
    try {
      // [aipdd fork delta] Base URL 不再可填：它是固定端点（与后端
      // MEDIAKIT_DEFAULT_BASE_URL 同一个值），提交常量顺手把历史上存歪的值改回来。
      const res = await saveVideoEnhance.mutateAsync({
        ...(key.trim() ? { apiKey: key.trim() } : {}),
        baseUrl: MEDIAKIT_BASE_URL,
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      setKey("");
      toast.success(t("settings.videoEnhance.saveSuccess"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.videoEnhance.saveFailed"),
        ),
      );
    }
  };
  // [aipdd fork delta] 清除入口从「供应商渠道」行搬回子区块：渠道行不再列
  // MediaKit，不把用户已有的清除能力顺手弄丢。
  const handleClear = async () => {
    const confirmed = await confirmDialog({
      title: t("settings.videoEnhance.clearTitle"),
      description: t("settings.videoEnhance.clearConfirm"),
      confirmText: t("settings.videoEnhance.clear"),
      confirmVariant: "destructive",
    });
    if (!confirmed) return;
    setClearing(true);
    try {
      const res = await saveVideoEnhance.mutateAsync({ clear: true });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      setKey("");
      toast.success(t("settings.videoEnhance.cleared"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.videoEnhance.saveFailed"),
        ),
      );
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="rounded-md border border-border/60 bg-white/[0.02] p-2.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium text-foreground">
            {t("settings.videoEnhance.title")}
          </p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.videoEnhance.connectDescription")}
          </p>
          <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
            {t("settings.videoEnhance.nestedHint")}
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 text-[11px]",
            configured ? "text-emerald-400" : "text-amber-300",
          )}
        >
          {configured
            ? `${t("settings.videoEnhance.configured")} ${saved?.apiKeyPreview ?? ""}`.trim()
            : t("settings.videoEnhance.notConfigured")}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">
            {t("settings.modelConfig.cards.baseUrlFixedLabel")}
          </Label>
          <p
            title={MEDIAKIT_BASE_URL}
            className="truncate rounded-md border border-border/60 bg-white/[0.03] px-2 py-1.5 font-mono text-[11px] text-muted-foreground"
          >
            {MEDIAKIT_BASE_URL}
          </p>
        </div>
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">API Key</Label>
          <div className="flex items-center gap-2">
            <Input
              type={reveal ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                saved?.apiKeyPreview ||
                t("settings.videoEnhance.fields.apiKeyPlaceholder")
              }
              className="h-8 text-xs"
              autoComplete="off"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              onClick={() => setReveal((v) => !v)}
            >
              {reveal ? (
                <EyeOff className="size-3.5" />
              ) : (
                <Eye className="size-3.5" />
              )}
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2">
          {configured ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="whitespace-nowrap text-destructive hover:text-destructive"
              onClick={() => void handleClear()}
              disabled={clearing || saveVideoEnhance.isPending}
            >
              {clearing ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Trash2 className="size-3.5" />
              )}
              {t("settings.videoEnhance.clear")}
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={saveVideoEnhance.isPending}
          >
            {saveVideoEnhance.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : null}
            {t("settings.videoEnhance.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ProviderConnectCards({
  config,
  loading: _loading,
  newApiBaseUrl,
}: {
  config: ModelGatewayConfig | undefined;
  loading: boolean;
  newApiBaseUrl: string;
}) {
  const { t } = useTranslation();
  const channels = config?.provisioner?.providerChannels ?? [];
  const savedFor = (p: string) =>
    channels.find((c) => c.provider === p);
  const configured = Boolean(config?.custom?.configured);
  if (!configured) {
    return (
      <p className="mt-4 rounded-md border border-dashed border-border/70 px-3 py-2 text-[11px] text-muted-foreground">
        {t("settings.modelConfig.cards.initFirst")}
      </p>
    );
  }
  return (
    <div className="mt-4 space-y-3">
      <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {t("settings.modelConfig.cards.channelConnectTitle")}
      </p>
      <ProviderConnectCard
        title="AIPDD"
        description={t("settings.modelConfig.cards.aipddDescription")}
        provider="aipdd"
        presetBaseUrl={AIPDD_BASE_URL}
        fixedBaseUrl
        saved={savedFor("aipdd")}
      />
      <ProviderConnectCard
        title="VolcEngine"
        description={t("settings.modelConfig.cards.volcengineDescription")}
        provider="volcengine"
        presetBaseUrl={ARK_BASE_URL}
        fixedBaseUrl
        saved={savedFor("volcengine")}
        asrCredentials
        extra={<MediaKitConnectCard saved={config?.videoEnhance} />}
      />
      <ProviderConnectCard
        title={t("settings.modelConfig.cards.newApiExternalTitle")}
        description={t("settings.modelConfig.cards.newApiExternalDescription")}
        provider="newapi"
        presetBaseUrl=""
        saved={savedFor("newapi")}
      />
      {/*
        [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方渠道：跟 MiniMax
        一样是「粘一个 API Key + 同步模型」的连接卡，Base URL 是官方端点的可改预填
        值（走自建代理 / 镜像站的用户可以改）。协议转换（Anthropic messages / Gemini
        generateContent → OpenAI 格式）由网关里的原生适配器（type 1 / 14 / 24）负责，
        平台只跟网关说话——所以即使 Claude 官方没有 OpenAI 兼容端点也能接。
        能力位按厂商真有的是什么写（channels.py 是唯一口径）：Claude 只有文本与图像
        理解，Gemini / OpenAI 还带向量 / 生图 / 视频 / 语音；两家的音乐都没接
        （网关里没有 music 路由，只有厂商直连适配器才能做）。
      */}
      <ProviderConnectCard
        title="OpenAI"
        description={t("settings.modelConfig.cards.openaiDescription")}
        provider="openai_official"
        presetBaseUrl={OPENAI_OFFICIAL_BASE_URL}
        saved={savedFor("openai_official")}
      />
      <ProviderConnectCard
        title="Claude"
        description={t("settings.modelConfig.cards.claudeDescription")}
        provider="anthropic"
        presetBaseUrl={ANTHROPIC_BASE_URL}
        saved={savedFor("anthropic")}
      />
      <ProviderConnectCard
        title="Gemini"
        description={t("settings.modelConfig.cards.geminiDescription")}
        provider="gemini"
        presetBaseUrl={GEMINI_BASE_URL}
        saved={savedFor("gemini")}
      />
      {/*
        [aipdd fork delta req46] MiniMax 两张卡（海外站 / 国内站）：两边 API Key 不通用，
        所以是两个独立 provider，不是一个卡里切换站点。Base URL 可改（官方多域名同栈
        可达，用户也可能走自建代理）。Speech 2.8 默认音色随卡保存。
      */}
      <ProviderConnectCard
        title={t("settings.modelConfig.cards.minimaxTitle")}
        description={t("settings.modelConfig.cards.minimaxDescription")}
        provider="minimax"
        presetBaseUrl={MINIMAX_BASE_URL}
        saved={savedFor("minimax")}
        minimaxVoiceCredentials
      />
      <ProviderConnectCard
        title={t("settings.modelConfig.cards.minimaxCnTitle")}
        description={t("settings.modelConfig.cards.minimaxCnDescription")}
        provider="minimax_cn"
        presetBaseUrl={MINIMAX_CN_BASE_URL}
        saved={savedFor("minimax_cn")}
        minimaxVoiceCredentials
      />
      <ComfyUILocalCard
        config={config}
        newApiBaseUrl={newApiBaseUrl}
        saved={savedFor("comfyui")}
      />
    </div>
  );
}

// [aipdd fork delta] 「分析模型」下拉的哨兵项：不显式选，后端按文本功能位三层解析。
// 哨兵值不能是空串，否则 Base UI Select 当未选中处理。
const ANALYZER_AUTO = "__auto__";

// [aipdd fork delta] 本地 ComfyUI 配置卡：用户只填「ComfyUI 地址 + 按模型分类的
// 模型名 + 工作流名称」。点「拉取并保存」时，后端连上用户的 ComfyUI 按名称取回
// 工作流、在服务端转成 API 格式，再写入单个 comfyui 渠道的 workflow_by_model。
// [aipdd fork delta] 「AI分析并自动配置」再把每份工作流交给已配置的文本模型分析出
// 「节点绑定 + 真实能力」，适配落系统 state、渠道只存指针与能力声明；后续生成按声明
// 的节点写参数（不再按 class_type 猜），模型档位也不再按名字 token 猜。
function ComfyUILocalCard({
  saved,
}: {
  config: ModelGatewayConfig | undefined;
  newApiBaseUrl: string;
  saved?: SavedProviderChannelConfig;
}) {
  const { t } = useTranslation();
  const resolve = useResolveComfyUIWorkflows();
  const adapt = useAdaptComfyUIWorkflows();
  const save = useSaveProviderChannels();
  // 「分析模型」候选来自网关目录里声明了 text 能力的模型（不写死模型名）。
  const plazaQuery = useModelPlaza("gateway");
  // [aipdd fork delta] 保存成功后与「供应商渠道」保持一致：本地渠道行同步（幂等），
  // 「渠道管理」目录无需再手工添加 ComfyUI 渠道。
  const addFeatureProviderChannel = useSettingsStore(
    (s) => s.addFeatureProviderChannel,
  );
  const updateFeatureProviderChannel = useSettingsStore(
    (s) => s.updateFeatureProviderChannel,
  );

  const comfy = (
    (saved?.settings as Record<string, unknown> | undefined)?.comfyui ?? {}
  ) as Record<string, unknown>;
  const savedWorkflows = (comfy.workflow_by_model ?? {}) as Record<
    string,
    unknown
  >;
  const savedNames = (comfy.workflow_name_by_model ?? {}) as Record<
    string,
    string
  >;
  const savedTypes = (comfy.type_by_model ?? {}) as Record<string, string>;
  // [aipdd fork delta] AI 适配的两份渠道真源：指针表（适配文件在 state 里）与能力声明。
  const savedPointers = (comfy.adaptation_by_model ?? {}) as Record<string, unknown>;
  const savedCapabilities = (comfy.capabilities_by_model ?? {}) as Record<
    string,
    Record<string, unknown>
  >;

  // [aipdd fork delta req41] ComfyUI 跑在哪是部署形态数据，不预填本机地址：
  // 没存过就是空，用户填自己的地址（placeholder 只是格式示例）。
  const [baseUrl, setBaseUrl] = useState(saved?.baseUrl || "");
  const [rows, setRows] = useState<
    Array<{ model: string; workflowName: string; type: string }>
  >(() => {
    const models = Object.keys(savedWorkflows);
    const initial = models.map((m) => ({
      model: m,
      workflowName: savedNames[m] ?? "",
      type: savedTypes[m] ?? "image",
    }));
    return initial.length > 0
      ? initial
      : [{ model: "", workflowName: "", type: "image" }];
  });
  useEffect(() => {
    if (saved?.baseUrl) setBaseUrl(saved.baseUrl);
  }, [saved?.baseUrl]);

  // [aipdd fork delta] 分析用的文本模型：默认「跟随文本功能位」（置空，由后端
  // 三层解析）；网关未绑定时后端会直接回 400「去设置里绑定」，不默默降级。
  const [analyzerModel, setAnalyzerModel] = useState(ANALYZER_AUTO);
  const analyzerOptions = useMemo(() => {
    const models = plazaQuery.data?.data.models ?? [];
    const names = [
      ...new Set(
        models
          .filter((model) => model.capabilities.includes("text"))
          .map((model) => String(model.modelName || ""))
          .filter(Boolean),
      ),
    ].sort((a, b) => a.localeCompare(b));
    if (analyzerModel !== ANALYZER_AUTO && !names.includes(analyzerModel)) {
      names.unshift(analyzerModel);
    }
    return names;
  }, [plazaQuery.data, analyzerModel]);

  const updateRow = (
    idx: number,
    patch: Partial<{ model: string; workflowName: string; type: string }>,
  ) =>
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  const addRow = () =>
    setRows((prev) => [
      ...prev,
      { model: "", workflowName: "", type: "image" },
    ]);
  const removeRow = (idx: number) =>
    setRows((prev) =>
      prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx),
    );

  const busy = resolve.isPending || adapt.isPending || save.isPending;
  const handleSave = async () => {
    if (!baseUrl.trim()) {
      toast.error(t("settings.modelConfig.comfy.needBaseUrl"));
      return;
    }
    const filled = rows.filter((r) => r.model.trim() && r.workflowName.trim());
    if (filled.length === 0) {
      toast.error(t("settings.modelConfig.comfy.needOneRow"));
      return;
    }
    try {
      const res = await resolve.mutateAsync({
        baseUrl: baseUrl.trim(),
        items: filled.map((r) => ({
          model: r.model.trim(),
          workflowName: r.workflowName.trim(),
        })),
      });
      if (res.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.comfy.fetchFailed"),
          ),
        );
        return;
      }
      const resolvedMap = res.data.workflowByModel ?? {};
      const resolvedModels = Object.keys(resolvedMap);
      if (resolvedModels.length === 0) {
        const first =
          res.data.errors?.[0]?.error ||
          t("settings.modelConfig.comfy.noWorkflow");
        toast.error(
          t("settings.modelConfig.comfy.noWorkflowFetched", { error: first }),
        );
        return;
      }
      const nextNames: Record<string, string> = { ...savedNames };
      const nextTypes: Record<string, string> = { ...savedTypes };
      for (const r of filled) {
        const model = r.model.trim();
        if (resolvedMap[model]) {
          nextNames[model] = r.workflowName.trim();
          nextTypes[model] = r.type;
        }
      }
      const merged = { ...savedWorkflows, ...resolvedMap };
      const saveRes = await save.mutateAsync({
        preserveUnmentioned: true,
        channels: [
          {
            provider: "comfyui",
            baseUrl: baseUrl.trim(),
            settings: {
              comfyui: {
                workflow_by_model: merged,
                workflow_name_by_model: nextNames,
                type_by_model: nextTypes,
              },
            },
          },
        ],
      });
      if (saveRes.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            saveRes,
            t("settings.modelConfig.comfy.saveChannelFailed"),
          ),
        );
        return;
      }
      addFeatureProviderChannel("comfyui");
      updateFeatureProviderChannel("comfyui", { baseUrl: baseUrl.trim() });
      const errs = res.data.errors ?? [];
      if (errs.length > 0) {
        toast.warning(
          t("settings.modelConfig.comfy.savedWithErrors", {
            saved: resolvedModels.length,
            failed: errs.length,
            errors: errs
              .map((e) => `${e.model}(${e.error})`)
              .join(t("settings.modelConfig.sentenceSeparator")),
          }),
        );
      } else {
        toast.success(
          t("settings.modelConfig.comfy.savedCount", {
            count: resolvedModels.length,
          }),
        );
      }
      window.dispatchEvent(new Event("media-model-catalog-updated"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.cards.saveFailed"),
        ),
      );
    }
  };

  // [aipdd fork delta] 「AI分析并自动配置」：一次调用做完「取工作流 + 文本模型分析 +
  // 落适配文件」，再把工作流 / 指针 / 能力声明一起写进渠道。分析未通过的模型不落
  // 指针，错误逐条原文弹出，不把猜出来的适配当真源。
  const handleAdapt = async () => {
    if (!baseUrl.trim()) {
      toast.error(t("settings.modelConfig.comfy.needBaseUrl"));
      return;
    }
    const filled = rows.filter((r) => r.model.trim() && r.workflowName.trim());
    if (filled.length === 0) {
      toast.error(t("settings.modelConfig.comfy.needOneRow"));
      return;
    }
    try {
      const res = await adapt.mutateAsync({
        baseUrl: baseUrl.trim(),
        items: filled.map((r) => ({
          model: r.model.trim(),
          workflowName: r.workflowName.trim(),
        })),
        textModel: analyzerModel === ANALYZER_AUTO ? "" : analyzerModel,
      });
      if (res.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.comfy.adaptFailed"),
          ),
        );
        return;
      }
      const data = res.data;
      const adapted = data.adapted ?? [];
      if (adapted.length === 0) {
        const first =
          data.errors?.[0]?.error || t("settings.modelConfig.comfy.adaptNone");
        toast.error(
          t("settings.modelConfig.comfy.adaptNoneWithError", { error: first }),
        );
        return;
      }
      const nextWorkflows: Record<string, unknown> = { ...savedWorkflows };
      const nextNames: Record<string, string> = { ...savedNames };
      const nextTypes: Record<string, string> = { ...savedTypes };
      const nextPointers: Record<string, unknown> = { ...savedPointers };
      const nextCapabilities: Record<string, Record<string, unknown>> = {
        ...savedCapabilities,
      };
      for (const model of adapted) {
        const row = filled.find((r) => r.model.trim() === model);
        nextWorkflows[model] = data.workflowByModel?.[model];
        nextNames[model] =
          data.workflowNameByModel?.[model] || row?.workflowName.trim() || "";
        // 媒体类型以分析出的输出节点为准，拿不到才沿用行里选的。
        nextTypes[model] =
          data.typeByModel?.[model] || row?.type || nextTypes[model] || "video";
        if (data.adaptationPointers?.[model]) {
          nextPointers[model] = data.adaptationPointers[model];
        }
        nextCapabilities[model] = data.capabilitiesByModel?.[model] ?? {};
      }
      const saveRes = await save.mutateAsync({
        preserveUnmentioned: true,
        channels: [
          {
            provider: "comfyui",
            baseUrl: baseUrl.trim(),
            settings: {
              comfyui: {
                workflow_by_model: nextWorkflows,
                workflow_name_by_model: nextNames,
                type_by_model: nextTypes,
                adaptation_by_model: nextPointers,
                capabilities_by_model: nextCapabilities,
              },
            },
          },
        ],
      });
      if (saveRes.ok !== true) {
        toast.error(
          getResponseErrorMessage(
            saveRes,
            t("settings.modelConfig.comfy.saveChannelFailed"),
          ),
        );
        return;
      }
      addFeatureProviderChannel("comfyui");
      updateFeatureProviderChannel("comfyui", { baseUrl: baseUrl.trim() });
      const errs = data.errors ?? [];
      if (errs.length > 0) {
        toast.warning(
          t("settings.modelConfig.comfy.adaptedWithErrors", {
            saved: adapted.length,
            failed: errs.length,
            errors: errs
              .map((e) => `${e.model}(${e.error})`)
              .join(t("settings.modelConfig.sentenceSeparator")),
          }),
        );
      } else {
        toast.success(
          t("settings.modelConfig.comfy.adaptedCount", {
            count: adapted.length,
            model: data.analyzerModel || "",
          }),
        );
      }
      window.dispatchEvent(new Event("media-model-catalog-updated"));
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.cards.saveFailed"),
        ),
      );
    }
  };

  const connected = Boolean(saved?.configured);
  return (
    <div className="rounded-md border border-border/70 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.comfy.title")}
          </p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.comfy.description")}
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 text-[11px]",
            connected ? "text-emerald-400" : "text-amber-300",
          )}
        >
          {connected
            ? t("settings.modelConfig.cards.connected")
            : t("settings.modelConfig.cards.notConnected")}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">
            {t("settings.modelConfig.comfy.baseUrlLabel")}
          </Label>
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://<host>:8188"
            className="h-8 text-xs"
            autoComplete="off"
          />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label className="text-[11px] text-muted-foreground">
              {t("settings.modelConfig.comfy.rowsLabel")}
            </Label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-[11px]"
              onClick={addRow}
            >
              <Plus className="size-3" /> {t("settings.modelConfig.addModel")}
            </Button>
          </div>
          {rows.map((row, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <Select
                value={row.type}
                onValueChange={(type) => {
                  if (type) updateRow(idx, { type });
                }}
              >
                <SelectTrigger className="h-8 w-[90px] shrink-0 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent alignItemWithTrigger={false}>
                  {MEDIA_DEFAULT_TABS.map((tab) => (
                    <SelectItem key={tab.key} value={tab.key}>
                      {t(tab.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={row.model}
                onChange={(e) => updateRow(idx, { model: e.target.value })}
                placeholder={t("settings.modelConfig.comfy.modelPlaceholder")}
                className="h-8 flex-1 text-xs"
                autoComplete="off"
              />
              <Input
                value={row.workflowName}
                onChange={(e) =>
                  updateRow(idx, { workflowName: e.target.value })
                }
                placeholder={t("settings.modelConfig.comfy.workflowPlaceholder")}
                className="h-8 flex-1 text-xs"
                autoComplete="off"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-8 shrink-0"
                onClick={() => removeRow(idx)}
                disabled={rows.length <= 1}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">
            {t("settings.modelConfig.comfy.analyzerLabel")}
          </Label>
          <Select
            items={[
              {
                value: ANALYZER_AUTO,
                label: t("settings.modelConfig.comfy.analyzerAuto"),
              },
              ...analyzerOptions.map((name) => ({ value: name, label: name })),
            ]}
            value={analyzerModel}
            onValueChange={(value) => {
              if (value) setAnalyzerModel(value);
            }}
            disabled={busy}
          >
            <SelectTrigger className="h-8 w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              <SelectItem value={ANALYZER_AUTO}>
                {t("settings.modelConfig.comfy.analyzerAuto")}
              </SelectItem>
              {analyzerOptions.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.comfy.analyzerHint")}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleSave}
            disabled={busy}
          >
            {resolve.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : null}
            {t("settings.modelConfig.comfy.fetchAndSave")}
          </Button>
          <Button type="button" size="sm" onClick={handleAdapt} disabled={busy}>
            {adapt.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            {t("settings.modelConfig.comfy.aiAdapt")}
          </Button>
        </div>
      </div>
    </div>
  );
}

export {
  ProviderConnectCard,
  MediaKitConnectCard,
  ProviderConnectCards,
  ComfyUILocalCard,
};
