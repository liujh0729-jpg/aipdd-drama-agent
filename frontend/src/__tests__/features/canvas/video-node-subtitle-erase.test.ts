// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * [aipdd fork delta] 画布视频节点「智能去字幕 / 框选擦除」面板的 AI 去水印模型过滤。
 *
 * 守两条口径：
 * 1. 只有目录里显式声明了视频编辑能力（supportedModes 含 video_edit）的模型，
 *    或按名启发式支持视频编辑的模型（HappyHorse），才能出现在下拉里；
 * 2. 其余模型（声明不含 video_edit、Seedance 1.x、无声明的普通模型）一律剔除。
 */

import { describe, expect, it } from "vitest";

import { ERASE_AI_MODEL_FILTER } from "@/features/canvas/nodes/VideoNode";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

const base: Omit<ModelOption, "id" | "apiModel" | "label"> = {
  providerId: "newapi",
};

describe("擦除面板 AI 去水印模型过滤（ERASE_AI_MODEL_FILTER）", () => {
  it("目录显式声明 video_edit 的模型保留", () => {
    const model: ModelOption = {
      ...base,
      id: "newapi_video-edit-x",
      apiModel: "video-edit-x",
      label: "Video Edit X",
      supportedModes: ["text_to_video", "video_edit"],
    };
    expect(ERASE_AI_MODEL_FILTER(model)).toBe(true);
  });
  it("声明了 supportedModes 但不含 video_edit 的模型剔除", () => {
    const model: ModelOption = {
      ...base,
      id: "newapi_text-only",
      apiModel: "text-only",
      label: "Text Only",
      supportedModes: ["text_to_video", "image_to_video"],
    };
    expect(ERASE_AI_MODEL_FILTER(model)).toBe(false);
  });
  it("HappyHorse 无声明时按启发式保留（其默认支持视频编辑）", () => {
    const model: ModelOption = {
      ...base,
      id: "huimeng/happyhorse-1.0",
      apiModel: "happyhorse_1.0",
      label: "HappyHorse 1.0",
    };
    expect(ERASE_AI_MODEL_FILTER(model)).toBe(true);
  });
  it("Seedance 1.x 无声明时剔除（启发式只覆盖文生 / 首帧）", () => {
    const model: ModelOption = {
      ...base,
      id: "newapi_seedance-1.5-pro",
      apiModel: "newapi_seedance-1.5-pro",
      label: "Seedance 1.5 Pro",
    };
    expect(ERASE_AI_MODEL_FILTER(model)).toBe(false);
  });
  it("无声明的普通模型剔除（视频编辑必须后台显式配置）", () => {
    const model: ModelOption = {
      ...base,
      id: "newapi_seedance-2.0",
      apiModel: "newapi_seedance-2.0",
      label: "Seedance 2.0",
    };
    expect(ERASE_AI_MODEL_FILTER(model)).toBe(false);
  });
});
