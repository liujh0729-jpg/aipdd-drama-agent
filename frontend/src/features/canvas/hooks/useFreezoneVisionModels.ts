// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneVisionModels } from "@/api/ops";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

// [aipdd fork delta] 工具页「视频反推剧本」的视觉模型候选 hook。
// 数据源是全局端点 GET /api/v1/model-gateway/vision-models（后端按视觉理解
// 能力过滤渠道上游模型），不挂项目作用域，所以整个模块只有一份状态。
// 兜底策略与文本 hook 同理：拉取失败回落空列表；面板按「留空走后端默认
// 视觉链（FREEZONE_VISION_MODEL → VISION_MODEL_FALLBACK）」解释空选择。

export interface UseFreezoneVisionModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

const store = createCatalogStore<ModelOption[], UseFreezoneVisionModelsResult>({
  scope: "global",
  fetch: async () => {
    const models = await fetchFreezoneVisionModels();
    return models.map((entry) => ({
      id: entry.id,
      providerId: "newapi" as const,
      apiModel: entry.apiModel,
      label: entry.label,
      // 展示用渠道随条目下发（面板胶囊标注）；providerId 仍是执行身份。
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
    "[freezone] vision models fetch failed; the reverse-script panel falls back to the backend default vision model:",
  focusRefreshTtlMs: 5 * 60 * 1000,
});

if (typeof window !== "undefined") {
  const onCatalogUpdated = () => store.refreshRecentlyUsed();
  window.addEventListener("media-model-catalog-updated", onCatalogUpdated);
  import.meta.hot?.dispose(() => {
    window.removeEventListener("media-model-catalog-updated", onCatalogUpdated);
  });
}

/** Trigger the shared vision-model fetch eagerly. Idempotent. */
export function prefetchFreezoneVisionModels(): void {
  store.ensure();
}

/**
 * Read the aggregated vision model list from the shared store.
 *
 * Failures resolve to an empty list; the reverse-script panel still allows
 * submitting with an empty selection — the backend falls back to the default
 * vision chain when `model` is omitted. 传 `null` 可静默不拉取，与
 * text hook 的 activate 约定一致。
 */
export function useFreezoneVisionModels(
  activate?: string | null,
): UseFreezoneVisionModelsResult {
  return store.use(undefined, { enabled: activate !== null });
}
