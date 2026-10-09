// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  "src/components/layout/project-header-navigation.tsx",
  "utf-8",
);

describe("xiaji menu ordering", () => {
  // 用户口径（多次强调）：L1 剧集菜单第一项是「故事」，第二项才是「剧本」，
  // 故事永远在剧本前面。这里锚定源码顺序与默认落点，防止再次被改乱。
  it("keeps the story entry before the script entry in the L1 series menu", () => {
    const storyIndex = source.indexOf('labelKey: "nav.story"');
    const ingestIndex = source.indexOf('labelKey: "nav.ingest"');
    expect(storyIndex).toBeGreaterThanOrEqual(0);
    expect(ingestIndex).toBeGreaterThanOrEqual(0);
    expect(storyIndex).toBeLessThan(ingestIndex);
  });

  it("defaults the series mode to the story section", () => {
    expect(source).toContain(
      "const XIAJI_DEFAULT_ROUTE = PROJECT_SECTION_ROUTES.story;",
    );
  });

  // [aipdd fork delta] 顶部 L0 模式标签「工具」与「Agent」位置交换（Agent 在前）：
  // creationModes 数组里 agent 的 spread 必须排在 tools 之前，锚定源码顺序防回归。
  it("places the agent mode tab before the tools mode tab in the top nav", () => {
    const agentIndex = source.indexOf('value: "agent" as const');
    const toolsIndex = source.indexOf('value: "tools" as const');
    expect(agentIndex).toBeGreaterThanOrEqual(0);
    expect(toolsIndex).toBeGreaterThanOrEqual(0);
    expect(agentIndex).toBeLessThan(toolsIndex);
  });
});