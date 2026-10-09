// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 媒体参数解析定向测试：目录匹配（剥渠道前缀 / 大小写）、
// 图片「无声明不给档」、视频兜底档与默认收敛（720p 优先 / 时长 5-15）。
import { describe, expect, it } from "vitest";

import {
  displayImageSize,
  displayVideoDuration,
  displayVideoHdResolution,
  displayVideoResolution,
  imageSizeOptions,
  matchCatalogModel,
  videoDurationBounds,
  videoDurationChoices,
  videoResolutionOptions,
  VIDEO_HD_RESOLUTION_OPTIONS,
} from "@/features/agent/mediaParams";
import {
  clampDurationSeconds,
  defaultVideoResolution,
  videoDurationBoundsForModel,
  videoResolutionOptionsForModel,
} from "@/features/canvas/domain/mediaModelOptions";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

function catalogModel(overrides: Partial<ModelOption> & { id: string }): ModelOption {
  return {
    providerId: "newapi",
    apiModel: overrides.id,
    label: overrides.id,
    ...overrides,
  };
}

describe("matchCatalogModel", () => {
  it("matches by id / apiModel / label with channel-prefix-stripped comparison", () => {
    const model = catalogModel({ id: "ap-image-2.0" });

    expect(matchCatalogModel([model], "ap-image-2.0")?.id).toBe("ap-image-2.0");
    expect(matchCatalogModel([model], "aipdd::ap-image-2.0")?.id).toBe("ap-image-2.0");
    expect(matchCatalogModel([model], "AIPDD::AP-IMAGE-2.0")?.id).toBe("ap-image-2.0");
    expect(matchCatalogModel([model], "  ap-image-2.0  ")?.id).toBe("ap-image-2.0");
  });

  it("matches when only the label or apiModel carries the name", () => {
    const model = catalogModel({
      id: "catalog-uuid-1",
      apiModel: "doubao-seedance-2-0-fast",
      label: "Seedance 2.0 Fast",
    });

    expect(matchCatalogModel([model], "doubao-seedance-2-0-fast")?.id).toBe("catalog-uuid-1");
    expect(matchCatalogModel([model], "Seedance 2.0 Fast")?.id).toBe("catalog-uuid-1");
  });

  it("returns null for an empty name or unknown model", () => {
    const model = catalogModel({ id: "ap-image-2.0" });

    expect(matchCatalogModel([model], "")).toBeNull();
    expect(matchCatalogModel([model], "   ")).toBeNull();
    expect(matchCatalogModel([model], "ap-minimax-h3")).toBeNull();
    expect(matchCatalogModel([], "ap-image-2.0")).toBeNull();
  });
});

describe("image resolution options", () => {
  it("uses the catalog declaration; empty when undeclared", () => {
    expect(
      imageSizeOptions(catalogModel({ id: "m", resolutionOptions: ["1K", "2K", "4K"] })),
    ).toEqual(["1K", "2K", "4K"]);
    expect(imageSizeOptions(catalogModel({ id: "m" }))).toEqual([]);
    expect(imageSizeOptions(null)).toEqual([]);
  });

  it("shows the first declared tier by default and keeps valid explicit picks", () => {
    const options = ["1K", "2K", "4K"];

    expect(displayImageSize(undefined, options)).toBe("1K");
    expect(displayImageSize("4K", options)).toBe("4K");
    // 失效的显式值（如换到不支持 4K 的模型）收敛回首项。
    expect(displayImageSize("8K", options)).toBe("1K");
  });
});

describe("video resolution options", () => {
  it("falls back to the generic tiers when the catalog doesn't declare", () => {
    expect(videoResolutionOptions(catalogModel({ id: "m" }))).toEqual([
      "480p",
      "720p",
      "1080p",
    ]);
    expect(
      videoResolutionOptions(catalogModel({ id: "m", resolutionOptions: ["480p", "768p", "1080p"] })),
    ).toEqual(["480p", "768p", "1080p"]);
    expect(videoResolutionOptionsForModel(null)).toEqual(["480p", "720p", "1080p"]);
  });

  it("defaults to 720p when present, else the first tier", () => {
    expect(displayVideoResolution(undefined, ["480p", "720p", "1080p"])).toBe("720p");
    // ap-minimax-h3 这类目录没有 720p：退回首项（与画布视频节点同口径）。
    expect(displayVideoResolution(undefined, ["480p", "768p", "1080p"])).toBe("480p");
    expect(displayVideoResolution("1080p", ["480p", "768p", "1080p"])).toBe("1080p");
    // 大小写不敏感匹配，返回目录原值（下发值以目录为准）。
    expect(displayVideoResolution("1080P", ["480p", "768p", "1080p"])).toBe("1080p");
    expect(displayVideoResolution("8K", ["480p", "720p", "1080p"])).toBe("720p");
  });

  it("defaultVideoResolution prefers 720p case-insensitively", () => {
    expect(defaultVideoResolution(["480P", "720P"])).toBe("720P");
    expect(defaultVideoResolution(["240p"])).toBe("240p");
    expect(defaultVideoResolution([])).toBe("720p");
  });
});

describe("video duration bounds", () => {
  it("uses declared bounds and falls back to 5-15", () => {
    expect(
      videoDurationBounds(catalogModel({ id: "m", minDuration: 1, maxDuration: 15 })),
    ).toEqual({ min: 1, max: 15 });
    expect(videoDurationBounds(catalogModel({ id: "m" }))).toEqual({ min: 5, max: 15 });
    expect(videoDurationBounds(null)).toEqual({ min: 5, max: 15 });
  });

  it("degrades invalid declarations (max < min falls back to 15)", () => {
    expect(videoDurationBoundsForModel({ minDuration: 2, maxDuration: 1 })).toEqual({
      min: 2,
      max: 15,
    });
    expect(videoDurationBoundsForModel({ minDuration: 0, maxDuration: null })).toEqual({
      min: 5,
      max: 15,
    });
  });

  it("lists whole seconds across the closed range", () => {
    expect(videoDurationChoices({ min: 3, max: 6 })).toEqual([3, 4, 5, 6]);
    expect(videoDurationChoices({ min: 5, max: 5 })).toEqual([5]);
  });

  it("display clamps explicit picks and defaults to 5 seconds", () => {
    expect(displayVideoDuration(undefined, { min: 1, max: 15 })).toBe(5);
    expect(displayVideoDuration(12, { min: 1, max: 15 })).toBe(12);
    expect(displayVideoDuration(20, { min: 1, max: 15 })).toBe(15);
    expect(displayVideoDuration(2, { min: 5, max: 15 })).toBe(5);
  });

  it("clampDurationSeconds rounds to whole seconds within bounds", () => {
    expect(clampDurationSeconds(7.4, { min: 5, max: 15 })).toBe(7);
    expect(clampDurationSeconds(2, { min: 5, max: 15 })).toBe(5);
  });

  it("HD tiers are fixed 1080p/2k/4k and the display value falls back to 1080p", () => {
    expect(VIDEO_HD_RESOLUTION_OPTIONS.map((option) => option.value)).toEqual([
      "1080p",
      "2k",
      "4k",
    ]);
    expect(displayVideoHdResolution(undefined)).toBe("1080p");
    expect(displayVideoHdResolution("4k")).toBe("4k");
    expect(displayVideoHdResolution(" 2K ")).toBe("2k");
    // 白名单外（如手改存储）退回首档，与后端归一口径一致。
    expect(displayVideoHdResolution("8k")).toBe("1080p");
  });
});