// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import type { ReactNode } from "react";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          common: { cancel: "取消", error: "错误" },
          episode: {
            workbench: {
              insertManual: {
                titleBeforeFirst: "在首个 Beat 前插入手工镜头",
                titleAfter: "在 Beat {{n}} 后插入手工镜头",
                audioType: "镜头类型",
                audioTypeSilence: "无声",
                audioTypeNarration: "旁白",
                audioTypeDialogue: "对白",
                narration: "台词/旁白",
                narrationPlaceholder: "填写需要朗读的对白或旁白",
                narrationRequired: "请填写台词或旁白",
                silentHint: "无声镜头不会生成音频",
                speaker: "说话人",
                speakerPlaceholder: "选择或输入说话人身份",
                speakerRequired: "请选择说话人",
                narrator: "解说人",
                projectNarrator: "项目解说人",
                visualDescription: "画面描述",
                visualPlaceholder: "用一句话描述这个镜头里看到的画面",
                visualRequired: "画面描述不能为空",
                type: "类型",
                typeManual: "手工",
                location: "场景",
                sceneVariant: "变体",
                noSceneVariant: "无变体",
                locationPlaceholder: "选择场景",
                locationNone: "（不指定）",
                timeOfDay: "时间",
                timeOfDayPlaceholder: "选择时间",
                timeOfDayNone: "（不指定）",
                duration: "视频时长（秒）",
                identities: "出场身份",
                identitiesPlaceholder:
                  "逗号分隔，如 {{example}}；留空自动从画面描述提取",
                identitiesPlaceholderEmpty:
                  "逗号分隔身份ID；留空自动从画面描述提取",
                props: "出场道具",
                propsPlaceholder:
                  "逗号分隔，如 {{example}}；留空自动从画面描述提取",
                propsPlaceholderEmpty:
                  "逗号分隔道具ID；留空自动从画面描述提取",
                submit: "插入",
                success: "已插入手工镜头",
              },
            },
          },
        },
      },
    },
  });
});

const insertMutateAsync: Mock = vi
  .fn()
  .mockResolvedValue({ ok: true, data: null });

interface TestSceneMenuItem {
  scene_id: string;
  base_scene_id?: string;
  variant_id?: string;
  time_of_day?: string;
}

const queryState: {
  beats: Array<{
    beat_number: number;
    narration_segment: string;
    visual_description: string;
    scene_ref: { scene_id: string; variant_id?: string };
    time_of_day: string;
    estimated_duration: number;
    audio_type: string;
    detected_identities: string[];
    detected_props: string[];
    set_description: string;
  }>;
  episode: {
    number: number;
    title: string;
    identity_ids: string[];
    scene_menu: TestSceneMenuItem[];
    prop_menu: { prop_id: string }[];
  };
} = {
  beats: [
    {
      beat_number: 1,
      narration_segment: "旁白",
      visual_description: "原镜头",
      scene_ref: { scene_id: "仓库_夜晚" },
      time_of_day: "夜晚",
      estimated_duration: 4,
      audio_type: "narration",
      detected_identities: [],
      detected_props: [],
      set_description:
        '{"scene_description":"旧置景场景","props_description":"旧置景道具"}',
    },
  ],
  episode: {
    number: 1,
    title: "ep1",
    identity_ids: ["陆辰"],
    scene_menu: [{ scene_id: "仓库_夜晚" }],
    prop_menu: [{ prop_id: "玉佩" }],
  },
};

vi.mock("@/lib/queries/episodes", () => ({
  useEpisodeBeats: () => ({
    data: { ok: true, data: queryState.beats },
  }),
  useEpisodeDetail: () => ({
    data: { ok: true, data: queryState.episode },
  }),
  useInsertManualShot: () => ({
    mutateAsync: insertMutateAsync,
    isPending: false,
  }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

import { InsertManualShotDialog } from "@/components/episode/beat-workbench/insert-manual-shot-dialog";

function Wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  insertMutateAsync.mockClear();
  insertMutateAsync.mockResolvedValue({ ok: true, data: null });
  queryState.beats = [
    {
      beat_number: 1,
      narration_segment: "旁白",
      visual_description: "原镜头",
      scene_ref: { scene_id: "仓库_夜晚" },
      time_of_day: "夜晚",
      estimated_duration: 4,
      audio_type: "narration",
      detected_identities: [],
      detected_props: [],
      set_description:
        '{"scene_description":"旧置景场景","props_description":"旧置景道具"}',
    },
  ];
  queryState.episode = {
    number: 1,
    title: "ep1",
    identity_ids: ["陆辰"],
    scene_menu: [{ scene_id: "仓库_夜晚" }],
    prop_menu: [{ prop_id: "玉佩" }],
  };
});

describe("InsertManualShotDialog", () => {
  it("uses v2 scene fields and hides legacy staging editors", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    expect(screen.getByText("画面描述")).toBeInTheDocument();
    expect(screen.getByText("无声")).toBeInTheDocument();
    expect(screen.getByText("旁白")).toBeInTheDocument();
    expect(screen.getByText("对白")).toBeInTheDocument();
    expect(screen.getByText("场景")).toBeInTheDocument();
    expect(screen.queryByText("视频提示词")).not.toBeInTheDocument();
    expect(screen.queryByText(/置景/)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("旧置景场景")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("旧置景道具")).not.toBeInTheDocument();
  });

  it("submits an empty manual beat container by default", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      after_beat_number: 1,
      audio_type: "silence",
      narration_segment: null,
      speaker: null,
      visual_description: "\u200B",
      duration_seconds: 3,
      scene_ref: null,
      time_of_day: null,
      detected_identities: null,
      detected_props: null,
    });
    expect(insertMutateAsync.mock.calls[0][0]).not.toHaveProperty(
      "set_description",
    );
  });

  it("submits narration metadata when narration type is selected", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          spineTemplate="narrated"
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.change(
      screen.getByPlaceholderText("用一句话描述这个镜头里看到的画面"),
      { target: { value: "仓库门口的空镜" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "旁白" }));
    fireEvent.change(screen.getByPlaceholderText("填写需要朗读的对白或旁白"), {
      target: { value: "门外的脚步声越来越近。" },
    });

    expect(screen.getByText("解说人")).toBeInTheDocument();
    expect(screen.getByText("项目解说人")).toBeInTheDocument();
    expect(screen.queryByText("说话人")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      audio_type: "narration",
      narration_segment: "门外的脚步声越来越近。",
      speaker: null,
      visual_description: "仓库门口的空镜",
    });
  });

  it("submits canonical base and variant from split scene controls", async () => {
    const user = userEvent.setup();
    queryState.episode.scene_menu = [
      { scene_id: "卫生间", base_scene_id: "", variant_id: "", time_of_day: "" },
      {
        scene_id: "卫生间_漏水",
        base_scene_id: "卫生间",
        variant_id: "漏水",
        time_of_day: "",
      },
      {
        scene_id: "卫生间_漏水_夜晚",
        base_scene_id: "卫生间",
        variant_id: "漏水",
        time_of_day: "夜晚",
      },
    ];
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    await user.click(screen.getByRole("combobox", { name: "场景" }));
    await user.click(await screen.findByRole("option", { name: "卫生间" }));
    await user.click(screen.getByRole("combobox", { name: "变体" }));
    await user.click(await screen.findByRole("option", { name: "漏水" }));
    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      scene_ref: { scene_id: "卫生间", variant_id: "漏水" },
    });
  });

  it("does not show narrator speaker semantics for drama narration", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByRole("button", { name: "旁白" }));

    expect(screen.queryByText("解说人")).not.toBeInTheDocument();
    expect(screen.queryByText("项目解说人")).not.toBeInTheDocument();
    expect(screen.queryByText("说话人")).not.toBeInTheDocument();
  });

  it("does not show or submit speaker for drama dialogue", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.change(
      screen.getByPlaceholderText("用一句话描述这个镜头里看到的画面"),
      { target: { value: "陆辰在仓库门口回头" } },
    );
    fireEvent.click(screen.getByText("对白"));
    fireEvent.change(screen.getByPlaceholderText("填写需要朗读的对白或旁白"), {
      target: { value: "别回头。" },
    });

    expect(screen.queryByText("说话人")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      audio_type: "dialogue",
      narration_segment: "别回头。",
      speaker: null,
      visual_description: "陆辰在仓库门口回头",
    });
  });

  it("submits dialogue metadata when dialogue type is selected", async () => {
    const user = userEvent.setup();
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          spineTemplate="narrated"
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.change(
      screen.getByPlaceholderText("用一句话描述这个镜头里看到的画面"),
      { target: { value: "陆辰在仓库门口回头" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "无声" }));
    fireEvent.click(screen.getByText("对白"));
    fireEvent.change(screen.getByPlaceholderText("填写需要朗读的对白或旁白"), {
      target: { value: "别回头。" },
    });
    await user.click(screen.getByRole("combobox", { name: "说话人" }));
    await user.click(await screen.findByRole("option", { name: "陆辰" }));
    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      audio_type: "dialogue",
      narration_segment: "别回头。",
      speaker: "陆辰",
      visual_description: "陆辰在仓库门口回头",
    });
  });

  it("converts @ mentions and submits detected identity and prop references", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.change(
      screen.getByPlaceholderText("用一句话描述这个镜头里看到的画面"),
      { target: { value: "@陆辰 拿起 @玉佩" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    expect(insertMutateAsync).toHaveBeenCalledTimes(1);
    expect(insertMutateAsync.mock.calls[0][0]).toMatchObject({
      visual_description: "{{陆辰}} 拿起 [[玉佩]]",
      detected_identities: ["陆辰"],
      detected_props: ["玉佩"],
    });
  });

  it("fills identity and prop fields from visual @ mentions", () => {
    render(
      <Wrapper>
        <InsertManualShotDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          afterBeatNumber={1}
        />
      </Wrapper>,
    );

    fireEvent.change(
      screen.getByPlaceholderText("用一句话描述这个镜头里看到的画面"),
      { target: { value: "@陆辰 拿起 @玉佩" } },
    );

    expect(screen.getByDisplayValue("陆辰")).toBeInTheDocument();
    expect(screen.getByDisplayValue("玉佩")).toBeInTheDocument();
  });
});
