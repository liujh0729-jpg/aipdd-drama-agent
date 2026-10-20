// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 页冒烟：三栏渲染（会话 / 聊天 / 待办计划与技能、记忆）与 i18n 键位接线。
// 数据层整体桩掉——断言聚焦页面结构与交互路径，不触网。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentPage } from "@/features/agent/AgentPage";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

// 设置弹窗（关闭态）也会调用 useNavigate；桩掉路由避免 useRouter 警告。
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));

interface ConversationFixture {
  id: string;
  title: string;
  project: string;
  created_at: string;
  updated_at: string;
}

const FIXTURE_CONVERSATIONS: ConversationFixture[] = [
  {
    id: "c1",
    title: "First chat",
    project: "alpha",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  },
];

const hoisted = vi.hoisted(() => ({
  conversations: [] as Array<{
    id: string;
    title: string;
    project: string;
    created_at: string;
    updated_at: string;
  }>,
  createCalls: 0,
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentStatus: () => ({
    data: {
      data: {
        gateway_configured: true,
        gateway_error: "",
        text_model: "gpt-4o-mini",
        default_text_model: "gpt-4o-mini",
        image_model: "",
        default_image_model: "flux",
      },
    },
  }),
  useAgentSettings: () => ({
    data: {
      data: {
        text_model: "",
        image_model: "",
        video_model: "",
        default_video_model: "ap-minimax-h3",
      },
    },
  }),
  useUpdateAgentSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAgentConversations: () => ({
    data: { data: hoisted.conversations },
    isLoading: false,
    isSuccess: true,
  }),
  useCreateAgentConversation: () => ({
    mutateAsync: vi.fn(async () => {
      hoisted.createCalls += 1;
      return {
        id: "auto-created",
        title: "",
        project: "alpha",
        created_at: "2026-09-30T00:00:00Z",
        updated_at: "2026-09-30T00:00:00Z",
      };
    }),
    isPending: false,
  }),
  useUpdateAgentConversation: () => ({ mutate: vi.fn() }),
  useDeleteAgentConversation: () => ({ mutate: vi.fn() }),
  useAgentMessages: () => ({ data: { data: [] } }),
  useClearAgentMessages: () => ({ mutate: vi.fn() }),
  useCancelAgentTurn: () => ({ mutate: vi.fn() }),
  useAgentMemory: () => ({ data: { data: { content: "remember me" } }, isLoading: false }),
  useUpdateAgentMemory: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAgentPlan: () => ({ data: { data: { items: [], max_items: 30 } }, isLoading: false }),
  useAddAgentPlanItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAgentPlanItem: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDeleteAgentPlanItem: () => ({ mutate: vi.fn() }),
  useRunAgentPlan: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAgentSkills: () => ({ data: { data: [] }, isLoading: false, isError: false }),
  useAgentSkillDetail: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
  downloadAgentSkill: vi.fn(async () => undefined),
  useInstallAgentSkill: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetAgentSkillEnabled: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAgentSkill: () => ({ mutate: vi.fn() }),
  useAgentMcpServers: () => ({ data: { data: [] }, isLoading: false }),
  useUpsertAgentMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetAgentMcpServerEnabled: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAgentMcpServer: () => ({ mutate: vi.fn() }),
  useProbeAgentMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false, variables: undefined }),
  useResetAgentMcpServers: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSaveAgentMediaToLibrary: () => ({ mutateAsync: vi.fn(), isPending: false }),
  streamAgentTurn: vi.fn(),
}));

vi.mock("@/lib/queries/model-plaza", () => ({
  useModelPlaza: () => ({
    data: {
      data: {
        models: [
          {
            modelName: "gpt-4o-mini",
            displayName: "gpt-4o-mini",
            connector: "gateway",
            capabilities: ["text"],
            status: "mapped",
          },
        ],
      },
    },
  }),
}));

// 左栏「资产」页的取数（项目资产库）桩掉：本文件只验证页面接线。
vi.mock("@/lib/queries/freezone", () => ({
  useFreezoneAssetLibrary: () => ({
    data: [
      {
        id: "i1",
        name: "asset.png",
        media: "image",
        source: "upload",
        category: "other",
        folder: "other",
        url: "https://cdn.example/asset.png",
        raw: {},
      },
    ],
    isLoading: false,
  }),
  useFreezoneAssetLibraryFolders: () => ({ data: [], isLoading: false }),
}));

// 输入区媒体参数选择器（ComposerMediaParams）读取的媒体目录桩掉：空目录不触网，
// 图片无声明时不渲染选择器、视频兜底档位由 composer-media-params 测试另测。
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => ({ models: [], isLoading: false, isFallback: false, error: null }),
}));

vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({ models: [], isLoading: false, isFallback: false, error: null }),
}));

function renderPage(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("AgentPage", () => {
  beforeEach(() => {
    hoisted.conversations = [...FIXTURE_CONVERSATIONS];
    hoisted.createCalls = 0;
  });

  it("renders conversations, chat empty state and skills panel", () => {
    renderPage(<AgentPage project="alpha" />);

    expect(screen.getByText("agent.page.title")).toBeInTheDocument();
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.getByText("agent.chat.emptyTitle")).toBeInTheDocument();
    expect(screen.getByText("agent.skills.empty")).toBeInTheDocument();
    // 输入区的模型下拉（ComposerModelPicker）已接线。
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    // 右上角「连接器MCP」入口与输入区参考素材按钮已接线。
    expect(screen.getByRole("button", { name: "agent.connectors.title" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "agent.chat.reference.add" })).toBeInTheDocument();
  });

  it("switches to the memory panel and shows the stored memory", async () => {
    const user = userEvent.setup();
    renderPage(<AgentPage project="alpha" />);

    await user.click(screen.getByRole("button", { name: "agent.panel.memory" }));

    expect(screen.getByText("agent.memory.description")).toBeInTheDocument();
    expect(screen.getByDisplayValue("remember me")).toBeInTheDocument();
  });

  it("switches to the plan panel (left of skills/memory) and shows the empty state", async () => {
    const user = userEvent.setup();
    renderPage(<AgentPage project="alpha" />);

    await user.click(screen.getByRole("button", { name: "agent.panel.plan" }));

    expect(screen.getByText("agent.plan.description")).toBeInTheDocument();
    expect(screen.getByText("agent.plan.empty")).toBeInTheDocument();
  });

  it("auto-creates a conversation when none exists so the composer is ready", async () => {
    hoisted.conversations = [];
    renderPage(<AgentPage project="alpha" />);

    await waitFor(() => expect(hoisted.createCalls).toBe(1));
  });

  it("opens the connectors dialog with the MCP server manager inside", async () => {
    const user = userEvent.setup();
    renderPage(<AgentPage project="alpha" />);

    await user.click(screen.getByRole("button", { name: "agent.connectors.title" }));

    // 弹窗内嵌服务器管理器：标题/描述来自对话框，空态来自管理器。
    expect(await screen.findByText("agent.connectors.description")).toBeInTheDocument();
    expect(await screen.findByText("mcp.agentServers.empty")).toBeInTheDocument();
  });

  it("switches the left column to the asset tree and wires click-to-reference", async () => {
    const user = userEvent.setup();
    renderPage(<AgentPage project="alpha" />);

    // 默认在「会话」页：会话列表可见、资产树未挂载。
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.queryByText("asset.png")).toBeNull();

    await user.click(screen.getByRole("button", { name: "agent.leftTabs.assets" }));

    expect(screen.getByText("asset.png")).toBeInTheDocument();
    expect(screen.getByText("agent.assets.hint")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).toBeNull();

    // 点击素材 → 经 ComposerHandle 并入输入区参考 chips。
    await user.click(screen.getByText("asset.png"));
    expect(screen.getByTitle("agent.chat.reference.remove")).toBeInTheDocument();
  });
});
