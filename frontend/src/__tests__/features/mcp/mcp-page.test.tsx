// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] MCP 页（Tabs 结构）：「接入指南」＝占位符 + 进入项目快捷入口 +
// 添加到 Agent + 画布基础用法；「MCP 列表」＝服务器列表（双内置：aipdd-drama + Chrome MCP，
// 不可编辑/删除 + 自添加三类型），每个服务器展开可看「概览 / 工具」（aipdd-drama 读目录，
// 其它内置与自装服务器经「测试连接」探测）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { McpPage } from "@/features/mcp/McpPage";

const navigateMock = vi.hoisted(() => vi.fn());
const setEnabledMock = vi.hoisted(() => vi.fn());
const upsertMock = vi.hoisted(() => vi.fn());
const probeMock = vi.hoisted(() => vi.fn());
const projectsState = vi.hoisted(() => ({
  current: undefined as
    | undefined
    | { id: string; name: string; status: "active" | "archived"; updatedAt?: string }[],
}));
// [aipdd fork delta] 工具目录数据源是后端真实目录（89 个工具）；桩返回全量名册 。
const catalogState = vi.hoisted(() => ({
  current: [] as { name: string; description: string; input_schema: Record<string, unknown> }[],
}));

/** 服务器桩：字段与 AgentMcpServer 对齐（展开概览会读取 transport / 命令 / 地址 / 键名）。 */
interface ServerStub {
  id: string;
  name: string;
  description: string;
  transport: "stdio" | "http" | "sse";
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  enabled: boolean;
  builtin: boolean;
}

// [aipdd fork delta] Agent MCP 服务器列表桩（双内置由后端默认写入且不可编辑/删除）。
const serversState = vi.hoisted(() => ({ current: [] as ServerStub[] }));

/** 内置 AIPDD Drama 服务器（默认启用，stdio 传输）。 */
function builtinServer(overrides: Partial<ServerStub> = {}): ServerStub {
  return {
    id: "aipdd-drama",
    name: "AIPDD Drama",
    description: "内置 AIPDD Drama MCP",
    transport: "stdio",
    command: "uv",
    args: ["run", "python", "-m", "novelvideo.chat.aipdd_drama_mcp"],
    env: { AIPDD_DRAMA_API_URL: "http://localhost:8780" },
    url: "",
    headers: {},
    enabled: true,
    builtin: true,
    ...overrides,
  };
}

/** [aipdd fork delta] 第二内置 Chrome MCP（默认启用；连接客户自带 Chrome，不下载浏览器）。 */
function chromeServer(overrides: Partial<ServerStub> = {}): ServerStub {
  return {
    id: "chrome-devtools",
    name: "Chrome MCP",
    description: "内置：通过 Chrome DevTools 协议驱动本机已装的 Chrome",
    transport: "stdio",
    command: "chrome-devtools-mcp",
    args: ["--browserUrl", "http://host.docker.internal:9222"],
    env: {},
    url: "",
    headers: {},
    enabled: true,
    builtin: true,
    ...overrides,
  };
}

// [aipdd fork delta] 与后端 aipdd_drama_tools.TOOLS 同步的工具名册（13 组）。
const CATALOG_NAMES = [
  "aipdd_drama_get",
  "aipdd_drama_post",
  "aipdd_drama_patch",
  "aipdd_drama_delete",
  "aipdd_drama_env_setup",
  "aipdd_drama_pipeline_status",
  "aipdd_drama_list_tasks",
  "aipdd_drama_get_task",
  "aipdd_drama_get_episode_script",
  "aipdd_drama_list_ingest_uploads",
  "aipdd_drama_build_characters",
  "aipdd_drama_update_character_face_prompt",
  "aipdd_drama_plan_episodes",
  "aipdd_drama_plan_identities",
  "aipdd_drama_plan_scenes",
  "aipdd_drama_plan_props",
  "aipdd_drama_get_episode_story",
  "aipdd_drama_save_episode_story",
  "aipdd_drama_optimize_episode_story",
  "aipdd_drama_story_to_script",
  "aipdd_drama_generate_script",
  "aipdd_drama_generate_scene_master",
  "aipdd_drama_generate_scene_reverse",
  "aipdd_drama_generate_portrait",
  "aipdd_drama_generate_identity_image",
  "aipdd_drama_generate_sketches",
  "aipdd_drama_detect_sketch_identities",
  "aipdd_drama_generate_audio",
  "aipdd_drama_start_single_video",
  "aipdd_drama_render_first_frames",
  "aipdd_drama_compose_episode",
  "aipdd_drama_optimize_video_global",
  "aipdd_drama_get_sketches",
  "aipdd_drama_get_first_frames",
  "aipdd_drama_get_sketch_candidates",
  "aipdd_drama_get_scene_images",
  "aipdd_drama_get_character_media",
  "aipdd_drama_get_episode_media",
  "aipdd_drama_get_final_video",
  "vectcut_health",
  "vectcut_create_draft",
  "vectcut_add_image",
  "vectcut_add_video",
  "vectcut_add_audio",
  "vectcut_add_text",
  "vectcut_add_subtitle",
  "vectcut_add_effect",
  "vectcut_add_sticker",
  "vectcut_add_keyframe",
  "vectcut_list_options",
  "vectcut_get_duration",
  "vectcut_save_draft",
  "aipdd_drama_video_clone",
  "aipdd_drama_video_mix",
  "aipdd_drama_whiteboard_animation",
  "aipdd_drama_video_idea",
  "aipdd_drama_voice_clone",
  "aipdd_drama_music_create",
  "aipdd_drama_lip_sync",
  "aipdd_drama_replace_person",
  "aipdd_drama_replace_product",
  "aipdd_drama_video_watermark",
  "aipdd_drama_image_watermark",
  "aipdd_drama_douyin_copywrite",
  "aipdd_drama_image_generate",
  "aipdd_drama_image_edit",
  "aipdd_drama_image_upscale",
  "aipdd_drama_image_outpaint",
  "aipdd_drama_image_relight",
  "aipdd_drama_image_multi_view",
  "aipdd_drama_image_template_edit",
  "aipdd_drama_image_redraw",
  "aipdd_drama_image_reverse_prompt",
  "aipdd_drama_video_generate",
  "aipdd_drama_video_i2v",
  "aipdd_drama_video_omni_gen",
  "aipdd_drama_video_upscale",
  "aipdd_drama_video_edit",
  "aipdd_drama_video_extend",
  "aipdd_drama_audio_separate",
  "aipdd_drama_audio_music",
  "aipdd_drama_text_generate",
  "aipdd_drama_text_translate",
  "aipdd_drama_story_script_generate",
  "aipdd_drama_video_script_reverse",
  "aipdd_drama_media_copywrite",
  "aipdd_drama_list_assets",
  "aipdd_drama_get_character_library",
  "aipdd_drama_get_audio_references",
  "aipdd_drama_get_models",
  "aipdd_drama_auto_storyboard",
  "aipdd_drama_video_range",
];

const GENERIC_SCHEMA = { type: "object", properties: {}, required: [] };
const GET_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string", description: "API path" },
    query: { type: "string", description: "" },
  },
  required: ["path"],
};

function buildCatalog() {
  return CATALOG_NAMES.map((name) => ({
    name,
    description: `backend:${name}`,
    input_schema: name === "aipdd_drama_get" ? GET_SCHEMA : GENERIC_SCHEMA,
  }));
}

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigateMock }));
vi.mock("@/lib/queries/projects", () => ({
  useAllProjectSummaries: () => ({ data: projectsState.current, isLoading: false }),
}));
// [aipdd fork delta] MCP 栏目内置「MCP 服务器列表」与工具目录，桩掉数据层避免真实请求。
vi.mock("@/lib/queries/agent", () => ({
  useAgentMcpServers: () => ({ data: { data: serversState.current }, isLoading: false }),
  useUpsertAgentMcpServer: () => ({ mutateAsync: upsertMock, isPending: false }),
  useSetAgentMcpServerEnabled: () => ({
    mutate: vi.fn(),
    mutateAsync: setEnabledMock,
    isPending: false,
  }),
  useDeleteAgentMcpServer: () => ({ mutate: vi.fn() }),
  useProbeAgentMcpServer: () => ({ mutateAsync: probeMock, isPending: false, variables: undefined }),
  useResetAgentMcpServers: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAgentBuiltinMcpTools: () => ({
    data: { data: { server_id: "aipdd-drama", tools: catalogState.current } },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  // [aipdd fork delta] 本地 Agent 工具区块（「接入指南」页内嵌）：本文件不覆盖其行为，
  // 只保证渲染桩可用（专项用例见 local-agent-tools.test.tsx）。
  useAgentLocalTools: () => ({
    data: { data: { tools: [], hostHomeAvailable: true, hostHomePath: "/host-home" } },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useConnectAgentLocalTools: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDisconnectAgentLocalTools: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

// 页面渲染需要 QueryClient 上下文（MCP 服务器列表使用 TanStack Query）。
function renderPage(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** 切到「MCP 列表」Tab（服务器列表与内置工具目录都在这里）。 */
function openToolsTab() {
  fireEvent.click(screen.getByRole("tab", { name: "mcp.tabs.tools" }));
}

/** 展开某个服务器卡片（切换按钮的可访问名以服务器名开头，后跟徽标文案）。 */
function expandServer(name: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${name}`) }));
}

/** 切到服务器卡片的「工具」分页。 */
function openServerTools() {
  fireEvent.click(screen.getByRole("button", { name: "mcp.agentServers.detail.tools" }));
}

/** 「MCP 列表」→ 展开内置服务器 → 工具分页（内置目录渲染链路）。 */
function openBuiltinTools() {
  openToolsTab();
  expandServer("AIPDD Drama");
  openServerTools();
}

describe("McpPage global mode", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    setEnabledMock.mockReset();
    upsertMock.mockReset();
    probeMock.mockReset();
    upsertMock.mockResolvedValue(undefined);
    probeMock.mockResolvedValue({ ok: true, tools: [] });
    projectsState.current = undefined;
    catalogState.current = buildCatalog();
    serversState.current = [builtinServer()];
  });

  it("uses a project id placeholder and offers recent project shortcuts", () => {
    projectsState.current = [
      { id: "p-old", name: "Older", status: "active", updatedAt: "2026-01-01T00:00:00Z" },
      { id: "p-new", name: "Newer", status: "active", updatedAt: "2026-09-01T00:00:00Z" },
      { id: "p-gone", name: "Archived", status: "archived", updatedAt: "2026-09-20T00:00:00Z" },
    ];

    renderPage(<McpPage />);

    expect(screen.getByText("mcp.noProject.title")).toBeInTheDocument();
    expect(document.body.textContent).toContain("<your-project-id>");
    // 归档项目不进快捷区；活跃项目按更新时间倒序。
    expect(screen.queryByRole("button", { name: "Archived" })).toBeNull();
    expect(screen.getByRole("button", { name: "Newer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Older" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Newer" }));
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/projects/$project/mcp",
      params: { project: "p-new" },
    });
  });

  it("offers a create-project action when there are no projects yet", () => {
    projectsState.current = [];

    renderPage(<McpPage />);

    fireEvent.click(screen.getByRole("button", { name: "mcp.noProject.create" }));
    expect(navigateMock).toHaveBeenCalledWith({ to: "/" });
  });

  it("prefills the project id and hides the guidance when a project is provided", () => {
    renderPage(<McpPage project="alpha" />);

    expect(screen.queryByText("mcp.noProject.title")).toBeNull();
    expect(document.body.textContent).toContain('"AIPDD_DRAMA_PROJECT_ID": "alpha"');
    // 「接入指南」页必须带上画布基础用法（用途 → 工具 → 参数）。
    expect(document.body.textContent).toContain("mcp.basics.title");
  });

  // [aipdd fork delta] 「添加到 Agent」卡：官方内置服务器未接入时给一键接入按钮。
  it("offers a one-click connect for the built-in MCP server", async () => {
    serversState.current = [builtinServer({ enabled: false })];

    renderPage(<McpPage project="alpha" />);

    fireEvent.click(screen.getByRole("button", { name: "mcp.agentAdd.enable" }));
    await waitFor(() =>
      expect(setEnabledMock).toHaveBeenCalledWith({ serverId: "aipdd-drama", enabled: true }),
    );
  });

  it("shows the connected badge when the built-in server is enabled", () => {
    renderPage(<McpPage project="alpha" />);

    expect(screen.getByText("mcp.agentAdd.enabled")).toBeInTheDocument();
  });

  // [aipdd fork delta] 双内置（aipdd-drama + Chrome MCP）：带「内置」徽标，不可编辑/删除（可停用）。
  it("keeps the built-in servers badged and protected from deletion", () => {
    serversState.current = [builtinServer(), chromeServer()];
    renderPage(<McpPage project="alpha" />);
    openToolsTab();

    expect(screen.getAllByText("mcp.agentServers.builtin")).toHaveLength(2);
    // 编辑与删除按钮共用 builtinLocked 提示，全部禁用（2 个内置 × 2 个按钮）。
    const locked = screen.getAllByTitle("mcp.agentServers.builtinLocked");
    expect(locked).toHaveLength(4);
    for (const button of locked) expect(button).toBeDisabled();
  });

  // [aipdd fork delta] Chrome MCP 的工具分页走「拉取工具」探测（不是 aipdd-drama 只读目录）。
  it("probes tools for the built-in chrome server instead of the catalog", () => {
    serversState.current = [builtinServer(), chromeServer()];
    renderPage(<McpPage project="alpha" />);
    openToolsTab();
    expandServer("Chrome MCP");
    openServerTools();

    expect(
      screen.getByRole("button", { name: "mcp.agentServers.fetchTools" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("mcp.toolGroups.production")).toBeNull();
  });

  // [aipdd fork delta] 故事工具必须与后端 aipdd_drama_tools.TOOLS 同步展示（内置卡工具分页）。
  it("lists the episode story tool group", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    const text = document.body.textContent ?? "";
    expect(text).toContain("mcp.toolGroups.production");
    for (const name of [
      "aipdd_drama_get_episode_story",
      "aipdd_drama_save_episode_story",
      "aipdd_drama_optimize_episode_story",
      "aipdd_drama_story_to_script",
    ]) {
      expect(text).toContain(name);
    }
  });

  // [aipdd fork delta] 剪辑工具组必须与后端 aipdd_drama_tools.TOOLS 的 vectcut_* 工具同步展示。
  it("lists the vectcut editing tool group with all 13 tools", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    const text = document.body.textContent ?? "";
    expect(text).toContain("mcp.toolGroups.editing");
    for (const name of [
      "vectcut_health",
      "vectcut_create_draft",
      "vectcut_add_image",
      "vectcut_add_video",
      "vectcut_add_audio",
      "vectcut_add_text",
      "vectcut_add_subtitle",
      "vectcut_add_effect",
      "vectcut_add_sticker",
      "vectcut_add_keyframe",
      "vectcut_list_options",
      "vectcut_get_duration",
      "vectcut_save_draft",
    ]) {
      expect(text).toContain(name);
    }
  });

  // [aipdd fork delta] 工具页工具组（含抖音文案提取、白板动画）必须与后端目录同步展示。
  it("lists the tools-page group with all tools", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    const text = document.body.textContent ?? "";
    expect(text).toContain("mcp.toolGroups.toolsPage");
    for (const name of [
      "aipdd_drama_video_clone",
      "aipdd_drama_video_mix",
      // [aipdd fork delta] 白板动画视频：工具页新增工具必须同步到 MCP 目录。
      "aipdd_drama_whiteboard_animation",
      "aipdd_drama_video_idea",
      "aipdd_drama_voice_clone",
      // [aipdd fork delta req46] 音乐创作：工具页新增工具必须同步到 MCP 目录。
      "aipdd_drama_music_create",
      "aipdd_drama_lip_sync",
      "aipdd_drama_replace_person",
      "aipdd_drama_replace_product",
      "aipdd_drama_video_watermark",
      "aipdd_drama_image_watermark",
      "aipdd_drama_douyin_copywrite",
    ]) {
      expect(text).toContain(name);
    }
  });

  // [aipdd fork delta] 画布生图/生视频/音频/文本/资源五个新分组必须与后端目录同步展示。
  it("lists the canvas image/video/audio/text/resource tool groups", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    const text = document.body.textContent ?? "";
    const cases: Record<string, string[]> = {
      "mcp.toolGroups.imageTools": [
        "aipdd_drama_image_generate",
        "aipdd_drama_image_edit",
        "aipdd_drama_image_upscale",
        "aipdd_drama_image_outpaint",
        "aipdd_drama_image_relight",
        "aipdd_drama_image_multi_view",
        "aipdd_drama_image_template_edit",
        "aipdd_drama_image_redraw",
        "aipdd_drama_image_reverse_prompt",
      ],
      "mcp.toolGroups.videoTools": [
        "aipdd_drama_video_generate",
        "aipdd_drama_video_i2v",
        "aipdd_drama_video_omni_gen",
        "aipdd_drama_video_upscale",
        "aipdd_drama_video_edit",
        "aipdd_drama_video_extend",
      ],
      "mcp.toolGroups.audioTools": ["aipdd_drama_audio_separate", "aipdd_drama_audio_music"],
      "mcp.toolGroups.textTools": [
        "aipdd_drama_text_generate",
        "aipdd_drama_text_translate",
        "aipdd_drama_story_script_generate",
        "aipdd_drama_video_script_reverse",
        "aipdd_drama_media_copywrite",
      ],
      "mcp.toolGroups.resourceTools": [
        "aipdd_drama_list_assets",
        "aipdd_drama_get_character_library",
        "aipdd_drama_get_audio_references",
        "aipdd_drama_get_models",
      ],
      "mcp.toolGroups.automation": [
        "aipdd_drama_auto_storyboard",
        "aipdd_drama_video_range",
      ],
    };
    for (const [group, names] of Object.entries(cases)) {
      expect(text).toContain(group);
      for (const name of names) expect(text).toContain(name);
    }
  });

  // [aipdd fork delta] 工具行可展开调用字段、类型与必填标记（用户需求③）。
  it("expands a tool row to show its call fields and required flags", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    fireEvent.click(screen.getByRole("button", { name: "aipdd_drama_get mcp.tools.get" }));

    const text = document.body.textContent ?? "";
    expect(text).toContain("mcp.schema.colParam");
    expect(text).toContain("mcp.schema.required");
    expect(text).toContain("mcp.schema.optional");
    expect(text).toContain("API path");
  });

  // [aipdd fork delta] 目录支持按名称 / 说明过滤。
  it("filters the catalog by keyword", () => {
    renderPage(<McpPage project="alpha" />);
    openBuiltinTools();

    fireEvent.change(screen.getByPlaceholderText("mcp.catalog.search"), {
      target: { value: "vectcut_health" },
    });

    const text = document.body.textContent ?? "";
    expect(text).toContain("vectcut_health");
    expect(text).not.toContain("aipdd_drama_get_episode_story");
  });

  // [aipdd fork delta] 自添加服务器：展开看概览（描述 / 地址 / 键名），工具经「测试连接」探测。
  it("expands a user server and fetches its tools via probe", async () => {
    serversState.current = [
      builtinServer(),
      {
        id: "s-echo",
        name: "Echo",
        description: "自装回显服务",
        transport: "http",
        command: "",
        args: [],
        env: {},
        url: "https://echo.example.com/mcp",
        headers: { Authorization: "Bearer x" },
        enabled: true,
        builtin: false,
      },
    ];
    probeMock.mockResolvedValue({
      ok: true,
      tools: [
        {
          name: "echo_ping",
          qualified_name: "echo_ping",
          description: "ping",
          input_schema: GENERIC_SCHEMA,
        },
      ],
    });

    renderPage(<McpPage project="alpha" />);
    openToolsTab();
    expandServer("Echo");

    // 概览分页：描述 + 服务地址 + 请求头键名（徽标为接入类型）。
    const overview = document.body.textContent ?? "";
    expect(overview).toContain("自装回显服务");
    expect(overview).toContain("https://echo.example.com/mcp");
    expect(overview).toContain("mcp.agentServers.type.http");

    // 工具分页：未探测时给获取引导；点击「获取工具」调 probe 并展示结果。
    openServerTools();
    expect(document.body.textContent).toContain("mcp.agentServers.toolsHint");
    fireEvent.click(screen.getByRole("button", { name: "mcp.agentServers.fetchTools" }));

    await waitFor(() => expect(probeMock).toHaveBeenCalledWith("s-echo"));
    await waitFor(() => expect(document.body.textContent).toContain("echo_ping"));
    expect(document.body.textContent).toContain('mcp.agentServers.testOk:{"n":1}');
  });

  // [aipdd fork delta] 添加 MCP：三类型卡片（SSE / 流式 HTTP / 自定义代码）字段各异。
  it("adds a server through the type-specific form", async () => {
    renderPage(<McpPage project="alpha" />);
    openToolsTab();
    fireEvent.click(screen.getByRole("button", { name: "mcp.agentServers.add" }));

    // 三种接入类型卡片（默认自定义代码 → 启动命令字段）。
    expect(
      screen.getByRole("button", { name: /mcp\.agentServers\.type\.sse/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /mcp\.agentServers\.type\.http/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /mcp\.agentServers\.type\.custom/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("mcp.agentServers.command")).toBeInTheDocument();

    // 切到 SSE：输入项变为服务地址 + 请求头。
    fireEvent.click(screen.getByRole("button", { name: /mcp\.agentServers\.type\.sse/ }));
    expect(screen.queryByLabelText("mcp.agentServers.command")).toBeNull();
    fireEvent.change(screen.getByLabelText("mcp.agentServers.name"), {
      target: { value: "SSE 服务" },
    });
    fireEvent.change(screen.getByLabelText("mcp.agentServers.url"), {
      target: { value: "https://sse.example.com/events" },
    });
    fireEvent.click(screen.getByRole("button", { name: "mcp.agentServers.save" }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "SSE 服务",
          transport: "sse",
          url: "https://sse.example.com/events",
          command: "",
          env: {},
        }),
      ),
    );
  });
});