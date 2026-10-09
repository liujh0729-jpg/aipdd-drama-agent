// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 资产树定向测试：文件夹分组、素材拖拽载荷、点击直引、
// 剪映草稿不可引用、文件夹折叠与空态。freezone 取数整体桩掉——只验证交互契约。
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssetTreePanel } from "@/features/agent/components/AssetTreePanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const hoisted = vi.hoisted(() => ({
  items: [] as unknown[],
}));

vi.mock("@/lib/queries/freezone", () => ({
  useFreezoneAssetLibrary: () => ({ data: hoisted.items, isLoading: false }),
  useFreezoneAssetLibraryFolders: () => ({ data: [], isLoading: false }),
}));

const HERO = {
  id: "i1",
  name: "hero.png",
  media: "image",
  source: "upload",
  category: "other",
  folder: "other",
  url: "https://cdn.example/hero.png",
  raw: {},
};

const CLIP = {
  id: "i2",
  name: "clip.mp4",
  media: "video",
  source: "upload",
  category: "other",
  folder: "other",
  url: "https://cdn.example/clip.mp4",
  raw: {},
};

const DRAFT = {
  id: "i3",
  name: "draft-pack",
  media: "draft",
  source: "upload",
  category: "draft",
  folder: "draft",
  url: "",
  raw: { draft_id: "d1" },
};

describe("AssetTreePanel", () => {
  beforeEach(() => {
    hoisted.items = [];
  });

  it("renders folders with their items", () => {
    hoisted.items = [HERO, CLIP, DRAFT];
    render(<AssetTreePanel project="alpha" onAddReference={vi.fn()} />);

    expect(screen.getByText("hero.png")).toBeInTheDocument();
    expect(screen.getByText("clip.mp4")).toBeInTheDocument();
    expect(screen.getByText("draft-pack")).toBeInTheDocument();
    // 文件夹名走词条 key（mock t 直接回显）。
    expect(screen.getByText("canvas.assetLibrary.uncategorized")).toBeInTheDocument();
    expect(screen.getByText("canvas.assetLibrary.category.draft")).toBeInTheDocument();
  });

  it("starts a drag with the reference payload", () => {
    hoisted.items = [HERO];
    render(<AssetTreePanel project="alpha" onAddReference={vi.fn()} />);

    const setData = vi.fn();
    const row = screen.getByText("hero.png").closest('[draggable="true"]');
    expect(row).not.toBeNull();
    fireEvent.dragStart(row!, { dataTransfer: { setData, effectAllowed: "" } });

    expect(setData).toHaveBeenCalledWith(
      "application/x-supertale-agent-reference",
      JSON.stringify({
        name: "hero.png",
        url: "https://cdn.example/hero.png",
        media: "image",
      }),
    );
  });

  it("adds a reference on click", async () => {
    hoisted.items = [CLIP];
    const onAddReference = vi.fn();
    render(<AssetTreePanel project="alpha" onAddReference={onAddReference} />);
    const user = userEvent.setup();

    await user.click(screen.getByText("clip.mp4"));

    expect(onAddReference).toHaveBeenCalledWith({
      name: "clip.mp4",
      url: "https://cdn.example/clip.mp4",
      media: "video",
    });
  });

  it("keeps draft rows out of the reference flow", () => {
    hoisted.items = [DRAFT];
    const onAddReference = vi.fn();
    render(<AssetTreePanel project="alpha" onAddReference={onAddReference} />);

    const row = screen.getByText("draft-pack").closest("div");
    expect(row?.getAttribute("draggable")).not.toBe("true");
    expect(row?.getAttribute("aria-disabled")).toBe("true");

    // 即便强行派发 dragStart，也不会写出拖拽载荷。
    const setData = vi.fn();
    fireEvent.dragStart(screen.getByText("draft-pack"), {
      dataTransfer: { setData, effectAllowed: "" },
    });
    expect(setData).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("draft-pack"));
    expect(onAddReference).not.toHaveBeenCalled();
  });

  it("collapses a folder on header click", async () => {
    hoisted.items = [HERO];
    render(<AssetTreePanel project="alpha" onAddReference={vi.fn()} />);
    const user = userEvent.setup();

    expect(screen.getByText("hero.png")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /canvas\.assetLibrary\.uncategorized/ }),
    );
    expect(screen.queryByText("hero.png")).toBeNull();
  });

  it("shows the empty state when the library has no items", () => {
    render(<AssetTreePanel project="alpha" onAddReference={vi.fn()} />);

    expect(screen.getByText("agent.assets.empty")).toBeInTheDocument();
  });
});