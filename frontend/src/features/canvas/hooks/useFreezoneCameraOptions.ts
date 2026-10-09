// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchFreezoneCameraOptions,
  type FreezoneCameraOptions,
} from "@/api/ops";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneCameraOptionsResult {
  options: FreezoneCameraOptions | null;
  isLoading: boolean;
  error: Error | null;
}

// Per-project shared store. Mirrors useFreezoneImageModels — single fetch per
// project per tab lifetime, every consumer reads the same reference. 相机参数
// 是纯选项，失败就永久失败（没有 retry 出口），所以不挂 focus / 目录事件刷新。
const store = createCatalogStore<
  FreezoneCameraOptions | null,
  UseFreezoneCameraOptionsResult
>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneCameraOptions(project),
  fallback: () => null,
  toResult: ({ value, isLoading, error }) => ({
    options: value,
    isLoading,
    error,
  }),
  warnLabel: "[freezone] camera-options fetch failed:",
});

export function prefetchFreezoneCameraOptions(project: string): void {
  store.ensure(project);
}

export function useFreezoneCameraOptions(
  projectOverride?: string | null,
): UseFreezoneCameraOptionsResult {
  return store.use(projectOverride);
}