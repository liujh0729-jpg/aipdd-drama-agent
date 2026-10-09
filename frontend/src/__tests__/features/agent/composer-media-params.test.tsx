// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] ComposerMediaParams 定向测试：图片模式给分辨率（目录无声明则
// 隐藏）、视频模式给分辨率 + 时长（兜底档、默认 720p / 5 秒）、显式选择上抛与
// 失效值收敛回写。Settings 与媒体目录 hooks 全部桩掉，不触网。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ComposerMediaParams } from "@/features/agent/components/ComposerMediaParams";
import type { AgentMediaParamsValue } from "@/features/agent/mediaParams";
import type { AgentDialogMode } from "@/lib/queries/agent";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

const hoisted = vi.hoisted(() => ({
  imageModels: [] as unknown[],
  videoModels: [] as unknown[],
  settings: {
    image_model: "",
    video_model: "",
    default_image_model: "",
    default_video_model: "",
  },
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentSettings: () => ({ data: { data: hoisted.settings } }),
}));

vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => ({
    models: hoisted.imageModels,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));

vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({
    models: hoisted.videoModels,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));

function catalogModel(overrides: Partial<ModelOption> & { id: string }): ModelOption {
  return {
    providerId: "newapi",
    apiModel: overrides.id,
    label: overrides.id,
    ...overrides,
  };
}

function renderParams(
  value: AgentMediaParamsValue,
  onChange: (next: AgentMediaParamsValue) => void,
  mode: AgentDialogMode = "image",
) {
  return render(
    <ComposerMediaParams project="alpha" mode={mode} value={value} onChange={onChange} />,
  );
}

describe("ComposerMediaParams", () => {
  beforeEach(() => {
    hoisted.imageModels = [];
    hoisted.videoModels = [];
    hoisted.settings = {
      image_model: "",
      video_model: "",
      default_image_model: "",
      default_video_model: "",
    };
  });

  it("renders nothing for the text mode", () => {
    renderParams({}, vi.fn(), "text");

    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows the image resolution picker with catalog tiers and reports explicit picks", async () => {
    const user = userEvent.setup();
    hoisted.settings.default_image_model = "ap-image-2.0";
    hoisted.imageModels = [
      catalogModel({ id: "ap-image-2.0", resolutionOptions: ["1K", "2K", "4K"] }),
    ];
    const onChange = vi.fn();
    renderParams({}, onChange);

    const trigger = screen.getByRole("combobox", { name: "agent.chat.params.resolution" });
    // 未显式设定时展示目录首项（小写展示，下发值仍为目录原值）。
    expect(trigger).toHaveTextContent("1k");

    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name: "4k" }));

    expect(onChange).toHaveBeenCalledWith({ imageSize: "4K" });
  });

  it("hides the image picker when the model declares no resolution tiers", () => {
    hoisted.settings.default_image_model = "ap-no-tiers";
    hoisted.imageModels = [catalogModel({ id: "ap-no-tiers" })];

    renderParams({}, vi.fn());

    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows video resolution + duration with 720p / 5s defaults and reports picks", async () => {
    const user = userEvent.setup();
    hoisted.settings.default_video_model = "ap-minimax-h3";
    hoisted.videoModels = [
      catalogModel({
        id: "ap-minimax-h3",
        resolutionOptions: ["480p", "720p", "1080p"],
        minDuration: 1,
        maxDuration: 15,
      }),
    ];
    const onChange = vi.fn();
    renderParams({}, onChange, "video");

    const resolutionTrigger = screen.getByRole("combobox", {
      name: "agent.chat.params.resolution",
    });
    const durationTrigger = screen.getByRole("combobox", { name: "agent.chat.params.duration" });
    expect(resolutionTrigger).toHaveTextContent("720p");
    expect(durationTrigger).toHaveTextContent(
      'agent.chat.params.durationValue:{"seconds":5}',
    );

    await user.click(resolutionTrigger);
    await user.click(await screen.findByRole("option", { name: "1080p" }));
    expect(onChange).toHaveBeenCalledWith({ videoResolution: "1080p" });

    await user.click(durationTrigger);
    await user.click(
      await screen.findByRole("option", {
        name: 'agent.chat.params.durationValue:{"seconds":10}',
      }),
    );
    expect(onChange).toHaveBeenCalledWith({ videoDuration: 10 });
  });

  it("falls back to the generic video tiers without a catalog declaration", async () => {
    const user = userEvent.setup();
    hoisted.settings.default_video_model = "ap-custom";
    hoisted.videoModels = [catalogModel({ id: "ap-custom" })];
    renderParams({}, vi.fn(), "video");

    const resolutionTrigger = screen.getByRole("combobox", {
      name: "agent.chat.params.resolution",
    });
    expect(resolutionTrigger).toHaveTextContent("720p");

    await user.click(resolutionTrigger);
    expect(await screen.findByRole("option", { name: "480p" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1080p" })).toBeInTheDocument();
  });

  it("normalizes a stale explicit pick after the catalog changes", () => {
    hoisted.settings.default_image_model = "ap-image-2.0";
    hoisted.imageModels = [
      catalogModel({ id: "ap-image-2.0", resolutionOptions: ["1K", "2K", "4K"] }),
    ];
    const onChange = vi.fn();
    const { rerender } = renderParams({ imageSize: "4K" }, onChange);
    expect(onChange).not.toHaveBeenCalled();

    // 目录换成不含 4K 的档位（如后台改了模型声明）→ 显式值收敛回首项并回写。
    hoisted.imageModels = [
      catalogModel({ id: "ap-image-2.0", resolutionOptions: ["1K", "2K"] }),
    ];
    rerender(
      <ComposerMediaParams
        project="alpha"
        mode="image"
        value={{ imageSize: "4K" }}
        onChange={onChange}
      />,
    );

    expect(onChange).toHaveBeenCalledWith({ imageSize: "1K" });
  });

  // --------------------------------------------------- 超清开关（req22） ---

  it("hides the HD switch outside video mode", () => {
    renderParams({}, vi.fn(), "image");
    renderParams({}, vi.fn(), "text");

    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("turning the HD switch on reports the default 1080p tier", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderParams({}, onChange, "video");

    const toggle = screen.getByRole("switch", { name: "agent.chat.params.hd" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    // 未开启时不给超清档位下拉。
    expect(
      screen.queryByRole("combobox", { name: "agent.chat.params.hdResolution" }),
    ).toBeNull();

    await user.click(toggle);
    expect(onChange).toHaveBeenCalledWith({ videoHdResolution: "1080p" });
  });

  it("shows the fixed 1080P/2K/4K picker when enabled and reports tier picks", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderParams({ videoHdResolution: "2k" }, onChange, "video");

    expect(screen.getByRole("switch", { name: "agent.chat.params.hd" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    const hdTrigger = screen.getByRole("combobox", {
      name: "agent.chat.params.hdResolution",
    });
    expect(hdTrigger).toHaveTextContent("2K");

    await user.click(hdTrigger);
    await user.click(await screen.findByRole("option", { name: "4K" }));
    expect(onChange).toHaveBeenCalledWith({ videoHdResolution: "4k" });
  });

  it("turning the HD switch off clears the tier", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderParams({ videoHdResolution: "4k" }, onChange, "video");

    await user.click(screen.getByRole("switch", { name: "agent.chat.params.hd" }));
    expect(onChange).toHaveBeenCalledWith({ videoHdResolution: undefined });
  });
});