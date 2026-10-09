// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ViewToggles } from "@/components/episode/beat-workbench/view-toggles";

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
              sectionSketch: "草图",
              sectionRender: "渲染",
            },
            workbench: {
              view: {
                selectAll: "全选",
                deselectAll: "取消全选",
                selectedCount: "已选 {{count}}",
                clear: "清除",
                totalShots: "共{{count}}个镜头",
                activeBeat: "Beat {{n}}",
                selectionHint: "选择beat可进行草图、渲染图重抽",
                batchRegenSketch: "重抽草图",
                batchRegenRender: "重抽渲染图",
                batchGenVideoPrompt: "生成视频提示词",
              },
            },
          },
        },
      },
    },
  });
});

function renderToggles(
  selection: Parameters<typeof ViewToggles>[0]["selection"],
  totalBeats: number,
  handlers: Partial<Record<string, () => void>> = {},
) {
  const props = {
    toggles: new Set(["sketch", "render"] as const),
    onToggle: vi.fn(),
    onClearSelection: vi.fn(),
    onSelectAll: vi.fn(),
    onBatchRegenSketch: vi.fn(),
    onBatchRegenRender: vi.fn(),
    onBatchGenVideoPrompt: vi.fn(),
    ...handlers,
  };
  render(
    <I18nextProvider i18n={i18n}>
      <ViewToggles selection={selection} totalBeats={totalBeats} {...props} />
    </I18nextProvider>,
  );
  return props;
}

describe("ViewToggles", () => {
  it("shows select-all plus sketch/render/video-prompt batch actions when a subset is checked", async () => {
    const user = userEvent.setup();
    const props = renderToggles({ mode: "multi", checked: new Set([1]) }, 3);

    // [aipdd fork delta] 全选按钮常驻（部分选中→显示「全选」）。
    expect(screen.getByRole("button", { name: "全选" })).toBeInTheDocument();
    expect(screen.getByText("已选 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重抽草图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重抽渲染图" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "生成视频提示词" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全选" }));
    await user.click(screen.getByRole("button", { name: "重抽草图" }));
    await user.click(screen.getByRole("button", { name: "重抽渲染图" }));
    await user.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await user.click(screen.getByRole("button", { name: "清除" }));

    expect(props.onSelectAll).toHaveBeenCalledTimes(1);
    expect(props.onBatchRegenSketch).toHaveBeenCalledTimes(1);
    expect(props.onBatchRegenRender).toHaveBeenCalledTimes(1);
    expect(props.onBatchGenVideoPrompt).toHaveBeenCalledTimes(1);
    expect(props.onClearSelection).toHaveBeenCalledTimes(1);
  });

  it("switches the select-all button to deselect when every beat is checked", async () => {
    const user = userEvent.setup();
    const props = renderToggles(
      { mode: "multi", checked: new Set([1, 2, 3]) },
      3,
    );
    expect(
      screen.getByRole("button", { name: "取消全选" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全选" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消全选" }));
    expect(props.onSelectAll).toHaveBeenCalledTimes(1);
  });

  it("keeps select-all available but hides batch actions when nothing is checked", () => {
    renderToggles({ mode: "none" }, 3);
    expect(screen.getByRole("button", { name: "全选" })).toBeInTheDocument();
    expect(
      screen.getByText("选择beat可进行草图、渲染图重抽"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重抽草图" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "生成视频提示词" }),
    ).not.toBeInTheDocument();
  });

  it("keeps redraw actions hidden for single-beat selection", () => {
    renderToggles({ mode: "single", beatNum: 1 }, 3);
    expect(
      screen.getByText("选择beat可进行草图、渲染图重抽"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重抽草图" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重抽渲染图" }),
    ).not.toBeInTheDocument();
  });
});
