// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分集自动化编排器（薄壳）单测：episode-auto-pipeline 现在只是
// @/lib/pipeline-driver（唯一客户端循环）的一层壳。这里只钉住壳的契约：逐集串行、
// 正确的 ceiling/mode、前提旗标（脚本管线开资产规划、分镜图管线不开）、产物勾选→ceiling
// 的映射、故事版配置装配、回调翻译与错误/停止翻译；循环本身由 pipeline-driver.test.ts 覆盖。
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PipelineDriverOptions, PipelineRunResult } from "@/lib/pipeline-driver";

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

import {
  AutoPipelineFailedError,
  AutoPipelineNeedsInputError,
  AutoPipelineStoppedError,
  runScriptPipeline,
  runStoryboardPipeline,
} from "@/lib/episode-auto-pipeline";

const project = "proj-a";

function fakeQueryClient() {
  return {
    invalidateQueries: vi.fn(),
  } as unknown as Parameters<typeof runScriptPipeline>[0]["queryClient"];
}

/**
 * 记录每次调用 driver 的入参；默认成功并回调 onStep/onInvalidate。
 * 返回值必须是真实的 {@link PipelineRunResult} 形状（默认按「推进到 ceiling 正常收敛」），
 * 不能再返 undefined —— 之前 mock 返空、壳层又把返回值整个丢弃，才让 requires_input
 * 被当成「已完成」。`resultFor` 用于按集定制收敛结果。
 */
function captureDriver(
  onCall?: (opts: PipelineDriverOptions) => Promise<void> | void,
  resultFor: (opts: PipelineDriverOptions) => PipelineRunResult = () => ({
    kind: "ceiling",
    stage: "",
  }),
) {
  const seen: PipelineDriverOptions[] = [];
  runPipelineDriven.mockImplementation(async (opts: PipelineDriverOptions) => {
    seen.push(opts);
    if (onCall) await onCall(opts);
    const ep = opts.episode ?? 0;
    if (opts.ceiling === "script") {
      opts.onStep?.(ep, "identity");
      opts.onStep?.(ep, "script");
      if (opts.includeStoryVersion) opts.onStep?.(ep, "storyVersion");
    } else if (opts.ceiling === "sketch") {
      opts.onStep?.(ep, "colors");
      opts.onStep?.(ep, "sketch");
    }
    opts.onInvalidate?.(ep);
    return resultFor(opts);
  });
  return seen;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("runScriptPipeline", () => {
  it("drives each episode serially with mode=auto and ceiling=script", async () => {
    const seen = captureDriver();
    const qc = fakeQueryClient();
    const steps: string[] = [];
    await runScriptPipeline({
      project,
      queryClient: qc,
      storyVersionBasePrompt: "base {user_requirement}",
      storyVersionCategoryLabel: "故事版",
      episodes: [{ number: 1, summary: "第一集梗概" }, { number: 2, summary: "第二集梗概" }],
      callbacks: { onStep: (ep, step) => steps.push(`${ep}:${step}`) },
    });

    expect(seen.map((o) => o.episode)).toEqual([1, 2]);
    for (const o of seen) {
      // 需循环推进到 ceiling（资产规划→脚本→[故事版]），所以是 auto 而非发一步即返回的 manual。
      expect(o.mode).toBe("auto");
      expect(o.ceiling).toBe("script");
      expect(o.project).toBe(project);
      // 「自动化生成分镜脚本」把资产规划三件也当前提：缺哪步补哪步。
      expect(o.planEpisodeAssets).toBe(true);
    }
    // 故事版有基础提示词 → includeStoryVersion=true。
    expect(seen.every((o) => o.includeStoryVersion === true)).toBe(true);
    // onStep 顺序：EP1 三步、EP2 三步（driver 发射时回调）。
    expect(steps).toEqual([
      "1:identity",
      "1:script",
      "1:storyVersion",
      "2:identity",
      "2:script",
      "2:storyVersion",
    ]);
    expect(qc.invalidateQueries).toHaveBeenCalled();
  });

  it("skips story version when the base prompt is blank", async () => {
    const seen = captureDriver();
    await runScriptPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "   ",
      storyVersionCategoryLabel: "SV",
      episodes: [{ number: 1 }],
    });
    expect(seen[0].includeStoryVersion).toBe(false);
    expect(seen[0].storyVersion).toBeUndefined();
  });

  it("builds storyVersion.requirementFor from the episode summary, truncated", async () => {
    const seen = captureDriver();
    await runScriptPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "PROMPT::{user_requirement}",
      storyVersionCategoryLabel: "SV",
      episodes: [{ number: 3, summary: "abc", rawContent: "ignored when summary exists" }],
    });
    const sv = seen[0].storyVersion!;
    expect(sv.basePrompt).toBe("PROMPT::{user_requirement}");
    expect(sv.categoryLabel).toBe("SV");
    expect(sv.requirementFor(3)).toBe("abc");
    // 空白折叠 + 截断到 800 字。
    const long = { number: 4, summary: "x".repeat(1000) };
    const seen2 = captureDriver();
    await runScriptPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "b",
      storyVersionCategoryLabel: "SV",
      episodes: [long],
    });
    expect(seen2[0].storyVersion!.requirementFor(4)).toHaveLength(800);
  });

  it("translates a driver failure into AutoPipelineFailedError with the last step", async () => {
    runPipelineDriven.mockImplementation(async (opts: PipelineDriverOptions) => {
      const ep = opts.episode ?? 0;
      opts.onStep?.(ep, "identity");
      opts.onStep?.(ep, "script");
      throw new Error("尚未规划角色身份");
    });
    await expect(
      runScriptPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }],
      }),
    ).rejects.toMatchObject({
      name: "AutoPipelineFailedError",
      episode: 1,
      step: "script",
      message: "尚未规划角色身份",
    });
  });

  it("translates a driver PipelineStoppedError into AutoPipelineStoppedError", async () => {
    runPipelineDriven.mockImplementation(async () => {
      throw new MockStopped();
    });
    await expect(
      runScriptPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }, { number: 2 }],
      }),
    ).rejects.toBeInstanceOf(AutoPipelineStoppedError);
  });

  it("aborts before driving any episode when already stopped", async () => {
    const seen = captureDriver();
    await expect(
      runScriptPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }],
        callbacks: { isStopped: () => true },
      }),
    ).rejects.toBeInstanceOf(AutoPipelineStoppedError);
    expect(seen).toHaveLength(0);
  });

  it("raises a typed needs-input error when the planner refuses for missing input", async () => {
    // 回归：服务端回 requires_input（如项目还没有故事源）时一个动作都没发射，
    // 旧壳层丢弃返回值直接进 toast.success，用户看到「将补齐四步」但什么都没发生。
    const seen = captureDriver(undefined, () => ({
      kind: "requires-input",
      stage: "story",
      message: "项目还没有故事源，首次调用请传 story_text 导入故事",
    }));
    await expect(
      runScriptPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }, { number: 2 }],
      }),
    ).rejects.toMatchObject({
      name: "AutoPipelineNeedsInputError",
      episode: 1,
      stage: "story",
      message: "项目还没有故事源，首次调用请传 story_text 导入故事",
    });
    // 整轮中止：不能接着跑第 2 集，也不能报成功。
    expect(seen).toHaveLength(1);
  });

  it("treats ceiling convergence as a normal finish (no needs-input error)", async () => {
    captureDriver(undefined, () => ({ kind: "ceiling", stage: "episode_1_sketches_generating" }));
    await expect(
      runScriptPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }],
      }),
    ).resolves.toBeUndefined();
  });
});

describe("runStoryboardPipeline", () => {
  it("drives each episode with ceiling=sketch and asks for the story version", async () => {
    const seen = captureDriver();
    await runStoryboardPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "base {user_requirement}",
      storyVersionCategoryLabel: "故事版",
      episodes: [{ number: 5, summary: "第五集梗概" }],
      targets: ["sketch"],
    });
    expect(seen[0].ceiling).toBe("sketch");
    // 故事版是出图的前置：缺则先补一张，再配色出草图。
    expect(seen[0].includeStoryVersion).toBe(true);
    expect(seen[0].storyVersion?.requirementFor(5)).toBe("第五集梗概");
    // 按用户口径分镜图不替用户跑场景/道具规划。
    expect(seen[0].planEpisodeAssets).toBeFalsy();
  });

  it("lifts the ceiling to the first frame once render is one of the targets", async () => {
    // 勾了渲染图就得推到首帧；上限里的「草图 → 首帧」顺序由计划器保证，
    // 所以两个都勾时天然先草图、再据草图出渲染图（这里只钉住上限不钉步序）。
    const seen = captureDriver();
    await runStoryboardPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "base",
      storyVersionCategoryLabel: "SV",
      episodes: [{ number: 5 }],
      targets: ["sketch", "render"],
    });
    expect(seen[0].ceiling).toBe("full");

    const renderOnly = captureDriver();
    await runStoryboardPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "base",
      storyVersionCategoryLabel: "SV",
      episodes: [{ number: 6 }],
      targets: ["render"],
    });
    // 只勾渲染图而草图缺失时，草图作为前置也会被补（首帧吃的就是草图）。
    expect(renderOnly[0].ceiling).toBe("full");
  });

  it("drops the story version request when the base prompt is blank", async () => {
    const seen = captureDriver();
    await runStoryboardPipeline({
      project,
      queryClient: fakeQueryClient(),
      storyVersionBasePrompt: "   ",
      storyVersionCategoryLabel: "SV",
      episodes: [{ number: 5 }],
      targets: ["sketch"],
    });
    // 关键不变量：没有提示词时绝对不请求 include_story_version，否则服务端的
    // story_version_pending 阶段会反复返回、客户端根本跑不动那一步。
    expect(seen[0].includeStoryVersion).toBe(false);
    expect(seen[0].storyVersion).toBeUndefined();
  });

  it("aborts before driving any episode when already stopped", async () => {
    const seen = captureDriver();
    await expect(
      runStoryboardPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 1 }],
        targets: ["sketch"],
        callbacks: { isStopped: () => true },
      }),
    ).rejects.toBeInstanceOf(AutoPipelineStoppedError);
    expect(seen).toHaveLength(0);
  });

  it("raises a typed needs-input error too (storyboard prerequisites live server-side)", async () => {
    captureDriver(undefined, () => ({
      kind: "requires-input",
      stage: "story",
      message: "项目还没有故事源",
    }));
    await expect(
      runStoryboardPipeline({
        project,
        queryClient: fakeQueryClient(),
        storyVersionBasePrompt: "base",
        storyVersionCategoryLabel: "SV",
        episodes: [{ number: 5 }],
        targets: ["sketch"],
      }),
    ).rejects.toBeInstanceOf(AutoPipelineNeedsInputError);
  });
});

describe("AutoPipelineNeedsInputError", () => {
  it("carries episode / stage and the server message", () => {
    const e = new AutoPipelineNeedsInputError(2, "story", "需要先导入故事");
    expect(e.name).toBe("AutoPipelineNeedsInputError");
    expect(e.episode).toBe(2);
    expect(e.stage).toBe("story");
    expect(e.message).toBe("需要先导入故事");
  });
});

describe("AutoPipelineFailedError shape", () => {
  it("carries episode/step and a message", () => {
    const e = new AutoPipelineFailedError(7, "identity", "boom", new Error("root"));
    expect(e.episode).toBe(7);
    expect(e.step).toBe("identity");
    expect(e.message).toBe("boom");
  });
});
