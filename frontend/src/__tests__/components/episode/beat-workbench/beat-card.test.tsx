// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BeatCard } from "@/components/episode/beat-workbench/beat-card";
import type { Beat } from "@/types/episode";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string | number>) => {
      if (key === "episode.beat.select") return "选择 Beat";
      if (key === "episode.beat.deselect") return "取消选择 Beat";
      if (key === "episode.beat.insertAfter") return `在第 ${vars?.n} 个 beat 后插入`;
      if (key === "episode.beat.openFreezone") return "进入画布 Beat 工作台";
      return key;
    },
  }),
}));

const beat = {
  beat_number: 4,
  narration_segment: "发现密室里的书",
  visual_description: "主角翻开旧书",
  sketch_url: "/static/sketches/beat_04.png",
  frame_url: "/static/renders/beat_04.png",
} as Beat;

function renderBeatCard(overrides: Partial<Parameters<typeof BeatCard>[0]> = {}) {
  const props = {
    beat,
    displayNumber: beat.beat_number,
    showSketch: true,
    showRender: true,
    images: [],
    assignments: {},
    aspectRatio: "portrait" as const,
    isSelected: false,
    isChecked: false,
    onCardClick: vi.fn(),
    onCheckboxClick: vi.fn(),
    ...overrides,
  };

  render(<BeatCard {...props} />);

  return props;
}

describe("BeatCard", () => {
  it("keeps the multi-select control visible without hover-only opacity over thumbnails", () => {
    renderBeatCard();

    const selectButton = screen.getByRole("button", { name: "选择 Beat" });
    expect(selectButton.className).toContain("size-5");
    expect(selectButton.className).not.toContain("opacity-40");
    expect(selectButton.className).not.toContain("group-hover:opacity-100");
  });

  it("does not reveal the check icon on hover before the card is selected", () => {
    renderBeatCard();

    const selectButton = screen.getByRole("button", { name: "选择 Beat" });
    expect(selectButton.className).toContain("text-transparent");
    expect(selectButton.className).toContain("hover:border-white/[0.30]");
    expect(selectButton.className).toContain("hover:bg-black/50");
    expect(selectButton.className).not.toContain("hover:text-primary");
  });

  it("shows the check icon only after the card is checked", () => {
    renderBeatCard({ isChecked: true });

    const selectButton = screen.getByRole("button", { name: "取消选择 Beat" });
    expect(selectButton.className).toContain("text-primary");
    expect(selectButton.className).not.toContain("text-transparent");
  });

  it("toggles multi-select from the card control without selecting the card body", async () => {
    const user = userEvent.setup();
    const { onCardClick, onCheckboxClick } = renderBeatCard();

    await user.click(screen.getByRole("button", { name: "选择 Beat" }));

    expect(onCheckboxClick).toHaveBeenCalledWith(4);
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it("uses render as the main image and overlays sketch in dual-image mode", () => {
    renderBeatCard();

    const renderImage = screen.getByAltText("render");
    const sketchImage = screen.getByAltText("sketch");

    expect(renderImage.parentElement?.className).toContain("aspect-[2/3]");
    expect(sketchImage.closest(".absolute")).toBeInTheDocument();
  });

  it("does not duplicate the sketch overlay when dual-image mode falls back to sketch", () => {
    renderBeatCard({
      beat: {
        ...beat,
        frame_url: null,
      } as Beat,
    });

    expect(screen.getByAltText("sketch")).toBeInTheDocument();
    expect(screen.queryByAltText("render")).not.toBeInTheDocument();
    expect(screen.getAllByAltText("sketch")).toHaveLength(1);
  });

  it("opens the beat freezone workbench from the bottom-right icon without selecting the card", async () => {
    const user = userEvent.setup();
    const onOpenFreezone = vi.fn();
    const { onCardClick } = renderBeatCard({ onOpenFreezone });

    await user.click(screen.getByRole("button", { name: "进入画布 Beat 工作台" }));

    expect(onOpenFreezone).toHaveBeenCalledWith(4, "frame");
    expect(onCardClick).not.toHaveBeenCalled();
    expect(screen.queryByText("进入画布 Beat 工作台")).not.toBeInTheDocument();
  });
});
