// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 风格管理：逐个风格上传/删除本地图墙封面。
// 后端端点见 src/novelvideo/api/routes/styles.py 的 /style-gallery/*。
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import { coerceModelList, type FreezoneImageModelInfo } from "@/api/ops";
import { uploadApi } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse, OkResponse } from "@/types/api";

export interface StyleGalleryItem {
  id: string;
  label: string;
  category: string;
  /** 是否已上传本地封面（否则图墙该行显示占位块）。 */
  has_cover: boolean;
  /** 已上传时给带 cache-busting 的预览地址；未上传为 null。 */
  cover_url: string | null;
}

export function useStyleGalleryStyles(enabled = true) {
  return useQuery({
    queryKey: queryKeys.styleGalleryStyles(),
    queryFn: ({ signal }) =>
      uploadApi
        .get("api/v1/style-gallery/styles", { signal })
        .json<OkResponse<StyleGalleryItem[]>>(),
    enabled,
    staleTime: 15_000,
  });
}

/**
 * [aipdd fork delta] 「AI 生成封面」可选的图片模型目录（仅超管）。与生成端
 * 同一个作用域解析口径：选择器里能看到的模型就是生成时可解析执行的模型。
 * 条目形状与 /freezone/image/models 同源，防御式解析复用 coerceModelList。
 */
export function useStyleGalleryImageModels(enabled = true) {
  return useQuery({
    queryKey: queryKeys.styleGalleryImageModels(),
    queryFn: async ({ signal }): Promise<FreezoneImageModelInfo[]> => {
      const payload = await uploadApi
        .get("api/v1/style-gallery/image-models", { signal })
        .json<unknown>();
      return coerceModelList(payload);
    },
    enabled,
    staleTime: 60_000,
  });
}

/**
 * [aipdd fork delta] 封面上传的核心逻辑做成普通函数导出：资产库上传是一个自包含
 * 弹窗（不依赖 react-query 的 Provider），它也要能在入库后顺手把首张图片设为封面。
 * mutation hook 只是这个函数的壳，两处共用同一份请求与报错处理。
 */
export function uploadStyleGalleryCover(
  styleId: string,
  file: File,
): Promise<ApiResponse<StyleGalleryItem>> {
  const formData = new FormData();
  formData.append("file", file);
  return jsonWithBackendError<ApiResponse<StyleGalleryItem>>(
    uploadApi.post(p`api/v1/style-gallery/${styleId}/cover`, {
      body: formData,
      throwHttpErrors: false,
    }),
  );
}

/**
 * [aipdd fork delta] 封面变更（上传/删除/生成）同时改变资产库「风格」栏目：
 * 后端读取资产库时会把已配封面的风格实时投影成只读条目（见 video_node 的
 * compose_library_with_style_covers）。除图墙元数据外，把已挂载的资产库查询
 * 一起失效——否则画布侧栏要等下次重挂载/过期重取才看得到封面的出现与消失。
 */
function invalidateStyleCoverQueries(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: queryKeys.styleGalleryStyles() });
  void qc.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey;
      return (
        Array.isArray(key) &&
        key[0] === "projects" &&
        key[2] === "freezone" &&
        key[3] === "asset-library"
      );
    },
  });
}

export function useUploadStyleGalleryCover() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ styleId, file }: { styleId: string; file: File }) =>
      uploadStyleGalleryCover(styleId, file),
    onSuccess: () => invalidateStyleCoverQueries(qc),
  });
}

export function useDeleteStyleGalleryCover() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (styleId: string) =>
      jsonWithBackendError<ApiResponse<{ id: string; removed: boolean }>>(
        uploadApi.delete(p`api/v1/style-gallery/${styleId}/cover`, {
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => invalidateStyleCoverQueries(qc),
  });
}

export interface GenerateStyleGalleryCoverInput {
  styleId: string;
  /**
   * [aipdd fork delta] 生成所用图片模型（/style-gallery/image-models 条目的
   * id / catalogId / apiModel 均可）；缺省时沿用默认图片模型（历史行为）。
   */
  model?: string;
}

// [aipdd fork delta] 用该风格自身的 style_prompt 走所选图片模型同步 AI 生成封面，
// 可覆盖已有封面。后端见 routes/styles.py 的 /style-gallery/{id}/cover/generate。
export function useGenerateStyleGalleryCover() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ styleId, model }: GenerateStyleGalleryCoverInput) =>
      jsonWithBackendError<ApiResponse<StyleGalleryItem>>(
        uploadApi.post(p`api/v1/style-gallery/${styleId}/cover/generate`, {
          json: model ? { model } : {},
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => invalidateStyleCoverQueries(qc),
  });
}
