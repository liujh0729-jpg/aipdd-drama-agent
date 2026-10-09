// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] SmartEditNode 组件行为：
//   1. 识别上游视频 → 列举视频技能 → 运行 video-upscale → 下游长出结果视频节点
//      并带 smartEdit → video 的边（走真实 store 的 addEdge 规范化，白名单漏登记
//      会在这里暴露）；
//   2. 没连素材时只显示提示，不渲染技能区；
//   3. 模型候选遵循空兜底：加载中 / 无模型不凭空造 id，默认落目录首候选。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { SmartEditNode } from "@/features/canvas/nodes/SmartEditNode";
import { useCanvasStore } from "@/stores/canvasStore";

const api = vi.hoisted(() => ({
  ensureBackendImageUrl: vi.fn(),
  ensureBackendImageUrls: vi.fn(),
  fetchFreezoneAudioSeparateResult: vi.fn(),
  fetchFreezoneImageModels: vi.fn(),
  fetchFreezoneMediaCopywriteModels: vi.fn(),
  fetchFreezoneReversePromptResult: vi.fn(),
  fetchFreezoneTextModels: vi.fn(),
  fetchFreezoneVideoModels: vi.fn(),
  fetchFreezoneVisionModels: vi.fn(),
  submitFreezoneAudioSeparate: vi.fn(),
  submitFreezoneEdit: vi.fn(),
  submitFreezoneMediaCopywrite: vi.fn(),
  submitFreezoneReversePrompt: vi.fn(),
  submitFreezoneUpscale: vi.fn(),
  submitFreezoneVideoClone: vi.fn(),
  submitFreezoneVideoOmniGen: vi.fn(),
  submitFreezoneVideoScriptReverse: vi.fn(),
  submitFreezoneVideoUpscale: vi.fn(),
}));
const awaitTaskCompletion = vi.hoisted(() => vi.fn());

vi.mock("@/api/ops", () => api);

vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: (...args: unknown[]) => awaitTaskCompletion(...args),
  isTaskPollTimeoutError: () => false,
}));

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "demo", canvas: "default" }),
}));

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>(
    "@xyflow/react",
  );
  return {
    ...actual,
    Handle: () => <div />,
    useUpdateNodeInternals: () => vi.fn(),
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: () => <div />,
}));

function seedCanvas() {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: "video-1",
        type: CANVAS_NODE_TYPES.video,
        position: { x: 0, y: 0 },
        data: { videoUrl: "https://cdn/in.mp4" },
      },
      {
        id: "smart-1",
        type: CANVAS_NODE_TYPES.smartEdit,
        position: { x: 500, y: 0 },
        data: {},
      },
    ],
    [{ id: "e-1", source: "video-1", target: "smart-1" }],
  );
}

/** 取画布上 smart-1 的当前 data 渲染组件（模拟 React Flow 的数据下发）。 */
function renderNode() {
  const node = useCanvasStore
    .getState()
    .nodes.find((n) => n.id === "smart-1");
  return render(
    <SmartEditNode
      id="smart-1"
      type={CANVAS_NODE_TYPES.smartEdit}
      data={node?.data as never}
      selected={false}
      dragging={false}
      draggable
      selectable
      deletable
      zIndex={0}
      isConnectable
      positionAbsoluteX={500}
      positionAbsoluteY={0}
    />,
  );
}

/** 以 smart-1 为中心，把一批素材节点连进来作为上游。 */
function seedSources(
  sources: { id: string; type: string; data: Record<string, unknown> }[],
) {
  useCanvasStore.getState().setCanvasData(
    [
      ...sources.map((s) => ({ ...s, position: { x: 0, y: 0 } })),
      {
        id: "smart-1",
        type: CANVAS_NODE_TYPES.smartEdit,
        position: { x: 500, y: 0 },
        data: {},
      },
    ] as never,
    sources.map((s) => ({ id: `e-${s.id}`, source: s.id, target: "smart-1" })),
  );
}

/** 选中技能（写 store）后重新渲染，再点开始处理。 */
function selectAndRun(titleKey: string) {
  fireEvent.click(screen.getByText(titleKey));
  renderNode();
  fireEvent.click(screen.getByText("smartEdit.actions.run"));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.ensureBackendImageUrl.mockImplementation(
    async (_project: string, url: string) => url,
  );
  api.ensureBackendImageUrls.mockImplementation(
    async (_project: string, urls: readonly string[]) => [...(urls ?? [])],
  );
  api.fetchFreezoneImageModels.mockResolvedValue([]);
  api.fetchFreezoneVideoModels.mockResolvedValue([]);
  api.fetchFreezoneTextModels.mockResolvedValue([]);
  seedCanvas();
});

describe("SmartEditNode 视频高清流程", () => {
  it("识别视频上游 → 列举视频技能", () => {
    renderNode();

    expect(screen.getByText("smartEdit.input.video")).toBeTruthy();
    expect(screen.getByText("smartEdit.skills.videoUpscale.title")).toBeTruthy();
    expect(
      screen.getByText("smartEdit.skills.audioSeparate.title"),
    ).toBeTruthy();
    // 音频/图片技能不该出现在视频素材下。
    expect(screen.queryByText("smartEdit.skills.imageUpscale.title")).toBeNull();
  });

  it("运行 video-upscale：提交默认参数并在下游长出结果视频节点与边", async () => {
    api.submitFreezoneVideoUpscale.mockResolvedValue({
      task_key: "freezone_video_upscale:job-1",
      task_type: "freezone_video_upscale",
      job_id: "job-1",
    });
    awaitTaskCompletion.mockResolvedValue({
      result: { output: [{ result_url: "https://cdn/out-1080p.mp4" }] },
    });

    renderNode();
    fireEvent.click(screen.getByText("smartEdit.skills.videoUpscale.title"));

    // 点技能写的是 store（activeSkillId），重新按最新 data 渲染选中态。
    expect(
      useCanvasStore.getState().nodes.find((n) => n.id === "smart-1")?.data
        .activeSkillId,
    ).toBe("video-upscale");
    renderNode();

    fireEvent.click(screen.getByText("smartEdit.actions.run"));

    await waitFor(() => {
      expect(api.submitFreezoneVideoUpscale).toHaveBeenCalledOnce();
    });
    // 未改动参数时按面板默认档位提交（1080p / 1x）。
    expect(api.submitFreezoneVideoUpscale).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceUrl: "https://cdn/in.mp4",
        resolution: "1080p",
        denoiseStrength: "1x",
      }),
    );

    await waitFor(() => {
      const state = useCanvasStore.getState();
      const result = state.nodes.find(
        (n) => n.type === CANVAS_NODE_TYPES.video && n.id !== "video-1",
      );
      expect(result).toBeTruthy();
      expect(result!.data.videoUrl).toBe("https://cdn/out-1080p.mp4");
      expect(
        state.edges.some(
          (e) =>
            e.source === "smart-1" &&
            state.nodes.some((n) => n.id === e.target && n.id !== "video-1"),
        ),
      ).toBe(true);
    });

    // 运行结束后处理标记复位。
    const smart = useCanvasStore
      .getState()
      .nodes.find((n) => n.id === "smart-1");
    expect(smart?.data.isProcessing).toBeFalsy();
  });
});

describe("SmartEditNode 空态与空兜底", () => {
  it("未连素材时只显示提示，不渲染技能区", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "smart-1",
          type: CANVAS_NODE_TYPES.smartEdit,
          position: { x: 0, y: 0 },
          data: {},
        },
      ],
      [],
    );

    renderNode();

    expect(screen.getByText("smartEdit.noInput")).toBeTruthy();
    expect(screen.queryByText("smartEdit.skills.videoUpscale.title")).toBeNull();
    expect(screen.queryByText("smartEdit.actions.run")).toBeNull();
  });

  it("ASR 候选为空（后台未配置）时不凭空造模型 id，提交不带 model", async () => {
    api.fetchFreezoneMediaCopywriteModels.mockResolvedValue([]);
    api.submitFreezoneMediaCopywrite.mockResolvedValue({
      task_key: "freezone_media_copywrite:job-2",
      task_type: "freezone_media_copywrite",
      job_id: "job-2",
    });
    awaitTaskCompletion.mockResolvedValue({
      result: { text: "提取出的文案" },
    });

    renderNode();
    fireEvent.click(screen.getByText("smartEdit.skills.extractCopy.title"));
    renderNode();
    await waitFor(() => {
      expect(screen.getByText("modelParams.noModelsAvailable")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("smartEdit.actions.run"));
    await waitFor(() => {
      expect(api.submitFreezoneMediaCopywrite).toHaveBeenCalledOnce();
    });
    expect(api.submitFreezoneMediaCopywrite).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceType: "upload_video",
        url: "https://cdn/in.mp4",
        model: undefined,
      }),
    );

    // 文案结果落成文本节点。
    await waitFor(() => {
      const textNode = useCanvasStore
        .getState()
        .nodes.find((n) => n.type === CANVAS_NODE_TYPES.textAnnotation);
      expect(textNode?.data.content).toBe("提取出的文案");
    });
  });

  it("有候选时默认落目录首模型（= 后台默认模型设置）", async () => {
    api.fetchFreezoneMediaCopywriteModels.mockResolvedValue([
      { id: "fast-asr", label: "Fast ASR", ready: true },
      { id: "slow-asr", label: "Slow ASR", ready: true },
    ]);
    api.submitFreezoneMediaCopywrite.mockResolvedValue({
      task_key: "freezone_media_copywrite:job-3",
      task_type: "freezone_media_copywrite",
      job_id: "job-3",
    });
    awaitTaskCompletion.mockResolvedValue({ result: { text: "文案" } });

    renderNode();
    fireEvent.click(screen.getByText("smartEdit.skills.extractCopy.title"));
    renderNode();
    // 候选是异步拉的，等落到面板（首候选即默认）再点运行，否则读到 null。
    await waitFor(() => {
      expect(screen.getByText("Fast ASR")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("smartEdit.actions.run"));
    await waitFor(() => {
      expect(api.submitFreezoneMediaCopywrite).toHaveBeenCalledOnce();
    });
    expect(api.submitFreezoneMediaCopywrite).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ model: "fast-asr" }),
    );
  });
});

describe("SmartEditNode 去水印 / 多输入视频技能", () => {
  const VIDEO_SRC = {
    id: "src-video",
    type: CANVAS_NODE_TYPES.video,
    data: { videoUrl: "https://cdn/in.mp4" },
  };
  const AUDIO_SRC = {
    id: "src-audio",
    type: CANVAS_NODE_TYPES.audio,
    data: { audioUrl: "https://cdn/in.mp3" },
  };
  const IMAGE_SRC = {
    id: "src-img",
    type: CANVAS_NODE_TYPES.upload,
    data: { imageUrl: "https://cdn/ref.png" },
  };

  function jobRef(prefix: string) {
    return {
      task_key: `${prefix}:job-x`,
      task_type: prefix,
      job_id: "job-x",
    };
  }

  it("image-watermark：提交去水印并长出结果图片节点", async () => {
    seedSources([IMAGE_SRC]);
    renderNode();
    api.submitFreezoneEdit.mockResolvedValue(jobRef("freezone_edit"));
    awaitTaskCompletion.mockResolvedValue({
      result: { output: [{ result_url: "https://cdn/out-nowm.png" }] },
    });

    selectAndRun("smartEdit.skills.imageWatermark.title");

    await waitFor(() => {
      expect(api.submitFreezoneEdit).toHaveBeenCalledOnce();
    });
    expect(api.submitFreezoneEdit).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        baseUrl: "https://cdn/ref.png",
        prompt: "smartEdit.skills.imageWatermark.defaultPrompt",
        aspectRatio: "original",
        model: null,
        modelId: null,
      }),
    );
    await waitFor(() => {
      const result = useCanvasStore
        .getState()
        .nodes.find(
          (n) =>
            n.type === CANVAS_NODE_TYPES.exportImage &&
            n.data.imageUrl === "https://cdn/out-nowm.png",
        );
      expect(result).toBeTruthy();
    });
  });

  it("lip-sync：视频+音频两路拼成 allReference 引用提交", async () => {
    seedSources([VIDEO_SRC, AUDIO_SRC]);
    renderNode();
    api.submitFreezoneVideoOmniGen.mockResolvedValue(
      jobRef("freezone_video_omni_gen"),
    );
    awaitTaskCompletion.mockResolvedValue({
      result: { output: [{ result_url: "https://cdn/lipsync.mp4" }] },
    });

    selectAndRun("smartEdit.skills.lipSync.title");

    await waitFor(() => {
      expect(api.submitFreezoneVideoOmniGen).toHaveBeenCalledOnce();
    });
    const payload = api.submitFreezoneVideoOmniGen.mock.calls[0][1];
    expect(payload.genMode).toBe("allReference");
    expect(payload.references).toEqual([
      { type: "video", url: "https://cdn/in.mp4" },
      { type: "audio", url: "https://cdn/in.mp3" },
    ]);
    await waitFor(() => {
      const result = useCanvasStore
        .getState()
        .nodes.find(
          (n) =>
            n.type === CANVAS_NODE_TYPES.video &&
            n.data.videoUrl === "https://cdn/lipsync.mp4",
        );
      expect(result).toBeTruthy();
    });
  });

  it("replace-person：视频+图片两路，第二路作参考图", async () => {
    seedSources([VIDEO_SRC, IMAGE_SRC]);
    renderNode();
    api.submitFreezoneVideoOmniGen.mockResolvedValue(
      jobRef("freezone_video_omni_gen"),
    );
    awaitTaskCompletion.mockResolvedValue({
      result: { output: [{ result_url: "https://cdn/replace.mp4" }] },
    });

    selectAndRun("smartEdit.skills.replacePerson.title");

    await waitFor(() => {
      expect(api.submitFreezoneVideoOmniGen).toHaveBeenCalledOnce();
    });
    expect(api.submitFreezoneVideoOmniGen.mock.calls[0][1].references).toEqual([
      { type: "video", url: "https://cdn/in.mp4" },
      { type: "image", url: "https://cdn/ref.png" },
    ]);
  });

  it("video-clone：参考图走 ensureBackendImageUrls，默认 frames/generate", async () => {
    seedSources([VIDEO_SRC, IMAGE_SRC]);
    renderNode();
    api.submitFreezoneVideoClone.mockResolvedValue(
      jobRef("freezone_video_clone"),
    );
    awaitTaskCompletion.mockResolvedValue({
      result: { output: [{ result_url: "https://cdn/cloned.mp4" }] },
    });

    selectAndRun("smartEdit.skills.videoClone.title");

    await waitFor(() => {
      expect(api.submitFreezoneVideoClone).toHaveBeenCalledOnce();
    });
    expect(api.submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        videoUrl: "https://cdn/in.mp4",
        referenceUrls: ["https://cdn/ref.png"],
        referenceMode: "frames",
        audioMode: "generate",
      }),
    );
    // 复刻口不继承节点上下文：不传 canvasId/nodeId。
    expect(api.submitFreezoneVideoClone.mock.calls[0][1]).not.toHaveProperty(
      "canvasId",
    );
  });

  it("缺第二路素材（对口型无音频）：toast 提示且不调用 submit", async () => {
    seedSources([VIDEO_SRC]);
    renderNode();

    selectAndRun("smartEdit.skills.lipSync.title");

    await waitFor(() => {
      const smart = useCanvasStore
        .getState()
        .nodes.find((n) => n.id === "smart-1");
      expect(smart?.data.lastError).toBe("smartEdit.inputs.needDriverAudio");
    });
    expect(api.submitFreezoneVideoOmniGen).not.toHaveBeenCalled();
    expect(smart_isNotProcessing()).toBe(true);
  });
});

function smart_isNotProcessing(): boolean {
  const smart = useCanvasStore.getState().nodes.find((n) => n.id === "smart-1");
  return !smart?.data.isProcessing;
}
