// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * [aipdd fork delta] 画布视频节点的模型必须来自后台「媒体模型」目录。
 *
 * 守两条口径：
 * 1. 新建视频节点不预置任何模型 id（`newapi_seedance-2.0` 这些不来自后台配置，
 *    用户网关里也可能根本没这个名字），也不再继承「上次所选」：始终留空，
 *    由 VideoNode 按 `/freezone/video/models` 目录首模型（= 设置里的默认模型）落位。
 * 2. 画质（=分辨率档）严格等于模型自己的 `resolutionOptions` 声明，不拿通用档位凑菜单。
 */

import { describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { canvasNodeDefinitions } from "@/features/canvas/domain/nodeRegistry";
import { videoQualityOptionsForModel } from "@/features/canvas/nodes/VideoNode";

describe("视频节点模型来源", () => {
  it("新建节点不预置任何模型 id（留空，由目录首模型＝设置默认落位）", () => {
    const data = canvasNodeDefinitions[CANVAS_NODE_TYPES.video].createDefaultData() as Record<
      string,
      unknown
    >;
    expect(data.model).toBe("");
  });
});

describe("视频画质档位跟随目录声明", () => {
  it("声明了的分辨率档原样成为可选项", () => {
    const options = videoQualityOptionsForModel({ resolutionOptions: ["720p", "1080p"] });
    expect(options).toEqual(["720p", "1080p"]);
  });

  it("没声明时回落到通用档位（仅在网关未就绪的降级态出现）", () => {
    const options = videoQualityOptionsForModel({ resolutionOptions: [] });
    expect(options.length).toBeGreaterThan(0);
  });
});
