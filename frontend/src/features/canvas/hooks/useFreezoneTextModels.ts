// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneTextModels } from "@/api/ops";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

// [aipdd fork delta] 画布脚本/文本节点的文本模型候选 hook。
// 数据源是全局端点 GET /api/v1/model-gateway/text-models（后端聚合
// NewAPI 渠道 model_mapping，默认文本模型排首项），不挂项目作用域，
// 所以整个模块只有一份状态。兜底策略与音频 hook 同理：文本没有可
// 硬编码的官方列表，拉取失败回落空列表，面板/提交侧按「留空走后端
// 默认模型」解释。

export interface UseFreezoneTextModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

const store = createCatalogStore<ModelOption[], UseFreezoneTextModelsResult>({
  scope: "global",
  fetch: async () => {
    const models = await fetchFreezoneTextModels();
    return models.map((entry) => ({
      id: entry.id,
      providerId: "newapi" as const,
      apiModel: entry.apiModel,
      label: entry.label,
      // [aipdd fork delta] 默认标记与展示用渠道一并透传：工具页默认项回显
      // 「默认（模型名）+ 渠道」；providerId 仍是执行身份（文本调用统一经
      // newapi 渠道）。
      ...(entry.isDefault ? { isDefault: true } : {}),
      ...(entry.channel ? { channel: entry.channel } : {}),
    }));
  },
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel:
    "[freezone] text models fetch failed; panels fall back to the backend default model:",
  focusRefreshTtlMs: 5 * 60 * 1000,
});

// 监听挂在 hooks 模块（而不是工厂里）：HMR dispose 才能跟 store 单例的
// 生命周期对齐 —— 工厂模块单独热更时 hooks 模块会随之重执行并重装监听。
if (typeof window !== "undefined") {
  const onCatalogUpdated = () => store.refreshRecentlyUsed();
  window.addEventListener("media-model-catalog-updated", onCatalogUpdated);
  import.meta.hot?.dispose(() => {
    window.removeEventListener("media-model-catalog-updated", onCatalogUpdated);
  });
}

/** Trigger the shared text-model fetch eagerly. Idempotent. */
export function prefetchFreezoneTextModels(): void {
  store.ensure();
}

/**
 * Read the aggregated text model list from the shared store.
 *
 * Failures resolve to an empty list; callers treat an empty selection as
 * "use the backend default text model" rather than a hard block, since the
 * story-script route falls back to the configured default when `model` is
 * omitted. 传 `null` 可静默（picker 在非 text 域下不触发拉取），与
 * image/video hook 的 projectOverride 约定一致。
 */
export function useFreezoneTextModels(
  activate?: string | null,
): UseFreezoneTextModelsResult {
  return store.use(undefined, { enabled: activate !== null });
}