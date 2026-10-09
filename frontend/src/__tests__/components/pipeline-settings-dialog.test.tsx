// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「运行前设置」对话框单测：钉住输入编排 → onChange 的映射
// （画幅切换 / 时长数字解析 / 故事版开关在缺提示词时禁用），以及「故事正文不在弹窗录入」
// 这条联动约束（整链以「故事」栏目为唯一真源，弹窗只回显字数）。模型与风格用
// base-ui Select（弹层在 jsdom 里不便交互，其目录/枚举由 driver 与后端契约覆盖）。
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const onChange = vi.hoisted(() => vi.fn());
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
});
