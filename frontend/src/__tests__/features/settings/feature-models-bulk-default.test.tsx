// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] t14 回归：业务模型映射的「应用到全部」= 设为该区默认（全局兜底）
// 并清空各行单独配置（语义 β）。三个场景：
// 1) 历史收编：旧版把同一模型钉死在每个功能行，挂载时自动折算为该区默认并清空；
// 2) 应用到全部：交互后落「区默认」标记、行回到未单独配置态（占位符提示默认模型）；
// 3) 保存映射：区默认折算为别名真源（仅未单独配置的功能），单独配置行走渠道批量。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  FeatureModelCapabilitySection,
  FeatureModelsBlock,
} from "@/features/settings/model-gateway/feature-models-block";
import {
  splitFeatureModelGroups,
  TEXT_DEFAULT_KEY,
  VISION_DEFAULT_KEY,
} from "@/features/settings/model-gateway/shared";
import {
  FEATURE_MODEL_GROUPS,
  FEATURE_MODEL_PRODUCT_GROUPS,
} from "@/lib/feature-models";
import type { SavedProviderChannelConfig } from "@/lib/queries/model-gateway";
import {
  DEFAULT_FEATURE_MODEL_SETTINGS,
  useSettingsStore,
} from "@/stores/settingsStore";
import { toast } from "sonner";

const mocks = vi.hoisted(() => ({
  fetchUpstreamModels: vi.fn(),
  saveCustomChannel: vi.fn(),
  saveCustomChannelsBatch: vi.fn(),
  saveEmbeddingModel: vi.fn(),
  saveModelAliasMappings: vi.fn(),
  saveProviderChannels: vi.fn(),
  clearComfyUIConfig: vi.fn(),
  syncProviderChannel: vi.fn(),
  saveMediaModels: vi.fn(),
  saveVideoEnhanceConfig: vi.fn(),
  // [aipdd fork delta req40] 渠道目录默认空表（没同步过）。
  providerCatalog: { ok: true, data: { providers: [], meta: {} } },
  syncProviderCatalog: vi.fn(),
  aipddChannelType: {
    type: 9001,
    provider: "aipdd",
    name: "AIPDD",
    description: "AIPDD 官方渠道",
    icon: "",
    defaultBaseUrl: "https://aipdd.example/v1",
    status: 1,
    capabilities: ["text", "vision", "embedding"],
    requiresBaseUrl: false,
    supportsBaseUrlOverride: true,
  },
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
  useNewApiChannelTypes: () => ({
    data: { ok: true, data: { items: [mocks.aipddChannelType] } },
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useSaveCustomChannel: () => ({
    mutateAsync: mocks.saveCustomChannel,
    isPending: false,
  }),
  useSaveCustomChannelsBatch: () => ({
    mutateAsync: mocks.saveCustomChannelsBatch,
    isPending: false,
  }),
  useSaveEmbeddingModel: () => ({
    mutateAsync: mocks.saveEmbeddingModel,
    isPending: false,
  }),
  useSaveModelAliasMappings: () => ({
    mutateAsync: mocks.saveModelAliasMappings,
    isPending: false,
  }),
  useSaveProviderChannels: () => ({
    mutateAsync: mocks.saveProviderChannels,
    isPending: false,
  }),
  useClearComfyUIConfig: () => ({
    mutateAsync: mocks.clearComfyUIConfig,
    isPending: false,
  }),
  // [aipdd fork delta] t11：渠道目录的 mediakit 行「移除」走 video-enhance clear。
  useSaveVideoEnhanceConfig: () => ({
    mutateAsync: mocks.saveVideoEnhanceConfig,
    isPending: false,
  }),
  useSyncProviderChannel: () => ({
    mutateAsync: mocks.syncProviderChannel,
    isPending: false,
  }),
  useSaveMediaModels: () => ({
    mutateAsync: mocks.saveMediaModels,
    isPending: false,
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

const DEFAULT_MODEL = "ap-doubao-seed-2.1-turbo";

// 区块内可见行（产品分组口径，文本区不含 COGNEE）。
const TEXT_GROUPS = splitFeatureModelGroups(
  FEATURE_MODEL_PRODUCT_GROUPS,
  (feature) => !feature.requiresVision && feature.id !== "COGNEE",
);
const VISION_GROUPS = splitFeatureModelGroups(FEATURE_MODEL_PRODUCT_GROUPS, (feature) =>
  Boolean(feature.requiresVision),
);
const TEXT_ROW_IDS = TEXT_GROUPS.flatMap((group) =>
  group.features.map((feature) => feature.id),
);
const VISION_ROW_IDS = VISION_GROUPS.flatMap((group) =>
  group.features.map((feature) => feature.id),
);
// 区默认的功能覆盖面（FEATURE_MODEL_GROUPS 口径，文本含 COGNEE）。
const TEXT_FEATURE_IDS = FEATURE_MODEL_GROUPS.flatMap((group) => group.features)
  .filter((feature) => !feature.requiresVision)
  .map((feature) => feature.id);

function aipddChannel(configured = true): SavedProviderChannelConfig {
  return {
    provider: "aipdd",
    configured,
    upstreamKeyPreview: configured ? "sk-***" : "",
    baseUrl: "https://aipdd.example/v1",
  };
}

function aipddProviderChannel() {
  return {
    provider: "aipdd",
    upstreamKey: "sk-test",
    baseUrl: "",
    priority: 0,
    settings: {},
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  useSettingsStore.setState({
    featureModelConfig: DEFAULT_FEATURE_MODEL_SETTINGS,
  });
  mocks.fetchUpstreamModels.mockResolvedValue({
    ok: true,
    data: { models: [DEFAULT_MODEL, "ap-glm-5.2"], count: 2 },
  });
  mocks.saveModelAliasMappings.mockResolvedValue({
    ok: true,
    data: { mappings: {} },
  });
  mocks.saveCustomChannelsBatch.mockResolvedValue({
    ok: true,
    data: { succeeded: 1, failed: 0 },
  });
});

describe("业务模型映射：区默认语义（t14）", () => {
  it("历史数据收编：整区钉死同一模型时，挂载即折算为该区默认并清空各行", async () => {
    const uniform = { provider: "aipdd", model: DEFAULT_MODEL };
    const seed: Record<string, { provider: string; model: string }> = {};
    for (const group of FEATURE_MODEL_GROUPS) {
      for (const feature of group.features) seed[feature.id] = { ...uniform };
    }
    useSettingsStore.setState({
      featureModelConfig: {
        ...DEFAULT_FEATURE_MODEL_SETTINGS,
        featureModels: seed,
        providerChannels: { aipdd: aipddProviderChannel() },
      },
    });

    render(
      <FeatureModelsBlock
        newApiBaseUrl="https://newapi.example/v1"
        database={undefined}
        savedProviderChannels={[aipddChannel()]}
        savedEmbeddingModel={undefined}
        savedMediaModels={{}}
        backendSnapshotLoaded
      />,
    );

    await waitFor(() => {
      const fm = useSettingsStore.getState().featureModelConfig.featureModels;
      expect(fm[TEXT_DEFAULT_KEY]).toEqual(uniform);
    });
    const fm = useSettingsStore.getState().featureModelConfig.featureModels;
    expect(fm[VISION_DEFAULT_KEY]).toEqual(uniform);
    // 所有功能行都回到「未单独配置」态：除两个区默认标记外不留任何行。
    expect(Object.keys(fm).sort()).toEqual(
      [TEXT_DEFAULT_KEY, VISION_DEFAULT_KEY].sort(),
    );
    for (const id of [...TEXT_ROW_IDS, ...VISION_ROW_IDS, "COGNEE"]) {
      expect(fm[id], `残留行: ${id}`).toBeUndefined();
    }
  });

  it("应用到全部：设该区默认 + 清空各行，行占位符提示默认模型", async () => {
    useSettingsStore.setState({
      featureModelConfig: {
        ...DEFAULT_FEATURE_MODEL_SETTINGS,
        featureModels: {
          HERMES: { provider: "aipdd", model: "ap-old-model" },
          FREEZONE_TRANSLATION: { provider: "aipdd", model: "ap-old-model" },
        },
        providerChannels: { aipdd: aipddProviderChannel() },
      },
    });
    const user = userEvent.setup();

    render(
      <FeatureModelCapabilitySection
        title="文本理解与生成"
        groups={TEXT_GROUPS}
        newApiBaseUrl="https://newapi.example/v1"
        database={undefined}
        configuredProviders={["aipdd"]}
        channelTypeByProvider={new Map([["aipdd", mocks.aipddChannelType]])}
        capability="text"
        providerChannels={{ aipdd: aipddProviderChannel() }}
        savedChannelByProvider={new Map([["aipdd", aipddChannel()]])}
      />,
    );

    await waitFor(() =>
      // [aipdd fork delta req35/req40] 候选按区的能力拉：文本区明确带 kind=text。
      expect(mocks.fetchUpstreamModels).toHaveBeenCalledWith({
        provider: "aipdd",
        kind: "text",
      }),
    );

    await user.click(
      screen.getByRole("combobox", { name: "文本理解与生成 · 上游模型名" }),
    );
    await user.click(await screen.findByRole("option", { name: DEFAULT_MODEL }));
    await user.click(screen.getByRole("button", { name: "应用到全部" }));

    const fm = useSettingsStore.getState().featureModelConfig.featureModels;
    // 「应用到全部」不再钉死各行：只落区默认标记，行全部清空回到兜底态。
    expect(fm[TEXT_DEFAULT_KEY]).toEqual({
      provider: "aipdd",
      model: DEFAULT_MODEL,
    });
    expect(fm["HERMES"]).toBeUndefined();
    expect(fm["FREEZONE_TRANSLATION"]).toBeUndefined();
    expect(toast.success).toHaveBeenCalledWith(
      `已将 ${DEFAULT_MODEL} 设为该区默认，并清空 ${TEXT_ROW_IDS.length} 个功能的单独配置；请点击保存映射写入 LocalNewAPI。`,
    );

    // 展开「剧本」分组：行回到未单独配置态，占位符提示该区默认模型。
    await user.click(screen.getByRole("button", { name: "剧本" }));
    const placeholders = await screen.findAllByText(`默认（${DEFAULT_MODEL}）`);
    expect(placeholders.length).toBeGreaterThanOrEqual(1);
  });

  it("保存映射：区默认折算为别名真源，单独配置行随渠道批量写入", async () => {
    useSettingsStore.setState({
      featureModelConfig: {
        ...DEFAULT_FEATURE_MODEL_SETTINGS,
        featureModels: {
          [TEXT_DEFAULT_KEY]: { provider: "aipdd", model: DEFAULT_MODEL },
          HERMES: { provider: "aipdd", model: "ap-glm-5.2" },
        },
        providerChannels: { aipdd: aipddProviderChannel() },
      },
    });
    const user = userEvent.setup();

    render(
      <FeatureModelsBlock
        newApiBaseUrl="https://newapi.example/v1"
        database={undefined}
        savedProviderChannels={[aipddChannel()]}
        savedEmbeddingModel={undefined}
        savedMediaModels={{}}
        backendSnapshotLoaded
      />,
    );

    await user.click(screen.getByRole("button", { name: "保存映射" }));

    await waitFor(() =>
      expect(mocks.saveModelAliasMappings).toHaveBeenCalledTimes(1),
    );
    const aliasPayload = mocks.saveModelAliasMappings.mock.calls[0][0] as {
      newApiBaseUrl: string;
      mappings: Record<
        string,
        { provider: string; target: string; capability?: string }
      >;
    };
    expect(aliasPayload.newApiBaseUrl).toBe("https://newapi.example/v1");
    // 文本区除 HERMES（单独配置）外的全部功能折算为默认条目，COGNEE 一并覆盖。
    expect(Object.keys(aliasPayload.mappings)).toHaveLength(
      TEXT_FEATURE_IDS.length - 1,
    );
    expect(aliasPayload.mappings["DC-freezone-translator-LLM"]).toEqual({
      provider: "aipdd",
      target: DEFAULT_MODEL,
      capability: "text",
    });
    expect(aliasPayload.mappings["DC-cognee-LLM"]).toEqual({
      provider: "aipdd",
      target: DEFAULT_MODEL,
      capability: "text",
    });
    expect(aliasPayload.mappings["DC-hermes-LLM"]).toBeUndefined();
    expect(
      Object.values(aliasPayload.mappings).every(
        (mapping) => mapping.capability === "text",
      ),
    ).toBe(true);

    await waitFor(() =>
      expect(mocks.saveCustomChannelsBatch).toHaveBeenCalledTimes(1),
    );
    const batchPayload = mocks.saveCustomChannelsBatch.mock.calls[0][0] as {
      newApiBaseUrl: string;
      channels: Array<Record<string, unknown>>;
    };
    expect(batchPayload.channels).toHaveLength(1);
    expect(batchPayload.channels[0]).toMatchObject({
      provider: "aipdd",
      upstreamKey: "sk-test",
      modelMapping: { "DC-hermes-LLM": "ap-glm-5.2" },
    });
    // 先落别名真源（区默认），再写渠道批量（单独配置行）。
    expect(mocks.saveModelAliasMappings.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.saveCustomChannelsBatch.mock.invocationCallOrder[0],
    );
    expect(toast.success).toHaveBeenCalledWith("已保存 1 个渠道。");
  });
});