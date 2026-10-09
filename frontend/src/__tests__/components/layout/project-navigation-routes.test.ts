// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  PROJECT_SECTION_ROUTES,
  projectModeFromPath,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";

describe("project navigation routes", () => {
  it("uses freezone as the project dashboard entry", () => {
    expect(PROJECT_SECTION_ROUTES.freezone).toBe("/projects/$project/freezone");
  });

  it("classifies freezone as xiahua and every production section as xiaji", () => {
    expect(projectModeFromPath("/projects/demo/freezone")).toBe("xiahua");
    expect(projectModeFromPath("/projects/demo/ingest")).toBe("xiaji");
    expect(projectModeFromPath("/projects/demo/tasks")).toBe("xiaji");
  });

  // [aipdd fork delta] 「故事」是 L1 剧集菜单第一项（故事 → 剧本 → 资产 → 分镜 → 风格）。
  it("classifies the story section as xiaji with its own route", () => {
    expect(PROJECT_SECTION_ROUTES.story).toBe("/projects/$project/story");
    expect(projectSectionFromPath("/projects/demo/story")).toBe("story");
    expect(projectModeFromPath("/projects/demo/story")).toBe("xiaji");
  });

  // [aipdd fork delta] 「工具」与「MCP」是与画布/剧集并排的顶部模式标签。
  it("classifies tools and mcp as their own top-level modes", () => {
    expect(PROJECT_SECTION_ROUTES.mcp).toBe("/projects/$project/mcp");
    expect(projectModeFromPath("/projects/demo/tools")).toBe("tools");
    expect(projectModeFromPath("/projects/demo/mcp")).toBe("mcp");
  });

  it("preserves the tasks section when switching projects", () => {
    expect(projectSectionFromPath("/projects/demo/tasks")).toBe("tasks");
  });

  it("does not silently classify unknown project sections as freezone", () => {
    expect(projectSectionFromPath("/projects/demo/unknown")).toBeNull();
  });
});
