// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  ASSET_CATEGORIES,
  deriveCategory,
  topdownSceneOf,
  type LibraryItem,
} from "@/features/canvas/ui/assetLibraryItems";

function fakeEntry(overrides: Partial<LibraryItem>): LibraryItem {
  return {
    id: "topdown-1",
    name: "客厅走位",
    media: "image",
    source: "upload",
    category: "topdown",
    folder: "topdown",
    url: "/static/topdown-preview.png",
    raw: {},
    ...overrides,
  } as LibraryItem;
}

describe("asset library topdown category", () => {
  it("registers a topdown category that only accepts images", () => {
    const def = ASSET_CATEGORIES.find((c) => c.key === "topdown");
    expect(def).toBeDefined();
    expect(def?.media).toEqual(["image"]);
    expect(def?.labelKey).toBe("canvas.assetLibrary.category.topdown");
  });

  it("keeps an explicit topdown category through deriveCategory", () => {
    expect(deriveCategory("topdown", "upload", "image")).toBe("topdown");
  });
});

// [aipdd fork delta] 「故事版」类目：分镜脚本页 AI 生成的多格分镜板参考图。
describe("asset library storyVersion category", () => {
  it("registers a storyVersion category that only accepts images", () => {
    const def = ASSET_CATEGORIES.find((c) => c.key === "storyVersion");
    expect(def).toBeDefined();
    expect(def?.media).toEqual(["image"]);
    expect(def?.labelKey).toBe("canvas.assetLibrary.category.storyVersion");
  });

  it("keeps an explicit storyVersion category through deriveCategory", () => {
    expect(deriveCategory("storyVersion", "upload", "image")).toBe("storyVersion");
  });
});

describe("topdownSceneOf", () => {
  it("returns the inlined editable scene for a topdown entry", () => {
    const scene = { version: 1, size: 1280, items: [] };
    const entry = fakeEntry({ raw: { scene } });
    expect(topdownSceneOf(entry)).toBe(scene);
  });

  it("returns undefined for non-topdown entries even if a stray scene exists", () => {
    const entry = fakeEntry({
      category: "other",
      folder: "other",
      raw: { scene: { version: 1 } },
    });
    expect(topdownSceneOf(entry)).toBeUndefined();
  });

  it("returns undefined when the topdown entry carries no scene", () => {
    expect(topdownSceneOf(fakeEntry({ raw: {} }))).toBeUndefined();
  });
});
