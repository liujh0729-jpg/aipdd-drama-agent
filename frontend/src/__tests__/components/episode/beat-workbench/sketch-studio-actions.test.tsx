// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { SketchStudioActions } from "@/components/episode/beat-workbench/sketch-studio-actions";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          common: {
            cancel: "取消",
            confirmExecute: "确认执行",
            error: "错误",
          },
          episode: {
            workbench: {
              batch: {
                aiDetect: "AI 检测",
                aiDetectTooltip: "AI 检测",
                aiDetectRunning: "AI 检测中",
                aiDetectEmpty: "无检测结果",
                aiDetectSuccess:
                  "AI 检测完成：{{beats}} 个 beat，共 {{ids}} 个身份、{{props}} 个道具",
                reassignColors: "重新配色",
                reassignColorsTooltip: "重新配色",
                reassignColorsTitle: "重新配色？",
                reassignColorsDesc: "确认重新配色？",
                reassignColorsSuccess:
                  "已分配 {{count}} 个身份、{{propCount}} 个道具",
              },
              sketch: {
                identityColors: "身份",
                propColors: "道具",
                aiDetectResults: "AI检测结果",
                aiDetectResultCounts:
                  "{{beats}} beat / {{identities}} 身份 / {{props}} 道具",
                openGridGallery: "草图网格",
              },
              renderGrid: {
                title: "渲染网格",
              },
            },
          },
        },
      },
    },
  });
});

vi.mock("@/lib/queries/scripts", () => ({
  useScript: () => ({
    data: {
      ok: true,
      data: {
        sketch_colors: {
          "Hero_Main": "#ff0000 RED",
        },
      },
    },
  }),
}));

vi.mock("@/lib/queries/characters", () => ({
  useCharacters: () => ({
    data: [{ name: "Hero" }],
  }),
}));

vi.mock("@/lib/queries/episodes", () => ({
  useEpisodeBeats: () => ({
    data: {
      ok: true,
      data: [
        {
          beat_number: 1,
          detected_identities: ["Hero_Main"],
          detected_props: ["jade_sword"],
        },
        {
          beat_number: 2,
          detected_identities: ["Hero_Main", "Villain_Main"],
          detected_props: [],
        },
      ],
    },
  }),
  useEpisodeDetail: () => ({
    data: {
      ok: true,
      data: {
        number: 1,
        title: "ep1",
        prop_menu: [
          {
            prop_id: "jade_sword",
            marker_color: "#00ff00 GREEN",
            description: "Jade sword",
          },
        ],
      },
    },
  }),
}));

describe("SketchStudioActions", () => {
  it("does not expose the removed scene sketch gallery command", async () => {
    const user = userEvent.setup();
    const openSceneGallery = vi.fn();
    const openGridGallery = vi.fn();

    render(
      <I18nextProvider i18n={i18n}>
        <SketchStudioActions
          project="demo"
          episode={1}
          onOpenGridGallery={openGridGallery}
        />
      </I18nextProvider>,
    );

    expect(screen.queryByRole("button", { name: /场景草图画廊|openSceneGallery/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "草图网格" }));

    expect(openSceneGallery).not.toHaveBeenCalled();
    expect(openGridGallery).toHaveBeenCalledTimes(1);
  });


  it("hides sketch and render grid gallery actions when grid galleries are disabled", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <SketchStudioActions
          project="demo"
          episode={1}
          onOpenGridGallery={vi.fn()}
          onOpenRenderGridGallery={vi.fn()}
          showGridGalleryActions={false}
        />
      </I18nextProvider>,
    );

    expect(screen.queryByRole("button", { name: "草图网格" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "渲染网格" })).not.toBeInTheDocument();
  });

  it("shows identity and prop color legend entries", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <SketchStudioActions project="demo" episode={1} />
      </I18nextProvider>,
    );

    expect(screen.getByText("身份")).toBeInTheDocument();
    expect(screen.getByText(/Hero/)).toBeInTheDocument();
    expect(screen.getByText("Main")).toBeInTheDocument();
    expect(screen.getByText("道具")).toBeInTheDocument();
    expect(screen.getByText("jade_sword")).toBeInTheDocument();
  });

  it("shows a visible AI detection result summary from beat detections", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <SketchStudioActions project="demo" episode={1} />
      </I18nextProvider>,
    );

    expect(screen.getByText("AI检测结果")).toBeInTheDocument();
    expect(screen.getByText("2 beat / 2 身份 / 1 道具")).toBeInTheDocument();
  });

  it("does not render AI tool actions after they move to the top batch toolbar", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <SketchStudioActions project="demo" episode={1} />
      </I18nextProvider>,
    );

    expect(screen.queryByRole("button", { name: "AI 检测" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新配色" })).not.toBeInTheDocument();
  });
});
