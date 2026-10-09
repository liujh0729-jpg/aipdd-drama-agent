// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { providersSupportingCapability } from "@/components/settings/settings-dialog";
import type { NewApiChannelType } from "@/lib/queries/model-gateway";
import { FEATURE_MODEL_GROUPS, FEATURE_MODEL_PRODUCT_GROUPS } from "@/lib/feature-models";
import {
  DEFAULT_FEATURE_MODEL_SETTINGS,
  normalizeMediaModelEntries,
  useSettingsStore,
} from "@/stores/settingsStore";

describe("Custom settings feature model visibility", () => {
  it("includes character extraction among the displayed text model rows", () => {
    const textFeatures = FEATURE_MODEL_PRODUCT_GROUPS.flatMap((group) =>
      group.features.filter((feature) => !feature.requiresVision && feature.id !== "COGNEE"),
    );
    expect(textFeatures).toContainEqual({
      id: "CHARACTER_BUILD",
      defaultModel: "DC-character-builder-LLM",
    });
  });

  it("exposes every registered feature except the separately configured Cognee model", () => {
    const displayedIds = FEATURE_MODEL_PRODUCT_GROUPS.flatMap((group) =>
      group.features.map((feature) => feature.id),
    );
    const configurableIds = FEATURE_MODEL_GROUPS.flatMap((group) =>
      group.features.filter((feature) => feature.id !== "COGNEE").map((feature) => feature.id),
    );
    expect([...displayedIds].sort()).toEqual([...configurableIds].sort());
  });
});

function channelType(
  provider: string,
  capabilities: string[],
): NewApiChannelType {
  return {
    type: 1,
    provider,
    name: provider,
    description: "",
    icon: "",
    defaultBaseUrl: "",
    status: 1,
    capabilities,
    requiresBaseUrl: false,
    supportsBaseUrlOverride: true,
  };
}

describe("provider capability filtering", () => {
  const configuredProviders = ["openrouter", "volcengine", "comfyui", "legacy"];
  const channelTypes = new Map<string, NewApiChannelType>([
    ["openrouter", channelType("openrouter", ["text", "vision"])],
    ["volcengine", channelType("volcengine", ["video"])],
    ["comfyui", channelType("comfyui", ["image", "video", "audio"])],
  ]);

  it("only returns providers supporting the requested capability", () => {
    expect(
      providersSupportingCapability(configuredProviders, channelTypes, "text"),
    ).toEqual(["openrouter", "legacy"]);
    expect(
      providersSupportingCapability(configuredProviders, channelTypes, "video"),
    ).toEqual(["volcengine", "comfyui", "legacy"]);
  });

  it("keeps providers without channel metadata for backward compatibility", () => {
    expect(
      providersSupportingCapability(
        configuredProviders,
        channelTypes,
        "embedding",
      ),
    ).toEqual(["legacy"]);
  });

  it("keeps an existing incompatible provider visible for correction", () => {
    expect(
      providersSupportingCapability(
        configuredProviders,
        channelTypes,
        "video",
        "openrouter",
      ),
    ).toEqual(["openrouter", "volcengine", "comfyui", "legacy"]);
  });
});

describe("settingsStore feature model configuration", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      featureModelConfig: DEFAULT_FEATURE_MODEL_SETTINGS,
      featureModelConfigBackendSnapshotKey: "",
    });
  });

  it("hydrates one authoritative backend snapshot", () => {
    useSettingsStore.getState().hydrateFeatureModelConfigFromBackend(
      {
        providerChannels: {
          openrouter: {
            provider: "openrouter",
            upstreamKey: "",
            baseUrl: "https://openrouter.ai/api/v1",
            priority: 1,
            settings: {},
          },
        },
        providerKeys: {},
        mediaModels: {
          "image-model": {
            provider: "openrouter",
            upstreamModel: "upstream-image-model",
          },
        },
        embeddingModel: {
          provider: "openrouter",
          upstreamModel: "embedding-model",
          dimension: 1024,
        },
      },
      "backend-snapshot-1",
    );

    const state = useSettingsStore.getState();
    expect(state.featureModelConfigBackendSnapshotKey).toBe(
      "backend-snapshot-1",
    );
    expect(state.featureModelConfig.providerChannels).toHaveProperty(
      "openrouter",
    );
    expect(state.featureModelConfig.mediaModels).toHaveProperty("image-model");
    expect(state.featureModelConfig.embeddingModel?.upstreamModel).toBe(
      "embedding-model",
    );
  });

  it("keeps the consumed backend snapshot after a local provider deletion", () => {
    const store = useSettingsStore.getState();
    store.hydrateFeatureModelConfigFromBackend(
      {
        providerChannels: {
          openrouter: {
            provider: "openrouter",
            upstreamKey: "",
            baseUrl: "https://openrouter.ai/api/v1",
            priority: 0,
            settings: {},
          },
        },
        providerKeys: {},
        mediaModels: {},
        embeddingModel: {
          provider: "openrouter",
          upstreamModel: "embedding-model",
          dimension: 1024,
        },
      },
      "backend-snapshot-1",
    );

    useSettingsStore.getState().removeFeatureProviderChannel("openrouter");

    const state = useSettingsStore.getState();
    expect(state.featureModelConfigBackendSnapshotKey).toBe(
      "backend-snapshot-1",
    );
    expect(state.featureModelConfig.providerChannels).toEqual({});
  });

  it("persists only feature mappings from the model configuration draft", () => {
    const store = useSettingsStore.getState();
    store.updateFeatureModel("feature-1", {
      provider: "openrouter",
      model: "text-model",
    });
    store.updateFeatureProviderChannel("openrouter", {
      upstreamKey: "sk-secret",
      baseUrl: "https://openrouter.ai/api/v1",
    });
    store.setMediaModels({
      "image-model": {
        provider: "openrouter",
        upstreamModel: "upstream-image-model",
      },
    });
    store.setEmbeddingModel({
      provider: "openrouter",
      upstreamModel: "embedding-model",
      dimension: 1024,
    });

    const partialize = useSettingsStore.persist.getOptions().partialize;
    expect(partialize).toBeTypeOf("function");
    const persisted = partialize?.(useSettingsStore.getState()) as Record<
      string,
      unknown
    >;

    expect(persisted).not.toHaveProperty(
      "featureModelConfigBackendSnapshotKey",
    );
    const featureModelConfig = persisted.featureModelConfig as {
      featureModels: Record<string, unknown>;
      providerChannels: Record<string, unknown>;
      providerKeys: Record<string, unknown>;
      mediaModels: Record<string, unknown>;
      embeddingModel?: unknown;
    };
    expect(featureModelConfig.featureModels).toEqual({
      "feature-1": { provider: "openrouter", model: "text-model" },
    });
    expect(featureModelConfig.providerChannels).toEqual({});
    expect(featureModelConfig.providerKeys).toEqual({});
    expect(featureModelConfig.mediaModels).toEqual({});
    expect(featureModelConfig.embeddingModel).toBeUndefined();
  });

  it("normalizes backend media model snapshots before comparison", () => {
    expect(
      normalizeMediaModelEntries({
        " seedream-5.0-lite ": {
          provider: " VolcEngine ",
          upstreamModel: " doubao-seedream-5-0-lite ",
          mediaType: "image",
          label: " Seedream 5.0 Lite ",
          enabled: true,
          sortOrder: 20,
          config: {},
        },
        "empty-label": {
          provider: "openrouter",
          upstreamModel: "image-model",
          label: "   ",
        },
      }),
    ).toEqual({
      "seedream-5.0-lite": {
        provider: "volcengine",
        upstreamModel: "doubao-seedream-5-0-lite",
        mediaType: "image",
        label: "Seedream 5.0 Lite",
        enabled: true,
        sortOrder: 20,
        config: {},
      },
      "empty-label": {
        provider: "openrouter",
        upstreamModel: "image-model",
        enabled: true,
        sortOrder: 100,
        config: {},
      },
    });
  });
});

