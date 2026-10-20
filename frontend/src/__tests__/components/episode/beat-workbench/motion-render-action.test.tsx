// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MotionRenderAction } from "@/components/episode/beat-workbench/motion-render-action";
import type { Beat } from "@/types/episode";

// mock 工厂会被提升到模块顶部，所以这些共享替身必须走 vi.hoisted 才能在里面引用。
const submit = vi.hoisted(() => vi.fn());
const fetchResult = vi.hoisted(() => vi.fn());
vi.mock("@/api/ops", () => ({
  submitFreezoneMotionRender: (...args: unknown[]) => submit(...(args as [string, never])),
  fetchFreezoneJobResult: (...args: unknown[]) => fetchResult(...(args as [string, string, string])),
}));

const awaitCompletion = vi.hoisted(() => vi.fn());
vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: (...args: unknown[]) =>
    awaitCompletion(...(args as [string, string, unknown])),
}));

const toast = vi.hoisted(() => ({ info: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

// 图册本身已有独立套件（motion-gallery-modal.test.tsx）；这里只验「选完条目之后」的
// 提交契约与提示链路，所以把模态换成一个能直接触发 onApply 的替身。替身沿用真实
// 组件的口径：两个页签各交一份出处（kind 决定后端读哪份名录），onApply 之后紧接着
// onClose。
vi.mock("@/features/canvas/ui/MotionGalleryModal", () => ({
  MotionGalleryModal: ({
    onApply,
    onClose,
    cases,
    styles,
  }: {
    onApply: (
      prompt: string,
      source: { category: string; id: string; kind: "case" | "style"; label: string },
    ) => void;
    onClose: () => void;
    cases: Array<{ category: string; id: string; label: string }>;
    styles: Array<{ category: string; id: string; label: string }>;
  }) => (
    <div>
      {cases.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => {
            onApply("模型拼好的提示词", { ...item, kind: "case" });
            onClose();
          }}
        >
          {`apply-${item.id}`}
        </button>
      ))}
      {styles.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => {
            onApply("模型拼好的提示词", { ...item, kind: "style" });
            onClose();
          }}
        >
          {`apply-${item.id}`}
        </button>
      ))}
    </div>
  ),
}));

const catalog = vi.hoisted(() => ({
  assetBase: "",
  categories: ["产品宣传片"],
  methods: [] as never[],
  renderContract: "window.render(t)",
  cases: [
    {
      id: "M43",
      label: "苹果风产品宣传片",
      category: "产品宣传片",
      brief: "",
      tags: [],
      prompt_cn: "",
      cover: "M43/poster.webp",
      posterReady: true,
      videoReady: false,
      resolution: "1920x1080",
      durationSeconds: 29.4,
      author: "",
      mediaFile: "",
      mediaBytes: 0,
    },
  ],
  isLoading: false,
  error: null,
  retry: vi.fn(),
}));
const styleCatalog = vi.hoisted(() => ({
  categories: ["东方传统"],
  styles: [
    {
      id: "ink-wash",
      label: "水墨",
      labelEn: "Ink Wash",
      category: "东方传统",
      brief: "",
      tags: [],
      palette: ["#1f1f1f", "#f4f1e8"],
      prompt_cn: "",
      source: "参考名录 #7",
    },
  ],
  isLoading: false,
  error: null,
  retry: vi.fn(),
}));
vi.mock("@/features/canvas/hooks/useFreezoneMotionCases", () => ({
  useFreezoneMotionCases: () => catalog,
  useFreezoneMotionStyles: () => styleCatalog,
}));

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          episode: {
            beat: {
              motion: {
                render: "生成 MG 大片",
                apply: "渲染这一镜",
                running: "正在逐帧渲染「{{label}}」，成片需要一会儿",
                done: "MG 大片已出片：{{label}}",
                failed: "MG 大片渲染失败：{{label}}",
                open: "打开成片",
              },
            },
          },
        },
      },
    },
  });
});

function makeBeat(overrides: Partial<Beat> = {}): Beat {
  return {
    beat_number: 3,
    narration_segment: "旁白占位",
    visual_description: "一只手表在暗场里缓慢旋转",
    estimated_duration: 8.4,
    ...overrides,
  } as unknown as Beat;
}

function renderAction(beat = makeBeat(), orientation: "portrait" | "landscape" = "portrait") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MotionRenderAction beat={beat} project="p1" episode={7} orientation={orientation} />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  submit.mockResolvedValue({
    task_key: "freezone_motion_render|p1|0|mg_job",
    task_type: "freezone_motion_render",
    job_id: "mg_job",
  });
  awaitCompletion.mockResolvedValue({
    status: "completed",
    result: { output_url: "/static/mg/out.mp4" },
    error: null,
  });
});

describe("MotionRenderAction", () => {
  it("submits the case id plus this beat's subject and waits for the film", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-M43"));

    await waitFor(() => expect(screen.queryByText("apply-M43")).toBeNull());

    expect(submit).toHaveBeenCalledWith("p1", {
      caseId: "M43",
      // 案例与画风是互斥项：选了案例，另一栏必须是空串（后端同时收到两条会直接拒单）。
      styleId: "",
      subject: "一只手表在暗场里缓慢旋转",
      title: "苹果风产品宣传片",
      aspectRatio: "9:16",
      resolution: "720p",
      fps: 30,
      durationS: 8,
      // 集号/镜号要一起提交：渲染 sidecar 靠它们把成片归位到对应镜头，
      // 「导出剪映草稿」才有办法按镜把散落的成片排回原来的顺序。
      episode: 7,
      beatNumber: 3,
      archiveToLibrary: true,
    });
    expect(toast.info).toHaveBeenCalledWith(
      "正在逐帧渲染「苹果风产品宣传片」，成片需要一会儿",
    );
    expect(awaitCompletion).toHaveBeenCalledWith(
      "freezone_motion_render|p1|0|mg_job",
      "p1",
      { taskType: "freezone_motion_render" },
    );
    expect(toast.success).toHaveBeenCalledWith(
      "MG 大片已出片：苹果风产品宣传片",
      expect.objectContaining({ action: expect.objectContaining({ label: "打开成片" }) }),
    );
    // 后端把 url 直接写进了 result，不该再回查一次任务产物。
    expect(fetchResult).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("sends the style id instead of the case id for a gallery style", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-ink-wash"));

    await waitFor(() => expect(submit).toHaveBeenCalled());
    // 画风页签交的是观感规格：case_id 必须留空，标题用这条画风自己的名字。
    expect(submit.mock.calls[0][1]).toMatchObject({
      beatNumber: 3,
      caseId: "",
      episode: 7,
      styleId: "ink-wash",
      subject: "一只手表在暗场里缓慢旋转",
      title: "水墨",
    });
    expect(toast.success).toHaveBeenCalledWith(
      "MG 大片已出片：水墨",
      expect.objectContaining({ action: expect.objectContaining({ label: "打开成片" }) }),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("reads the url from the job result when the task payload has none", async () => {    awaitCompletion.mockResolvedValue({ status: "completed", result: {}, error: null });
    fetchResult.mockResolvedValue({ url: "/static/mg/fallback.mp4" });

    renderAction(makeBeat(), "landscape");
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-M43"));

    await waitFor(() => expect(fetchResult).toHaveBeenCalled());
    expect(fetchResult).toHaveBeenCalledWith("p1", "freezone_motion_render", "mg_job");
    expect(submit.mock.calls[0][1].aspectRatio).toBe("16:9");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("falls back to the beat narration when there is no visual description", async () => {
    renderAction(makeBeat({ visual_description: "" }));
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-M43"));

    await waitFor(() => expect(submit).toHaveBeenCalled());
    expect(submit.mock.calls[0][1].subject).toBe("旁白占位");
  });

  it("surfaces the backend failure text instead of the generic label", async () => {
    awaitCompletion.mockResolvedValue({
      status: "failed",
      result: null,
      error: "MG 页面生成失败：模型没有返回 HTML 文档",
    });

    renderAction();
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-M43"));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error).toHaveBeenCalledWith(
      "MG 页面生成失败：模型没有返回 HTML 文档",
      expect.anything(),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("keeps the 409 pre-flight text when the machine lacks Chrome or ffmpeg", async () => {
    submit.mockRejectedValue(
      new Error("本机缺少渲染用的 Chrome（请设置 ST_CHROME_PATH）"),
    );

    renderAction();
    fireEvent.click(screen.getByRole("button", { name: /生成 MG 大片/ }));
    fireEvent.click(screen.getByText("apply-M43"));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error).toHaveBeenCalledWith(
      "本机缺少渲染用的 Chrome（请设置 ST_CHROME_PATH）",
      expect.anything(),
    );
    expect(awaitCompletion).not.toHaveBeenCalled();
    // 按钮要能再点一次，否则这一镜就卡死了。
    expect(screen.getByRole("button", { name: /生成 MG 大片/ })).toBeEnabled();
  });
});
