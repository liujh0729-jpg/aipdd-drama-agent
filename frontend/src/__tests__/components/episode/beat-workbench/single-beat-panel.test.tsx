// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { SingleBeatPanel, type SectionId } from "@/components/episode/beat-workbench/single-beat-panel";
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
              deleteManualShotTitle: "删除手工镜头？",
              deleteManualShotDesc: "删除 Beat #{{n}}？",
            },
          },
        },
      },
    },
  });
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

function makeBeat(overrides: Partial<Beat> = {}): Beat {
  return {
    beat_number: 29,
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
    ...overrides,
  };
}

function renderPanel(
  options: { isSeedance2Backend?: boolean; spineTemplate?: "drama" | "narrated" } = {},
) {
  const openSections = new Set<SectionId>(["text", "sketch", "render", "audio", "video"]);
  return render(
    <I18nextProvider i18n={i18n}>
      <SingleBeatPanel
        beat={makeBeat()}
        project="demo"
        episode={1}
        stages={{ audio: "missing", video: "missing", sketch: "ready", render: "ready" }}
        defaultBackend="huimeng_seedance-2.0-fast"
        onDefaultBackendChange={vi.fn()}
        spineTemplate={options.spineTemplate}
        isSeedance2Backend={options.isSeedance2Backend}
        openSections={openSections}
        onToggleSection={vi.fn()}
      />
    </I18nextProvider>,
  );
}

describe("SingleBeatPanel", () => {
  it("shows the audio pane for 解说剧 (narrated) projects", () => {
    renderPanel({ isSeedance2Backend: true, spineTemplate: "narrated" });

    expect(screen.getByText("音频")).toBeInTheDocument();
    expect(screen.getByText("AudioPane")).toBeInTheDocument();
    expect(screen.getByText("VideoPane")).toBeInTheDocument();
  });

  it("hides the audio pane for 精品剧 (drama) projects", () => {
    renderPanel({ isSeedance2Backend: true, spineTemplate: "drama" });

    expect(screen.queryByText("音频")).not.toBeInTheDocument();
    expect(screen.queryByText("AudioPane")).not.toBeInTheDocument();
    expect(screen.getByText("VideoPane")).toBeInTheDocument();
  });
});
