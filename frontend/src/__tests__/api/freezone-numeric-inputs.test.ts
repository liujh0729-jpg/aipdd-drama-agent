// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// [aipdd fork delta] 工具页数字输入防 422：输入框可被清空（Number("") = 0）或
// 键入越界值，提交前必须归一为后端 schema 合法域，否则被 Pydantic 422 拒收
// （实测：freezone/video/idea 数字字段越界 → 422）。需求6 后 idea 面板只保留
// 总时长输入，分场/分镜/单镜时长交给后端 AI 依总时长规划。
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiCall } from "@/api/client";
import {
  submitFreezoneVideoErase,
  submitFreezoneVideoIdea,
  submitFreezoneExplainerVideo,
  submitFreezoneVideoMix,
  submitFreezoneWhiteboardAnimation,
} from "@/api/ops";

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
  apiClient: {},
}));

function lastBody(): Record<string, unknown> {
  const calls = vi.mocked(apiCall).mock.calls;
  const request = calls[calls.length - 1]?.[1] as {
    json?: Record<string, unknown>;
  };
  return request?.json ?? {};
}

describe("freezone numeric inputs normalize before submit", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: "job-1" });
  });

  it("idea: cleared duration falls back and out-of-range duration clamps", async () => {
    await submitFreezoneVideoIdea("project-1", {
      idea: "一只流浪猫",
      totalDuration: 0, // 清空输入框 → Number("") = 0 → 默认 60
    });
    expect(lastBody()).toMatchObject({ total_duration: 60 });

    await submitFreezoneVideoIdea("project-1", {
      idea: "一只流浪猫",
      totalDuration: 2, // 低于下限 → 5
    });
    expect(lastBody()).toMatchObject({ total_duration: 5 });

    await submitFreezoneVideoIdea("project-1", {
      idea: "一只流浪猫",
      totalDuration: 9999, // 越过上限 → 300
    });
    const body = lastBody();
    expect(body.total_duration).toBe(300);
    // 总时长口径下不再发送旧的三个数字字段（分场/分镜交给 AI）。
    expect(body).not.toHaveProperty("max_scenes");
    expect(body).not.toHaveProperty("max_shots_per_scene");
    expect(body).not.toHaveProperty("shot_duration");
  });

  it("idea: absent total duration keeps the tool default of 60", async () => {
    await submitFreezoneVideoIdea("project-1", { idea: "一只流浪猫" });
    expect(lastBody()).toMatchObject({ total_duration: 60 });
  });

  it("mix: clip duration clamps while an explicit 0 volume stays zero", async () => {
    await submitFreezoneVideoMix("project-1", {
      materialUrls: ["/static/a.jpg"],
      clipDuration: 0, // 清空 → 默认 3
      bgmVolume: 1.5, // 越过上限 → 1
      maxDurationSeconds: 2, // 低于下限 → 5
    });
    expect(lastBody()).toMatchObject({
      clip_duration: 3,
      bgm_volume: 1,
      max_duration_seconds: 5,
    });

    await submitFreezoneVideoMix("project-1", {
      materialUrls: ["/static/a.jpg"],
      clipDuration: 15, // 越过上限 → 10
      bgmVolume: 0, // 0 是显式合法值，不被当空值丢弃/替换
    });
    const body = lastBody();
    expect(body.clip_duration).toBe(10);
    expect(body.bgm_volume).toBe(0);
    expect(body.max_duration_seconds).toBeNull();
  });

  it("whiteboard: scene cap rounds and clamps while an explicit 0 volume stays zero", async () => {
    await submitFreezoneWhiteboardAnimation("project-1", {
      script: "一句话文案",
      maxScenes: 0, // 清空输入框 → 0（1-12 非法）→ 默认 8
      bgmVolume: 1.8, // 越过上限 → 1
    });
    expect(lastBody()).toMatchObject({ max_scenes: 8, bgm_volume: 1 });

    await submitFreezoneWhiteboardAnimation("project-1", {
      script: "一句话文案",
      maxScenes: 6.4, // 整数字段四舍五入
      bgmVolume: 0, // 0 是显式合法值（静音 BGM），不被当空丢弃
    });
    const body = lastBody();
    expect(body.max_scenes).toBe(6);
    expect(body.bgm_volume).toBe(0);
    // 未选模型 / 未烧录字幕时不发送对应字段，由后端默认兜底。
    expect(body).not.toHaveProperty("model_text");
    expect(body).not.toHaveProperty("model_image");
    expect(body).not.toHaveProperty("subtitle_font_size");
    expect(body.subtitle_enabled).toBe(false);
    expect(body.pen_cursor).toBe(true);
  });

  it("explainer: target duration clamps into the sample band while an explicit 0 volume stays zero", async () => {
    await submitFreezoneExplainerVideo("project-1", {
      referenceContent: "RAG 先把文档切块入库。",
      durationS: 0, // 清空输入框 → 0（90-330 非法）→ 默认 180
      bgmVolume: 1.8, // 越过上限 → 1
    });
    expect(lastBody()).toMatchObject({ duration_s: 180, bgm_volume: 1 });

    await submitFreezoneExplainerVideo("project-1", {
      referenceContent: "RAG 先把文档切块入库。",
      durationS: 1200, // 越过上限 → 330（样片档）
      bgmVolume: 0, // 0 是显式合法值（静音 BGM），不被当空丢弃
    });
    const body = lastBody();
    expect(body.duration_s).toBe(330);
    expect(body.bgm_volume).toBe(0);
    // 未选模型时不发送对应字段，由后端默认兜底；幕底/清晰度按面板默认送出。
    expect(body).not.toHaveProperty("model_text");
    expect(body).not.toHaveProperty("model");
    expect(body.background).toBe("dots");
    expect(body.resolution).toBe("720p");
  });

  it("erase: box region clamps and keeps width/height strictly positive", async () => {
    await submitFreezoneVideoErase("project-1", {
      sourceUrl: "/static/a.mp4",
      mode: "box",
      box: { x: 1.2, y: -0.5, width: 0, height: 2 },
    });
    expect(lastBody()).toMatchObject({
      box_x: 1,
      box_y: 0,
      box_width: 0.01, // gt=0：清空产生的 0 必须保底为最小正值
      box_height: 1,
    });
  });
});