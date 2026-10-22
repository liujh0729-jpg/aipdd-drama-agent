// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  listFreezoneMotionCases,
  listFreezoneMotionStyles,
  type FreezoneMotionCatalog,
  type FreezoneMotionCase,
  type FreezoneMotionStyle,
  type FreezoneMotionStyleCatalog,
} from "@/api/ops";
import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

export interface UseFreezoneMotionCasesResult extends Omit<FreezoneMotionCatalog, "cases"> {
  cases: FreezoneMotionCase[];
  isLoading: boolean;
  error: Error | null;
  retry: () => void;
}

const EMPTY: FreezoneMotionCatalog = {
  assetBase: "",
  version: "",
  categories: [],
  methods: [],
  modelHint: "",
  renderContract: "",
  source: "",
  cases: [],
};

// 与画风图册同一个内核：按项目分桶，一版清单一次拉取，失败回落空目录（图册空着
// 是能看懂的降级，白屏不是）。清单是随包静态数据，不做 focus 刷新。
const store = createCatalogStore<FreezoneMotionCatalog, UseFreezoneMotionCasesResult>({
  scope: "project",
  fetch: (project) => listFreezoneMotionCases(project),
  fallback: () => EMPTY,
  toResult: ({ value, isLoading, error }, { retry }) => ({
    ...value,
    isLoading,
    error,
    retry,
  }),
  warnLabel: "[freezone] motion-cases fetch failed:",
});

export function prefetchFreezoneMotionCases(project: string): void {
  store.ensure(project);
}

export function useFreezoneMotionCases(
  projectOverride?: string | null,
): UseFreezoneMotionCasesResult {
  return store.use(projectOverride);
}

// --------------------------------------------------------------------------- //
// 画风图册（66 条风格）：与案例图册同一个内核、同一个宿主弹层，只是没有素材。
// --------------------------------------------------------------------------- //

export interface UseFreezoneMotionStylesResult
  extends Omit<FreezoneMotionStyleCatalog, "styles"> {
  styles: FreezoneMotionStyle[];
  isLoading: boolean;
  error: Error | null;
  retry: () => void;
}

const EMPTY_STYLES: FreezoneMotionStyleCatalog = {
  version: "",
  categories: [],
  source: "",
  styles: [],
};

const styleStore = createCatalogStore<
  FreezoneMotionStyleCatalog,
  UseFreezoneMotionStylesResult
>({
  scope: "project",
  fetch: (project) => listFreezoneMotionStyles(project),
  fallback: () => EMPTY_STYLES,
  toResult: ({ value, isLoading, error }, { retry }) => ({
    ...value,
    isLoading,
    error,
    retry,
  }),
  warnLabel: "[freezone] motion-styles fetch failed:",
});

export function prefetchFreezoneMotionStyles(project: string): void {
  styleStore.ensure(project);
}

export function useFreezoneMotionStyles(
  projectOverride?: string | null,
): UseFreezoneMotionStylesResult {
  return styleStore.use(projectOverride);
}

/**
 * 案例素材地址的唯一解析点（与 `resolveStyleAssetUrl` 同一条约定）。
 *
 * 后端下发 `asset_base`：配了 OSS/CDN 就是外部前缀，留空时指向同源
 * `/api/v1/motion-gallery`（海报随包、原片按需落本机缓存）。
 */
export function resolveMotionAssetUrl(rel: string, assetBase: string): string {
  if (!rel) return "";
  const item = rel.startsWith("/") ? rel.slice(1) : rel;
  if (!assetBase) return `/api/v1/motion-gallery/${item}`;
  return `${assetBase.replace(/\/+$/, "")}/${item}`;
}

/** 一条案例的原片地址；海报或原片不该出现时才判空，取不到由前端隐藏按钮。 */
export function motionVideoUrl(
  item: FreezoneMotionCase,
  assetBase: string,
  posterRel = "poster.webp",
): string {
  if (!item.mediaFile) return "";
  const dir = item.cover.endsWith(posterRel)
    ? item.cover.slice(0, -posterRel.length)
    : `${item.id}/`;
  // 原片文件名带中文和空格（`M43_twoclipping_产品原片.mp4`），不编码会被
  // 浏览器截断或转义成对不上的路径，镜像/缓存两边都取不到。
  return resolveMotionAssetUrl(`${dir}${encodeURIComponent(item.mediaFile)}`, assetBase);
}

/**
 * 随包小样片地址：始终走本机 API，不拼 `assetBase`。
 *
 * 小样片是安装包里的新产物，OSS/CDN 镜像上只有海报与原片；跟着 assetBase 走
 * 会在配了镜像的部署里 404。版本号取后端给的 mtime，重截后自动换 URL。
 */
export function motionPreviewUrl(item: FreezoneMotionCase): string {
  if (!item.previewReady) return "";
  const version = item.previewVersion
    ? `?v=${encodeURIComponent(item.previewVersion)}`
    : "";
  return `/api/v1/motion-gallery/${encodeURIComponent(item.id)}/preview.mp4${version}`;
}
