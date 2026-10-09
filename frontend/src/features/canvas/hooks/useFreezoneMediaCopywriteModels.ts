// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchFreezoneMediaCopywriteModels,
  type FreezoneMediaAsrModelOption,
} from "@/api/ops";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

// [aipdd fork delta] req21：音视频提取文案面板的 ASR 模型候选目录（与抖音文案
// 提取同族，共用统一目录 store 语义）：一次拉取共享、失败诚实回落空列表（面板
// 要求显式选择模型，无默认项；留空仅作程序化调用兜底）。端点从
// /douyin-copywrite/models 迁到中性的 /media-copywrite/models。

export interface UseFreezoneMediaCopywriteModelsResult {
  models: FreezoneMediaAsrModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

const store = createCatalogStore<
  FreezoneMediaAsrModelOption[],
  UseFreezoneMediaCopywriteModelsResult
>({
  scope: "project",
  // 惰性 wrapper：顶层直传会让「@/api/ops mock 未提供该导出」在模块加载期
  // 就抛错；包一层后访问推迟到调用时，被 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneMediaCopywriteModels(project),
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel: "[freezone] media copywrite models fetch failed:",
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

export function prefetchFreezoneMediaCopywriteModels(project: string): void {
  store.ensure(project);
}

export function useFreezoneMediaCopywriteModels(
  projectOverride?: string | null,
): UseFreezoneMediaCopywriteModelsResult {
  return store.use(projectOverride);
}
