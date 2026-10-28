// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FreezoneStyleTemplate } from "@/api/ops";
import {
  StyleGalleryModal,
  resolveStyleSelectionState,
} from "@/features/canvas/ui/StyleGalleryModal";

// 图墙现在内嵌封面管理（上传 / AI 生成 / 删除），这些 hook 真调用需要 react-query
// Provider 与后端，测试里统一换成可断言的替身；role 开关用来覆盖 owner 与非 owner
// 两条渲染路径。
const mocks = vi.hoisted(() => ({
  uploadCoverMutate: vi.fn(),
  generateCoverMutate: vi.fn(),
  deleteCoverMutate: vi.fn(),
  bustCover: vi.fn(),
  role: "owner" as string,
  galleryMeta: undefined as
    | { data: Array<{ id: string; has_cover: boolean }> }
    | undefined,
  // AI 生成封面的图片模型目录：两条不同渠道，用来断言「出处胶囊」随选择切换。
  imageModels: [
    {
      id: "aipdd/ap-image-2",
      providerId: "aipdd",
      apiModel: "ap-image-2.0",
      label: "AP Image 2.0",
      channel: "aipdd",
    },
    {
      id: "newapi/seedream-4",
      providerId: "newapi",
      apiModel: "seedream-4",
      label: "Seedream 4",
      channel: "newapi",
    },
  ],
}));

vi.mock("@/lib/queries/style-gallery", () => ({
  useUploadStyleGalleryCover: () => ({
    mutate: mocks.uploadCoverMutate,
    isPending: false,
    variables: undefined,
  }),
  useGenerateStyleGalleryCover: () => ({
    mutate: mocks.generateCoverMutate,
    isPending: false,
    variables: undefined,
  }),
  useDeleteStyleGalleryCover: () => ({
    mutate: mocks.deleteCoverMutate,
    isPending: false,
    variables: undefined,
  }),
  useStyleGalleryStyles: () => ({ data: mocks.galleryMeta }),
  useStyleGalleryImageModels: () => ({
    data: mocks.imageModels,
    isLoading: false,
  }),
}));

vi.mock("@/features/canvas/hooks/useFreezoneStyleTemplates", () => ({
  bustStyleTemplateCover: (...args: unknown[]) => mocks.bustCover(...args),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { role: string }) => unknown) =>
    selector({ role: mocks.role }),
}));

function makeTemplate(
  id: string,
  label: string,
  category: string,
): FreezoneStyleTemplate {
  return {
    id,
    label,
    category,
    cover: `${id}/cover.webp`,
    samples: [
      `${id}/female.webp`,
      `${id}/youth.webp`,
      `${id}/male.webp`,
      `${id}/elder.webp`,
    ],
    style_prompt: `${label}的提示词第一行\n${label}的提示词第二行`,
  };
}

const TEMPLATES: FreezoneStyleTemplate[] = [
  makeTemplate("golden_age", "黄金时代", "年代"),
  makeTemplate("wuxia", "武侠江湖", "古装"),
];

describe("StyleGalleryModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "owner";
    mocks.galleryMeta = undefined;
    // 「上次所选模型」记忆与画布共用一个 localStorage 键，测试间必须清掉，
    // 否则上一个用例的写入会改变下一个用例的默认模型。
    window.localStorage.clear();
  });

  it("renders one card per template with the bundled cover url", () => {
    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("黄金时代")).toBeInTheDocument();
    expect(screen.getByText("武侠江湖")).toBeInTheDocument();
    expect(screen.getByAltText("黄金时代")).toHaveAttribute(
      "src",
      "/style-gallery/golden_age/cover.webp",
    );
  });

  it("prefixes covers with the configured asset base", () => {
    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase="https://cdn.example.com/styles"
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByAltText("黄金时代")).toHaveAttribute(
      "src",
      "https://cdn.example.com/styles/golden_age/cover.webp",
    );
  });

  it("matches the fixed-card poster hover behavior", () => {
    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const cover = screen.getByAltText("黄金时代");
    const card = cover.closest("button")?.parentElement;
    expect(cover).toHaveClass("origin-top", "group-hover:scale-[1.02]");
    expect(card).toHaveClass("transition-[border-color,box-shadow]");
    expect(card?.className).not.toContain("translate-y");
  });

  it("card body opens the detail view instead of selecting outright", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));

    // 一张封面判断不了这套风格怎么处理不同人物,所以点卡片是「先看看」而不是「就它了」。
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByAltText("黄金时代 示例 1")).toBeInTheDocument();
  });

  it("the always-visible 使用 button selects without a detour", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    // 快路径不能藏在 hover 后面 —— 键盘用户 Tab 得到它,才算这个入口存在。
    const quickUse = screen.getByRole("button", { name: "使用黄金时代" });
    expect(quickUse.className).not.toContain("opacity-0");

    await user.click(quickUse);

    expect(onSelect).toHaveBeenCalledWith("golden_age");
  });

  it("clears the selection and only offers clearing while something is selected", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    const { rerender } = render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "清除风格" })).toBeNull();

    rerender(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId="golden_age"
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "清除风格" }));

    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("opens the detail view with all four samples and the full prompt", async () => {
    const user = userEvent.setup();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));

    expect(screen.getByAltText("黄金时代 示例 1")).toHaveAttribute(
      "src",
      "/style-gallery/golden_age/female.webp",
    );
    expect(screen.getByAltText("黄金时代 示例 4")).toHaveAttribute(
      "src",
      "/style-gallery/golden_age/elder.webp",
    );
    expect(
      screen.getByText(/黄金时代的提示词第一行/),
    ).toHaveTextContent("黄金时代的提示词第二行");
  });

  it("fits the detail dialog height to its content", async () => {
    const user = userEvent.setup();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "风格图墙" });
    expect(dialog).toHaveClass("h-[min(720px,82vh)]");

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));

    expect(dialog).not.toHaveClass("h-[min(720px,82vh)]");
    expect(dialog).toHaveClass("max-h-[82vh]");
  });

  it("uses the style from the detail view", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看武侠江湖详情" }));
    await user.click(screen.getByRole("button", { name: "使用" }));

    expect(onSelect).toHaveBeenCalledWith("wuxia");
  });

  it("narrows the grid to one category and back", async () => {
    const user = userEvent.setup();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "古装" }));

    expect(
      screen.getByRole("button", { name: "查看武侠江湖详情" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看黄金时代详情" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "全部" }));

    expect(
      screen.getByRole("button", { name: "查看黄金时代详情" }),
    ).toBeInTheDocument();
  });

  it("shows a placeholder instead of an empty grid", () => {
    const { rerender } = render(
      <StyleGalleryModal
        templates={[]}
        assetBase=""
        selectedId={null}
        isLoading
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("加载中…")).toBeInTheDocument();

    rerender(
      <StyleGalleryModal
        templates={[]}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("暂无风格模板")).toBeInTheDocument();
  });

  it("returns from the detail view to the gallery", async () => {
    const user = userEvent.setup();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));
    expect(screen.queryByRole("button", { name: "查看武侠江湖详情" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "返回" }));

    expect(
      screen.getByRole("button", { name: "查看武侠江湖详情" }),
    ).toBeInTheDocument();
  });

  it("owner 可以就地在图墙里给某个风格换封面", async () => {
    const user = userEvent.setup();
    mocks.galleryMeta = { data: [{ id: "golden_age", has_cover: true }] };

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        project="demo"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    // 卡片上的快捷入口不必先进详情——键盘可达靠 aria-label，不只是 hover。
    await user.click(
      screen.getByRole("button", { name: "为「黄金时代」上传封面" }),
    );

    const file = new File(["image"], "cover.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传封面"), {
      target: { files: [file] },
    });

    expect(mocks.uploadCoverMutate).toHaveBeenCalledWith(
      { styleId: "golden_age", file },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    // 成功回调必须把图墙缓存里这一条的封面地址换新——否则浏览器缓存的旧图
    // 会继续显示最多 5 分钟。
    const options = mocks.uploadCoverMutate.mock.calls[0]![1] as {
      onSuccess: () => void;
    };
    options.onSuccess();
    expect(mocks.bustCover).toHaveBeenCalledWith("demo", "golden_age");
  });

  it("详情页的生成 / 删除只作用于当前风格，且删除仅在已有封面时出现", async () => {
    const user = userEvent.setup();
    mocks.galleryMeta = { data: [{ id: "golden_age", has_cover: true }] };

    const view = render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        project="demo"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));
    // 详情页里的「上传封面」是带文案的按钮，和隐藏 input 的 aria-label 不同名。
    expect(screen.getByText("上传封面")).toBeInTheDocument();

    await user.click(screen.getByText("AI 生成封面"));
    // 没有「上次所选」记录时默认落到目录首项；模型 id 随请求提交给后端解析。
    expect(mocks.generateCoverMutate).toHaveBeenCalledWith(
      { styleId: "golden_age", model: "aipdd/ap-image-2" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    // 这条风格已有封面 → 提供删除；没有封面时不该给一个注定 404 的按钮。
    await user.click(screen.getByRole("button", { name: "删除封面" }));
    expect(mocks.deleteCoverMutate).toHaveBeenCalledWith(
      "golden_age",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    mocks.galleryMeta = undefined;
    view.rerender(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        project="demo"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "删除封面" })).toBeNull();
  });

  it("生成前可切换模型：触发器带渠道胶囊，选择被记忆并随请求提交", async () => {
    const user = userEvent.setup();

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        project="demo"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));

    // 默认落到目录首项：触发器上同时有模型名和渠道出处胶囊。
    const trigger = screen.getByRole("combobox", { name: "生成模型" });
    expect(trigger).toHaveTextContent("AP Image 2.0");
    expect(trigger).toHaveTextContent("AIPDD");

    // 展开弹层，切到另一条渠道的模型（候选项同样带出处胶囊）。
    await user.click(trigger);
    await user.click(screen.getByRole("option", { name: /Seedream 4/ }));

    expect(trigger).toHaveTextContent("Seedream 4");
    expect(trigger).toHaveTextContent("LocalAPI");

    await user.click(screen.getByText("AI 生成封面"));
    expect(mocks.generateCoverMutate).toHaveBeenCalledWith(
      { styleId: "golden_age", model: "newapi/seedream-4" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    // 「上次所选」记忆与画布图片节点共用同一个键。
    expect(window.localStorage.getItem("canvas.lastImageModel")).toBe(
      "newapi/seedream-4",
    );
  });

  it("非 owner 看不到任何封面管理入口", async () => {
    const user = userEvent.setup();
    mocks.role = "member";

    render(
      <StyleGalleryModal
        templates={TEMPLATES}
        assetBase=""
        selectedId={null}
        project="demo"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "为「黄金时代」上传封面" }),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看黄金时代详情" }));
    expect(screen.queryByText("上传封面")).toBeNull();
    expect(screen.queryByText("AI 生成封面")).toBeNull();
  });
});

describe("resolveStyleSelectionState", () => {
  const template = TEMPLATES[0];

  it("没选就是没选", () => {
    expect(
      resolveStyleSelectionState(null, null, {
        isLoading: false,
        hasError: false,
      }),
    ).toBe("none");
  });

  it("选了且查得到才是 ready", () => {
    expect(
      resolveStyleSelectionState("golden_age", template, {
        isLoading: false,
        hasError: false,
      }),
    ).toBe("ready");
  });

  // 下面三种都会让 describeStyleSelection 返回 null，但成因完全不同，
  // UI 不能一律显示成「未选择风格」—— 那个 id 还在跟着生成请求走。
  it("清单还在路上是 loading", () => {
    expect(
      resolveStyleSelectionState("golden_age", null, {
        isLoading: true,
        hasError: false,
      }),
    ).toBe("loading");
  });

  it("清单拉取失败是 failed", () => {
    expect(
      resolveStyleSelectionState("golden_age", null, {
        isLoading: false,
        hasError: true,
      }),
    ).toBe("failed");
  });

  it("清单到了但 id 不在里面是 missing", () => {
    expect(
      resolveStyleSelectionState("three_oclock_2300", null, {
        isLoading: false,
        hasError: false,
      }),
    ).toBe("missing");
  });
});
