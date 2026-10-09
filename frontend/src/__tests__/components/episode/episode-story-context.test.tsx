// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EpisodeStoryContext } from "@/components/episode/episode-story-context";

const labels = {
  summary: "剧情摘要",
  noSummary: "暂无摘要",
  keyEvents: "关键事件",
  noKeyEvents: "暂无关键事件",
  cliffhanger: "悬念钩子",
  noCliffhanger: "暂无悬念",
};

describe("EpisodeStoryContext", () => {
  it("renders episode summary, key events, and cliffhanger as read-only context", () => {
    render(
      <EpisodeStoryContext
        contentSummary="秦在雨夜进入王城。"
        keyEvents={["入宫", "遇见旧友"]}
        cliffhanger="城门后传来熟悉的声音。"
        labels={labels}
      />,
    );

    expect(screen.getByText("剧情摘要")).toBeInTheDocument();
    expect(screen.getByText("秦在雨夜进入王城。")).toBeInTheDocument();
    expect(screen.getByText("关键事件")).toBeInTheDocument();
    expect(screen.getByText("入宫")).toBeInTheDocument();
    expect(screen.getByText("遇见旧友")).toBeInTheDocument();
    expect(screen.getByText("悬念钩子")).toBeInTheDocument();
    expect(screen.getByText("城门后传来熟悉的声音。")).toBeInTheDocument();
  });

  it("shows explicit empty states when episode planning fields are absent", () => {
    render(
      <EpisodeStoryContext
        contentSummary=""
        keyEvents={[]}
        cliffhanger=""
        labels={labels}
      />,
    );

    expect(screen.getByText("暂无摘要")).toBeInTheDocument();
    expect(screen.getByText("暂无关键事件")).toBeInTheDocument();
    expect(screen.getByText("暂无悬念")).toBeInTheDocument();
  });
});
