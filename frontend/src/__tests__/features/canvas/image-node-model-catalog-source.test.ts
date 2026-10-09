// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * [aipdd fork delta] 画布图片节点的模型与参数都必须来自后台「媒体模型」目录。
 *
 * 守两条曾经漂移过的口径：
 * 1. 新建图片节点不再写死默认模型 id（`huimeng/gpt-image-2` / `openrouter/default`
 *    都不在目录里，选中后请求必然打到不存在的模型上）；只继承上次所选。
 * 2. 分辨率 / 比例的可选项严格等于模型自己的声明，前端不拿通用档位凑菜单；
 *    没声明时只留实际会发出的那一个值。
 */

import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { canvasNodeDefinitions } from "@/features/canvas/domain/nodeRegistry";
import {
  toImageModelDefinition,
  type CatalogImageModelEntry,
} from "@/features/canvas/domain/catalogImageModels";

const LAST_IMAGE_MODEL_KEY = "canvas.lastImageModel";

function entry(overrides: Partial<CatalogImageModelEntry> = {}): CatalogImageModelEntry {
  return {
    id: "newapi/ap-image-2.0",
    providerId: "newapi",
    apiModel: "ap-image-2.0",
    label: "ap-image-2.0",
    ...overrides,
  };
}

describe("图片节点模型来源", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("没有上次所选时不写死任何模型 id", () => {
    for (const type of [
      CANVAS_NODE_TYPES.imageGen,
      CANVAS_NODE_TYPES.imageEdit,
      CANVAS_NODE_TYPES.storyboardGen,
    ]) {
      const data = canvasNodeDefinitions[type].createDefaultData() as Record<string, unknown>;
      expect(data.model).toBe("");
    }
  });

  it("继承上一次选择的目录模型", () => {
    window.localStorage.setItem(LAST_IMAGE_MODEL_KEY, "newapi/ap-jimeng-image-4.0");

    const data = canvasNodeDefinitions[CANVAS_NODE_TYPES.imageGen].createDefaultData() as Record<
      string,
      unknown
    >;
    expect(data.model).toBe("newapi/ap-jimeng-image-4.0");
  });
});

describe("图片模型参数档位跟随目录声明", () => {
  it("声明了的档位原样成为可选项", () => {
    const model = toImageModelDefinition(
      entry({
        resolutionOptions: ["1K", "4K"],
        ratioOptions: ["16:9", "9:16"],
      }),
    );

    expect(model.resolutions.map((item) => item.value)).toEqual(["1K", "4K"]);
    expect(model.aspectRatios.map((item) => item.value)).toEqual(["16:9", "9:16"]);
  });

  it("没声明时不套通用兜底档位，只留实际会发出的那一个值", () => {
    const model = toImageModelDefinition(entry());

    expect(model.resolutions.map((item) => item.value)).toEqual(["2K"]);
    expect(model.aspectRatios.map((item) => item.value)).toEqual(["1:1"]);
  });
});
