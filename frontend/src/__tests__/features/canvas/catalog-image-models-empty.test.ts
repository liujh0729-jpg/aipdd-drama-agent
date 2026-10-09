// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// 「后台一个图片模型都没配」和「目录接口挂了」必须是两种状态；而两者都不再
// 拿前端写死的模型凑数。
//
// [aipdd fork delta] 以前 useCatalogImageModels 会在空目录 / 加载中回落到写死的
// SHARED_MODELS，把「没配模型」伪装成「有模型」，用户在画布上看到一堆能选能点
// 的模型（含 OpenAI 等非约定渠道），选中、点生成才被后端 409 顶回来。现在
// 失败 / 加载中 / 权威空都返回空列表，靠 isLoading / isEmpty 区分状态。
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCatalogImageModels } from "@/features/canvas/domain/catalogImageModels";
import type { UseFreezoneImageModelsResult } from "@/features/canvas/hooks/useFreezoneImageModels";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

let hookState: UseFreezoneImageModelsResult;

// 只替换取数的 hook；`isAuthoritativeEmptyCatalog` 是纯函数判据，
// `useCatalogImageModels` 正是靠它算 isEmpty，必须留真实实现。
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@/features/canvas/hooks/useFreezoneImageModels")
  >()),
  useFreezoneImageModels: () => hookState,
  prefetchFreezoneImageModels: () => {},
}));

const CATALOG_MODEL: ModelOption = {
  id: "studio-image-v1",
  providerId: "openrouter",
  apiModel: "google/gemini-2.5-flash-image-preview",
  label: "Studio Image",
};

function read(state: UseFreezoneImageModelsResult) {
  hookState = state;
  return renderHook(() => useCatalogImageModels()).result.current;
}

describe("useCatalogImageModels 的空目录语义", () => {
  it("拉取失败：不再拿写死模型兜底，列表为空但不算权威空", () => {
    const result = read({
      models: [],
      isLoading: false,
      isFallback: true,
      error: new Error("boom"),
    });
    // 失败态与权威空态区分开（isFallback=true → isEmpty=false），但绝不凭空造模型。
    expect(result.isEmpty).toBe(false);
    expect(result.models).toEqual([]);
    expect(result.getModel(null)).toBeUndefined();
  });

  it("加载中：列表为空、据 isLoading 显加载态，不拿兜底充数", () => {
    const result = read({
      models: [],
      isLoading: true,
      isFallback: true,
      error: null,
    });
    expect(result.isEmpty).toBe(false);
    expect(result.isLoading).toBe(true);
    expect(result.models).toEqual([]);
  });

  it("接口成功但返回空列表：这是后台的权威答案，不许拿硬编码模型盖掉", () => {
    const result = read({
      models: [],
      isLoading: false,
      isFallback: false,
      error: null,
    });
    expect(result.isEmpty).toBe(true);
    expect(result.models).toEqual([]);
    // 节点上存着的旧模型 id 也不能凭空复活成兜底模型。
    expect(result.getModel("huimeng_gpt_image2")).toBeUndefined();
    expect(result.getModel(null)).toBeUndefined();
  });

  it("正常目录：只出目录里的模型，不掺兜底", () => {
    const result = read({
      models: [CATALOG_MODEL],
      isLoading: false,
      isFallback: false,
      error: null,
    });
    expect(result.isEmpty).toBe(false);
    expect(result.models.map((model) => model.id)).toEqual([CATALOG_MODEL.id]);
    expect(result.getModel(CATALOG_MODEL.apiModel)?.id).toBe(CATALOG_MODEL.id);
  });
});
