// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 输入区模型下拉契约（用户实测反馈的两条）：
// 1. 「跟随系统默认」项必须与同名候选项字面可区分——现网视频默认 ap-minimax-h3 本身
//    就在候选里，两行字面一致时用户看到的就是「两个 MiniMax H3」。
// 2. 图片/视频回合同样必需文本模型；解析不到时要就地给出文本模型下拉，而不是把用户
//    丢到设置页（那在当前页面是一条走不通的死路）。
// 数据层整体桩掉（Agent 设置 + 网关模型目录），不触网。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ComposerModelPicker } from "@/features/agent/components/ComposerModelPicker";

// i18n 桩：直接回显键名，断言盯住「用了哪个词条」而不是译文本身。
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

interface PlazaFixture {
  modelName: string;
  provider: string;
  channelName: string;
  capabilities: string[];
}

const hoisted = vi.hoisted(() => ({
  settings: {} as Record<string, string>,
  models: [] as PlazaFixture[],
  updates: [] as Record<string, string>[],
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentSettings: () => ({ data: { data: hoisted.settings }, isLoading: false }),
  useUpdateAgentSettings: () => ({
    mutateAsync: vi.fn(async (payload: Record<string, string>) => {
      hoisted.updates.push(payload);
      return { data: hoisted.settings };
    }),
    isPending: false,
  }),
}));

vi.mock("@/lib/queries/model-plaza", () => ({
  useModelPlaza: () => ({ data: { data: { models: hoisted.models } }, isLoading: false }),
}));

function plazaModel(name: string, capability: string): PlazaFixture {
  return {
    modelName: name,
    provider: "aipdd",
    channelName: "AIPDD",
    capabilities: [capability],
  };
}

beforeEach(() => {
  hoisted.settings = {};
  hoisted.models = [];
  hoisted.updates = [];
});

describe("ComposerModelPicker", () => {
  it("labels the follow-default row apart from the identically named candidate", async () => {
    // 现网实测形态：默认视频模型就是目录里那一条 ap-minimax-h3，用户没有单独绑定。
    hoisted.settings = { text_model: "gw-text-1", video_model: "", default_video_model: "ap-minimax-h3" };
    hoisted.models = [plazaModel("ap-minimax-h3", "video")];
    render(<ComposerModelPicker mode="video" onModeChange={vi.fn()} />);

    const trigger = screen.getByRole("combobox");
    // 关闭态也要能看出这是「跟随系统默认」，否则回显与显式绑定那条一模一样。
    expect(trigger.textContent).toContain("agent.chat.model.followDefault");

    const user = userEvent.setup();
    await user.click(trigger);
    const options = await screen.findAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0].textContent).toContain("ap-minimax-h3");
    expect(options[1].textContent).toContain("ap-minimax-h3");
    // 两行的差别只在语义标注：哨兵项带标注、候选项不带，字面不许相同。
    expect(options[0].textContent).toContain("agent.chat.model.followDefault");
    expect(options[1].textContent).not.toContain("agent.chat.model.followDefault");
    expect(options[0].textContent).not.toBe(options[1].textContent);
  });

  it("writes an empty value when the follow-default row is picked", async () => {
    hoisted.settings = { text_model: "gw-text-1", video_model: "other-video", default_video_model: "ap-minimax-h3" };
    hoisted.models = [plazaModel("ap-minimax-h3", "video")];
    render(<ComposerModelPicker mode="video" onModeChange={vi.fn()} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /agent\.chat\.model\.followDefault/ }));

    expect(hoisted.updates).toEqual([{ video_model: "" }]);
  });

  it("shows an explicit unset-default hint instead of a bare dash", async () => {
    hoisted.settings = { text_model: "gw-text-1", image_model: "", default_image_model: "" };
    hoisted.models = [plazaModel("ap-image-2.0", "image")];
    render(<ComposerModelPicker mode="image" onModeChange={vi.fn()} />);

    // 默认位空着时，下拉不能再回显一个「-」让用户猜是没绑还是没同步。
    expect(screen.getByRole("combobox").textContent).toContain("agent.chat.model.noDefault");
    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox"));
    expect(await screen.findByRole("option", { name: /agent\.chat\.model\.noDefault/ })).toBeInTheDocument();
  });

  it("adds an in-place text model picker for image turns when no text model resolves", async () => {
    hoisted.settings = { image_model: "ap-image-2.0", default_image_model: "ap-image-2.0" };
    hoisted.models = [plazaModel("ap-image-2.0", "image"), plazaModel("gw-text-1", "text")];
    render(<ComposerModelPicker mode="image" onModeChange={vi.fn()} />);

    expect(screen.getAllByRole("combobox")).toHaveLength(2);
    expect(screen.getByText("agent.chat.model.textModelLabel")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: /gw-text-1/ }));

    expect(hoisted.updates).toEqual([{ text_model: "gw-text-1" }]);
  });

  it("adds the same text model picker for video turns", async () => {
    hoisted.settings = { video_model: "ap-minimax-h3", default_video_model: "ap-minimax-h3" };
    hoisted.models = [plazaModel("ap-minimax-h3", "video"), plazaModel("gw-text-1", "text")];
    render(<ComposerModelPicker mode="video" onModeChange={vi.fn()} />);

    expect(screen.getAllByRole("combobox")).toHaveLength(2);
  });

  it("keeps a single picker for text turns", () => {
    hoisted.settings = { text_model: "", default_text_model: "" };
    hoisted.models = [plazaModel("gw-text-1", "text")];
    render(<ComposerModelPicker mode="text" onModeChange={vi.fn()} />);

    // 文字模式本身就在选文本模型，不需要再补一个。
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("does not add the text picker once a text model resolves", () => {
    hoisted.settings = { default_text_model: "gw-text-1", video_model: "", default_video_model: "ap-minimax-h3" };
    hoisted.models = [plazaModel("ap-minimax-h3", "video")];
    render(<ComposerModelPicker mode="video" onModeChange={vi.fn()} />);

    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("switches the dialog type through the mode buttons", async () => {
    hoisted.settings = { text_model: "gw-text-1" };
    const onModeChange = vi.fn();
    render(<ComposerModelPicker mode="text" onModeChange={onModeChange} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "agent.chat.mode.video" }));

    expect(onModeChange).toHaveBeenCalledWith("video");
  });
});
