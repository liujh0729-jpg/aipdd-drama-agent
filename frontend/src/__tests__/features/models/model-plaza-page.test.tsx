// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] WP-E 模型广场：渠道展示名必须走 FORK_PROVIDER_LABELS 映射
// （openai→OpenAI、aipdd→AIPDD）——不能把内部 provider id 原样抄给用户。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelPlazaPage } from "@/features/models/ModelPlazaPage";

const plazaState = vi.hoisted(() => ({
  current: undefined as Record<string, unknown> | undefined,
}));

vi.mock("@/lib/queries/model-plaza", () => ({
  useModelPlaza: () => ({
    data: plazaState.current,
    isPending: false,
    isError: false,
    error: null,
  }),
  useTestPlazaModel: () => ({ mutate: vi.fn(), isPending: false, variables: undefined }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// t 直接回 key：断言结构与 key，不依赖语言包内容。
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function makeModel(overrides: Record<string, unknown>) {
  return {
    id: "gateway:x",
    displayName: "X",
    connector: "gateway",
    invokeKind: "async-video-task",
    capabilities: ["video"],
    modelName: "x",
    provider: "openai",
    channelName: "",
    upstreamModel: "x",
    vendor: "",
    baseUrl: "",
    endpoint: "",
    status: "mapped",
    inUseBy: [],
    sortOrder: 0,
    note: "",
    apiKeyPreview: "",
    examples: {},
    testable: false,
    ...overrides,
  };
}

function setPlaza(models: Record<string, unknown>[]) {
  plazaState.current = {
    data: {
      connectors: [],
      capabilities: ["text", "image", "video"],
      models,
      gateway: { mode: "custom", publicBaseUrl: "", apiKeyPreview: "", configured: true },
      aliasHealth: {},
    },
  };
}

describe("ModelPlazaPage fork channel labels", () => {
  it("shows OpenAI / AIPDD instead of the raw provider ids on model cards", () => {
    setPlaza([
      makeModel({ id: "gateway:AP Seedance-2.0 标准版", displayName: "AP Seedance-2.0 标准版" }),
      makeModel({
        id: "gateway:DC-hermes-LLM",
        displayName: "DC-hermes-LLM",
        provider: "aipdd",
        channelName: "AIPDD",
        capabilities: ["text"],
        invokeKind: "openai-chat",
      }),
    ]);

    render(<ModelPlazaPage />);

    // 后端未给 channelName 时前端兜底映射；后端已映射时原样展示。
    expect(screen.getAllByTitle("OpenAI").some((node) => node.textContent === "OpenAI")).toBe(
      true,
    );
    expect(screen.getAllByText("AIPDD").length).toBeGreaterThan(0);
  });

  it("maps the channel filter labels while keeping raw provider values", () => {
    setPlaza([
      makeModel({ id: "gateway:a", displayName: "A", provider: "openai", channelName: "OpenAI" }),
      makeModel({ id: "gateway:b", displayName: "B", provider: "aipdd", channelName: "AIPDD" }),
    ]);

    render(<ModelPlazaPage />);

    const select = screen.getByLabelText("models.plaza.filter.channel") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      "models.plaza.filter.allChannels",
      "AIPDD",
      "OpenAI",
    ]);
    // 过滤值必须保持原始 provider id，否则筛选会失效。
    expect(Array.from(select.options).map((option) => option.value)).toEqual([
      "",
      "aipdd",
      "openai",
    ]);
  });

  it("maps fallbackProvider through the same table", () => {
    setPlaza([
      makeModel({
        id: "gateway:c",
        displayName: "C",
        provider: "openai",
        channelName: "OpenAI",
        fallbackProvider: "aipdd",
      }),
    ]);

    render(<ModelPlazaPage />);

    expect(screen.getByTitle("AIPDD")).toBeInTheDocument();
  });

  // [aipdd fork delta] env 名（SCREENPLAY_NORMALIZER_MODEL 等）是业务标识：
  // 卡片标题必须是模型名（方便调用），env 只能作为「功能位」辅助展示。
  it("shows the model name as the title and the env as the feature slot for env-override cards", () => {
    setPlaza([
      makeModel({
        id: "env-override:SCREENPLAY_NORMALIZER_MODEL",
        displayName: "DC-screenplay-normalizer-LLM",
        modelName: "DC-screenplay-normalizer-LLM",
        connector: "env-override",
        invokeKind: "openai-chat",
        capabilities: ["text"],
        provider: "aipdd",
        channelName: "AIPDD",
        env: "SCREENPLAY_NORMALIZER_MODEL",
        inUseBy: ["SCREENPLAY_NORMALIZER_MODEL"],
        note: "perEnv",
      }),
    ]);

    render(<ModelPlazaPage />);

    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
      "DC-screenplay-normalizer-LLM",
    );
    // 功能位行：label 走 i18n key，值是 env 业务标识。
    expect(screen.getByText("models.plaza.field.feature")).toBeInTheDocument();
    expect(screen.getByText("SCREENPLAY_NORMALIZER_MODEL")).toBeInTheDocument();
  });

  it("matches env-override cards when searching by the feature-slot env name", () => {
    setPlaza([
      makeModel({
        id: "env-override:STYLE_ANALYZER_MODEL",
        displayName: "DC-style-analyzer-LLM",
        modelName: "DC-style-analyzer-LLM",
        connector: "env-override",
        invokeKind: "openai-chat",
        capabilities: ["text"],
        env: "STYLE_ANALYZER_MODEL",
        inUseBy: ["STYLE_ANALYZER_MODEL"],
      }),
      makeModel({ id: "gateway:other", displayName: "OtherModel" }),
    ]);

    render(<ModelPlazaPage />);

    fireEvent.change(screen.getByPlaceholderText("models.plaza.search.placeholder"), {
      target: { value: "STYLE_ANALYZER_MODEL" },
    });

    // displayName 在标题与调用名两处渲染，用标题断言唯一性。
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
      "DC-style-analyzer-LLM",
    );
    expect(screen.queryByText("OtherModel")).not.toBeInTheDocument();
  });

  // [aipdd fork delta] req33 §6.3：后台实测判过不可用的名字要在卡上说得出口，
  // 但「没测过 / 拿不准」绝不能被画成不行——只有一档红标。
  it("marks only probe-rejected cards unusable and leaves the rest untouched", () => {
    setPlaza([
      makeModel({
        id: "gateway:aipdd::glm-4.6",
        displayName: "glm-4.6",
        provider: "aipdd",
        capabilities: ["text"],
        invokeKind: "openai-chat",
        probeState: "not-granted",
        probeUnusable: true,
      }),
      makeModel({
        id: "gateway:ark::doubao-vision",
        displayName: "doubao-vision",
        provider: "ark",
        capabilities: ["vision", "text"],
        invokeKind: "openai-chat",
        probeState: "unprobed",
        probeUnusable: false,
      }),
      makeModel({
        id: "gateway:ark::seed-1.6",
        displayName: "seed-1.6",
        provider: "ark",
        capabilities: ["text"],
        invokeKind: "openai-chat",
        probeState: "unknown",
        probeUnusable: false,
      }),
    ]);

    render(<ModelPlazaPage />);

    expect(screen.getByText("models.plaza.probe.not-granted")).toBeInTheDocument();
    expect(screen.getAllByText(/^models\.plaza\.probe\./)).toHaveLength(1);
  });
});