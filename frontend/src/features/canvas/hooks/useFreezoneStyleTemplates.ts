// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  listFreezoneStyleTemplates,
  type FreezoneStyleTemplate,
} from "@/api/ops";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneStyleTemplatesResult {
  templates: FreezoneStyleTemplate[];
  assetBase: string;
  isLoading: boolean;
  error: Error | null;
  /**
   * 失败后重来一次。风格直接决定出图效果，不像相机参数那样可有可无，所以这里
   * 比同族 hook 多给一个出口。只在 error 态生效，其余状态调用是空操作 —— 不做
   * 自动重试是因为 ensure 在 render 里调用，自动重试会变成失败→重渲染→
   * 再失败的死循环。
   */
  retry: () => void;
}

// Per-project shared store — mirrors useFreezoneImageModels /
// useFreezoneCameraOptions. One fetch per project per tab lifetime.
const store = createCatalogStore<
  { templates: FreezoneStyleTemplate[]; assetBase: string },
  UseFreezoneStyleTemplatesResult
>({
  scope: "project",
  // 惰性转发防顶层取绑定：mock @/api/ops 缺该导出时 vitest 在访问处抛错，
  // 推迟到调用时才能被内核的 Promise 链捕获成失败回落。
  fetch: (project) => listFreezoneStyleTemplates(project),
  fallback: () => ({ templates: [], assetBase: "" }),
  toResult: ({ value, isLoading, error }, { retry }) => ({
    templates: value.templates,
    assetBase: value.assetBase,
    isLoading,
    error,
    retry,
  }),
  warnLabel: "[freezone] style-templates fetch failed:",
});

export function prefetchFreezoneStyleTemplates(project: string): void {
  store.ensure(project);
}

/**
 * [aipdd fork delta] 风格封面刚被上传/生成/删除后，给这一条的 cover 拼一个
 * cache-busting 参数。封面走 `/api/v1/style-gallery/<id>/cover.webp`，响应带
 * `Cache-Control: max-age=300`——地址不变的话浏览器最多五分钟还显示旧图（
 * 删除后则一直显示已删的图）。清单里的 cover 是相对路径，多带一个 query 对
 * 下游拼 URL 的用法完全兼容。
 *
 * 只改这一条而不是整表重拉：其余风格的封面没变，不动它们的缓存。store 还没
 * 加载时是空操作——首次加载的请求本来就拿的是服务端最新状态。
 */
export function bustStyleTemplateCover(project: string, styleId: string): void {
  if (!project || !styleId) return;
  const bust = Date.now();
  store.patch(project, (value) => ({
    ...value,
    templates: value.templates.map((item) =>
      item.id === styleId
        ? { ...item, cover: `${item.cover.split("?")[0]}?v=${bust}` }
        : item,
    ),
  }));
}

export function useFreezoneStyleTemplates(
  projectOverride?: string | null,
): UseFreezoneStyleTemplatesResult {
  return store.use(projectOverride);
}