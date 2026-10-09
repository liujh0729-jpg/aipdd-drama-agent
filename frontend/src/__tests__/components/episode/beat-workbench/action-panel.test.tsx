// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionPanel } from "@/components/episode/beat-workbench/action-panel";
import { useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import type { BeatStates } from "@/types/beat-state";
import type { Beat } from "@/types/episode";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          episode: {
            beat: {
              sectionText: "文案",
              sectionSketch: "草图",
              sectionRender: "渲染图",
              sectionAudio: "音频",
              sectionVideo: "视频",
              edited: "已编辑",
              notEdited: "未编辑",
              selected: "已选择",
              notSelected: "未选择",
              rendered: "已渲染",
              notRendered: "未渲染",
              generated: "已生成",
              notGenerated: "未生成",
              clickToView: "点击镜头查看详情",
            },
          },
        },
      },
    },
  });
});

beforeEach(() => {
  localStorage.clear();
  useEpisodeWorkbenchStore.getState().reset();
});

vi.mock("@/lib/queries/sketches", () => ({
  useGridsByBeat: () => ({ byBeat: new Map(), assignments: {} }),
}));

vi.mock("@/lib/queries/episodes", () => ({
  useDeleteManualShot: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/queries/video", () => ({
  useVideoBackends: () => ({
    data: {
      ok: true,
      data: [
        {
          value: "huimeng_seedance-2.0-fast",
          label: "Seedance 2.0 Fast",
          is_seedance2: true,
        },
      ],
    },
  }),
}));

vi.mock("@/stores/save-status-store", () => ({
  saveScopes: {
    beatText: () => "beat-text",
  },
  useSaveState: () => ({ status: "idle" }),
}));

vi.mock("@/hooks/use-escape-to-close", () => ({
  useEscapeToClose: vi.fn(),
}));

vi.mock("@/components/save-status", () => ({
  SaveStatus: () => null,
}));

vi.mock("@/components/episode/beat-workbench/text-pane", () => ({
  TextPane: () => <div>TextPane</div>,
}));

vi.mock("@/components/episode/beat-workbench/sketch-section", () => ({
  SketchSection: () => <div>SketchSection</div>,
}));

vi.mock("@/components/episode/beat-workbench/render-section", () => ({
  RenderSection: () => <div>RenderSection</div>,
}));

vi.mock("@/components/episode/beat-workbench/audio-pane", () => ({
  AudioPane: () => <div>AudioPane</div>,
}));

vi.mock("@/components/episode/beat-workbench/video-pane", () => ({
  VideoPane: () => <div>VideoPane</div>,
}));

function makeBeat(beatNumber: number): Beat {
  return {
    beat_number: beatNumber,
    narration_segment: "旁白",
    visual_description: "画面",
    audio_type: "narration",
    video_mode: "first_frame",
    detected_identities: [],
    video_prompt: "",
    keyframe_prompt: "",
    audio_url: "",
    frame_url: "",
    video_url: "",
  };
}

describe("ActionPanel", () => {
  it("defaults to the 文案 section expanded with other sections collapsed (#21)", () => {
    const states: BeatStates = {
      1: {
        script: "missing",
        audio: "missing",
        video: "missing",
        sketch: "missing",
      },
    };

    render(
      <I18nextProvider i18n={i18n}>
        <ActionPanel
          selection={{ mode: "single", beatNum: 1 }}
          beats={[makeBeat(1)]}
          states={states}
          project="demo"
          episode={1}
          defaultBackend="huimeng_seedance-2.0-fast"
          onDefaultBackendChange={vi.fn()}
        />
      </I18nextProvider>,
    );

    // 文案默认展开，用户无需点击即可看到文案内容；其它区块仍折叠。
    expect(screen.getByText("TextPane")).toBeInTheDocument();
    expect(screen.queryByText("SketchSection")).not.toBeInTheDocument();
  });

  it("keeps section open state after the panel remounts for the same episode", async () => {
    const states: BeatStates = {
      1: {
        script: "missing",
        audio: "missing",
        video: "missing",
        sketch: "missing",
      },
    };
    const props = {
      selection: { mode: "single", beatNum: 1 } as const,
      beats: [makeBeat(1)],
      states,
      project: "demo",
      episode: 1,
      defaultBackend: "huimeng_seedance-2.0-fast",
      onDefaultBackendChange: vi.fn(),
    };

    const { unmount } = render(
      <I18nextProvider i18n={i18n}>
        <ActionPanel {...props} />
      </I18nextProvider>,
    );

    // 用一个非默认展开的区块（草图）验证展开状态跨 remount 持久化。
    fireEvent.click(screen.getByRole("button", { name: /草图/ }));

    await waitFor(() => {
      expect(screen.getByText("SketchSection")).toBeInTheDocument();
    });

    unmount();

    render(
      <I18nextProvider i18n={i18n}>
        <ActionPanel {...props} />
      </I18nextProvider>,
    );

    expect(screen.getByText("SketchSection")).toBeInTheDocument();
  });

  it("keeps section open state when switching between beats", () => {
    const states: BeatStates = {
      1: {
        script: "missing",
        audio: "missing",
        video: "missing",
        sketch: "missing",
      },
      2: {
        script: "missing",
        audio: "missing",
        video: "missing",
        sketch: "missing",
      },
    };

    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <ActionPanel
          selection={{ mode: "single", beatNum: 1 }}
          beats={[makeBeat(1), makeBeat(2)]}
          states={states}
          project="demo"
          episode={1}
          defaultBackend="huimeng_seedance-2.0-fast"
          onDefaultBackendChange={vi.fn()}
        />
      </I18nextProvider>,
    );

    // 用一个非默认展开的区块（草图）验证切换 beat 时展开状态保留。
    fireEvent.click(screen.getByRole("button", { name: /草图/ }));
    expect(screen.getByText("SketchSection")).toBeInTheDocument();

    rerender(
      <I18nextProvider i18n={i18n}>
        <ActionPanel
          selection={{ mode: "single", beatNum: 2 }}
          beats={[makeBeat(1), makeBeat(2)]}
          states={states}
          project="demo"
          episode={1}
          defaultBackend="huimeng_seedance-2.0-fast"
          onDefaultBackendChange={vi.fn()}
        />
      </I18nextProvider>,
    );

    expect(screen.getByText("SketchSection")).toBeInTheDocument();
  });

  it("opens the target section from a deep link", () => {
    const states: BeatStates = {
      1: {
        script: "missing",
        audio: "missing",
        video: "missing",
        sketch: "missing",
      },
    };

    render(
      <I18nextProvider i18n={i18n}>
        <ActionPanel
          selection={{ mode: "single", beatNum: 1 }}
          beats={[makeBeat(1)]}
          states={states}
          project="demo"
          episode={1}
          defaultBackend="huimeng_seedance-2.0-fast"
          onDefaultBackendChange={vi.fn()}
          targetSection="sketch"
        />
      </I18nextProvider>,
    );

    expect(screen.getByText("SketchSection")).toBeInTheDocument();
  });
});
