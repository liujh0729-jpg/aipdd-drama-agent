// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const PROJECT_SECTION_ROUTES = {
  freezone: "/projects/$project/freezone",
  // [aipdd fork delta] 「故事」栏目：L1 剧集菜单第一项，汇总各集故事底稿（故事 → 剧本 → 分镜脚本）。
  story: "/projects/$project/story",
  ingest: "/projects/$project/ingest",
  characters: "/projects/$project/characters",
  episodes: "/projects/$project/episodes",
  tools: "/projects/$project/tools",
  // [aipdd fork delta] 「Agent」栏目：位于「工具」与「MCP」之间，独立于主线的创作代理。
  agent: "/projects/$project/agent",
  // [aipdd fork delta] 「MCP」栏目：展示当前项目作为 MCP server 的 AI 接入方式。
  mcp: "/projects/$project/mcp",
  // [aipdd fork delta] WP-E:「模型广场」——只读目录 + 探活 + 调用示例，无计费。
  models: "/projects/$project/models",
  styles: "/projects/$project/styles",
  tasks: "/projects/$project/tasks",
  assistant: "/projects/$project/assistant",
} as const;

export type ProjectSection = keyof typeof PROJECT_SECTION_ROUTES;

export function projectSectionFromPath(pathname: string): ProjectSection | null {
  const segment = pathname.match(/^\/projects\/[^/]+\/([^/]+)/)?.[1];
  return segment && segment in PROJECT_SECTION_ROUTES
    ? (segment as ProjectSection)
    : null;
}

export function projectModeFromPath(
  pathname: string,
): "xiahua" | "xiaji" | "tools" | "agent" | "mcp" | "models" {
  const section = projectSectionFromPath(pathname);
  if (section === "freezone") return "xiahua";
  // [aipdd fork delta] 「工具」/「Agent」/「MCP」/「模型广场」是与「画布 / 剧集」并排的顶部模式标签。
  if (section === "tools") return "tools";
  if (section === "agent") return "agent";
  if (section === "mcp") return "mcp";
  if (section === "models") return "models";
  return "xiaji";
}
