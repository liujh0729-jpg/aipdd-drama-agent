// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [openspec configure-newapi-feature-models 8.2] 配置页交互测试，覆盖：
// 1) 未知模型：无法分类的模型计入「未识别」并提供查看入口（切到「其它」TAB）；
// 2) 全部模型：渠道拉取到的模型全量列出（不因推荐/默认状态被过滤）；
// 3) 失效模型：已保存但上游列表已缺失的模型仍回显，不静默丢失；
// 4) Key 保留：已配置渠道留空 Key 保存时不发送 upstreamKey（后端按
//    preserveUnmentioned 保留原 Key），未配置渠道留空则拦截并提示。
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DefaultGenerationModelsPanel } from "@/features/settings/model-gateway/default-generation-models-panel";
import {
  ComfyUILocalCard,
  ProviderConnectCard,
  ProviderConnectCards,
} from "@/features/settings/model-gateway/provider-connect-cards";
import { ProviderChannelsBlock } from "@/features/settings/model-gateway/provider-channels-block";
import type { ModelGatewayConfig } from "@/lib/queries/model-gateway";
import { DEFAULT_FEATURE_MODEL_SETTINGS, useSettingsStore } from "@/stores/settingsStore";
import { toast } from "sonner";

const mocks = vi.hoisted(() => ({
  fetchUpstreamModels: vi.fn(),
  saveProviderChannels: vi.fn(),
  saveMediaModelMappings: vi.fn(),
  saveCustomChannelsBatch: vi.fn(),
  saveModelAliasMappings: vi.fn(),
  saveEmbeddingModel: vi.fn(),
  checkAliasMappingHealth: vi.fn(),
  resolveComfyUIWorkflows: vi.fn(),
  // [aipdd fork delta] 「AI分析并自动配置」：卡片调的就是这个 hook，默认桩在 beforeEach 里给。
  adaptComfyUIWorkflows: vi.fn(),
  saveVideoEnhanceConfig: vi.fn(),
  clearComfyUIConfig: vi.fn(),
  syncProviderChannel: vi.fn(),
  confirmDialog: vi.fn(),
  // [aipdd fork delta req40] 渠道目录默认空表（没同步过）；需要候选的用例自己覆盖。
  providerCatalog: { ok: true, data: { providers: [], meta: {} } },
  syncProviderCatalog: vi.fn(),
  modelPlaza: undefined as unknown,
}));

vi.mock("@/lib/queries/model-gateway", () => ({
  useProviderCatalog: () => ({
    data: mocks.providerCatalog,
    isLoading: false,
  }),
  useSyncProviderCatalog: () => ({
    mutateAsync: mocks.syncProviderCatalog,
    isPending: false,
  }),
  useFetchUpstreamModels: () => ({
    mutateAsync: mocks.fetchUpstreamModels,
    isPending: false,
  }),
  useSaveProviderChannels: () => ({
    mutateAsync: mocks.saveProviderChannels,
    mutate: mocks.saveProviderChannels,
    isPending: false,
  }),
  useSaveMediaModelMappings: () => ({
    mutateAsync: mocks.saveMediaModelMappings,
    isPending: false,
  }),
  useSaveCustomChannelsBatch: () => ({
    mutateAsync: mocks.saveCustomChannelsBatch,
    isPending: false,
  }),
  useSaveModelAliasMappings: () => ({
    mutateAsync: mocks.saveModelAliasMappings,
    isPending: false,
  }),
  useSaveEmbeddingModel: () => ({
    mutateAsync: mocks.saveEmbeddingModel,
    isPending: false,
  }),
  useCheckAliasMappingHealth: () => ({
    mutateAsync: mocks.checkAliasMappingHealth,
    isPending: false,
  }),
  useResolveComfyUIWorkflows: () => ({
    mutateAsync: mocks.resolveComfyUIWorkflows,
    isPending: false,
  }),
  useAdaptComfyUIWorkflows: () => ({
    mutateAsync: mocks.adaptComfyUIWorkflows,
    isPending: false,
  }),
  useSaveVideoEnhanceConfig: () => ({
    mutateAsync: mocks.saveVideoEnhanceConfig,
    isPending: false,
  }),
  useClearComfyUIConfig: () => ({
    mutateAsync: mocks.clearComfyUIConfig,
    isPending: false,
  }),
  useSyncProviderChannel: () => ({
    mutateAsync: mocks.syncProviderChannel,
    isPending: false,
  }),
}));

vi.mock("@/components/confirm-dialog-host", () => ({
  confirmDialog: mocks.confirmDialog,
}));

// [aipdd fork delta] ComfyUI 卡的「分析模型」候选来自网关模型广场；本文件手搭渲染
// 没有 QueryClientProvider，直接给桩避开 useQuery 报无客户端。
vi.mock("@/lib/queries/model-plaza", () => ({
  useModelPlaza: () => ({
    data: mocks.modelPlaza,
    isLoading: false,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// Base UI 的 ScrollArea / Dialog 依赖 ResizeObserver，jsdom 不提供。
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// Base UI ScrollArea 在定时器里用 getAnimations 检测动画，jsdom 未实现。
if (typeof Element.prototype.getAnimations !== "function") {
  Element.prototype.getAnimations = () => [];
}

function providerChannel(provider: string, configured = true) {
  return {
    provider,
    configured,
    upstreamKeyPreview: configured ? "sk-***" : "",
    baseUrl: `${provider}.example/v1`,
  };
}

/** custom 网关已就绪 → ready=true，面板会拉取上游模型。 */
function panelConfig(): ModelGatewayConfig {
  return {
    mode: "custom",
    effective: {
      source: "custom",
      baseUrl: "https://newapi.example/v1",
      apiKeyPreview: "sk-***",
      configured: true,
    },
    official: {
      source: "database",
      baseUrl: "",
      apiKeyPreview: "",
      configured: false,
      environment: { baseUrl: "", apiKeyPreview: "", configured: false },
    },
    custom: {
      baseUrl: "https://newapi.example/v1",
      apiKeyPreview: "sk-***",
      configured: true,
      adminBaseUrl: "https://newapi.example/admin",
      tokenName: "t",
      tokenId: "1",
    },
    provisioner: {
      enabled: true,
      adminBaseUrl: "https://newapi.example/admin",
      dbConfigured: true,
      adminUsername: "root",
      relayTokenName: "t",
      relayBaseUrl: "https://newapi.example/v1",
      providers: {},
      providerChannels: [
        providerChannel("aipdd"),
        providerChannel("newapi"),
        providerChannel("comfyui"),
      ],
      mediaModels: {
        // 失效模型：已保存但上游 /models 列表已不包含它。
        "ap-jimeng-image-3.0": {
          provider: "newapi",
          upstreamModel: "ap-jimeng-image-3.0",
        },
      },
      aliasMappings: {},
      aliasChannelRoles: {},
    },
  } as unknown as ModelGatewayConfig;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  // 默认无广场数据（需要候选的用例自己覆盖）。
  mocks.modelPlaza = undefined;
  useSettingsStore.setState({
    featureModelConfig: DEFAULT_FEATURE_MODEL_SETTINGS,
  });
  mocks.fetchUpstreamModels.mockResolvedValue({
    ok: true,
    data: {
      models: [
        "ap-jimeng-image-4.0",
        "ap-flux-9-turbo",
        "ap-seedream-5.0-lite",
        "brand-new-unknown",
      ],
      count: 4,
    },
  });
});

describe("默认生成模型面板：全部模型与失效模型", () => {
  it("列出渠道拉取到的全部模型（不因非默认被过滤）", async () => {
    render(
      <DefaultGenerationModelsPanel
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    // [aipdd fork delta] 拍平：同一模型跨渠道各占一行——fixture 中 aipdd / newapi
    // 两个渠道参与上游拉取（comfyui 除外），故每个模型 2 行。
    expect(await screen.findAllByText("ap-jimeng-image-4.0")).toHaveLength(2);
    expect(screen.getAllByText("ap-flux-9-turbo")).toHaveLength(2);
    expect(screen.getAllByText("ap-seedream-5.0-lite")).toHaveLength(2);
  });

  it("已保存但上游列表已缺失的模型仍回显（不静默丢失）", async () => {
    render(
      <DefaultGenerationModelsPanel
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    expect(
      (await screen.findAllByText("ap-jimeng-image-4.0")).length,
    ).toBeGreaterThan(0);
    // ap-jimeng-image-3.0 只存在于已保存 mediaModels，不在拉取结果里。
    expect(screen.getByText("ap-jimeng-image-3.0")).toBeInTheDocument();
  });
});

describe("默认生成模型面板：未知模型", () => {
  it("无法分类的模型计入未识别并提供查看入口", async () => {
    render(
      <DefaultGenerationModelsPanel
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    expect(
      (await screen.findAllByText("ap-jimeng-image-4.0")).length,
    ).toBeGreaterThan(0);
    // 初始在「图片」TAB：未知模型不混入，但有「查看其它未归类模型（2）」入口
    // ——[aipdd fork delta] 拍平：该未知模型在 aipdd / newapi 两个渠道各一条。
    expect(screen.queryByText("brand-new-unknown")).not.toBeInTheDocument();
    const viewUnknown = screen.getByRole("button", {
      name: /查看其它未归类模型/,
    });
    expect(viewUnknown).toHaveTextContent("2");

    // 点入口切到「其它」TAB，未知模型可见（拍平后每个渠道一行）。
    fireEvent.click(viewUnknown);
    expect(await screen.findAllByText("brand-new-unknown")).toHaveLength(2);
  });
});

// [aipdd fork delta] 火山 AI MediaKit（独立 connector）连接后其模型进「其它」TAB：
// 只读目录展示、标注子产品线（火山 AI MediaKit，渠道归属 VolcEngine），不混入「视频」TAB、不参与设默认。
describe("默认生成模型面板：火山 AI MediaKit", () => {
  function mediaKitPanelConfig(): ModelGatewayConfig {
    return {
      ...panelConfig(),
      videoEnhance: {
        source: "database",
        apiKeyPreview: "mk-***",
        configured: true,
        baseUrl: "https://mediakit.example/api/v1",
        backend: "auto",
      },
    } as unknown as ModelGatewayConfig;
  }

  it("MediaKit 已连接：模型归入「其它」TAB，不混入「视频」TAB", async () => {
    render(
      <DefaultGenerationModelsPanel
        config={mediaKitPanelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    // 名字含 video，但不得被 guessMediaType 误分到「视频」TAB。
    await screen.findAllByText("ap-jimeng-image-4.0");
    fireEvent.click(screen.getByRole("tab", { name: "视频" }));
    expect(
      screen.queryByText("enhance-video-generative"),
    ).not.toBeInTheDocument();

    // 「其它」TAB：条目出现，标注子产品线 火山 AI MediaKit，并带只读说明。
    fireEvent.click(screen.getByRole("tab", { name: "其它" }));
    expect(
      await screen.findByText("enhance-video-generative"),
    ).toBeInTheDocument();
    expect(screen.getByText("火山 AI MediaKit")).toBeInTheDocument();
    expect(
      screen.getByText(/由上方「渠道连接」VolcEngine 卡内的 火山 AI MediaKit 子区块接入/),
    ).toBeInTheDocument();
  });

  it("MediaKit 未连接：不注入该条目", async () => {
    render(
      <DefaultGenerationModelsPanel
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    await screen.findAllByText("ap-jimeng-image-4.0");
    fireEvent.click(screen.getByRole("tab", { name: "其它" }));
    expect(
      screen.queryByText("enhance-video-generative"),
    ).not.toBeInTheDocument();
  });
});

describe("渠道连接卡：Key 保留", () => {
  it("已配置渠道留空 Key 保存：不发送 upstreamKey 且声明 preserveUnmentioned", async () => {
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: { channels: [] },
    });

    render(
      <ProviderConnectCard
        title="NewAPI"
        description="外部 OpenAI 兼容渠道"
        provider="newapi"
        presetBaseUrl="https://newapi.example/v1"
        saved={{
          provider: "newapi",
          configured: true,
          upstreamKeyPreview: "sk-***",
          baseUrl: "https://newapi.example/v1",
        }}
      />,
    );

    // 已配置渠道的 Key 输入占位提示为「留空则不修改」。
    const keyInput = screen.getByPlaceholderText("留空则不修改");
    expect(keyInput).toHaveValue("");
    expect(screen.getByText(/已连接/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存并同步/ }));

    await waitFor(() => expect(mocks.saveProviderChannels).toHaveBeenCalled());
    const payload = mocks.saveProviderChannels.mock.calls[0][0] as {
      preserveUnmentioned: boolean;
      channels: Array<Record<string, unknown>>;
    };
    expect(payload.preserveUnmentioned).toBe(true);
    expect(payload.channels[0]).toMatchObject({
      provider: "newapi",
      baseUrl: "https://newapi.example/v1",
    });
    // 留空 = 不修改：不得携带 upstreamKey 字段（后端保留原 Key）。
    expect(payload.channels[0]).not.toHaveProperty("upstreamKey");
  });

  it("未配置渠道留空 Key 保存：拦截并提示，不发起保存", async () => {
    render(
      <ProviderConnectCard
        title="NewAPI"
        description="外部 OpenAI 兼容渠道"
        provider="newapi"
        presetBaseUrl="https://newapi.example/v1"
        saved={{
          provider: "newapi",
          configured: false,
          upstreamKeyPreview: "",
          baseUrl: "https://newapi.example/v1",
        }}
      />,
    );

    expect(screen.getByPlaceholderText("粘贴 Key")).toBeInTheDocument();
    expect(screen.getByText("未连接")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存并同步/ }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("请先填写 API Key"),
    );
    expect(mocks.saveProviderChannels).not.toHaveBeenCalled();
  });
});

// [aipdd fork delta] 渠道名旁的官网直达：已登记官网的厂商给链接（新窗口 + noopener），
// 没登记的渠道（newapi 这类自配网关）不渲染链接——不给一个点开是空地址的死图标。
describe("渠道连接卡：官网直达链接", () => {
  it("AIPDD 卡给出官网链接，新窗口打开且不回传 referrer", () => {
    render(
      <ProviderConnectCard
        title="AIPDD"
        description="平台闲置算力渠道"
        provider="aipdd"
        presetBaseUrl="https://api.aipdd.work/v1"
        fixedBaseUrl
        saved={{
          provider: "aipdd",
          configured: false,
          upstreamKeyPreview: "",
          baseUrl: "",
        }}
      />,
    );

    const link = screen.getByRole("link", { name: "打开 AIPDD 官网" });
    expect(link).toHaveAttribute("href", "https://www.aipdd.work");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // title 就是地址本身：用户悬停能看到官网域名，不必先点开。
    expect(link).toHaveAttribute("title", "https://www.aipdd.work");
  });

  it("未登记官网的渠道不渲染链接", () => {
    render(
      <ProviderConnectCard
        title="NewAPI"
        description="外部 OpenAI 兼容渠道"
        provider="newapi"
        presetBaseUrl="https://newapi.example/v1"
        saved={{
          provider: "newapi",
          configured: false,
          upstreamKeyPreview: "",
          baseUrl: "",
        }}
      />,
    );

    expect(screen.queryByRole("link", { name: /官网/ })).not.toBeInTheDocument();
  });
});

describe("渠道连接：火山 AI MediaKit 归属 VolcEngine", () => {
  it("MediaKit 子区块渲染在 VolcEngine 卡内，顶层不再多一张独立卡", async () => {
    mocks.fetchUpstreamModels.mockResolvedValue({
      ok: true,
      data: { models: [], count: 0 },
    });
    render(
      <ProviderConnectCards
        config={panelConfig()}
        loading={false}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    // 渠道主体标题与其子区块标题：MediaKit 配置必须落在 VolcEngine 卡容器之内。
    const subBlock = await screen.findByText("火山 AI MediaKit");
    const volcCard = screen.getByText("VolcEngine").closest("div.rounded-md");
    expect(volcCard).not.toBeNull();
    expect(within(volcCard as HTMLElement).getByText("火山 AI MediaKit")).toBeInTheDocument();

    // 顶层「渠道连接」的卡与渠道目录一一对应（AIPDD / VolcEngine / NewAPI / 三家
    // 官方 / MiniMax 两站 / ComfyUI）：
    // MediaKit 不再自成一张顶层卡，全页只出现一次子区块标题。
    expect(screen.getAllByText("火山 AI MediaKit")).toHaveLength(1);
    expect(subBlock).toBeInTheDocument();
  });

  // [aipdd fork delta] AIPDD / 火山方舟 / MediaKit 的 Base URL 都是本产品固定端点：
  // 输入框取消，只读回显常量（填错反而会把已跑通的渠道拉坏）。
  function connectedCardsConfig(): ModelGatewayConfig {
    const base = panelConfig() as unknown as Record<string, unknown>;
    const provisioner = (base.provisioner ?? {}) as Record<string, unknown>;
    const providerChannels = (provisioner.providerChannels ?? []) as unknown[];
    return {
      ...base,
      provisioner: {
        ...provisioner,
        providerChannels: [
          ...providerChannels,
          {
            provider: "volcengine",
            configured: true,
            upstreamKeyPreview: "sk-***",
            baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
          },
        ],
      },
      videoEnhance: {
        source: "database",
        apiKeyPreview: "mk-***",
        configured: true,
        baseUrl: "https://mediakit.cn-beijing.volces.com/api/v1",
        backend: "auto",
      },
    } as unknown as ModelGatewayConfig;
  }

  it("固定端点只读回显：AIPDD / VolcEngine / MediaKit 都没有 Base URL 输入框", async () => {
    render(
      <ProviderConnectCards
        config={connectedCardsConfig()}
        loading={false}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    await screen.findByText("火山 AI MediaKit");
    // 三处只读回显：AIPDD 卡、VolcEngine 卡、VolcEngine 卡内的 MediaKit 子区块。
    expect(screen.getAllByText("Base URL（固定端点，无需填写）")).toHaveLength(
      3,
    );
    expect(screen.getByText("https://api.aipdd.work/v1")).toBeInTheDocument();
    expect(
      screen.getByText("https://ark.cn-beijing.volces.com/api/v3"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("https://mediakit.cn-beijing.volces.com/api/v1"),
    ).toBeInTheDocument();
    // 只有地址因人而异的渠道保留可填 Base URL：NewAPI、MiniMax 两站，以及
    // [aipdd fork delta req48] 的 OpenAI / Claude / Gemini（官方地址只是预填值）。
    expect(screen.getAllByText("Base URL")).toHaveLength(6);
    expect(
      screen.queryByPlaceholderText("https://api.aipdd.work/v1"),
    ).not.toBeInTheDocument();
  });

  it("区块次序：上游模型在豆包语音凭证之前，保存并同步在两者之后", async () => {
    render(
      <ProviderConnectCards
        config={connectedCardsConfig()}
        loading={false}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    const volcCard = screen
      .getByText("VolcEngine")
      .closest("div.rounded-md") as HTMLElement;
    const upstream = await within(volcCard).findByText(/上游模型（/);
    const asr = within(volcCard).getByText("豆包语音识别凭证（可选）");
    const save = within(volcCard).getByRole("button", { name: /保存并同步/ });
    // 可选凭证不再插在主 Key 与模型列表之间，但仍在保存按钮上方。
    expect(
      upstream.compareDocumentPosition(asr) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      asr.compareDocumentPosition(save) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("MediaKit 子区块：保存提交固定 Base URL，清除凭证走 video-enhance clear", async () => {
    mocks.confirmDialog.mockResolvedValue(true);
    mocks.saveVideoEnhanceConfig.mockResolvedValue({
      ok: true,
      data: {
        source: "database",
        apiKeyPreview: "",
        configured: false,
        baseUrl: "https://mediakit.cn-beijing.volces.com/api/v1",
        backend: "auto",
      },
    });

    render(
      <ProviderConnectCards
        config={connectedCardsConfig()}
        loading={false}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    const mediaKitBlock = screen
      .getByText("火山 AI MediaKit")
      .closest("div.rounded-md") as HTMLElement;
    fireEvent.change(within(mediaKitBlock).getByPlaceholderText("mk-***"), {
      target: { value: "mk-new" },
    });
    fireEvent.click(
      within(mediaKitBlock).getByRole("button", { name: /保存 MediaKit 配置/ }),
    );

    await waitFor(() =>
      expect(mocks.saveVideoEnhanceConfig).toHaveBeenCalledWith({
        apiKey: "mk-new",
        baseUrl: "https://mediakit.cn-beijing.volces.com/api/v1",
      }),
    );

    fireEvent.click(
      within(mediaKitBlock).getByRole("button", { name: /清除凭证/ }),
    );

    await waitFor(() =>
      expect(mocks.saveVideoEnhanceConfig).toHaveBeenCalledWith({
        clear: true,
      }),
    );
  });
});

// [aipdd fork delta req48] OpenAI / Claude / Gemini 三张官方渠道卡：用户只做两件事
// ——粘 API Key、（可选）改 Base URL。协议转换（Anthropic messages /
// Gemini generateContent → OpenAI 格式）在网关的原生适配器里（type 1 / 14 / 24），
// 卡本身不碰协议。
describe("渠道连接：OpenAI / Claude / Gemini 官方卡", () => {
  const trio = [
    {
      title: "OpenAI",
      provider: "openai_official",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-official-openai",
    },
    {
      title: "Claude",
      provider: "anthropic",
      baseUrl: "https://api.anthropic.com/v1",
      apiKey: "sk-ant-official",
    },
    {
      title: "Gemini",
      provider: "gemini",
      baseUrl: "https://generativelanguage.googleapis.com/v1beta",
      apiKey: "AIza-official",
    },
  ];

  function renderCards() {
    return render(
      <ProviderConnectCards
        config={panelConfig()}
        loading={false}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );
  }

  function cardOf(title: string) {
    return screen.getByText(title).closest("div.rounded-md") as HTMLElement;
  }

  it("三张卡各占一张，预填各自的官方 Base URL", () => {
    renderCards();
    for (const item of trio) {
      const card = cardOf(item.title);
      // 预填值即占位提示：输入框里已经是官方地址，用户不用抄。
      const urlInput = within(card).getByPlaceholderText(item.baseUrl);
      expect(urlInput).toHaveValue(item.baseUrl);
      expect(within(card).getByText("未连接")).toBeInTheDocument();
    }
  });

  it("填 Key 保存：按各自的 provider 提交渠道行，携带 upstreamKey 与 baseUrl", async () => {
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: { channels: [] },
    });
    renderCards();

    for (const item of trio) {
      const card = cardOf(item.title);
      fireEvent.change(within(card).getByPlaceholderText("粘贴 Key"), {
        target: { value: item.apiKey },
      });
      fireEvent.click(
        within(card).getByRole("button", { name: /保存并同步/ }),
      );

      await waitFor(() => expect(mocks.saveProviderChannels).toHaveBeenCalled());
      const last = mocks.saveProviderChannels.mock.calls.length - 1;
      const payload = mocks.saveProviderChannels.mock.calls[last][0] as {
        preserveUnmentioned: boolean;
        channels: Array<Record<string, unknown>>;
      };
      expect(payload.preserveUnmentioned).toBe(true);
      expect(payload.channels).toHaveLength(1);
      expect(payload.channels[0]).toMatchObject({
        provider: item.provider,
        baseUrl: item.baseUrl,
        upstreamKey: item.apiKey,
      });
      mocks.saveProviderChannels.mockClear();
    }
  });

  it("Base URL 可改：走自建代理的用户改地址后按新地址保存（官方域名不是硬编码）", async () => {
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: { channels: [] },
    });
    renderCards();

    const proxy = "https://my-proxy.example/v1beta";
    const card = cardOf("Gemini");
    fireEvent.change(
      within(card).getByPlaceholderText(
        "https://generativelanguage.googleapis.com/v1beta",
      ),
      { target: { value: proxy } },
    );
    fireEvent.change(within(card).getByPlaceholderText("粘贴 Key"), {
      target: { value: "AIza-proxy" },
    });
    fireEvent.click(within(card).getByRole("button", { name: /保存并同步/ }));

    await waitFor(() => expect(mocks.saveProviderChannels).toHaveBeenCalled());
    const payload = mocks.saveProviderChannels.mock.calls[0][0] as {
      channels: Array<Record<string, unknown>>;
    };
    expect(payload.channels[0]).toMatchObject({
      provider: "gemini",
      baseUrl: proxy,
    });
  });
});

describe("ComfyUI 卡：保存后同步本地渠道行", () => {
  it("拉取并保存成功后本地渠道行出现 comfyui（与「供应商渠道」对应）", async () => {
    mocks.resolveComfyUIWorkflows.mockResolvedValue({
      ok: true,
      data: {
        workflowByModel: { "my-sdxl": { "1": { class_type: "KSampler" } } },
        errors: [],
      },
    });
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: {
        channels: [
          {
            provider: "comfyui",
            configured: true,
            upstreamKeyPreview: "",
            baseUrl: "http://comfy.test:8188",
          },
        ],
      },
    });

    render(
      <ComfyUILocalCard
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );

    // [req41] 卡片不再预填本机地址：未填 ComfyUI 地址时保存会被拦下，这里当成
    // 部署方自己填的地址来走完整链路。
    fireEvent.change(screen.getByPlaceholderText("http://<host>:8188"), {
      target: { value: "http://comfy.test:8188" },
    });
    fireEvent.change(screen.getByPlaceholderText("模型名（如 my-sdxl）"), {
      target: { value: "my-sdxl" },
    });
    fireEvent.change(screen.getByPlaceholderText("ComfyUI 工作流名称"), {
      target: { value: "wf-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /拉取并保存/ }));

    await waitFor(() =>
      expect(
        useSettingsStore.getState().featureModelConfig.providerChannels[
          "comfyui"
        ],
      ).toBeTruthy(),
    );
    expect(
      useSettingsStore.getState().featureModelConfig.providerChannels["comfyui"]
        .baseUrl,
    ).toBe("http://comfy.test:8188");
  });
});

describe("ComfyUI 卡：AI分析并自动配置", () => {
  function fillRows() {
    fireEvent.change(screen.getByPlaceholderText("http://<host>:8188"), {
      target: { value: "http://comfy.test:8188" },
    });
    fireEvent.change(screen.getByPlaceholderText("模型名（如 my-sdxl）"), {
      target: { value: "my-wan" },
    });
    fireEvent.change(screen.getByPlaceholderText("ComfyUI 工作流名称"), {
      target: { value: "wf-wan" },
    });
  }

  it("分析成功：适配指针与能力声明一起写进渠道，本地渠道行同步", async () => {
    mocks.adaptComfyUIWorkflows.mockResolvedValue({
      ok: true,
      data: {
        baseUrl: "http://comfy.test:8188",
        analyzerModel: "dc-text",
        workflowByModel: { "my-wan": { "12": { class_type: "KSampler" } } },
        workflowNameByModel: { "my-wan": "wf-wan" },
        typeByModel: { "my-wan": "video" },
        adaptationFile: "comfyui/adaptations/abc.json",
        adaptationPointers: {
          "my-wan": { file: "comfyui/adaptations/abc.json", entry: "my-wan" },
        },
        capabilitiesByModel: {
          "my-wan": { mediaType: "video", maxDuration: 10 },
        },
        adapted: ["my-wan"],
        errors: [],
      },
    });
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: { channels: [] },
    });

    render(
      <ComfyUILocalCard
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );
    fillRows();
    fireEvent.click(screen.getByRole("button", { name: /AI分析并自动配置/ }));

    await waitFor(() => expect(mocks.saveProviderChannels).toHaveBeenCalled());
    // 默认“跟随文本功能位”：textModel 空串交给后端三层解析
    expect(mocks.adaptComfyUIWorkflows).toHaveBeenCalledWith({
      baseUrl: "http://comfy.test:8188",
      items: [{ model: "my-wan", workflowName: "wf-wan" }],
      textModel: "",
    });
    const payload = mocks.saveProviderChannels.mock.calls[0][0];
    const comfy = payload.channels[0].settings.comfyui;
    expect(comfy.adaptation_by_model["my-wan"].file).toBe(
      "comfyui/adaptations/abc.json",
    );
    expect(comfy.capabilities_by_model["my-wan"].maxDuration).toBe(10);
    // 媒体类型以分析出的输出节点为准（而不是行里的默认值）
    expect(comfy.type_by_model["my-wan"]).toBe("video");
    expect(comfy.workflow_by_model["my-wan"]).toBeTruthy();
    await waitFor(() =>
      expect(
        useSettingsStore.getState().featureModelConfig.providerChannels["comfyui"],
      ).toBeTruthy(),
    );
    expect(toast.success).toHaveBeenCalled();
  });

  it("分析模型候选来自网关目录的 text 模型（去重），选中后按选择名提交", async () => {
    mocks.modelPlaza = {
      data: {
        models: [
          { modelName: "zz-text", capabilities: ["text"] },
          { modelName: "aa-text", capabilities: ["text"] },
          { modelName: "dup-text", capabilities: ["text", "reasoning"] },
          { modelName: "dup-text", capabilities: ["text"] },
          { modelName: "only-image", capabilities: ["image"] },
        ],
      },
    };
    mocks.adaptComfyUIWorkflows.mockResolvedValue({
      ok: true,
      data: {
        analyzerModel: "aa-text",
        workflowByModel: {},
        adaptationPointers: {},
        capabilitiesByModel: {},
        adapted: ["my-wan"],
        errors: [],
      },
    });
    mocks.saveProviderChannels.mockResolvedValue({ ok: true, data: { channels: [] } });

    const user = userEvent.setup();
    render(
      <ComfyUILocalCard
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );
    fillRows();

    // 两个下拉：行里的类型（第一个）与分析模型（第二个）
    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[1]);
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "跟随默认文本模型",
      "aa-text",
      "dup-text",
      "zz-text",
    ]);
    expect(screen.queryByRole("option", { name: "only-image" })).toBeNull();

    await user.click(screen.getByRole("option", { name: "aa-text" }));
    fireEvent.click(screen.getByRole("button", { name: /AI分析并自动配置/ }));

    await waitFor(() => expect(mocks.adaptComfyUIWorkflows).toHaveBeenCalled());
    expect(mocks.adaptComfyUIWorkflows.mock.calls[0][0].textModel).toBe("aa-text");
  });

  it("全部模型分析未通过：不写渠道，错误原文直接弹出", async () => {
    mocks.adaptComfyUIWorkflows.mockResolvedValue({
      ok: true,
      data: {
        analyzerModel: "dc-text",
        workflowByModel: {},
        adaptationPointers: {},
        capabilitiesByModel: {},
        adapted: [],
        errors: [
          {
            model: "my-wan",
            workflowName: "wf-wan",
            error: "未能确定输出节点（binding.outputs 缺失或全部越界），适配不可用",
          },
        ],
      },
    });

    render(
      <ComfyUILocalCard
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );
    fillRows();
    fireEvent.click(screen.getByRole("button", { name: /AI分析并自动配置/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // 猜出来的适配不能当真源：一个都没成时不发起渠道保存
    expect(mocks.saveProviderChannels).not.toHaveBeenCalled();
    const message = String((toast.error as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0]);
    expect(message).toContain("未能确定输出节点");
  });
});

describe("渠道目录：状态对齐（MediaKit 不占渠道行）", () => {
  const channelTypeItems = [
    {
      type: 1,
      provider: "newapi",
      name: "NewAPI",
      description: "",
      icon: "",
      defaultBaseUrl: "",
      status: 1,
      capabilities: ["text", "vision"],
      requiresBaseUrl: true,
      supportsBaseUrlOverride: true,
    },
    {
      type: 63,
      provider: "comfyui",
      name: "ComfyUI",
      description: "",
      icon: "",
      defaultBaseUrl: "http://127.0.0.1:8188",
      status: 1,
      capabilities: ["video"],
      requiresBaseUrl: false,
      supportsBaseUrlOverride: true,
    },
    {
      type: 1,
      provider: "mediakit",
      name: "火山 AI MediaKit",
      description: "火山 AI MediaKit（归属 VolcEngine 渠道）：视频高清（生成式超分）与智能去字幕",
      icon: "",
      defaultBaseUrl: "https://mediakit.cn-beijing.volces.com/api/v1",
      status: 1,
      capabilities: ["video"],
      requiresBaseUrl: true,
      supportsBaseUrlOverride: true,
    },
  ];

  function renderChannelsBlock() {
    render(
      <ProviderChannelsBlock
        savedProviderChannels={[
          {
            provider: "newapi",
            configured: true,
            upstreamKeyPreview: "sk-***",
            baseUrl: "https://newapi.example/v1",
          },
        ]}
        newApiBaseUrl="https://newapi.example/v1"
        database={undefined}
        channelTypes={channelTypeItems}
        channelTypesLoading={false}
        channelTypesLoadFailed={false}
        onRetryChannelTypes={() => {}}
      />,
    );
  }

  it("MediaKit 不在渠道目录占行：真源已配置的 NewAPI 显示已添加，未配置渠道显示添加", async () => {
    renderChannelsBlock();

    fireEvent.click(screen.getByRole("button", { name: /渠道管理/ }));
    await screen.findByText("供应商渠道管理");

    // [aipdd fork delta] MediaKit 跟随 VolcEngine 存在（凭证真源是 video-enhance
    // 配置，不进渠道表）：即使后端 channel/types 仍会注入 mediakit 品牌行，
    // 前端也要在目录里过滤掉。
    expect(screen.queryByText("火山 AI MediaKit")).not.toBeInTheDocument();
    // NewAPI（后端真源已配置、本地行未 hydrate）显示「删除」。
    expect(screen.getAllByRole("button", { name: "删除" })).toHaveLength(1);
    // ComfyUI 未配置 → 显示「添加」。
    expect(screen.getAllByRole("button", { name: "添加" })).toHaveLength(1);
  });

  it("主列表口径对齐：真源已配置渠道渲染为行", async () => {
    renderChannelsBlock();

    // 不打开「渠道管理」弹窗：真源已配置的 NewAPI 也应出现在主列表，而不是只在
    // 弹窗里显示「删除」（用户添加了渠道却看不到的回归项）。
    expect(
      screen.getByRole("button", { name: /保存渠道配置/ }),
    ).toBeInTheDocument();
    // NewAPI 主列表行（真源已配置，本地行未 hydrate）。
    expect(await screen.findAllByText("NewAPI")).not.toHaveLength(0);
    // [aipdd fork delta] MediaKit 只读行与「已连接（视频增强）」标识一并撤掉：
    // 填写 / 清除都在上方 VolcEngine 卡内的子区块。
    expect(screen.queryByText("火山 AI MediaKit")).not.toBeInTheDocument();
    expect(screen.queryByText("已连接（视频增强）")).not.toBeInTheDocument();
  });

  it("部分渠道缺上游 Key 保存：显式警告被跳过渠道，仍保存其余渠道", async () => {
    // 本地添加两个渠道：newapi 有 Key、volcengine 未填 Key（模拟用户添加了
    // 渠道但未完成 Key 配置，旧行为会静默丢弃只剩真源渠道）。
    useSettingsStore.setState({
      featureModelConfig: {
        ...DEFAULT_FEATURE_MODEL_SETTINGS,
        providerChannels: {
          newapi: {
            provider: "newapi",
            upstreamKey: "sk-real",
            baseUrl: "https://newapi.example/v1",
            priority: 0,
            settings: {},
          },
          volcengine: {
            provider: "volcengine",
            upstreamKey: "",
            baseUrl: "",
            priority: 0,
            settings: {},
          },
        },
      },
    });
    mocks.saveProviderChannels.mockResolvedValue({
      ok: true,
      data: {
        channels: [{ provider: "newapi", configured: true }],
        mediaModels: {},
        embeddingModel: null,
      },
    });

    renderChannelsBlock();

    fireEvent.click(screen.getByRole("button", { name: /保存渠道配置/ }));

    await waitFor(() =>
      expect(mocks.saveProviderChannels).toHaveBeenCalled(),
    );
    // 只保存有 Key 的 newapi。
    const payload = mocks.saveProviderChannels.mock.calls[0][0] as {
      channels: Array<{ provider: string }>;
    };
    expect(payload.channels.map((channel) => channel.provider)).toEqual([
      "newapi",
    ]);
    // 缺 Key 的 volcengine 必须显式警告（显示名 VolcEngine），不再静默丢弃。
    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining("VolcEngine"),
    );
  });
});