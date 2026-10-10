// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「运行前设置」对话框单测：钉住输入编排 → onChange 的映射
// （画幅切换 / 时长数字解析 / 故事版开关在缺提示词时禁用），以及「故事正文不在弹窗录入」
// 这条联动约束（整链以「故事」栏目为唯一真源，弹窗只回显字数）。模型与风格用
// base-ui Select，候选一律来自目录 hook（这里 mock，避开真实拉取）；「留空 = 沿用
// 当前」是配齐契约的一部分，所以除了选项映射还钉住 __keep__ 回空串。
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const onChange = vi.hoisted(() => vi.fn());
// 三类候选都给一条真实形状的条目：文本是 {provider}::{model} 复合名，
// 图片/视频是媒体目录条目 id（后端据此改默认，不需要 provider 前缀）。
vi.mock("@/features/canvas/hooks/useFreezoneTextModels", () => ({
  useFreezoneTextModels: () => ({
    models: [{ id: "aipdd::ap-live-llm", label: "ap-live-llm" }],
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({
    models: [{ id: "ap-video-1", label: "ap-video-1" }],
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => ({
    models: [{ id: "huimeng/gpt-image-2", label: "GPT Image 2" }],
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import {
  EMPTY_PIPELINE_RUN_SETTINGS,
  PipelineSettingsDialog,
  type PipelineRunSettings,
} from "@/components/pipeline-settings-dialog";

const base: PipelineRunSettings = { ...EMPTY_PIPELINE_RUN_SETTINGS };

beforeEach(() => {
  vi.clearAllMocks();
});

function renderDialog(
  value: PipelineRunSettings = base,
  storyVersionAvailable = false,
  storyCharCount?: number,
) {
  render(
    <PipelineSettingsDialog
      project="proj-a"
      open
      onOpenChange={() => {}}
      value={value}
      onChange={onChange}
      storyVersionAvailable={storyVersionAvailable}
      storyCharCount={storyCharCount}
    />,
  );
}

describe("PipelineSettingsDialog", () => {
  it("switches the aspect ratio to landscape (16:9)", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("radio", { name: /pipeline.settings.aspectLandscape/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ aspectRatio: "16:9" }));
  });

  it("parses an episode-count number", () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("pipeline.settings.episodeCount"), {
      target: { value: "12" },
    });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ episodeCount: 12 }));
  });

  it("clears episode count to null when the field is emptied", () => {
    renderDialog({ ...base, episodeCount: 12 });
    fireEvent.change(screen.getByLabelText("pipeline.settings.episodeCount"), {
      target: { value: "" },
    });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ episodeCount: null }));
  });

  it("takes the story from the page instead of a text box in the dialog", () => {
    renderDialog(base, false, 3210);
    // 不再提供故事输入：避免同一个项目同时存在两份故事正文。
    expect(screen.queryByLabelText("pipeline.settings.story")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    // 只回显「将用页面上面那份故事」。
    expect(screen.getByText("pipeline.settings.storyFromPage")).toBeInTheDocument();
  });

  it("hides the story-source echo when the host page has no story text", () => {
    renderDialog();
    expect(
      screen.queryByText("pipeline.settings.storyFromPage"),
    ).not.toBeInTheDocument();
  });

  it("shows the storyboard toggle as unavailable when the prompt library has none", () => {
    renderDialog(base, false);
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("pipeline.settings.storyVersionMissing")).toBeInTheDocument();
  });

  it("shows the storyboard toggle as available once a prompt is configured", () => {
    renderDialog(base, true);
    expect(screen.getByRole("checkbox")).not.toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("pipeline.settings.includeStoryVersionHint")).toBeInTheDocument();
  });

  // 「三个模型」是本轮加的：一条整链要用的功能位太多，让用户在创建处一次挑定。
  it("offers the three pipeline model slots, defaulting to keep-current", () => {
    renderDialog();
    for (const key of [
      "pipeline.settings.textModel",
      "pipeline.settings.imageModel",
      "pipeline.settings.videoModel",
    ]) {
      expect(screen.getByText(key)).toBeInTheDocument();
    }
    // 文本 / 图片 / 视频 / 导演风格 = 四个下拉。
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    // 什么都没选时不拿空列表迷惑用户：占位就是「沿用当前」；而且 base-ui 收起时靠
    // Root 上的 items 映射标签，不能把 __keep__ / 复合调用名直接甩给用户。
    for (const trigger of screen.getAllByRole("combobox").slice(0, 3)) {
      expect(trigger).toHaveTextContent("pipeline.settings.modelKeep");
      expect(trigger).not.toHaveTextContent("__keep__");
    }
    expect(screen.getAllByRole("combobox")[3]).toHaveTextContent("storyPage.style.auto");
    expect(screen.getByText("pipeline.settings.modelsHint")).toBeInTheDocument();
  });

  it("maps a picked text model to the composite call name", async () => {
    const user = userEvent.setup();
    renderDialog();
    const [textSelect] = screen.getAllByRole("combobox");
    await user.click(textSelect);
    await user.click(await screen.findByRole("option", { name: "ap-live-llm" }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ textModel: "aipdd::ap-live-llm" }),
    );
  });

  it("maps keep-current back to an empty slot so provisioning skips it", async () => {
    const user = userEvent.setup();
    renderDialog({ ...base, videoModel: "ap-video-1" });
    const videoSelect = within(screen.getByRole("dialog")).getAllByRole("combobox")[2];
    await user.click(videoSelect);
    await user.click(
      await screen.findByRole("option", { name: "pipeline.settings.modelKeep" }),
    );
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ videoModel: "" }));
  });
});
