// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：默认生成模型组合面板。

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  Loader2,
  Plus,
  RotateCw,
  ShieldCheck,
} from "lucide-react";
// [aipdd fork delta] 模型名分类真源（前端）：guessMediaType / VISION_CAPABLE_RE 等，
// 与后端 media_model_discovery 镜像互钉。
import {
  guessMediaType,
  isVisionCapable,
  type ModelKind,
} from "@/components/settings/model-classification";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { FEATURE_MODEL_GROUPS } from "@/lib/feature-models";
// [aipdd fork delta] WP-A: 别名映射单一真源。
// [aipdd fork delta] WP-B: 手动触发别名映射一致性自检。
import {
  useProviderCatalog,
  useSaveCustomChannelsBatch,
  useSaveModelAliasMappings,
  useCheckAliasMappingHealth,
  useSaveEmbeddingModel,
  useSaveMediaModelMappings,
  useFetchUpstreamModels,
  type ModelGatewayConfig,
  type SavedMediaModelConfig,
} from "@/lib/queries/model-gateway";
import { catalogCandidates } from "./provider-catalog-block";
import {
  useSettingsStore,
  type FeatureModelProvider,
} from "@/stores/settingsStore";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  featureProviderLabel,
  MEDIA_DEFAULT_TABS,
  isMediaSaveKind,
  normalizeMediaConfig,
  TEXT_DEFAULT_KEY,
  VISION_DEFAULT_KEY,
} from "./shared";

// [aipdd fork delta] 火山 AI MediaKit 视频增强（生成式超分）的固定模型名：MediaKit
// 是独立 connector、不来自网关上游 /models，连接成功后按此名注入「其它」TAB 作
// 只读目录展示。真源：后端 model_plaza.py `_mediakit_entries`。
const MEDIAKIT_ENHANCE_MODEL = "enhance-video-generative";

// [aipdd fork delta] 拍平展示口径：渠道上游清单按「渠道::模型」复合名平铺——
// 同一模型在多个渠道各占一行，渠道由用户显式选择，系统不再预设优先级/备援。
// 裸名条目（媒体默认注入 / ComfyUI / MediaKit / 手输添加）保持原样。
const SPOT_SEPARATOR = "::";

function DefaultGenerationModelsPanel({
  config,
  newApiBaseUrl,
}: {
  config: ModelGatewayConfig | undefined;
  newApiBaseUrl: string;
}) {
  const { t } = useTranslation();
  const fetchModels = useFetchUpstreamModels();
  const saveMappings = useSaveMediaModelMappings();
  const saveBatch = useSaveCustomChannelsBatch();
  // [aipdd fork delta] WP-A: 文本/视觉「设为默认」改走别名真源端点。
  const saveAliases = useSaveModelAliasMappings();
  const saveEmbedding = useSaveEmbeddingModel();
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);
  const featureModels = useSettingsStore(
    (s) => s.featureModelConfig.featureModels,
  );
  const embeddingConfig = config?.provisioner?.embeddingModel;
  const mediaModels = config?.provisioner?.mediaModels ?? {};
  const channels = config?.provisioner?.providerChannels ?? [];
  // [aipdd fork delta] 火山 AI MediaKit 连接状态：连接后把其模型注入默认生成模型池，
  // 使「其它」TAB 完整展示本项目全部可调用模型（只读，不参与「设为默认」）。
  const mediakitConfigured = Boolean(config?.videoEnhance?.configured);
  // [aipdd fork delta] WP-A: 别名映射真源快照（后端 /config 一并回，不额外请求）。
  const aliasMappings = config?.provisioner?.aliasMappings ?? {};
  // [aipdd fork delta] WP-B: 自检快照也是只读附带；真算靠「立即检查」按钮。
  const checkAliasHealth = useCheckAliasMappingHealth();
  const aliasHealth = config?.aliasMappingHealth;
  // [aipdd fork delta] WP-B: 三类告警归并成一句人话；只提示，不阻断任何操作。
  const aliasHealthIssues = useMemo(() => {
    if (!aliasHealth?.checked) return [];
    const parts: string[] = [];
    const listSep = t("settings.modelConfig.listSeparator");
    const unmapped = aliasHealth.unmapped ?? [];
    if (unmapped.length > 0) {
      parts.push(
        t("settings.modelConfig.aliasHealth.unmapped", {
          count: unmapped.length,
        }),
      );
    }
    const stale = aliasHealth.staleTargets ?? [];
    if (stale.length > 0) {
      const names = `${stale
        .map((item) => item.target)
        .slice(0, 3)
        .join(listSep)}${stale.length > 3 ? " …" : ""}`;
      parts.push(
        t("settings.modelConfig.aliasHealth.stale", {
          count: stale.length,
          names,
        }),
      );
    }
    const mismatch = aliasHealth.capabilityMismatch ?? [];
    if (mismatch.length > 0) {
      const names = `${mismatch
        .map((item) => item.alias)
        .slice(0, 3)
        .join(listSep)}${mismatch.length > 3 ? " …" : ""}`;
      parts.push(
        t("settings.modelConfig.aliasHealth.mismatch", {
          count: mismatch.length,
          names,
        }),
      );
    }
    return parts;
  }, [aliasHealth, t]);

  const comfyChannel = channels.find((c) => c.provider === "comfyui");
  const comfySettings = (
    (comfyChannel?.settings as Record<string, unknown> | undefined)?.comfyui ??
    {}
  ) as Record<string, unknown>;
  const comfyModels = Object.keys(
    (comfySettings.workflow_by_model ?? {}) as Record<string, unknown>,
  );
  // [aipdd fork delta] ComfyUI 明确指定的模型类型，优先于名称正则分类。
  const comfyTypeByModel = (comfySettings.type_by_model ?? {}) as Record<
    string,
    string
  >;

  const fetchProviders = useMemo(
    () =>
      channels
        .filter((c) => c.configured && c.provider !== "comfyui")
        .map((c) => c.provider),
    [channels],
  );
  const providersKey = fetchProviders.join(",");
  const comfyKey = comfyModels.join(",");
  // [aipdd fork delta] 拍平：复合名「渠道::模型」的解析真源。仅当前缀是已知渠道
  // 时才视为复合名（防上游模型名本身含「::」时误拆）。
  const knownProviders = useMemo(
    () => new Set(channels.map((c) => c.provider)),
    [channels],
  );
  const spotParts = (m: string): { provider: string; model: string } => {
    const idx = m.indexOf(SPOT_SEPARATOR);
    if (idx > 0) {
      const prefix = m.slice(0, idx);
      if (knownProviders.has(prefix)) {
        return { provider: prefix, model: m.slice(idx + SPOT_SEPARATOR.length) };
      }
    }
    return { provider: "", model: m };
  };
  const displayModelName = (m: string) => spotParts(m).model;

  // 模型名 -> 来源渠道。
  const [pool, setPool] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [tab, setTab] = useState<ModelKind>("image");
  // Embedding TAB：维度为必填（后端 SaveEmbeddingModelInput 要求 dimension:int）。
  const [embDimension, setEmbDimension] = useState<string>(
    embeddingConfig?.dimension ? String(embeddingConfig.dimension) : "1024",
  );

  // [aipdd fork delta] 网关就绪（自定义渠道已配置）即拉取默认生成模型。
  const ready = Boolean(config?.custom?.configured);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const run = async () => {
      const next: Record<string, string> = {};
      for (const [m, e] of Object.entries(mediaModels)) {
        if (e?.provider) next[m] = e.provider;
      }
      for (const m of comfyModels) next[m] = "comfyui";
      // [aipdd fork delta] 火山 AI MediaKit 模型不来自上游清单（独立 connector），
      // 连接后按固定条目注入；effectiveType 特判归「其它」（只读展示）。
      if (mediakitConfigured) next[MEDIAKIT_ENHANCE_MODEL] = "mediakit";
      // [aipdd fork delta] 拍平改版：next 覆盖 prev——媒体/ComfyUI 条目的渠道
      // 跟随最新配置刷新（旧值只作兜底），不再让首轮值永远胜出。
      setPool((prev) => ({ ...prev, ...next }));
      if (fetchProviders.length === 0) return;
      setLoading(true);
      for (const p of fetchProviders) {
        try {
          const r = await fetchModels.mutateAsync({ provider: p });
          if (!cancelled && r.ok === true) {
            for (const m of r.data.models ?? []) {
              // [aipdd fork delta] 拍平：上游清单一律按「渠道::模型」落池，同名
              // 模型跨渠道各成一行，由用户挑选渠道。
              const spot = `${p}${SPOT_SEPARATOR}${m}`;
              if (!next[spot]) next[spot] = p;
            }
          }
        } catch {
          /* 单渠道拉取失败不阻断其余 */
        }
      }
      if (!cancelled) {
        setPool((prev) => ({ ...prev, ...next }));
        setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providersKey, comfyKey, refreshNonce, ready, mediakitConfigured]);

  // [aipdd fork delta] 修复 #2：以模型名关键词为权威分类，避免存储里被写错的
  // mediaType（如 ap-jimeng-image-4.0 被标成 audio）把模型放错 TAB；
  // ComfyUI 明确指定的类型优先级最高；仅当名字无法识别时才回退到已存 mediaType。
  const effectiveType = (m: string): ModelKind => {
    // [aipdd fork delta] 火山 AI MediaKit 条目（enhance-video-generative）不是生成
    // 模型而是外部工具：固定归「其它」TAB（只读展示、无「设为默认」）。放在最前，
    // 避免名字里的 video 关键词被 guessMediaType 误分到「视频」TAB。
    if (pool[m] === "mediakit") return "other";
    // [aipdd fork delta] 拍平后条目可能是复合名：分类一律只看真名（渠道前缀不
    // 参与关键词判定）。
    const name = displayModelName(m);
    // ComfyUI 用户指定类型优先
    if (pool[m] === "comfyui" && comfyTypeByModel[name]) {
      return comfyTypeByModel[name] as ModelKind;
    }
    const g = guessMediaType(name);
    if (g !== "other") return g;
    return mediaModels[name]?.mediaType ?? "other";
  };

  const modelList = useMemo(
    () =>
      Object.keys(pool)
        .filter((m) => {
          const { provider, model } = spotParts(m);
          const matchesTab =
            effectiveType(m) === tab ||
            // [aipdd fork delta] 多模态（doubao-seed 系列）额外列入「视觉理解」TAB：
            // guessMediaType 因 \bdoubao\b 将其归 text（作文本模型同样合法，继续保留
            // 在文本 TAB），但作为视觉默认也要在这里可点选；后端 validator 已放行。
            (tab === "vision" && isVisionCapable(model));
          if (!matchesTab) return false;
          // [aipdd fork delta] 拍平去重：同一 (模型, 渠道) 已由裸名条目代表时，
          // 复合名条目不再重复展示（裸名条目承载默认高亮与渠道回显）。
          return !(provider && pool[model] === provider);
        })
        .sort((a, b) => displayModelName(a).localeCompare(displayModelName(b))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pool, tab, mediaModels],
  );

  const unknownCount = useMemo(
    () =>
      // [aipdd fork delta] 拍平：与「其它」TAB 列表同口径（含同 (模型, 渠道)
      // 去重）——入口计数等于点进去后实际展示的行数。
      Object.keys(pool).filter((m) => {
        if (effectiveType(m) !== "other") return false;
        const { provider, model } = spotParts(m);
        return !(provider && pool[model] === provider);
      }).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pool, mediaModels],
  );

  const defaultFor = (mt: ModelKind) => {
    if (!isMediaSaveKind(mt)) return "";
    const matches = Object.entries(mediaModels).filter(
      ([model, v]) =>
        v.mediaType === mt &&
        v.enabled !== false &&
        // 与展示分类保持一致：名字能识别为非该类型时不当作默认。
        effectiveType(model) === mt,
    );
    if (matches.length === 0) return "";
    matches.sort(
      (a, b) => (a[1].sortOrder ?? 100) - (b[1].sortOrder ?? 100),
    );
    return matches[0][0];
  };

  // 文本 TAB 的「当前默认」：优先读专用兜底 marker；兼容旧数据回退到主对话 HERMES。
  const textDefaultModel =
    featureModels[TEXT_DEFAULT_KEY]?.model?.trim() ||
    featureModels["HERMES"]?.model?.trim() ||
    "";
  // [aipdd fork delta] 视觉 TAB 的「当前默认」：读专用兜底 marker（无旧数据兼容）。
  const visionDefaultModel = featureModels[VISION_DEFAULT_KEY]?.model?.trim() || "";
  const embeddingDefaultModel = embeddingConfig?.upstreamModel?.trim() ?? "";

  const applyDefault = async (mt: ModelKind, model: string) => {
    if (!isMediaSaveKind(mt) || !model) return;
    // [aipdd fork delta] 拍平条目可能是复合名：存储键 / upstreamModel 用真名，
    // provider 取该条目所属渠道（pool 值即复合名前缀渠道）。
    const realName = displayModelName(model);
    const provider = pool[model] ?? mediaModels[realName]?.provider ?? "ali";
    const next: Record<string, SavedMediaModelConfig> = { ...mediaModels };
    for (const [m, e] of Object.entries(next)) {
      if (e.mediaType === mt && m !== realName) {
        next[m] = { ...e, sortOrder: Math.max(e.sortOrder ?? 100, 50) };
      }
    }
    next[realName] = {
      ...(next[realName] ?? {}),
      provider,
      upstreamModel: realName,
      mediaType: mt,
      enabled: true,
      sortOrder: 0,
      config: normalizeMediaConfig(next[realName]?.config, mt),
    };
    // 存量可能存在 config.endpoint 与 mediaType 不一致的行，整批一起对齐，
    // 避免任一旧行卡住后端校验导致本次设默认整体 400。
    for (const [m, e] of Object.entries(next)) {
      if (e.mediaType === "image" || e.mediaType === "video") {
        next[m] = { ...e, config: normalizeMediaConfig(e.config, e.mediaType) };
      }
    }
    try {
      const res = await saveMappings.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        models: next,
      });
      if (res.ok === true) {
        const labelKey = MEDIA_DEFAULT_TABS.find(
          (tab) => tab.key === mt,
        )?.labelKey;
        const label = labelKey ? t(labelKey) : mt;
        // [aipdd fork delta] 拍平：立即同步池内裸名条目的渠道，并移除同 (模型,
        // 渠道) 的复合名行，避免高亮与渠道回显滞后一步。
        setPool((prev) => {
          const nextPool = { ...prev, [realName]: provider };
          delete nextPool[`${provider}${SPOT_SEPARATOR}${realName}`];
          return nextPool;
        });
        toast.success(
          t("settings.modelConfig.modelList.setDefaultSuccess", {
            model: realName,
            label,
          }),
        );
        window.dispatchEvent(new Event("media-model-catalog-updated"));
      } else {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.modelList.setDefaultFailed"),
          ),
        );
      }
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.modelList.setDefaultFailed"),
        ),
      );
    }
  };

  // [aipdd fork delta] WP-A: 文本默认 = 全局兜底语义，写入的是**项目自有别名真源**
  // （不再是直推网关 mapping）：把选中模型应用到「未单独配置」的非视觉功能，
  // 后端落库后再编译投影到网关渠道。
  const applyTextDefault = async (model: string) => {
    if (tab !== "text" || !model) return;
    // [aipdd fork delta] 拍平：复合名条目拆出真名与渠道——别名真源的 target
    // 写入真名，渠道写入 provider 字段。
    const target = displayModelName(model);
    const provider =
      pool[model] || featureModels[TEXT_DEFAULT_KEY]?.provider || "aipdd";
    const prevDefault = featureModels[TEXT_DEFAULT_KEY]?.model?.trim() ?? "";
    const mapping: Record<string, string> = {};
    let repointed = 0;
    let kept = 0;
    for (const group of FEATURE_MODEL_GROUPS) {
      for (const feature of group.features) {
        if (feature.requiresVision) continue; // 视觉功能需专门支持视觉的模型，默认不覆盖
        const cur = featureModels[feature.id]?.model?.trim();
        // 已自定义（与旧默认不同）才视为显式配置，保留不动；
        // 未配置或恰为旧默认（此前被当兜底填入的）都跟随新默认。
        if (cur && cur !== prevDefault) {
          kept++;
          continue;
        }
        mapping[feature.defaultModel] = target;
        repointed++;
        // 仅同步本地镜像中已存在兜底值的行；从未配置过的行保持空，以便下次改默认继续生效。
        if (cur) updateFeatureModel(feature.id, { provider, model: target });
      }
    }
    // 无论是否有可应用的功能，都先落保留 key，作为「当前默认」的权威来源。
    updateFeatureModel(TEXT_DEFAULT_KEY, { provider, model: target });
    if (repointed === 0) {
      toast.info(
        t("settings.modelConfig.modelList.textDefaultNoop", { model: target }),
      );
      return;
    }
    try {
      const res = await saveAliases.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        mappings: mapping,
        defaultProvider: provider,
        // [aipdd fork delta] WP-B: 声明能力位，后端按此做上游同类校验并在错配时回候选。
        capability: "text",
        merge: true,
      });
      if (res.ok === true) {
        const failed =
          "data" in res ? (res.data.projectionFailed?.length ?? 0) : 0;
        toast.success(
          t("settings.modelConfig.modelList.textDefaultSuccess", {
            model: target,
            count: repointed,
            kept:
              kept > 0
                ? t("settings.modelConfig.modelList.keptSuffix", { count: kept })
                : "",
            failed:
              failed > 0
                ? t("settings.modelConfig.modelList.projectionFailedSuffix", {
                    count: failed,
                  })
                : "",
          }),
        );
      } else {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.modelList.textDefaultFailed"),
          ),
        );
      }
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.modelList.textDefaultFailed"),
        ),
      );
    }
  };

  // [aipdd fork delta] WP-A: 视觉理解默认（镜像 applyTextDefault，方向相反）：
  // 只应用到「未单独配置」的 requiresVision 功能，同样走别名真源 + 投影。
  const applyVisionDefault = async (model: string) => {
    if (tab !== "vision" || !model) return;
    // [aipdd fork delta] 拍平：复合名条目拆出真名与渠道（同 applyTextDefault）。
    const target = displayModelName(model);
    const provider =
      pool[model] || featureModels[VISION_DEFAULT_KEY]?.provider || "aipdd";
    const prevDefault = featureModels[VISION_DEFAULT_KEY]?.model?.trim() ?? "";
    const mapping: Record<string, string> = {};
    let repointed = 0;
    let kept = 0;
    for (const group of FEATURE_MODEL_GROUPS) {
      for (const feature of group.features) {
        if (!feature.requiresVision) continue; // 只重指向需要视觉的功能
        const cur = featureModels[feature.id]?.model?.trim();
        // 已自定义（与旧视觉默认不同）才视为显式配置，保留不动。
        if (cur && cur !== prevDefault) {
          kept++;
          continue;
        }
        mapping[feature.defaultModel] = target;
        repointed++;
        if (cur) updateFeatureModel(feature.id, { provider, model: target });
      }
    }
    updateFeatureModel(VISION_DEFAULT_KEY, { provider, model: target });
    if (repointed === 0) {
      toast.info(
        t("settings.modelConfig.modelList.visionDefaultNoop", {
          model: target,
        }),
      );
      return;
    }
    try {
      const res = await saveAliases.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        mappings: mapping,
        defaultProvider: provider,
        // [aipdd fork delta] WP-B: 视觉位必须声明 capability=vision，后端才会
        // 拦住「把纯文本模型写进视觉默认」（400 不支持图片能力的成因）。
        capability: "vision",
        merge: true,
      });
      if (res.ok === true) {
        const failed =
          "data" in res ? (res.data.projectionFailed?.length ?? 0) : 0;
        toast.success(
          t("settings.modelConfig.modelList.visionDefaultSuccess", {
            model: target,
            count: repointed,
            kept:
              kept > 0
                ? t("settings.modelConfig.modelList.keptSuffix", { count: kept })
                : "",
            failed:
              failed > 0
                ? t("settings.modelConfig.modelList.projectionFailedSuffix", {
                    count: failed,
                  })
                : "",
          }),
        );
      } else {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.modelList.visionDefaultFailed"),
          ),
        );
      }
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.modelList.visionDefaultFailed"),
        ),
      );
    }
  };

  // [aipdd fork delta] 修复 #3（Embedding TAB）：需内联填写维度（后端必填 int）。
  const applyEmbedding = async (model: string) => {
    if (tab !== "embedding" || !model) return;
    const dim = Number.parseInt(embDimension, 10);
    if (!Number.isFinite(dim) || dim <= 0) {
      toast.error(t("settings.modelConfig.modelList.needDimension"));
      return;
    }
    // [aipdd fork delta] 拍平：复合名条目拆真名（upstreamModel 写真名，渠道取
    // 条目所属渠道）。
    const target = displayModelName(model);
    const provider = pool[model] ?? embeddingConfig?.provider ?? "ali";
    try {
      const res = await saveEmbedding.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        provider,
        upstreamModel: target,
        dimension: dim,
      });
      if (res.ok === true) {
        toast.success(
          t("settings.modelConfig.modelList.embeddingDefaultSuccess", {
            model: target,
            dim,
          }),
        );
      } else {
        toast.error(
          getResponseErrorMessage(
            res,
            t("settings.modelConfig.modelList.embeddingDefaultFailed"),
          ),
        );
      }
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.modelList.embeddingDefaultFailed"),
        ),
      );
    }
  };

  // [aipdd fork delta] 「添加模型」：把目录里尚未出现在当前清单里的条目加进展示池，
  // 图片/视频/音频会写入 mediaModels 持久化；文本/Embedding 先加入本地 list，
  // 点「设为默认」时再持久化。
  // [aipdd fork delta req40] 模型名**不再手输**：下拉只给该渠道目录里的条目。以前留
  // 的自由输入框正是「从网关摘掉的模型还能配出来」的入口 —— 拼错一个字符、或渠道根本
  // 没这个模型，任务会一路跑到中后段才 404。
  const [showAdd, setShowAdd] = useState(false);
  const [addModel, setAddModel] = useState("");
  const [addType, setAddType] = useState<ModelKind>("image");
  const [addProvider, setAddProvider] = useState("");
  const providerCatalog = useProviderCatalog();
  const providerOptions = useMemo(() => {
    const set = new Set<string>(
      channels.filter((c) => c.configured).map((c) => c.provider),
    );
    for (const p of Object.values(pool)) set.add(p);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [channels, pool]);
  const addCatalogEntries = useMemo(
    () =>
      catalogCandidates(
        providerCatalog.data?.data?.providers ?? [],
        addProvider || providerOptions[0] || "",
        [addType],
      ),
    [addProvider, addType, providerCatalog.data, providerOptions],
  );

  const handleAddModel = async () => {
    const name = addModel.trim();
    if (!name) {
      toast.error(t("settings.modelConfig.modelList.needModelName"));
      return;
    }
    if (!addCatalogEntries.some((entry) => String(entry.name || "").trim() === name)) {
      // [aipdd fork delta req40] 目录里没有的名字 = 渠道不卖这个模型，配上也是死映射。
      toast.error(t("settings.modelConfig.providerCatalog.catalogPickRequired"));
      return;
    }
    const provider = addProvider || providerOptions[0] || "ali";
    const typeLabelKey = MEDIA_DEFAULT_TABS.find(
      (tab) => tab.key === addType,
    )?.labelKey;
    const typeLabel = typeLabelKey ? t(typeLabelKey) : addType;
    if (isMediaSaveKind(addType)) {
      const existing = mediaModels[name];
      const next: Record<string, SavedMediaModelConfig> = {
        ...mediaModels,
        [name]: {
          provider: provider as FeatureModelProvider,
          upstreamModel: existing?.upstreamModel || name,
          // [aipdd fork delta req46] 已由 isMediaSaveKind 收敛成 MediaSaveKind（含
          // music）：旧写法的三类型硬 cast 会让音乐条目被当成 audio 存进真源。
          mediaType: addType,
          enabled: true,
          sortOrder: existing?.sortOrder ?? 100,
          config: normalizeMediaConfig(existing?.config, addType),
        },
      };
      for (const [m, e] of Object.entries(next)) {
        if (e.mediaType === "image" || e.mediaType === "video") {
          next[m] = {
            ...e,
            config: normalizeMediaConfig(e.config, e.mediaType),
          };
        }
      }
      try {
        const res = await saveMappings.mutateAsync({
          newApiBaseUrl: newApiBaseUrl.trim(),
          models: next,
        });
        if (res.ok === true) {
          setPool((prev) => ({ ...prev, [name]: provider }));
          setTab(addType);
          setShowAdd(false);
          setAddModel("");
          toast.success(
            t("settings.modelConfig.modelList.addedToList", {
              model: name,
              label: typeLabel,
            }),
          );
          window.dispatchEvent(new Event("media-model-catalog-updated"));
        } else {
          toast.error(
            getResponseErrorMessage(
              res,
              t("settings.modelConfig.modelList.addFailed"),
            ),
          );
        }
      } catch (error) {
        toast.error(
          await getRequestErrorMessage(
            error,
            t("settings.modelConfig.modelList.addFailed"),
          ),
        );
      }
      return;
    }
    // 文本 / 视觉理解 / Embedding：仅加入本地 list 供点选（设为默认时再走各自持久化）。
    setPool((prev) => ({ ...prev, [name]: provider }));
    setTab(addType);
    setShowAdd(false);
    setAddModel("");
    toast.success(
      t("settings.modelConfig.modelList.addedToPick", {
        model: name,
        label: typeLabel,
      }),
    );
  };

  const isMediaTab = isMediaSaveKind(tab);
  const isOtherTab = tab === "other";
  const current = isMediaTab
    ? defaultFor(tab)
    : tab === "text"
      ? textDefaultModel
      : tab === "vision"
        ? visionDefaultModel
        : isOtherTab
          ? ""
          : embeddingDefaultModel;
  const rowBusy = isMediaTab
    ? saveMappings.isPending
    : tab === "text" || tab === "vision"
      ? saveAliases.isPending || saveBatch.isPending
      : isOtherTab
        ? false
        : saveEmbedding.isPending;
  const tabHasDefault = (key: ModelKind) =>
    key === "other"
      ? false
      : isMediaSaveKind(key)
        ? Boolean(defaultFor(key))
        : key === "text"
          ? Boolean(textDefaultModel)
          : key === "vision"
            ? Boolean(visionDefaultModel)
            : Boolean(embeddingDefaultModel);
  const currentLabelKey = MEDIA_DEFAULT_TABS.find(
    (tabDef) => tabDef.key === tab,
  )?.labelKey;
  const currentLabel = currentLabelKey ? t(currentLabelKey) : "";

  // [aipdd fork delta] 拍平口径：没有主 / 备援渠道之分——当前默认的生效渠道即
  // 所选条目自身归属的渠道，直接展示。
  const currentChannelSummary = (() => {
    if (!current || isOtherTab) return "";
    const provider = isMediaTab
      ? mediaModels[current]?.provider
      : tab === "embedding"
        ? embeddingConfig?.provider
        : tab === "text"
          ? featureModels[TEXT_DEFAULT_KEY]?.provider ||
            featureModels["HERMES"]?.provider
          : featureModels[VISION_DEFAULT_KEY]?.provider;
    if (!provider) return "";
    return t("settings.modelConfig.modelList.channelSummary", {
      primary: featureProviderLabel(provider),
      backup: "",
    });
  })();

  // [aipdd fork delta] 网关未配置时隐藏面板（上方渠道连接区已有引导提示）。
  if (!ready) return null;

  return (
    <div className="mt-4 rounded-md border border-border/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.modelList.panelTitle")}
          </p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.modelList.description")}
          </p>
          {/* [aipdd fork delta] WP-A: 明确告知真源位置，避免用户误以为网关才是配置本体。 */}
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.modelList.truthSource", {
              count: Object.keys(aliasMappings ?? {}).length,
            })}
          </p>
          {/* [aipdd fork delta] WP-B: 自检横幅——悬空别名 / 失效目标 / 能力错配，
              只告警不阻断启动也不阻断使用；点「立即检查」才会拉上游清单。 */}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] leading-relaxed">
            {!aliasHealth?.checked ? (
              <span className="text-muted-foreground">
                {t("settings.modelConfig.modelList.aliasNotChecked")}
              </span>
            ) : aliasHealthIssues.length > 0 ? (
              <span className="flex items-start gap-1 text-amber-500">
                <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                {t("settings.modelConfig.modelList.aliasIssues", {
                  issues: aliasHealthIssues.join(
                    t("settings.modelConfig.sentenceSeparator"),
                  ),
                })}
              </span>
            ) : (
              <span className="text-muted-foreground">
                {t("settings.modelConfig.modelList.aliasChecked", {
                  total: aliasHealth.total ?? 0,
                  degraded:
                    (aliasHealth.degraded?.length ?? 0) > 0
                      ? t("settings.modelConfig.modelList.aliasCheckedDegraded", {
                          count: aliasHealth.degraded?.length ?? 0,
                        })
                      : "",
                })}
              </span>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-[11px]"
              disabled={checkAliasHealth.isPending}
              onClick={() => {
                checkAliasHealth.mutate(undefined, {
                  onSuccess: () =>
                    toast.success(
                      t("settings.modelConfig.modelList.aliasCheckDone"),
                    ),
                  onError: (error) =>
                    toast.error(
                      error instanceof Error
                        ? error.message
                        : t("settings.modelConfig.modelList.aliasCheckFailed"),
                    ),
                });
              }}
            >
              {checkAliasHealth.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ShieldCheck className="size-3" />
              )}
              {t("settings.modelConfig.modelList.checkNow")}
            </Button>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-[11px]"
            onClick={() => {
              setAddType(tab);
              setShowAdd(true);
            }}
          >
            <Plus className="size-3" />
            {t("settings.modelConfig.addModel")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-[11px]"
            onClick={() => setRefreshNonce((n) => n + 1)}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RotateCw className="size-3" />
            )}
            {t("settings.modelConfig.modelList.refreshModels")}
          </Button>
        </div>
      </div>

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as ModelKind)}
        className="mt-3"
      >
        <TabsList className="h-8">
          {MEDIA_DEFAULT_TABS.map((tabDef) => (
            <TabsTrigger
              key={tabDef.key}
              value={tabDef.key}
              className="text-xs"
            >
              {t(tabDef.labelKey)}
              {tabHasDefault(tabDef.key) ? " ✓" : ""}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {modelList.length > 0 ? (
        <ScrollArea className="mt-3 h-56">
          <div className="space-y-1 pr-2">
            {modelList.map((m) => {
              const { provider: spotProvider } = spotParts(m);
              // [aipdd fork delta] 拍平：默认高亮 = 真名 + 渠道双匹配。
              const realProvider = isMediaTab
                ? mediaModels[current]?.provider
                : tab === "embedding"
                  ? embeddingConfig?.provider
                  : featureModels[
                      tab === "text" ? TEXT_DEFAULT_KEY : VISION_DEFAULT_KEY
                    ]?.provider;
              const isDefault =
                Boolean(current) &&
                displayModelName(m) === current &&
                (spotProvider || pool[m]) === realProvider;
              return (
                <div
                  key={m}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                    isDefault
                      ? "border-emerald-500/50 bg-emerald-500/10"
                      : "border-border/60",
                  )}
                >
                  <span className="flex min-w-0 flex-1 items-center gap-1.5">
                    <span className="min-w-0 truncate">
                      {displayModelName(m)}
                    </span>
                    {effectiveType(m) === "other" && tab !== "other" ? (
                      <span className="shrink-0 rounded bg-amber-500/15 px-1 py-0.5 text-[9px] text-amber-300">
                        {t("settings.modelConfig.modelList.unknownBadge")}
                      </span>
                    ) : null}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="text-[10px] text-muted-foreground">
                      {featureProviderLabel(pool[m])}
                    </span>
                    {tab === "embedding" ? (
                      isDefault ? (
                        <span className="text-[10px] text-muted-foreground">
                          {t("settings.modelConfig.modelList.dimension", {
                            dim: embDimension,
                          })}
                        </span>
                      ) : (
                        <Input
                          type="number"
                          min={1}
                          value={embDimension}
                          onChange={(e) => setEmbDimension(e.target.value)}
                          placeholder={t(
                            "settings.modelConfig.modelList.dimensionPlaceholder",
                          )}
                          className="h-6 w-24 text-[11px]"
                        />
                      )
                    ) : null}
                    {isDefault ? (
                      <span className="rounded-full bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">
                        {t("settings.modelConfig.modelList.isDefaultBadge")}
                      </span>
                    ) : isOtherTab ? null : (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-6 px-2 text-[11px]"
                        onClick={() =>
                          void (isMediaTab
                            ? applyDefault(tab, m)
                            : tab === "text"
                              ? applyTextDefault(m)
                              : tab === "vision"
                                ? applyVisionDefault(m)
                                : applyEmbedding(m))
                        }
                        disabled={rowBusy}
                      >
                        {t("settings.modelConfig.modelList.setAsDefault")}
                      </Button>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        </ScrollArea>
      ) : (
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          {loading
            ? t("settings.modelConfig.modelList.loadingList")
            : Object.keys(pool).length === 0
              ? t("settings.modelConfig.modelList.emptyPool")
              : t("settings.modelConfig.modelList.emptyTab", {
                  label: currentLabel,
                })}
        </p>
      )}
      {/* [aipdd fork delta] 「其它」TAB 说明：火山 AI MediaKit 条目为只读目录，
          接入入口在「渠道连接」VolcEngine 卡内的火山 AI MediaKit 子区块（不参与「设为默认」）。 */}
      {tab === "other" && pool[MEDIAKIT_ENHANCE_MODEL] === "mediakit" ? (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {t("settings.modelConfig.modelList.mediakitHint")}
        </p>
      ) : null}
      {unknownCount > 0 && !loading && tab !== "other" ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-2 h-6 px-2 text-[11px] text-muted-foreground"
          onClick={() => setTab("other")}
        >
          {t("settings.modelConfig.modelList.viewUnknown", {
            count: unknownCount,
          })}
        </Button>
      ) : null}
      {current ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {t("settings.modelConfig.modelList.currentDefault", {
            label: currentLabel,
            model: current,
          })}
          {currentChannelSummary}
        </p>
      ) : null}

      <Dialog open={showAdd} onOpenChange={setShowAdd}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("settings.modelConfig.addModel")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">
                {t("settings.modelConfig.addModelDialog.nameLabel")}
              </Label>
              <Select
                value={addModel}
                onValueChange={(v) => setAddModel(v ?? "")}
                disabled={addCatalogEntries.length === 0}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue
                    placeholder={t(
                      "settings.modelConfig.providerCatalog.empty",
                    )}
                  />
                </SelectTrigger>
                <SelectContent>
                  {addCatalogEntries.map((entry) => (
                    <SelectItem
                      key={entry.name}
                      value={entry.name}
                      className="text-xs"
                    >
                      {entry.name}
                      {entry.inferred ? " *" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-[11px] text-muted-foreground">
                  {t("settings.modelConfig.addModelDialog.typeLabel")}
                </Label>
                <Select
                  value={addType}
                  onValueChange={(v) => setAddType(v as ModelKind)}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MEDIA_DEFAULT_TABS.map((tabDef) => (
                      <SelectItem
                        key={tabDef.key}
                        value={tabDef.key}
                        className="text-xs"
                      >
                        {t(tabDef.labelKey)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-[11px] text-muted-foreground">
                  {t("settings.modelConfig.addModelDialog.providerLabel")}
                </Label>
                <Select
                  value={addProvider || providerOptions[0] || undefined}
                  onValueChange={(v) => setAddProvider(v ?? "")}
                  disabled={providerOptions.length === 0}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue
                      placeholder={t(
                        "settings.modelConfig.addModelDialog.providerPlaceholder",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {providerOptions.map((p) => (
                      <SelectItem key={p} value={p} className="text-xs">
                        {featureProviderLabel(p)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {addCatalogEntries.length === 0
                ? t("settings.modelConfig.providerCatalog.empty")
                : t("settings.modelConfig.addModelDialog.hint")}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setShowAdd(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void handleAddModel()}
                disabled={
                  saveMappings.isPending || addCatalogEntries.length === 0
                }
              >
                {saveMappings.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : null}
                {t("settings.modelConfig.addModelDialog.add")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export {
  DefaultGenerationModelsPanel,
};
