// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FreezoneMotionCase, FreezoneMotionStyle } from "@/api/ops";
import { MotionGalleryModal } from "@/features/canvas/ui/MotionGalleryModal";

// 取提示词是两个图册唯一的后端动作（拼法由后端 compose_motion_prompt /
// compose_motion_style_prompt 各定一次），其余全是纯渲染。
const fetchPrompt = vi.fn();
const fetchStylePrompt = vi.fn();
vi.mock("@/api/ops", () => ({
  fetchFreezoneMotionPrompt: (...args: unknown[]) =>
    fetchPrompt(...(args as [string, string])),
  fetchFreezoneMotionStylePrompt: (...args: unknown[]) =>
    fetchStylePrompt(...(args as [string, string])),
}));

function makeCase(id: string, label: string, category: string): FreezoneMotionCase {
  return {
    id,
    label,
    category,
    brief: `${label} 在做什么`,
    tags: ["一镜到底", "文档驱动"],
    prompt_cn: `${label}的分镜规格第一行\n${label}的分镜规格第二行`,
    cover: `${id}/poster.webp`,
    posterReady: true,
    videoReady: true,
    resolution: "1920x1080",
    durationSeconds: 29.4,
    author: "@twoclipping",
    mediaFile: `${id}_产品原片.mp4`,
    mediaBytes: 14503825,
  };
}

const CASES: FreezoneMotionCase[] = [
  makeCase("M43", "苹果风产品宣传片", "产品宣传片"),
  { ...makeCase("N22", "波形科普动画", "科普动画"), durationSeconds: 12.3 },
];

function makeStyle(id: string, label: string, category: string): FreezoneMotionStyle {
  return {
    brief: `${label}的观感约束`,
    category,
    id,
    label,
    labelEn: `${label} EN`,
    palette: ["#101820", "#f7f4ea", "#d5a43c"],
    prompt_cn: `${label}的风格规格第一行\n${label}的风格规格第二行`,
    source: "参考名录 #12",
    tags: ["留白", "手绘质感"],
  };
}

const STYLES: FreezoneMotionStyle[] = [
  makeStyle("ink-wash", "水墨", "东方传统"),
  makeStyle("swiss-grid", "瑞士网格", "MG 动态设计"),
];

function renderModal(overrides: Partial<ComponentProps<typeof MotionGalleryModal>> = {}) {
  return render(
    <MotionGalleryModal
      applyLabelKey="node.videoOps.motionApply"
      assetBase=""
      cases={CASES}
      categories={["产品宣传片", "科普动画"]}
      isLoading={false}
      methods={[
        {
          id: "beat-grid",
          label: "文档驱动统一节拍",
          summary: "每一拍都有变化",
          where: "分镜时长",
        },
      ]}
      project="p1"
      renderContract="window.render(t) 必须确定性返回一帧"
      onApply={vi.fn()}
      onClose={vi.fn()}
      styleCategories={["东方传统", "MG 动态设计"]}
      styleLoading={false}
      styles={STYLES}
      {...overrides}
    />,
  );
}

describe("MotionGalleryModal", () => {
  beforeEach(() => {
    // 取提示词的 mock 是模块级的：不清掉的话「没二次请求」这类断言会被上一个用例染色。
    vi.clearAllMocks();
    fetchPrompt.mockResolvedValue("完整提示词");
    fetchStylePrompt.mockResolvedValue("完整风格提示词");
  });

  it("renders one card per case with the same-origin poster url", () => {
    renderModal();

    expect(screen.getByText("苹果风产品宣传片")).toBeInTheDocument();
    expect(screen.getByAltText("苹果风产品宣传片 的动效海报")).toHaveAttribute(
      "src",
      "/api/v1/motion-gallery/M43/poster.webp",
    );
    expect(screen.getByText("29 秒")).toBeInTheDocument();
  });

  it("prefixes posters with the configured asset base", () => {
    renderModal({ assetBase: "https://cdn.example.com/motion/" });

    expect(screen.getByAltText("波形科普动画 的动效海报")).toHaveAttribute(
      "src",
      "https://cdn.example.com/motion/N22/poster.webp",
    );
  });

  it("falls back to a placeholder block when the bundled poster is missing", () => {
    const noPoster = CASES.map((item) => ({ ...item, posterReady: false }));
    renderModal({ cases: noPoster });

    expect(screen.queryByAltText("苹果风产品宣传片 的动效海报")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "苹果风产品宣传片 的动效海报" })).toBeInTheDocument();
  });

  it("filters by the category order given by the backend", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "科普动画" }));

    expect(screen.queryByText("苹果风产品宣传片")).not.toBeInTheDocument();
    expect(screen.getByText("波形科普动画")).toBeInTheDocument();
  });

  it("copies the composed prompt fetched from the backend, not the raw spec", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    fetchPrompt.mockResolvedValue("分镜规格\n\n渲染契约");
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    fireEvent.click(screen.getByRole("button", { name: "复制提示词" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("分镜规格\n\n渲染契约"));
    expect(fetchPrompt).toHaveBeenCalledWith("p1", "M43");
  });

  it("applies the prompt to the host target and closes", async () => {
    fetchPrompt.mockResolvedValue("完整提示词");
    const onApply = vi.fn();
    const onClose = vi.fn();
    renderModal({ onApply, onClose });

    fireEvent.click(screen.getByRole("button", { name: "查看波形科普动画的动效规格" }));
    fireEvent.click(screen.getByRole("button", { name: "填进当前节点" }));

    await waitFor(() =>
      expect(onApply).toHaveBeenCalledWith("完整提示词", {
        category: "科普动画",
        id: "N22",
        kind: "case",
        label: "波形科普动画",
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("previews the same text that copy hands out, not the raw manifest spec", async () => {
    // 正文与「复制/填入」必须同源：图册显示的是服务端拼装（含提示词库覆盖值）的那条。
    fetchPrompt.mockResolvedValue("分镜规格第一行\n\n渲染契约");
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));

    await waitFor(() => expect(fetchPrompt).toHaveBeenCalledWith("p1", "M43"));
    expect(await screen.findByText(/渲染契约/)).toBeInTheDocument();
  });

  it("falls back to the manifest spec while the prompt is unavailable", () => {
    // 取不到（没项目上下文）时正文留在清单的分镜规格上，不出现空白详情。
    renderModal({ project: null });

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));

    expect(screen.getByText(/分镜规格第一行/)).toBeInTheDocument();
    expect(fetchPrompt).not.toHaveBeenCalled();
  });

  it("plays the original with a percent-encoded file name", async () => {
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "播放原片" }));

    const video = document.querySelector("video");
    expect(video?.getAttribute("src")).toContain(
      `/api/v1/motion-gallery/M43/${encodeURIComponent("M43_产品原片.mp4")}`,
    );
  });

  it("hides playback and says why when this case's original is unreachable", async () => {
    // 逐条判定：全局「缓存过任意一条」不能替一条没下载的案例决定按钮，否则点了只剩 404。
    renderModal({ cases: CASES.map((item) => ({ ...item, videoReady: false })) });

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalled());

    expect(screen.queryByRole("button", { name: "播放原片" })).not.toBeInTheDocument();
    expect(
      screen.getByText("原片不随包发布，配好素材镜像后才能播放"),
    ).toBeInTheDocument();
  });

  it("shows playback only for the case whose original is actually reachable", async () => {
    // 缓存了 M43、N22 还没下：只有 M43 给按钮。
    renderModal({
      cases: CASES.map((item) => ({ ...item, videoReady: item.id === "M43" })),
    });

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "播放原片" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "查看波形科普动画的动效规格" }));
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalledWith("p1", "N22"));
    expect(screen.queryByRole("button", { name: "播放原片" })).not.toBeInTheDocument();
    expect(
      screen.getByText("原片不随包发布，配好素材镜像后才能播放"),
    ).toBeInTheDocument();
  });

  it("shows the method cards and the render contract on the other tab", () => {
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "做法" }));

    expect(screen.getByText("文档驱动统一节拍")).toBeInTheDocument();
    expect(screen.getByText("在本系统里落在：分镜时长")).toBeInTheDocument();
    expect(screen.getByText("本系统渲染契约")).toBeInTheDocument();
    expect(
      screen.getByText("window.render(t) 必须确定性返回一帧"),
    ).toBeInTheDocument();
  });

  it("explains a failed methods list instead of showing a blank tab", () => {
    // 做法页以前只有 methods.map：清单接口挂了整页空白，看起来像这台机器没配数据。
    const onRetry = vi.fn();
    renderModal({ error: new Error("409"), methods: [], onRetry, renderContract: "" });

    fireEvent.click(screen.getByRole("button", { name: "做法" }));

    expect(
      screen.getByText("动效案例清单加载失败，请确认后端服务可用后重新加载"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("labels an empty methods list as methods, not as no cases", () => {
    renderModal({ methods: [], renderContract: "" });

    fireEvent.click(screen.getByRole("button", { name: "做法" }));

    expect(screen.getByText("没有取到动效做法，可点下面的重新加载")).toBeInTheDocument();
    expect(
      screen.queryByText("没有取到动效案例，可点下面的重新加载"),
    ).not.toBeInTheDocument();
  });

  it("walks back from detail to the grid before closing on Escape", async () => {
    const onClose = vi.fn();
    renderModal({ onClose });

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalled());
    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "做法" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("offers a retry instead of a dead end when the catalogue came back empty", () => {
    const onRetry = vi.fn();
    renderModal({ cases: [], onRetry });

    expect(screen.getByText("没有取到动效案例，可点下面的重新加载")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("says the load failed, not that there are no cases, when the request errored", () => {
    // 需求要的是能把两条出路分开：接口挂了就说挂并让人重试，别让人以为清单是空的。
    const onRetry = vi.fn();
    renderModal({ cases: [], error: new Error("502 Bad Gateway"), onRetry });

    expect(
      screen.getByText("动效案例清单加载失败，请确认后端服务可用后重新加载"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("没有取到动效案例，可点下面的重新加载"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("hands the selected case to the host render flow without waiting on the prompt", async () => {
    const onRenderCase = vi.fn();
    const onClose = vi.fn();
    renderModal({ onRenderCase, onClose });

    fireEvent.click(screen.getByRole("button", { name: "查看波形科普动画的动效规格" }));
    fireEvent.click(screen.getByRole("button", { name: "按这条案例渲染" }));

    expect(onRenderCase).toHaveBeenCalledWith(CASES[1]);
    expect(onClose).toHaveBeenCalled();

    // 渲染是同步交出去的（上面两条断言即证明）；这里只是让详情里的取词落定。
    await waitFor(() => expect(fetchPrompt).toHaveBeenCalledWith("p1", "N22"));
  });

  it("hides the render action when the host cannot render", async () => {
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await screen.findByRole("button", { name: "填进当前节点" });

    expect(screen.queryByRole("button", { name: "按这条案例渲染" })).not.toBeInTheDocument();
  });

  it("draws a style card from its own palette, with no bundled asset request", () => {
    // 画风名录一张上游素材都不随包：样张由清单里的色板等宽画出，顺序照搬清单。
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    const swatch = screen.getByRole("img", { name: "水墨 的色板" });
    expect(swatch).toBeInTheDocument();
    expect(swatch.querySelector("img")).toBeNull();
    expect(swatch.children).toHaveLength(3);
    expect(swatch.children[2]).toHaveStyle({ background: "#d5a43c" });
    expect(screen.getByText("瑞士网格")).toBeInTheDocument();
  });

  it("filters the style tab by its own categories, not the case ones", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    // 换页签必须退回「全部」并换成画风那 11 个分类：带着案例的分类过来会筛成空白。
    expect(screen.queryByRole("button", { name: "产品宣传片" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "MG 动态设计" }));

    expect(screen.queryByText("水墨")).not.toBeInTheDocument();
    expect(screen.getByText("瑞士网格")).toBeInTheDocument();
  });

  it("copies the style prompt from the style endpoint and hands the host a style source", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const onApply = vi.fn();
    renderModal({ onApply });
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    fireEvent.click(screen.getByRole("button", { name: "查看水墨的动效规格" }));
    await waitFor(() => expect(fetchStylePrompt).toHaveBeenCalledWith("p1", "ink-wash"));
    expect(fetchPrompt).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "复制提示词" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("完整风格提示词"));

    fireEvent.click(screen.getByRole("button", { name: "填进当前节点" }));
    await waitFor(() =>
      expect(onApply).toHaveBeenCalledWith("完整风格提示词", {
        category: "东方传统",
        id: "ink-wash",
        kind: "style",
        label: "水墨",
      }),
    );
  });

  it("renders a chosen style through the host and never offers playback", async () => {
    const onRenderStyle = vi.fn();
    const onRenderCase = vi.fn();
    renderModal({ onRenderCase, onRenderStyle });
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    fireEvent.click(screen.getByRole("button", { name: "查看瑞士网格的动效规格" }));
    await screen.findByRole("button", { name: "按这条画风渲染" });

    // 画风名录没有原片：按钮不给，也不留「原片取不到」的误导说明。
    expect(screen.queryByRole("button", { name: "播放原片" })).not.toBeInTheDocument();
    expect(screen.queryByText("原片不随包发布，配好素材镜像后才能播放")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "按这条画风渲染" }));
    expect(onRenderStyle).toHaveBeenCalledWith(STYLES[1]);
    expect(onRenderCase).not.toHaveBeenCalled();
  });

  it("keeps the case render button off a style and the style one off a case", async () => {
    renderModal({ onRenderCase: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    fireEvent.click(screen.getByRole("button", { name: "查看水墨的动效规格" }));
    await screen.findByRole("button", { name: "填进当前节点" });
    // 宿主只接了案例渲染：画风这条不该冒出一个点了没反应的按钮。
    expect(screen.queryByRole("button", { name: "按这条画风渲染" })).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "案例" }));
    fireEvent.click(screen.getByRole("button", { name: "查看苹果风产品宣传片的动效规格" }));
    await screen.findByRole("button", { name: "按这条案例渲染" });
  });

  it("says the style list failed instead of borrowing the case wording", () => {
    // 每个页签说自己那份：画风清单挂了不能说成「案例清单加载失败」。
    const onStyleRetry = vi.fn();
    renderModal({ onStyleRetry, styleError: new Error("502 Bad Gateway"), styles: [] });
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    expect(
      screen.getByText("动效画风清单加载失败，请确认后端服务可用后重新加载"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("没有取到动效画风，可点下面的重新加载"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onStyleRetry).toHaveBeenCalled();
  });

  it("offers a retry for an empty style list", () => {
    const onStyleRetry = vi.fn();
    renderModal({ onStyleRetry, styles: [] });
    fireEvent.click(screen.getByRole("button", { name: "画风" }));

    expect(screen.getByText("没有取到动效画风，可点下面的重新加载")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onStyleRetry).toHaveBeenCalled();

    // 案例那一份名录不受影响：换回案例页签还是原来的两条。
    fireEvent.click(screen.getByRole("button", { name: "案例" }));
    expect(screen.getByText("苹果风产品宣传片")).toBeInTheDocument();
  });
});
