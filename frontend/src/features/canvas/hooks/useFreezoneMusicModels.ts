// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req46] 工具页「音乐创作」的模型目录 hook。
// 与 useFreezoneAudioModels 同语义：module-level per-project store +
// useSyncExternalStore，一次拉取全体消费者共享。音乐没有可硬编码的兜底列表
// （req40：模型清单唯一来源是渠道目录），拉取失败或没配置都是空列表，由面板
// 显示「去设置页配置音乐模型」，绝不拿写死模型名伪装可用。
import { fetchFreezoneMusicModels } from "@/api/ops";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneMusicModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

const store = createCatalogStore<ModelOption[], UseFreezoneMusicModelsResult>({
  scope: "project",
  // 惰性转发：mock @/api/ops 缺该导出时在访问处抛错，能被内核的 Promise 链
  // 捕获成失败回落，而不是在模块顶层就把整个测试文件带崩。
  fetch: (project) => fetchFreezoneMusicModels(project),
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel:
    "[freezone] music models fetch failed; panel shows the empty-catalog hint:",
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

/** 非 hook 的读取口：提交路径要把目录 id 换成 apiModel 时用（同 audio）。 */
export function readFreezoneMusicModels(
  projectOverride?: string | null,
): ModelOption[] {
  return store.read(projectOverride).models;
}

/**
 * Read the music model list from a shared module-level store.
 *
 * Hits `GET /api/v1/projects/{project}/freezone/music/models`. There is no
 * hardcoded fallback list: an empty catalog stays empty.
 */
export function useFreezoneMusicModels(
  projectOverride?: string | null,
): UseFreezoneMusicModelsResult {
  return store.use(projectOverride);
}
