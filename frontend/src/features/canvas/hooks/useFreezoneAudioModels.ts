// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneAudioModels } from "@/api/ops";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

// [aipdd fork delta] 画布音频节点（语音/音乐）的模型目录 hook。
// 形态对齐 useFreezoneImageModels：module-level per-project store +
// useSyncExternalStore，一次拉取全体消费者共享。差异在于兜底策略 ——
// 音频没有可硬编码的官方模型列表（语音默认引擎不依赖目录，后端对空
// model 走内置引擎），所以无论拉取成功为空还是失败，回落的都是空列表；
// 面板侧按「默认引擎」首项解释，绝不伪装成有模型可用。

export interface UseFreezoneAudioModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

const store = createCatalogStore<ModelOption[], UseFreezoneAudioModelsResult>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneAudioModels(project),
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel:
    "[freezone] audio models fetch failed; panel falls back to the default engine only:",
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

/**
 * Trigger the shared audio-model fetch eagerly for a project. Idempotent —
 * safe to call from FreezoneShell mount alongside the image/video prefetch.
 */
export function prefetchFreezoneAudioModels(project: string): void {
  store.ensure(project);
}

/**
 * 非 hook 的读取口：提交路径（useAudioGeneration）要把节点存的目录 id 换成
 * apiModel 时用。读同一个 module-level store；还没加载出来时回空列表，
 * 调用方按「未选模型 → 走后端默认引擎」解释。不在这里触发 ensureLoaded。
 */
export function readFreezoneAudioModels(
  projectOverride?: string | null,
): ModelOption[] {
  return store.read(projectOverride).models;
}

/**
 * Read the audio model list from a shared module-level store.
 *
 * Hits `GET /api/v1/projects/{project}/freezone/audio/models`. Unlike the
 * image/video hooks there is no hardcoded fallback list: failures resolve to
 * an empty catalog, which the audio panel renders as "默认引擎" only.
 */
export function useFreezoneAudioModels(
  projectOverride?: string | null,
): UseFreezoneAudioModelsResult {
  return store.use(projectOverride);
}