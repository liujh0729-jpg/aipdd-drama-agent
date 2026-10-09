// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneVideoModels } from "@/api/ops";
// [aipdd fork delta] 视频模型候选只有一个来源：后台「媒体模型」目录经
// /freezone/video/models 下发的真实列表。以前 pending / 拉取失败时会顶上一份
// 写死的 VIDEO_MODELS，用户先看到假列表再被真实列表闪一下。现在彻底去掉那份
// 写死列表 —— 这里只保留类型，加载前就是空列表。
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneVideoModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

// Module-level shared store, mirrors useFreezoneImageModels but keyed under
// a separate namespace so image and video fetches don't collide. One fetch
// per project per tab lifetime.
const store = createCatalogStore<ModelOption[], UseFreezoneVideoModelsResult>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneVideoModels(project),
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel: "[freezone] video models fetch failed:",
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
 * Trigger the shared video-model fetch eagerly for a project. Idempotent —
 * safe to call from FreezoneShell mount alongside the image-model prefetch.
 */
export function prefetchFreezoneVideoModels(project: string): void {
  store.ensure(project);
}

/**
 * Force a fresh pull of the live video-model catalog. Idempotent and safe to
 * call on every picker open — a refresh keeps the currently shown list on
 * screen and swaps it in place when the response arrives (no flash to empty).
 * Falls back to the current project in the URL when no project is given.
 */
export function refreshFreezoneVideoModels(project?: string | null): void {
  store.refresh(project);
}

/**
 * 非 hook 的读取口：store / 建边校验这类跑在 React 之外的代码要按当前模型算能力
 * （比如引用上限）时用。读的是同一个 module-level store，所以和组件看到的是同一份
 * 目录；还没加载出来时返回空列表，与 hook 的初始状态一致（不再有写死的兜底列表）。
 *
 * 不在这里触发 ensureLoaded —— 建边是同步路径，等不到网络回来，而且这个函数会被
 * 高频调用。目录的加载由 FreezoneShell 的 prefetch 和各视频节点的 hook 负责。
 */
export function readFreezoneVideoModels(
  projectOverride?: string | null,
): ModelOption[] {
  return store.read(projectOverride).models;
}

/**
 * Read the video model list from a shared module-level store.
 *
 * Mirrors `useFreezoneImageModels` in plumbing but hits
 * `GET /api/v1/projects/{project}/freezone/video/models`. [aipdd fork delta]
 * There is NO hardcoded fallback list: the live gateway-driven catalog is the
 * only source. Before the first fetch resolves, or if it fails, the list is
 * simply empty (plus `isLoading` / `error`) — never a fabricated model set.
 */
export function useFreezoneVideoModels(
  projectOverride?: string | null,
): UseFreezoneVideoModelsResult {
  return store.use(projectOverride);
}