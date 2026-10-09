// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneImageModels } from "@/api/ops";
// [aipdd fork delta] 图片目录不再有可硬编码的兜底模型列表：模型清单唯一来源是
// 后台「媒体模型」配置经 /freezone/image/models 下发的那份。这里只保留类型引用。
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneImageModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
}

// Module-level shared store. One state snapshot per project, one fetch per
// project per tab lifetime. Every consumer reads the same reference via
// useSyncExternalStore, so a freshly mounted picker sees the cached result
// immediately (no per-component re-fetch, no loading flicker).
const store = createCatalogStore<ModelOption[], UseFreezoneImageModelsResult>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneImageModels(project),
  // [aipdd fork delta] 空兜底：管理员没配模型时不凭空造一堆前端写死的模型（那正是
  // 画布图片节点会闪出 OpenAI 等非约定渠道、过一会又被真实目录替换的根因）。
  // 加载中 / 拉取失败都回落空列表，由选择器据 isLoading / 权威空目录展示「加载中」
  // 或「无可用模型」，绝不伪装成有模型可用。默认取值交给目录首模型（后台默认项）。
  fallback: () => [],
  toResult: ({ value, isLoading, isFallback, error }) => ({
    models: value,
    isLoading,
    isFallback,
    error,
  }),
  warnLabel: "[freezone] image models fetch failed, using hardcoded fallback:",
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
 * Trigger the shared model fetch eagerly for a project. Idempotent — safe
 * to call from a root-level useEffect (e.g. FreezoneShell mount) so the
 * request is in-flight before any picker / panel mounts. Subsequent picker
 * renders read straight from the populated store.
 */
export function prefetchFreezoneImageModels(project: string): void {
  store.ensure(project);
}

/**
 * Read the image model list from a shared module-level store.
 *
 * The first call for a given `project` triggers
 * `GET /api/v1/projects/{project}/freezone/image/models`. All subsequent
 * consumers (any picker on any panel) read the same cached snapshot and
 * re-render together when the fetch resolves. [aipdd fork delta] 拉取失败或还在
 * 路上时列表为空（配合 `isLoading` / `isFallback` 让面板显式区分「加载中」与「没
 * 配模型」），绝不回落到前端硬编码的模型；成功返回空 EE 目录同样保持为空。
 *
 * Returning focus to the tab refreshes the catalog (throttled by a 5-minute
 * TTL) so Admin changes become visible without rebuilding the canvas.
 */
export function useFreezoneImageModels(
  projectOverride?: string | null,
): UseFreezoneImageModelsResult {
  return store.use(projectOverride);
}

/**
 * 后台**确实**一个模型都没配（接口成功返回空列表），不是拉取失败兜底、
 * 也不是还在加载。
 *
 * 为 true 时调用方必须禁用提交：后端 `_resolve_catalog_request` 对权威空目录
 * 直接 409，让用户拿一个前端硬编码的模型点下去再收一个报错是最糟的体验。
 * 三个状态必须一起看 —— 只判 `models.length === 0` 会把「还在加载」也算成
 * 「没配」，面板会闪一下不可用。
 *
 * 只吃 models/isLoading/isFallback 三个字段，`useFreezoneVideoModels` 的返回值
 * 形状相同，视频目录也用这一条判据。
 */
export function isAuthoritativeEmptyCatalog(
  result: Pick<UseFreezoneImageModelsResult, "models" | "isLoading" | "isFallback">,
): boolean {
  return !result.isLoading && !result.isFallback && result.models.length === 0;
}