// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 一键流水线控制条单测：只验证 UI 编排（三档切换、发射入参、
// 运行前设置对话框、检查点审批对话框、requires_input 不误报完成、停止接线），
// 循环本身由 pipeline-driver.test.ts 覆盖。
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const runPipelineDriven = vi.hoisted(() => vi.fn());
const MockStopped = vi.hoisted(
  () =>
    class PipelineStoppedError extends Error {
      constructor() {
        super("pipeline stopped by user");
        this.name = "PipelineStoppedError";
      }
    },
);

vi.mock("@/lib/pipeline-driver", () => ({
  runPipelineDriven,
  PipelineStoppedError: MockStopped,
}));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastInfo = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const toastWarning = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, info: toastInfo, error: toastError, warning: toastWarning },
}));

const invalidateQueries = vi.hoisted(() => vi.fn());
const getQueryData = vi.hoisted(() => vi.fn(() => undefined));
vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<
    typeof import("@tanstack/react-query")
  >("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries, getQueryData }),
  };
});

// 控制条新增的读依赖：提示词库（故事版是否可用）/ 分集（摘要）/ 画幅 store / 图片模型目录。
const promptItems = vi.hoisted(() => ({ current: [] as Array<{ key: string; value?: string; default?: string }> }));
vi.mock("@/lib/queries/prompt-library", () => ({
  usePromptLibrary: () => ({ data: { items: promptItems.current } }),
}));
vi.mock("@/lib/queries/episodes", () => ({
  useEpisodes: () => ({ data: { data: [] } }),
}));
vi.mock("@/stores/aspect-ratio-store", () => ({
  useProjectAspectRatio: () => ({
    orientation: "portrait",
    spec: { renderAspect: "2:3", sketchAspect: "2:3" },
    setOrientation: vi.fn(),
  }),
}));
// [aipdd fork delta] 运行前「一键配齐」：控制条在发射整链之前，先把用户选的三个模型
// 铺开成整链要用的全部功能位绑定。这里只验编排（什么时候发、发什么、失败就不跑），
// 真实铺绑由后端 tests/test_feature_provisioner.py 覆盖。
const provisionMutateAsync = vi.hoisted(() => vi.fn());
vi.mock("@/lib/queries/model-gateway", () => ({
  useProvisionFeatureRoutes: () => ({ mutateAsync: provisionMutateAsync }),
}));

// 弹窗里的三类模型候选：给固定条目，用例才点得到具体选项（id 与真实目录同形）。
const catalogModels = vi.hoisted(() => ({
  text: [{ id: "aipdd::ap-live-llm", label: "ap-live-llm" }],
  image: [{ id: "ap-image-2.0", label: "ap-image-2.0" }],
  video: [{ id: "ap-video-1", label: "ap-video-1" }],
}));
vi.mock("@/features/canvas/hooks/useFreezoneTextModels", () => ({
  useFreezoneTextModels: () => ({
    models: catalogModels.text,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({
    models: catalogModels.video,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => ({
    models: catalogModels.image,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { PipelineControlBar } from "@/components/pipeline-control-bar";

beforeEach(() => {
  vi.clearAllMocks();
  promptItems.current = [];
  // 默认「配齐」成功且没有需要提示的缺口：失败路径由具体用例覆写。
  provisionMutateAsync.mockResolvedValue({ hints: [], touched: [] });
});

describe("PipelineControlBar", () => {
  it("renders the three automation modes with checkpoint as the default", () => {
    render(<PipelineControlBar project="proj-a" />);
    for (const key of ["pipeline.modeManual", "pipeline.modeCheckpoint", "pipeline.modeAuto"]) {
      expect(screen.getByRole("radio", { name: key })).toBeInTheDocument();
    }
    expect(
      screen.getByRole("radio", { name: "pipeline.modeCheckpoint" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: /pipeline.runAll/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pipeline.settings.button/ })).toBeInTheDocument();
  });

  it("opens the pre-run settings dialog", () => {
    render(<PipelineControlBar project="proj-a" storyCharCount={128} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.settings.button/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("pipeline.settings.title");
    // 故事正文不在弹窗里录入：只回显「将用页面上方哪一份故事」。
    expect(dialog).toHaveTextContent("pipeline.settings.storyFromPage");
    expect(dialog).not.toHaveTextContent("pipeline.settings.storyPlaceholder");
  });

  it("manual mode steps once without reporting completion", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "manual-step", stage: "episode_1_script_generating", message: "m" });
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("radio", { name: "pipeline.modeManual" }));
    fireEvent.click(screen.getByRole("button", { name: /pipeline.stepOnce/ }));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
    const opts = runPipelineDriven.mock.calls[0][0];
    expect(opts.mode).toBe("manual");
    expect(opts.ceiling).toBe("full");
    expect(opts.project).toBe("proj-a");
    // 单步 ≠ 完成：不应弹「已完成」。
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("reports completion only when the driver says completed", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("pipeline.completed"));
  });

  it("warns (no false completion) when the project needs a story", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "requires-input", stage: "story", message: "没有故事源" });
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(toastWarning).toHaveBeenCalledWith("pipeline.needStory"));
    expect(toastSuccess).not.toHaveBeenCalled();
    // 不再把用户引去设置弹窗贴故事：故事只能在「故事」页编辑。
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // 以下三例是「全自动入口搬到故事页」后的联动契约。
  it("blocks the run when the page has no story text", async () => {
    render(<PipelineControlBar project="proj-a" storyCharCount={0} />);
    const run = screen.getByRole("button", { name: /pipeline.runAll/ });
    expect(run).toBeDisabled();
    expect(run).toHaveAttribute("title", "pipeline.needStory");
    fireEvent.click(run);
    expect(runPipelineDriven).not.toHaveBeenCalled();
  });

  it("blocks the run while a sibling task is busy", () => {
    render(<PipelineControlBar project="proj-a" storyCharCount={50} busy />);
    const run = screen.getByRole("button", { name: /pipeline.runAll/ });
    expect(run).toBeDisabled();
    expect(run).toHaveAttribute("title", "pipeline.busy");
    fireEvent.click(run);
    expect(runPipelineDriven).not.toHaveBeenCalled();
  });

  it("flushes the page story before firing, and aborts when flushing fails", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    const prepareStory = vi.fn().mockResolvedValue(false);
    render(<PipelineControlBar project="proj-a" storyCharCount={50} prepareStory={prepareStory} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(prepareStory).toHaveBeenCalledTimes(1));
    // 落盘失败 = 不拿旧故事静默跑一轮。
    await waitFor(() => expect(runPipelineDriven).not.toHaveBeenCalled());

    prepareStory.mockResolvedValue(true);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
    // 故事正文不再走运行参数：一律以服务端故事源为准。
    expect(runPipelineDriven.mock.calls[0][0].storyText).toBeUndefined();
  });

  it("takes the story page style / episode count as linked defaults", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    render(
      <PipelineControlBar
        project="proj-a"
        defaults={{ storyStyle: "wong_kar_wai", episodeCount: 12 }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
    expect(runPipelineDriven.mock.calls[0][0].settings).toMatchObject({
      storyStyle: "wong_kar_wai",
      episodeCount: 12,
    });
  });

  it("checkpoint mode surfaces the approval dialog with the planned actions", async () => {
    let approved: boolean | undefined;
    runPipelineDriven.mockImplementation(async (opts) => {
      const gate = opts.approveGate;
      if (!gate) return { kind: "completed", stage: "done", message: "" };
      approved = await gate({
        ok: true,
        stage: "episode_1_script_generating",
        status: "planned",
        message: "第 1 集分镜脚本已入队",
        next_hint: "",
        actions: [{ method: "POST", path: "/api/v1/proj-a/episodes/1/story/to-script" }],
      } as never);
      if (approved) opts.onInvalidate?.(1);
      return approved
        ? { kind: "completed", stage: "done", message: "" }
        : { kind: "ceiling", stage: "episode_1_script_generating" };
    });
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));

    // 审批对话框列出待发射动作。
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("pipeline.approvalTitle");
    expect(dialog).toHaveTextContent("/episodes/1/story/to-script");

    fireEvent.click(screen.getByRole("button", { name: "pipeline.approvalApprove" }));
    await waitFor(() => expect(approved).toBe(true));
    await waitFor(() => expect(invalidateQueries).toHaveBeenCalled());
  });

  it("cancelling the approval resolves the gate with false and fires nothing", async () => {
    let approved: boolean | undefined;
    runPipelineDriven.mockImplementation(async (opts) => {
      approved = await opts.approveGate?.({
        ok: true,
        stage: "episode_1_script_generating",
        status: "planned",
        message: "m",
        next_hint: "",
        actions: [{ method: "POST", path: "/api/v1/proj-a/episodes/1/story/to-script" }],
      } as never);
      return { kind: "ceiling", stage: "episode_1_script_generating" };
    });
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "pipeline.approvalCancel" }));
    await waitFor(() => expect(approved).toBe(false));
    expect(invalidateQueries).not.toHaveBeenCalled();
  });

  it("stop flips the isStopped flag the driver reads", async () => {
    let captured: { isStopped: () => boolean } | undefined;
    runPipelineDriven.mockImplementation(
      (opts) => {
        captured = opts;
        return new Promise(() => {}); // 运行中，等待停止
      },
    );
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured?.isStopped()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.stop/ }));
    expect(captured?.isStopped()).toBe(true);
  });

  it("reports a driver failure via toast", async () => {
    runPipelineDriven.mockRejectedValue(new Error("模型不可用"));
    render(<PipelineControlBar project="proj-a" />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("模型不可用"));
  });

  // 「三个模型 → 一键配齐」的编排契约（[aipdd fork delta]）。
  it("skips provisioning when none of the three models was picked", async () => {
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    render(<PipelineControlBar project="proj-a" storyCharCount={50} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
    // 三个都留空 = 什么都没选：不该白写一遍设置，照旧交后端解析链（含运行期兜底）。
    expect(provisionMutateAsync).not.toHaveBeenCalled();
  });

  it("provisions the picked models before firing the pipeline", async () => {
    const user = userEvent.setup();
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    render(<PipelineControlBar project="proj-a" storyCharCount={50} />);

    fireEvent.click(screen.getByRole("button", { name: /pipeline.settings.button/ }));
    const dialog = await screen.findByRole("dialog");
    // 四个下拉：文本 / 图片 / 视频 / 导演风格（风格不参与配齐）。
    const [textSelect, imageSelect, videoSelect] = within(dialog).getAllByRole("combobox");
    await user.click(textSelect);
    await user.click(await screen.findByRole("option", { name: "ap-live-llm" }));
    await user.click(imageSelect);
    await user.click(await screen.findByRole("option", { name: "ap-image-2.0" }));
    await user.click(videoSelect);
    await user.click(await screen.findByRole("option", { name: "ap-video-1" }));
    await user.click(within(dialog).getByRole("button", { name: "pipeline.settings.close" }));

    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(provisionMutateAsync).toHaveBeenCalledTimes(1));
    // 图片模型走 settings.model（媒体目录条目 id），文本走复合调用名。
    expect(provisionMutateAsync.mock.calls[0][0]).toEqual({
      textModel: "aipdd::ap-live-llm",
      imageModel: "ap-image-2.0",
      videoModel: "ap-video-1",
    });
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
    // 配齐先于发射：整链读到的就是这一次刚铺好的绑定。
    expect(
      provisionMutateAsync.mock.invocationCallOrder[0],
    ).toBeLessThan(runPipelineDriven.mock.invocationCallOrder[0]);
  });

  it("aborts the run when provisioning fails, surfacing the backend reason", async () => {
    const user = userEvent.setup();
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    // 后端在名字不在渠道清单时回 400 中文 —— 这是唯一该拦住一次运行的理由。
    provisionMutateAsync.mockRejectedValueOnce(
      new Error("Acme-IMG-2 不在渠道 aipdd 的模型清单里（已从渠道下架或尚未开通）"),
    );
    render(<PipelineControlBar project="proj-a" storyCharCount={50} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.settings.button/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "ap-live-llm" }));
    await user.click(within(dialog).getByRole("button", { name: "pipeline.settings.close" }));

    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(provisionMutateAsync).toHaveBeenCalledTimes(1));
    expect(runPipelineDriven).not.toHaveBeenCalled();
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError.mock.calls[0][0]).toContain("不在渠道");
  });

  it("warns about gaps without blocking the run", async () => {
    const user = userEvent.setup();
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    // 挑不到的那几位（典型是向量位要人填维度）：只提醒，不拦这次运行。
    provisionMutateAsync.mockResolvedValueOnce({
      hints: ["向量模型还没绑定，请到「设置 → 模型与渠道」补绑定向量模型。"],
      touched: [],
    });
    render(<PipelineControlBar project="proj-a" storyCharCount={50} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.settings.button/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "ap-live-llm" }));
    await user.click(within(dialog).getByRole("button", { name: "pipeline.settings.close" }));

    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(toastWarning).toHaveBeenCalledWith(
      "向量模型还没绑定，请到「设置 → 模型与渠道」补绑定向量模型。",
    ));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
  });

  it("keeps the run going when only the gateway projection failed", async () => {
    const user = userEvent.setup();
    runPipelineDriven.mockResolvedValue({ kind: "completed", stage: "done", message: "" });
    // 真源已经落对了，只是没投到网关渠道：提醒去哪儿补同步，不拦这次运行。
    provisionMutateAsync.mockResolvedValueOnce({
      hints: [],
      touched: ["aipdd"],
      projectionError: "admin token 获取失败",
    });
    render(<PipelineControlBar project="proj-a" storyCharCount={50} />);
    fireEvent.click(screen.getByRole("button", { name: /pipeline.settings.button/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "ap-live-llm" }));
    await user.click(within(dialog).getByRole("button", { name: "pipeline.settings.close" }));

    fireEvent.click(screen.getByRole("button", { name: /pipeline.runAll/ }));
    await waitFor(() => expect(toastWarning).toHaveBeenCalledWith(
      "pipeline.provisionProjectionFailed",
    ));
    await waitFor(() => expect(runPipelineDriven).toHaveBeenCalledTimes(1));
  });
});
