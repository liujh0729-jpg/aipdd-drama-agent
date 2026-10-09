// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] MCP「本地 Agent 工具」区块：检测本机安装（宿主 home 经 compose 挂载
// 探测）→ 已安装的可多选 →「连接选中」写入对应客户端配置 /「断开选中」移除并吊销密钥；
// 未安装不可选、宿主 home 不可用时整体禁用。数据层与 MCP 页解耦，这里全部桩掉。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LocalAgentTools } from "@/features/mcp/LocalAgentTools";

/** 与后端 local_tools.scan() 输出同形（四工具：三装一连）。 */
interface ToolStub {
  id: string;
  name: string;
  installed: boolean;
  evidence: "app" | "config" | "none";
  connected: boolean;
  configPath: string;
}

function buildTools(): ToolStub[] {
  return [
    {
      id: "workbuddy",
      name: "Workbuddy",
      installed: true,
      evidence: "app",
      connected: false,
      configPath: "~/.workbuddy/mcp.json",
    },
    {
      id: "claude",
      name: "Claude Code",
      installed: false,
      evidence: "none",
      connected: false,
      configPath: "~/.claude.json",
    },
    {
      id: "cursor",
      name: "Cursor",
      installed: true,
      evidence: "app",
      connected: true,
      configPath: "~/.cursor/mcp.json",
    },
    {
      id: "codex",
      name: "Codex (ChatGPT)",
      installed: true,
      evidence: "app",
      connected: false,
      configPath: "~/.codex/config.toml",
    },
  ];
}

const toolsState = vi.hoisted(() => ({ current: [] as unknown[] }));
const hostHomeState = vi.hoisted(() => ({ current: true }));
const connectMock = vi.hoisted(() => vi.fn());
const disconnectMock = vi.hoisted(() => vi.fn());
const refetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/queries/agent", () => ({
  useAgentLocalTools: () => ({
    data: {
      data: {
        tools: toolsState.current,
        hostHomeAvailable: hostHomeState.current,
        hostHomePath: "/host-home",
      },
    },
    isLoading: false,
    isFetching: false,
    refetch: refetchMock,
  }),
  useConnectAgentLocalTools: () => ({ mutateAsync: connectMock, isPending: false }),
  useDisconnectAgentLocalTools: () => ({ mutateAsync: disconnectMock, isPending: false }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/** 复选框 id 与组件约定一致（`local-tool-${id}`），label 按同一 id 关联。 */
function checkboxOf(id: string): HTMLButtonElement {
  const element = document.getElementById(`local-tool-${id}`);
  if (!element) throw new Error(`checkbox not found: local-tool-${id}`);
  return element as HTMLButtonElement;
}

describe("LocalAgentTools", () => {
  beforeEach(() => {
    toolsState.current = buildTools();
    hostHomeState.current = true;
    connectMock.mockReset();
    disconnectMock.mockReset();
    refetchMock.mockReset();
    connectMock.mockResolvedValue({ tools: [], results: [{ id: "workbuddy", ok: true }] });
    disconnectMock.mockResolvedValue({ tools: [], results: [{ id: "cursor", ok: true }] });
  });

  it("lists every tool with installed / connected badges", () => {
    render(<LocalAgentTools project="P1" />);

    expect(screen.getByText("Workbuddy")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("Cursor")).toBeInTheDocument();
    expect(screen.getByText("Codex (ChatGPT)")).toBeInTheDocument();
    // 三装一连：已安装 3 枚、未安装 1 枚、已连接 1 枚。
    expect(screen.getAllByText("mcp.localTools.installed")).toHaveLength(3);
    expect(screen.getAllByText("mcp.localTools.notInstalled")).toHaveLength(1);
    expect(screen.getAllByText("mcp.localTools.connected")).toHaveLength(1);
    // 全部 app 级：不展示证据分级（残留）说明。
    expect(screen.queryByText("mcp.localTools.configDetectedHint")).not.toBeInTheDocument();
    // 配置路径来自宿主 home 视图（容器内为挂载点下的相对位置）。
    expect(screen.getByText("~/.workbuddy/mcp.json")).toBeInTheDocument();
  });

  it("grades config-only traces as config detected instead of installed", () => {
    // req20：卸载残留的配置目录不再冒充「已安装」（installed 保持真但展示降级）。
    toolsState.current = buildTools().map((tool) =>
      tool.id === "cursor" ? { ...tool, evidence: "config" as const } : tool,
    );
    render(<LocalAgentTools project="P1" />);

    expect(screen.getAllByText("mcp.localTools.installed")).toHaveLength(2);
    expect(screen.getByText("mcp.localTools.configDetected")).toBeInTheDocument();
    // 证据分级说明（仅存在 config 级工具时展示）。
    expect(screen.getByText("mcp.localTools.configDetectedHint")).toBeInTheDocument();
    // 连接闸门不收紧：config 级仍可勾选连接。
    expect(checkboxOf("cursor")).toBeEnabled();
  });

  it("only lets installed tools be picked", () => {
    render(<LocalAgentTools project="P1" />);

    expect(checkboxOf("workbuddy")).toBeEnabled();
    expect(checkboxOf("cursor")).toBeEnabled();
    // Claude Code 未安装：不可选中。
    expect(checkboxOf("claude")).toBeDisabled();
  });

  it("connects picked installed tools through the connect endpoint", async () => {
    render(<LocalAgentTools project="P1" />);

    const connectButton = screen.getByRole("button", { name: "mcp.localTools.connect" });
    expect(connectButton).toBeDisabled();

    fireEvent.click(checkboxOf("workbuddy"));
    expect(connectButton).toBeEnabled();

    fireEvent.click(connectButton);
    await waitFor(() =>
      expect(connectMock).toHaveBeenCalledWith({ tools: ["workbuddy"], project: "P1" }),
    );
  });

  it("disconnects picked connected tools through the disconnect endpoint", async () => {
    render(<LocalAgentTools project="P1" />);

    const disconnectButton = screen.getByRole("button", { name: "mcp.localTools.disconnect" });
    expect(disconnectButton).toBeDisabled();

    fireEvent.click(checkboxOf("cursor"));
    expect(disconnectButton).toBeEnabled();

    fireEvent.click(disconnectButton);
    await waitFor(() =>
      expect(disconnectMock).toHaveBeenCalledWith({ tools: ["cursor"], project: "P1" }),
    );
  });

  it("warns and disables actions when the host home is not mounted", () => {
    hostHomeState.current = false;
    render(<LocalAgentTools project="P1" />);

    expect(screen.getByText("mcp.localTools.hostHomeUnavailable")).toBeInTheDocument();
    expect(checkboxOf("workbuddy")).toBeDisabled();
    expect(screen.getByRole("button", { name: "mcp.localTools.connect" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "mcp.localTools.disconnect" })).toBeDisabled();
  });
});