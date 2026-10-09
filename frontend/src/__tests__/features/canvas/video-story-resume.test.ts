// Copyright (c) 2026 ClaymoreLab
// videoStory 解析的恢复路径：
//   - 解析的「生成中」标志是 isAnalyzing（不是 isGenerating），刷新后必须由
//     resumeGeneration 按 video-story 口径接回 rows，否则画布永久停在 99%；
//   - video 节点上的 isAnalyzing 只是「解析」按钮的防重入标志，跨会话遗留会
//     把按钮永久吞掉，由 staleAnalysisFlagPatch 收拾。
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CanvasNode } from "@/features/canvas/domain/canvasNodes";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { CURRENT_RUNTIME_SESSION_ID } from "@/features/canvas/application/generationErrorReport";
import {
  nodeNeedsGenerationResume,
  resumeNodeGeneration,
  staleAnalysisFlagPatch,
} from "@/features/canvas/application/resumeGeneration";
import { TaskCompletionError, TaskPollTimeoutError } from "@/api/tasks";

const listTasks = vi.fn();
const awaitTaskCompletion = vi.fn();

vi.mock("@/api/tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/tasks")>();
  return {
    ...actual,
    listTasks: (...args: unknown[]) => listTasks(...args),
    awaitTaskCompletion: (...args: unknown[]) => awaitTaskCompletion(...args),
  };
});

vi.mock("@/api/ops", () => ({
  fetchFreezoneJobResult: vi.fn().mockResolvedValue({ url: "out.png" }),
  fetchFreezoneReversePromptResult: vi.fn(),
  fetchFreezoneStoryScriptResult: vi.fn(),
  fetchFreezoneTextGenerateResult: vi.fn(),
}));

const TASK_KEY = "freezone_video_story:job-77";

/** 刷新后待恢复的视频故事节点：句柄齐全、仍在解析中。 */
function resumableStoryNode(extra: Record<string, unknown> = {}): CanvasNode {
  return {
    id: "story-1",
    type: CANVAS_NODE_TYPES.videoStory,
    position: { x: 0, y: 0 },
    data: {
      isAnalyzing: true,
      analysisStartedAt: 1_700_000_000_000,
      generationTaskKey: TASK_KEY,
      generationTaskType: "freezone_video_story",
      generationTaskJobId: "job-77",
      rows: [],
      ...extra,
    },
  } as unknown as CanvasNode;
}

function videoNode(extra: Record<string, unknown> = {}): CanvasNode {
  return {
    id: "video-1",
    type: CANVAS_NODE_TYPES.video,
    position: { x: 0, y: 0 },
    data: { videoUrl: "http://x/a.mp4", ...extra },
  } as unknown as CanvasNode;
}

const STORY_RESULT = {
  video_story: {
    title: "雨夜车站",
    shots: [
      { shot: 1, start_time: "00:00", end_time: "00:03", visual_description: "雨落在铁轨上" },
    ],
  },
  frame_urls: ["http://x/scene_1.png"],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("恢复路径：视频解析按 video-story 口径回填", () => {
  it("解析中刷新后重新接上任务，完成后把 shots 回填进 rows", async () => {
    listTasks.mockResolvedValue([{ task_key: TASK_KEY, status: "running" }]);
    awaitTaskCompletion.mockResolvedValue({
      task_key: TASK_KEY,
      status: "completed",
      result: STORY_RESULT,
    });
    const updateNodeData = vi.fn();

    await resumeNodeGeneration({
      node: resumableStoryNode(),
      projectId: "demo",
      updateNodeData,
    });

    const calls = updateNodeData.mock.calls;
    const patch = calls[calls.length - 1]?.[1] as Record<string, unknown>;
    expect(patch.isAnalyzing).toBe(false);
    expect(patch.analysisStartedAt).toBeNull();
    expect(patch.analysisError).toBeNull();
    expect(patch.generationTaskKey).toBeNull();
    expect(Array.isArray(patch.rows)).toBe(true);
    expect((patch.rows as unknown[]).length).toBe(1);
    expect(patch.rawResult).toEqual(STORY_RESULT);
  });

  it("解析失败时把错误写到故事节点的 analysisError", async () => {
    listTasks.mockResolvedValue([{ task_key: TASK_KEY, status: "running" }]);
    awaitTaskCompletion.mockRejectedValue(new Error("vision model exploded"));
    const updateNodeData = vi.fn();

    await resumeNodeGeneration({
      node: resumableStoryNode(),
      projectId: "demo",
      updateNodeData,
    });

    const calls = updateNodeData.mock.calls;
    const patch = calls[calls.length - 1]?.[1] as Record<string, unknown>;
    expect(patch.isAnalyzing).toBe(false);
    expect(patch.analysisError).toBe("vision model exploded");
    expect(patch.generationTaskKey).toBeNull();
  });

  it("用户终止过的任务按取消处理：清态但不写 analysisError", async () => {
    listTasks.mockResolvedValue([{ task_key: TASK_KEY, status: "running" }]);
    awaitTaskCompletion.mockRejectedValue(
      new TaskCompletionError("cancelled", "cancelled", TASK_KEY),
    );
    const updateNodeData = vi.fn();

    await resumeNodeGeneration({
      node: resumableStoryNode(),
      projectId: "demo",
      updateNodeData,
    });

    const calls = updateNodeData.mock.calls;
    const patch = calls[calls.length - 1]?.[1] as Record<string, unknown>;
    expect(patch.isAnalyzing).toBe(false);
    expect(patch.analysisError).toBeNull();
  });

  it("轮询脱离监听时保留句柄与 isAnalyzing，别把活着的任务写成失败", async () => {
    listTasks.mockResolvedValue([{ task_key: TASK_KEY, status: "running" }]);
    awaitTaskCompletion.mockRejectedValue(new TaskPollTimeoutError(TASK_KEY, 600_000));
    const updateNodeData = vi.fn();

    await resumeNodeGeneration({
      node: resumableStoryNode(),
      projectId: "demo",
      updateNodeData,
    });

    expect(updateNodeData).not.toHaveBeenCalled();
  });

  it("isAnalyzing + 句柄的节点会被恢复扫描选中", () => {
    expect(nodeNeedsGenerationResume(resumableStoryNode())).toBe(true);
  });

  it("videoStory 无句柄时恢复扫描不接管（交给 stale 清理）", () => {
    const node = resumableStoryNode({
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
    expect(nodeNeedsGenerationResume(node)).toBe(false);
  });
});

describe("跨会话遗留的 isAnalyzing 清理", () => {
  it("video 节点：旧会话的标志必须清掉，否则「解析」按钮被永久吞掉", () => {
    const node = videoNode({
      isAnalyzing: true,
      analysisClientSessionId: "session-from-before-the-refresh",
    });
    expect(staleAnalysisFlagPatch(node)).toEqual({ isAnalyzing: false });
  });

  it("video 节点：本会话写下的标志照常保留（解析正在进行）", () => {
    const node = videoNode({
      isAnalyzing: true,
      analysisClientSessionId: CURRENT_RUNTIME_SESSION_ID,
    });
    expect(staleAnalysisFlagPatch(node)).toBeNull();
  });

  it("videoStory 有句柄：不清理，交给 resume 回填结果", () => {
    const node = resumableStoryNode({
      analysisClientSessionId: "session-from-before-the-refresh",
    });
    expect(staleAnalysisFlagPatch(node)).toBeNull();
  });

  it("videoStory 无句柄：清遮罩并写明任务失联，别停在 99%", () => {
    const node = resumableStoryNode({
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
      analysisClientSessionId: "session-from-before-the-refresh",
    });
    const patch = staleAnalysisFlagPatch(node)!;
    expect(patch.isAnalyzing).toBe(false);
    expect(patch.analysisStartedAt).toBeNull();
    expect(patch.analysisError).toBeTruthy();
  });

  it("不在解析中的节点一律不管", () => {
    expect(staleAnalysisFlagPatch(videoNode({ isAnalyzing: false }))).toBeNull();
    expect(
      staleAnalysisFlagPatch(videoNode({ analysisClientSessionId: "old-session" })),
    ).toBeNull();
  });
});