// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [openspec catalog-driven-model-settings 7] 目录驱动候选测试，覆盖 req40 的三条口径：
// 1) 候选只来自渠道目录：目录里没有的名字不进下拉，也不许凭空生成（`catalogCandidates`
//    在无目录时返回空表，而不是回落到任何硬编码名）；
// 2) 失效槽位提示：同步成功后后端返回的 `pruned`（因下架被自动取消的默认绑定）必须
//    原样播报给用户，让「重选默认」这件事有明确入口；
// 3) 无自由文本模型输入：「添加模型」弹窗里模型名是目录下拉，既没有输入框，目录为空
//    时整体禁用 —— 手输框正是「渠道已下架的模型还能配出来」的入口。
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DefaultGenerationModelsPanel } from "@/features/settings/model-gateway/default-generation-models-panel";
import {
  ProviderCatalogBlockSection,
  catalogCandidates,
  catalogEntryKind,
} from "@/features/settings/model-gateway/provider-catalog-block";
import type {
  ModelGatewayConfig,
  ProviderCatalogBlock,
  ProviderCatalogResult,
} from "@/lib/queries/model-gateway";
import {
  DEFAULT_FEATURE_MODEL_SETTINGS,
  useSettingsStore,
} from "@/stores/settingsStore";
import { toast } from "sonner";

const mocks = vi.hoisted(() => ({
  fetchUpstreamModels: vi.fn(),
  saveMediaModelMappings: vi.fn(),
  saveProviderChannels: vi.fn(),
  saveCustomChannelsBatch: vi.fn(),
  saveModelAliasMappings: vi.fn(),
  saveEmbeddingModel: vi.fn(),
  checkAliasMappingHealth: vi.fn(),
  resolveComfyUIWorkflows: vi.fn(),
  saveVideoEnhanceConfig: vi.fn(),
  clearComfyUIConfig: vi.fn(),
  syncProviderChannel: vi.fn(),
  // 类型标注只给下面的用例改写 providers 用（运行时被擦除，不影响 hoisting）。
  providerCatalog: {
    ok: true,
    data: { providers: [], meta: {} },
  } as unknown as { ok: boolean; data: ProviderCatalogResult },

  // `mutate` 直接回调 onSuccess：只验「目录同步结果如何影响提示」，不验 react-query 本身。
  syncProviderCatalog: vi.fn((input: any, handlers: any) =>
    handlers?.onSuccess?.({
      ok: true,
      data: { provider: input?.provider, pruned: { "DC-hermes": ["ap-dead-model"] } },
    }),
  ),
}));

vi.mock("@/lib/queries/model-gateway", () => ({
  useProviderCatalog: () => ({ data: mocks.providerCatalog, isLoading: false }),
  useSyncProviderCatalog: () => ({
    mutate: mocks.syncProviderCatalog,
    mutateAsync: mocks.syncProviderCatalog,
    isPending: false,
  }),
  useFetchUpstreamModels: () => ({
    mutateAsync: mocks.fetchUpstreamModels,
    mutate: mocks.fetchUpstreamModels,
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
    mutate: mocks.checkAliasMappingHealth,
    isPending: false,
  }),
  useResolveComfyUIWorkflows: () => ({
    mutateAsync: mocks.resolveComfyUIWorkflows,
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

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof Element.prototype.getAnimations !== "function") {
  Element.prototype.getAnimations = () => [];
}

function providerChannel(provider: string) {
  return {
    provider,
    configured: true,
    upstreamKeyPreview: "sk-***",
    baseUrl: `${provider}.example/v1`,
  };
}

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
      providerChannels: [providerChannel("aipdd"), providerChannel("newapi")],
      mediaModels: {},
      aliasMappings: {},
      aliasChannelRoles: {},
    },
  } as unknown as ModelGatewayConfig;
}

// 目录 fixture：`ap-declared-image` / `ap-declared-video` 有渠道声明的 kind，
// `ap-inferred-image` 是渠道没给元数据、只能按名字推断的条目。
const CATALOG: ProviderCatalogBlock[] = [
  {
    provider: "aipdd",
    baseUrl: "https://aipdd.example/v1",
    fetchedAt: "2026-10-06 03:00:00",
    entries: [
      {
        name: "ap-declared-image",
        kind: "image",
        endpoints: ["/v1/images/generations"],
        inferred: false,
      },
      { name: "ap-declared-video", kind: "video", inferred: false },
      {
        name: "ap-embedding",
        kind: "embedding",
        dimensions: 1536,
        inferred: false,
      },
      { name: "ap-inferred-image", mediaType: "image", inferred: true },
    ],
  },
  {
    provider: "newapi",
    baseUrl: "https://newapi.example/v1",
    fetchedAt: "",
    entries: [{ name: "np-declared-image", kind: "image", inferred: false }],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  mocks.providerCatalog = { ok: true, data: { providers: [], meta: {} } };
  useSettingsStore.setState({
    featureModelConfig: DEFAULT_FEATURE_MODEL_SETTINGS,
  });
  mocks.fetchUpstreamModels.mockResolvedValue({
    ok: true,
    data: { models: [], count: 0 },
  });
  mocks.saveMediaModelMappings.mockResolvedValue({ ok: true });
});

describe("目录候选：唯一数据源是渠道目录", () => {
  it("条目归类优先渠道声明的 kind，其次推断出的 mediaType", () => {
    expect(catalogEntryKind({ name: "x", kind: "Vision" })).toBe("vision");
    expect(catalogEntryKind({ name: "x", mediaType: "embedding" })).toBe(
      "embedding",
    );
    // mediaType=other 与完全没有元数据都算未分类，不混进任何能力下拉。
    expect(catalogEntryKind({ name: "x", mediaType: "other" })).toBe("other");
    expect(catalogEntryKind({ name: "x" })).toBe("other");
  });

  it("候选只取指定渠道 + 指定类型的目录条目，去重并排序", () => {
    expect(
      catalogCandidates(CATALOG, "aipdd", ["image"]).map((entry) => entry.name),
    ).toEqual(["ap-declared-image", "ap-inferred-image"]);
    // 渠道之间不串味：newapi 的条目不会因为「同为图片」混进 aipdd 下拉。
    expect(
      catalogCandidates(CATALOG, "aipdd", ["image"]).map((entry) => entry.name),
    ).not.toContain("np-declared-image");
    expect(
      catalogCandidates(CATALOG, "newapi", ["image"]).map((entry) => entry.name),
    ).toEqual(["np-declared-image"]);
  });

  it("provider 不匹配 / 目录为空时返回空表，而不是兜底任何模型名", () => {
    expect(catalogCandidates(CATALOG, "volcengine", ["image"])).toEqual([]);
    expect(catalogCandidates([], "aipdd", ["image", "video"])).toEqual([]);
    expect(
      catalogCandidates([{ ...CATALOG[0], entries: [] }], "aipdd", ["image"]),
    ).toEqual([]);
  });

  it("同一条目在多渠道重复出现时只留一行（下拉不出现重复 key）", () => {
    const duplicated: ProviderCatalogBlock[] = [
      CATALOG[0],
      { ...CATALOG[1], entries: [{ name: "ap-declared-image", kind: "image" }] },
    ];
    expect(
      catalogCandidates(duplicated, "", ["image"]).map((entry) => entry.name),
    ).toEqual(["ap-declared-image", "ap-inferred-image"]);
  });
});

describe("渠道目录块：同步与失效绑定提示", () => {
  it("目录块区分「渠道声明」与「按名字推断」，并回显维度与端点", async () => {
    mocks.providerCatalog = {
      ok: true,
      data: { providers: CATALOG, meta: {} },
    };
    render(<ProviderCatalogBlockSection />);

    const declared = await screen.findByText("ap-declared-image");
    expect(declared).toBeInTheDocument();
    expect(screen.getAllByText("渠道声明").length).toBeGreaterThan(0);
    expect(screen.getByText("按名字推断")).toBeInTheDocument();
    expect(screen.getByText("1536 维")).toBeInTheDocument();
    expect(screen.getByText("/v1/images/generations")).toBeInTheDocument();
    // 上次同步时间 / 从未同步两种状态都要能区分（用户得知道目录有多新）。
    expect(screen.getByText(/2026-10-06 03:00:00/)).toBeInTheDocument();
    // 从未同步过的渠道：文案与 BaseURL 同段落拼接，用正则匹配整段。
    expect(
      screen.getByText(/还没有成功同步过 · https:\/\/newapi\.example\/v1/),
    ).toBeInTheDocument();
  });

  it("同步成功后播报「因下架自动取消」的默认绑定，指向需要重选的槽位", async () => {
    mocks.providerCatalog = {
      ok: true,
      data: { providers: [CATALOG[0]], meta: {} },
    };
    const user = userEvent.setup();
    render(<ProviderCatalogBlockSection />);

    await screen.findByText("ap-declared-image");
    await user.click(
      screen.getByRole("button", { name: "同步模型清单" }),
    );

    await waitFor(() => expect(mocks.syncProviderCatalog).toHaveBeenCalled());
    expect(mocks.syncProviderCatalog.mock.calls[0][0]).toEqual({
      provider: "aipdd",
      force: true,
    });
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("DC-hermes → ap-dead-model"),
    );
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("因下架自动取消"),
    );
  });

  it("目录为空时给出「还没同步过」的提示，而不是渲染空表让用户以为渠道没模型", () => {
    mocks.providerCatalog = { ok: true, data: { providers: [], meta: {} } };
    render(<ProviderCatalogBlockSection />);
    expect(
      screen.getByText("还没有同步过任何渠道的模型清单，先在渠道上点「同步模型清单」。"),
    ).toBeInTheDocument();
  });
});

describe("添加模型弹窗：模型名只能从目录里选", () => {
  async function openAddDialog(user: ReturnType<typeof userEvent.setup>) {
    render(
      <DefaultGenerationModelsPanel
        config={panelConfig()}
        newApiBaseUrl="https://newapi.example/v1"
      />,
    );
    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    return screen.findByRole("dialog");
  }

  it("有目录：下拉只列该渠道该类型的条目，选中后按目录名落库", async () => {
    mocks.providerCatalog = {
      ok: true,
      data: { providers: CATALOG, meta: {} },
    };
    const user = userEvent.setup();
    const dialog = await openAddDialog(user);

    // 三个下拉：模型 / 类型 / 渠道（模型在第一位）。
    const comboboxes = within(dialog).getAllByRole("combobox");
    expect(comboboxes).toHaveLength(3);
    // 自由文本框已废除 —— 目录之外的手输名就是死映射的源头。
    expect(within(dialog).queryAllByRole("textbox")).toHaveLength(0);

    await user.click(comboboxes[0]);
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "ap-declared-image",
      // 推断条目仍可选项，但带角标提示「能力为推断」。
      "ap-inferred-image *",
    ]);
    expect(
      screen.queryByRole("option", { name: "np-declared-image" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("option", { name: "ap-declared-image" }));
    await user.click(within(dialog).getByRole("button", { name: "添加到列表" }));

    await waitFor(() =>
      expect(mocks.saveMediaModelMappings).toHaveBeenCalledWith(
        expect.objectContaining({
          models: expect.objectContaining({
            "ap-declared-image": expect.objectContaining({
              provider: "aipdd",
              upstreamModel: "ap-declared-image",
              mediaType: "image",
            }),
          }),
        }),
      ),
    );
  });

  it("没目录：模型下拉与「添加到列表」都禁用，并提示先去同步清单", async () => {
    const user = userEvent.setup();
    const dialog = await openAddDialog(user);

    const [modelTrigger] = within(dialog).getAllByRole("combobox");
    expect(modelTrigger).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "添加到列表" })).toBeDisabled();
    // 同一句话出现在两处：下拉的占位符 + 弹窗底部的引导文案。
    expect(
      within(dialog).getAllByText("这个渠道还没有同步过模型清单").length,
    ).toBeGreaterThan(1);
    expect(within(dialog).queryByRole("option")).not.toBeInTheDocument();
  });
});
