// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchFreezoneVideoCameraTemplates,
  type FreezoneVideoCameraTemplate,
} from "@/api/ops";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneVideoCameraTemplatesResult {
  templates: FreezoneVideoCameraTemplate[];
  isLoading: boolean;
  error: Error | null;
}

// Per-project shared store, mirrors useFreezoneCameraOptions. 一次性目录：
// 失败就永久失败，不挂 focus / 目录事件刷新。
const store = createCatalogStore<
  FreezoneVideoCameraTemplate[],
  UseFreezoneVideoCameraTemplatesResult
>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => fetchFreezoneVideoCameraTemplates(project),
  fallback: () => [],
  toResult: ({ value, isLoading, error }) => ({
    templates: value,
    isLoading,
    error,
  }),
  warnLabel: "[freezone] video/camera-templates fetch failed:",
});

export function prefetchFreezoneVideoCameraTemplates(project: string): void {
  store.ensure(project);
}

export function useFreezoneVideoCameraTemplates(
  projectOverride?: string | null,
): UseFreezoneVideoCameraTemplatesResult {
  return store.use(projectOverride);
}