// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装」：部署环境真实依赖探测 + 融入 3D 世界 + 本地 Agent 工具。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { EnvSetupSection } from "@/components/settings/env-setup-section";

const { envState, agentState, planState, installState, emptyPlan } = vi.hoisted(() => ({
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
  // 待办计划（环境自检 → 逐项安装）：默认给一份「全部就绪、零待办」的计划，
  // 免得计划行与下面的依赖行重名，让 getByText 的断言产生歧义。具体用例自行改写。
  planState: {
    value: {
      isLoading: false,
      refetch: vi.fn(),
      data: undefined,
    } as Record<string, unknown>,
  },
  installState: {
    value: { isPending: false, mutate: vi.fn() } as Record<string, unknown>,
  },
  emptyPlan: () => ({
    items: [],
    summary: { total: 0, ready: 0, installable: 0, missing: 0, all_ready: true },
    world: {},
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/queries/env", () => ({
  useEnvStatus: () => envState.value,
  useEnvPlan: () => planState.value,
  useEnvInstall: () => installState.value,
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

/** 待办计划单项的骨架：只让用例覆盖自己要断言的字段，其余跟后端默认一致。 */
function planItem(
  id: string,
  status: "ready" | "installable" | "missing",
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    status,
    installed: status === "ready",
    auto_installable: false,
    action: null,
    version: null,
    detail: null,
    guide: null,
    optional: false,
    ...extra,
  };
}

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
  installState.value = { isPending: false, mutate: vi.fn() };
  planState.value = {
    isLoading: false,
    refetch: vi.fn(),
    data: { data: emptyPlan() },
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

  // [aipdd fork delta 需求第 4 条] 环境自检 → 待办清单 → 逐项安装：探测结果得变成
  // 能动作的条目，不然用户看完一堆「缺失」还是不知道该点哪里。
  it("turns the environment plan into a to-do list with a per-item install action", () => {
    planState.value = {
      isLoading: false,
      refetch: vi.fn(),
      data: {
        data: {
          items: [
            planItem("docker", "ready", { version: "27.1.1" }),
            planItem("world", "installable", { auto_installable: true }),
            planItem("npm", "missing"),
            planItem("model_channel", "missing"),
            planItem("chrome", "missing", { optional: true }),
          ],
          summary: { total: 5, ready: 1, installable: 1, missing: 2, all_ready: false },
          world: {},
        },
      },
    } as Record<string, unknown>;
    render(<EnvSetupSection />);

    expect(screen.getByText("settings.envSetup.plan.title")).toBeInTheDocument();
    // 可装项与必需缺失项进待办；就绪项不占待办，按需项（chrome）也不算「待补」。
    expect(screen.getByText("settings.envSetup.plan.items.world.name")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.plan.items.modelChannel.name")).toBeInTheDocument();
    // 就绪项不进待办：docker 不在依赖表里，它出现就等于计划行漏了过滤。
    expect(
      screen.queryByText("settings.envSetup.dependencies.docker.name"),
    ).not.toBeInTheDocument();
    // npm 同时出现在待办行与依赖表（两处都在说同一件事，不互相顶替）。
    expect(screen.getAllByText("settings.envSetup.dependencies.npm.name")).toHaveLength(2);
    // 只有可自动装的条目给安装按钮，点击就报回对应 id。
    const installButtons = screen.getAllByText("settings.envSetup.plan.install");
    expect(installButtons).toHaveLength(1);
    fireEvent.click(installButtons[0]);
    expect(installState.value.mutate).toHaveBeenCalledWith("world");
    // 模型渠道没密钥不能自动装，只能给可执行的指路（导航名必须是真实栏目）。
    expect(
      screen.getByText("settings.envSetup.plan.items.modelChannel.fix"),
    ).toBeInTheDocument();
    expect(screen.queryByText("settings.envSetup.plan.allReady")).not.toBeInTheDocument();
  });

  it("says everything is ready when the plan has nothing left to do", () => {
    planState.value = {
      isLoading: false,
      refetch: vi.fn(),
      data: { data: emptyPlan() },
    } as Record<string, unknown>;
    render(<EnvSetupSection />);
    expect(screen.getByText("settings.envSetup.plan.allReady")).toBeInTheDocument();
    expect(screen.queryByText("settings.envSetup.plan.install")).not.toBeInTheDocument();
  });

  it("locks the install action while the 3D world runtime is installing", () => {
    planState.value = {
      isLoading: false,
      refetch: vi.fn(),
      data: {
        data: {
          items: [planItem("world", "installable", { auto_installable: true })],
          summary: { total: 1, ready: 0, installable: 1, missing: 0, all_ready: false },
          world: { installing: true, message: "PULLING" },
        },
      },
    } as Record<string, unknown>;
    render(<EnvSetupSection />);
    expect(screen.getByText("PULLING")).toBeInTheDocument();
    // 安装中：按钮文案换成「安装中…」并置 disabled，误点不会叠出第二次安装。
    const button = screen.getByText("settings.envSetup.plan.installing").closest("button");
    expect(button).toBeDisabled();
    fireEvent.click(button as HTMLButtonElement);
    expect(installState.value.mutate).not.toHaveBeenCalled();
  });

  it("shows a detecting state for the plan before the first probe lands", () => {
    planState.value = { isLoading: true, refetch: vi.fn(), data: undefined };
    render(<EnvSetupSection />);
    expect(screen.getByText("settings.envSetup.plan.loading")).toBeInTheDocument();
  });

  // [aipdd fork delta 需求·环境安装补齐] 每条运行依赖旁给一个安装 / 重装按钮（Docker 源码部署下
  // 不谎称一键装好，而是复制一条含 Docker 前提的终端命令）；缺失=安装、已装=重装；chrome 按需项无命令不给按钮。
  it("gives each runtime dependency an install / reinstall copy-command action", () => {
    render(<EnvSetupSection />);
    // 已装的 ffmpeg / node / uv / opencv → 重装。
    expect(screen.getAllByText("settings.envSetup.action.reinstall")).toHaveLength(4);
    // 缺失的 npm / fonts → 安装。
    expect(screen.getAllByText("settings.envSetup.action.install")).toHaveLength(2);
  });

  it("shows a re-check control, a Docker prerequisite and an operation guide on the runtime panel", () => {
    render(<EnvSetupSection />);
    expect(screen.getAllByText("settings.envSetup.plan.refresh").length).toBeGreaterThan(0);
    expect(screen.getByText("settings.envSetup.dockerRequire.title")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.dockerRequire.body")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.howto.title")).toBeInTheDocument();
    expect(screen.getByText("settings.envSetup.howto.body")).toBeInTheDocument();
  });

  // 点「安装」复制的不是裸 apt 命令，而是包好的 `docker compose exec -u root api bash -lc "…"` 终端命令（带 Docker 前提注释）。
  it("copies a runnable docker compose exec terminal command when an install action is clicked", () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => new Promise<void>(() => {}));
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<EnvSetupSection />);
    fireEvent.click(screen.getAllByText("settings.envSetup.action.install")[0]);
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = String(writeText.mock.calls[0][0]);
    expect(copied).toContain("docker compose exec -u root api bash -lc");
    expect(copied).toContain("apt-get install -y --no-install-recommends nodejs npm");
  });
});
