// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// 无模型选择器的面板（360 全景 / 宫格动作）：调用方选定模型后，提交字段必须由
// 该模型自己声明的能力档位解析出来。
//
// [aipdd fork delta] WP-D: 原来这份契约的另一半是「报价与提交同源」，额度体系
// 拆掉后报价那一侧不复存在，剩下的价值是「界面显示的能力档位 == 真正下发的字段」。
//
// 早先提交什么都不带，后端于是用自己的默认模型和固定 2K 去跑。目录首模型不是
// 后端默认模型、或它根本不支持 2K 时，界面标的能力档位与真正执行的是两回事。
import { describe, expect, it } from "vitest";

import { resolveFixedFeatureModelRequest } from "@/features/canvas/domain/fixedFeatureModelRequest";

const CATALOG_MODEL = {
  catalogId: "cat-77",
  apiModel: "google/gemini-2.5-flash-image-preview",
  resolutionOptions: ["1K", "4K"],
  qualityOptions: ["low", "high"],
};

describe("resolveFixedFeatureModelRequest", () => {
  it("模型不支持默认档位时收敛到它真支持的档位，而不是硬发 2K/medium", () => {
    const { submit } = resolveFixedFeatureModelRequest(CATALOG_MODEL);
    // 目录只配了 1K / 4K 与 low / high。
    expect(submit.imageSize).toBe("1K");
    expect(submit.quality).toBe("low");
  });

  it("带上模型与目录身份，不把模型选择留给后端默认", () => {
    const { submit } = resolveFixedFeatureModelRequest(CATALOG_MODEL);
    expect(submit.model).toBe(CATALOG_MODEL.apiModel);
    expect(submit.catalogId).toBe("cat-77");
  });

  it("模型名不加 provider 前缀", () => {
    const { submit } = resolveFixedFeatureModelRequest(CATALOG_MODEL);
    // scene-360 那条老路由把 provider 恒定当成 newapi，整串 `provider/model`
    // 会被原样当作模型名送给网关。
    expect(submit.model).not.toContain("openrouter/");
  });

  it("目录没声明画质就不下发 quality", () => {
    const { submit } = resolveFixedFeatureModelRequest({
      apiModel: "huimeng_gpt_image2",
      resolutionOptions: ["1K", "2K"],
    });
    expect(submit.quality).toBeUndefined();
    expect(submit.imageSize).toBe("2K");
  });

  it("目录没配分辨率时用通用兜底档位", () => {
    const { submit } = resolveFixedFeatureModelRequest({
      apiModel: "huimeng_gpt_image2",
    });
    expect(submit.imageSize).toBe("2K");
  });

  it("没有可用模型时不伪造模型身份", () => {
    const { submit } = resolveFixedFeatureModelRequest(undefined);
    expect(submit.model).toBeUndefined();
    expect(submit.catalogId).toBeUndefined();
  });
});
