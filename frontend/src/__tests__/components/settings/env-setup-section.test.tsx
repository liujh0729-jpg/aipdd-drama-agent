// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装」：部署环境真实依赖探测 + 融入 3D 世界 + 本地 Agent 工具。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { EnvSetupSection } from "@/components/settings/env-setup-section";

const { envState, agentState } = vi.hoisted(() => ({
  envState: {
    value: {
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      data: {
        data: {
          items: [
            { id: "ffmpeg", installed: true, version: "6.1.1", detail: "/usr/bin/ffmpeg" },
            { id: "node", installed: true, version: "20.11.0", detail: "/usr/bin/node" },
            { id: "npm", installed: false, version: null, detail: null },
            { id: "uv", installed: true, version: "0.4.8", detail: "/usr/bin/uv" },
            { id: "fonts", installed: false, version: null, detail: null },
            { id: "chrome", installed: true, version: null, detail: "http://127.0.0.1:9222" },
          ],
        },
      },
    } as Record<string, unknown>,
  },
  agentState: {
    value: {
      isLoading: false,
      data: {
        data: {
          tools: [
            { id: "workbuddy", name: "Workbuddy", installed: true, evidence: "config", connected: false },
            { id: "claude", name: "Claude Code", installed: false, evidence: "none", connected: false },
            { id: "cursor", name: "Cursor", installed: true, evidence: "app", connected: false },
            { id: "codex", name: "Codex (ChatGPT)", installed: false, evidence: "none", connected: false },
          ],
          hostHomeAvailable: true,
          hostHomePath: "/host-home",
        },
      },
    } as Record<string, unknown>,
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/queries/env", () => ({
  useEnvStatus: () => envState.value,
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentLocalTools: () => agentState.value,
}));

// 3D 世界区块在本用例里以存根呈现（其自身逻辑由 world-section 的测试覆盖）。
vi.mock("@/components/settings/world-section", () => ({
  WorldSection: () => <div>WORLD_SECTION_MARKER</div>,
}));

const DEFAULT_ITEMS = [
  { id: "ffmpeg", installed: true, version: "6.1.1", detail: "/usr/bin/ffmpeg" },
  { id: "node", installed: true, version: "20.11.0", detail: "/usr/bin/node" },
  { id: "npm", installed: false, version: null, detail: null },
  { id: "uv", installed: true, version: "0.4.8", detail: "/usr/bin/uv" },
  { id: "fonts", installed: false, version: null, detail: null },
  { id: "chrome", installed: true, version: null, detail: "http://127.0.0.1:9222" },
  { id: "opencv", installed: true, version: "4.11.0", detail: null },
];

beforeEach(() => {
  envState.value = {
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    data: { data: { items: DEFAULT_ITEMS } },
  };
  agentState.value = {
    isLoading: false,
    data: {
      data: {
        tools: [
          { id: "workbuddy", name: "Workbuddy", installed: true, evidence: "config", connected: false },
          { id: "claude", name: "Claude Code", installed: false, evidence: "none", connected: false },
          { id: "cursor", name: "Cursor", installed: true, evidence: "app", connected: false },
          { id: "codex", name: "Codex (ChatGPT)", installed: false, evidence: "none", connected: false },
        ],
        hostHomeAvailable: true,
        hostHomePath: "/host-home",
      },
    },
  };
});

describe("EnvSetupSection", () => {
  it("renders every detected dependency with its name and usage", async () => {
    render(<EnvSetupSection />);
    for (const id of ["ffmpeg", "node", "npm", "uv", "fonts", "chrome", "opencv"]) {
      expect(screen.getByText(`settings.envSetup.dependencies.${id}.name`)).toBeInTheDocument();
      expect(screen.getByText(`settings.envSetup.dependencies.${id}.usage`)).toBeInTheDocument();
    }
  });

  it("shows version chips and ready badges for installed items", () => {
    render(<EnvSetupSection />);
    expect(screen.getByText("v6.1.1")).toBeInTheDocument();
    // ffmpeg / node / uv / chrome / opencv 就绪 → 5 个 ready 徽标。
    expect(screen.getAllByText("settings.envSetup.status.ready")).toHaveLength(5);
  });

  it("shows a missing badge and fix guidance only for absent items", () => {
    render(<EnvSetupSection />);
    expect(screen.getAllByText("settings.envSetup.status.missing")).toHaveLength(2);
    expect(screen.getByText("settings.envSetup.dependencies.npm.fix")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.dependencies.fonts.fix")).toBeInTheDocument();
    // 已就绪项不给修复指引。
    expect(
      screen.queryByText("settings.envSetup.dependencies.ffmpeg.fix"),
    ).not.toBeInTheDocument();
  });

  it("renders no desktop-client download entry (removed from this fork)", () => {
    render(<EnvSetupSection />);
    expect(screen.queryByText("settings.envSetup.components.desktop.name")).not.toBeInTheDocument();
    expect(screen.queryByText("settings.envSetup.systemLabel")).not.toBeInTheDocument();
  });

  it("embeds the 3D world block within the page", () => {
    render(<EnvSetupSection />);
    expect(screen.getByText("WORLD_SECTION_MARKER")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.groups.features")).toBeInTheDocument();
  });

  // [aipdd fork delta] Chrome（CDP 调试端口）是宿主机按需项：不再占据「运行依赖」，
  // 连不上也不是故障，不得给黄色「缺失」告警。
  it("moves Chrome into optional features and reports it as not started", () => {
    envState.value = {
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      data: {
        data: {
          items: DEFAULT_ITEMS.map((item) =>
            item.id === "chrome" ? { ...item, installed: false } : item,
          ),
        },
      },
    } as Record<string, unknown>;
    render(<EnvSetupSection />);

    const chromeName = screen.getByText("settings.envSetup.dependencies.chrome.name");
    const featuresHeading = screen.getByText("settings.envSetup.groups.features");
    const worldMarker = screen.getByText("WORLD_SECTION_MARKER");
    // 行落在「可选特性」标题与 3D 世界区块之间（运行依赖表格在标题之前）。
    expect(
      featuresHeading.compareDocumentPosition(chromeName) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      chromeName.compareDocumentPosition(worldMarker) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // 按需项未就绪 = 中性「未启动」；黄色「缺失」只剩真的运行依赖 npm / fonts。
    expect(
      screen.getByText("settings.envSetup.status.notStarted"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("settings.envSetup.status.missing")).toHaveLength(
      2,
    );
    expect(screen.getAllByText("settings.envSetup.status.ready")).toHaveLength(4);
  });

  it("shows a detecting state while the environment probe is in flight", () => {
    envState.value = { isLoading: true, error: null, refetch: vi.fn(), data: undefined };
    render(<EnvSetupSection />);
    expect(screen.getByText("settings.envSetup.loading")).toBeInTheDocument();
  });

  it("shows a retry control when the probe fails", () => {
    envState.value = { isLoading: false, error: new Error("boom"), refetch: vi.fn(), data: undefined };
    render(<EnvSetupSection />);
    expect(screen.getByText("settings.envSetup.loadFailed")).toBeInTheDocument();
    expect(screen.getByText("common.retry")).toBeInTheDocument();
  });

  it("renders local agent tools with evidence-graded states and official entries", () => {
    render(<EnvSetupSection />);
    expect(screen.getByText("mcp.localTools.installed")).toBeInTheDocument();
    expect(screen.getByText("mcp.localTools.configDetected")).toBeInTheDocument();
    expect(screen.getAllByText("mcp.localTools.notInstalled")).toHaveLength(2);

    const entries = screen.getAllByRole("link", {
      name: "settings.envSetup.localTools.openSite",
    });
    const hrefs = entries.map((entry) => entry.getAttribute("href"));
    expect(hrefs).toEqual(
      expect.arrayContaining(["https://claude.ai/code", "https://github.com/openai/codex"]),
    );
    // app 级（真装）不给入口；workbuddy config 级：无公开下载 → 渠道提示。
    expect(hrefs).not.toContain("https://www.cursor.com/downloads");
    expect(screen.getByText("settings.envSetup.localTools.workbuddyHint")).toBeInTheDocument();
  });

  it("shows the detection-unavailable state when host home is not mounted", () => {
    agentState.value = {
      isLoading: false,
      data: {
        data: {
          tools: [
            { id: "workbuddy", name: "Workbuddy", installed: true, evidence: "config", connected: false },
          ],
          hostHomeAvailable: false,
          hostHomePath: "/host-home",
        },
      },
    };
    render(<EnvSetupSection />);
    expect(screen.getByText("mcp.localTools.hostHomeUnavailable")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.notDetectable")).toBeInTheDocument();
  });

  it("shows a scanning row while the local tool scan is in flight", () => {
    agentState.value = { isLoading: true, data: undefined };
    render(<EnvSetupSection />);
    expect(screen.getByText("mcp.localTools.loading")).toBeInTheDocument();
  });
});
