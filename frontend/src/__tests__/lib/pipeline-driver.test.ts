// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 唯一客户端流水线循环（pipeline-driver）单测：决策交给 mock 的
// 服务端计划器 planPipeline，这里钉住循环/上限/发射/等待/故事类型化步/停止/失败/
// 检查点确认等纯客户端行为，证明 episode-auto 只是它的一层壳。
import { beforeEach, describe, expect, it, vi } from "vitest";

const planPipeline = vi.hoisted(() => vi.fn());
const listTasks = vi.hoisted(() => vi.fn(async () => [] as unknown[]));
const awaitTaskCompletion = vi.hoisted(() =>
  vi.fn(async () => ({ status: "completed" })),
);
const submitFreezoneGen = vi.hoisted(() =>
  vi.fn(async () => ({ task_type: "freezone_gen", job_id: "j1", task_key: "fk" })),
);
const fetchFreezoneJobResult = vi.hoisted(() =>
  vi.fn(async () => ({ url: "https://cdn/s.png" })),
);
const submitFreezoneAddVideoCharacterLibraryItem = vi.hoisted(() => vi.fn(async () => ({})));
const api = vi.hoisted(() => ({
  post: vi.fn(async (): Promise<Record<string, unknown>> => ({ ok: true })),
  put: vi.fn(async (): Promise<Record<string, unknown>> => ({ ok: true })),
  patch: vi.fn(async (): Promise<Record<string, unknown>> => ({ ok: true })),
  get: vi.fn(async (): Promise<Record<string, unknown>> => ({ ok: true })),
}));
const jsonWithBackendError = vi.hoisted(() =>
  vi.fn(async (req: Promise<unknown>) => req),
);

vi.mock("@/api/pipeline", () => ({ planPipeline }));
vi.mock("@/api/tasks", () => ({ listTasks, awaitTaskCompletion }));
vi.mock("@/api/ops", () => ({
  submitFreezoneGen,
  fetchFreezoneJobResult,
  submitFreezoneAddVideoCharacterLibraryItem,
}));
vi.mock("@/lib/api", () => ({ api }));
vi.mock("@/lib/api-errors", () => ({ jsonWithBackendError }));

import {
  PipelineStoppedError,
  runPipelineDriven,
  type PipelineDriverOptions,
} from "@/lib/pipeline-driver";

const project = "proj-a";

/** 让 planPipeline 依序返回给定步骤，超出后返回最后一步（通常是 done）。 */
function queueSteps(steps: Array<Record<string, unknown>>) {
  let i = 0;
  planPipeline.mockImplementation(async () => steps[Math.min(i++, steps.length - 1)]);
}

function step(over: Record<string, unknown>): Record<string, unknown> {
  return { ok: true, status: "planned", actions: [], ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  listTasks.mockImplementation(async () => []);
});

describe("runPipelineDriven loop", () => {
  it("executes a planned step's REST action, waits for idle, then advances to done", async () => {
    api.post.mockResolvedValueOnce({ ok: true, task_key: "k-script", task_type: "script_writer" });
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [
          {
            method: "POST",
            path: "/api/v1/projects/proj-a/episodes/1/story/to-script",
            body: {},
            await_task: true,
          },
        ],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({ project, mode: "auto", episode: 1 });
    expect(planPipeline).toHaveBeenCalledTimes(2);
    expect(api.post).toHaveBeenCalledTimes(1);
    // await_task 时阻塞到任务完成。
    expect(awaitTaskCompletion).toHaveBeenCalledTimes(1);
  });

  it("honours the ceiling: does not execute past the requested stage", async () => {
    queueSteps([
      step({
        stage: "episode_1_sketches_generating",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/sketches/generate" },
        ],
      }),
    ]);
    const res = await runPipelineDriven({
      project,
      mode: "auto",
      episode: 1,
      ceiling: "script",
    });
    // 草图阶段(rank 3)超出 script 上限(rank 2)：不发射、只取一步。
    expect(res.kind).toBe("ceiling");
    expect(api.post).not.toHaveBeenCalled();
    expect(planPipeline).toHaveBeenCalledTimes(1);
  });

  it("waits when the planner reports a running stage, then continues", async () => {
    queueSteps([
      step({ stage: "screenplay_generating", status: "running" }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({ project, mode: "auto" });
    expect(planPipeline).toHaveBeenCalledTimes(2);
    expect(listTasks).toHaveBeenCalled(); // waitUntilIdle
    expect(api.post).not.toHaveBeenCalled();
  });

  it("waits on an episode-level running stage even at full ceiling (no premature stop)", async () => {
    // 回归：集级运行态 stage 后缀是 {task_type}_running（不在 EPISODE_STAGE_RANK），
    // 旧排序会先走 ceiling 判定→rank 99 越界→提前返回不等待。
    queueSteps([
      step({ stage: "episode_1_script_writer_running", status: "running", episode: 1 }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({ project, mode: "auto", episode: 1, ceiling: "full" });
    expect(listTasks).toHaveBeenCalled(); // 先等待空闲
    expect(planPipeline).toHaveBeenCalledTimes(2); // 再续跑到 done
    expect(api.post).not.toHaveBeenCalled();
  });

  it("reports requires_input (not completion) so the caller can prompt for input", async () => {
    queueSteps([step({ stage: "story", status: "requires_input", message: "没有故事源" })]);
    const res = await runPipelineDriven({ project, mode: "auto" });
    expect(res.kind).toBe("requires-input");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("reports completed on a terminal done/idle stage", async () => {
    queueSteps([step({ stage: "done", status: "done", message: "全部完成" })]);
    const res = await runPipelineDriven({ project, mode: "auto" });
    expect(res.kind).toBe("completed");
  });

  it("throws on a failed stage", async () => {
    queueSteps([step({ stage: "screenplay_failed", status: "failed", error: "模型超时" })]);
    await expect(runPipelineDriven({ project, mode: "auto" })).rejects.toThrow("模型超时");
  });

  it("stops before the first plan call when already stopped", async () => {
    const opts: PipelineDriverOptions = {
      project,
      mode: "auto",
      isStopped: () => true,
    };
    await expect(runPipelineDriven(opts)).rejects.toBeInstanceOf(PipelineStoppedError);
    expect(planPipeline).not.toHaveBeenCalled();
  });

  it("manual mode executes one step then returns", async () => {
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/story/to-script" },
        ],
      }),
    ]);
    const res = await runPipelineDriven({ project, mode: "manual", episode: 1 });
    expect(res.kind).toBe("manual-step");
    expect(planPipeline).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

describe("checkpoint approval gate", () => {
  it("does not fire actions when the gate rejects", async () => {
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/story/to-script" },
        ],
      }),
    ]);
    await runPipelineDriven({
      project,
      mode: "checkpoint",
      episode: 1,
      approveGate: async () => false,
    });
    expect(api.post).not.toHaveBeenCalled();
    expect(planPipeline).toHaveBeenCalledTimes(1);
  });

  it("fires actions when the gate approves", async () => {
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/story/to-script" },
        ],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({
      project,
      mode: "checkpoint",
      episode: 1,
      approveGate: async () => true,
    });
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

describe("story_version typed action", () => {
  it("runs the freezone gen + library ingest when a story version config is provided", async () => {
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/story/to-script" },
          { kind: "story_version", episode: 1 },
        ],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({
      project,
      mode: "auto",
      episode: 1,
      ceiling: "script",
      includeStoryVersion: true,
      storyVersion: {
        basePrompt: "IMG {user_requirement}",
        categoryLabel: "故事版",
        requirementFor: () => "第一集梗概",
      },
    });
    expect(submitFreezoneGen).toHaveBeenCalledTimes(1);
    expect(fetchFreezoneJobResult).toHaveBeenCalledTimes(1);
    expect(submitFreezoneAddVideoCharacterLibraryItem).toHaveBeenCalledWith(
      project,
      expect.objectContaining({ category: "storyVersion" }),
    );
  });

  it("skips the typed action when there is no story version config", async () => {
    queueSteps([
      step({
        stage: "episode_1_script_generating",
        episode: 1,
        actions: [{ kind: "story_version", episode: 1 }],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    await runPipelineDriven({ project, mode: "auto", episode: 1, ceiling: "script" });
    expect(submitFreezoneGen).not.toHaveBeenCalled();
  });
});

describe("request body", () => {
  it("always plans with execute=false (client executes actions itself)", async () => {
    queueSteps([step({ stage: "done", status: "done" })]);
    await runPipelineDriven({
      project,
      mode: "auto",
      episode: 7,
      settings: { aspectRatio: "16:9", model: "nb", targetBeats: 30 },
    });
    const body = planPipeline.mock.calls[0][1];
    expect(body.execute).toBe(false);
    expect(body.episode).toBe(7);
    expect(body.aspect_ratio).toBe("16:9");
    expect(body.model).toBe("nb");
    expect(body.target_beats).toBe(30);
  });

  it("maps duration/episode-count settings onto the plan request", async () => {
    queueSteps([step({ stage: "done", status: "done" })]);
    await runPipelineDriven({
      project,
      mode: "auto",
      settings: {
        storyStyle: "suspense",
        episodeCount: 12,
        totalSeconds: 600,
        secondsPerEpisode: 50,
      },
    });
    const body = planPipeline.mock.calls[0][1];
    expect(body.story_style).toBe("suspense");
    expect(body.episode_count).toBe(12);
    expect(body.total_seconds).toBe(600);
    expect(body.seconds_per_episode).toBe(50);
  });

  it("omits unset duration/episode-count fields", async () => {
    queueSteps([step({ stage: "done", status: "done" })]);
    await runPipelineDriven({ project, mode: "auto", settings: { aspectRatio: "2:3" } });
    const body = planPipeline.mock.calls[0][1];
    expect("episode_count" in body).toBe(false);
    expect("total_seconds" in body).toBe(false);
    expect("seconds_per_episode" in body).toBe(false);
  });
});

describe("episode asset-planning prerequisite", () => {
  it("fires scene/props plan actions within the script ceiling and labels them", async () => {
    api.post
      .mockResolvedValueOnce({ ok: true, task_key: "k-scene", task_type: "episode_scene_planner" })
      .mockResolvedValueOnce({ ok: true, task_key: "k-prop", task_type: "episode_prop_planner" });
    queueSteps([
      step({
        stage: "episode_1_scenes_planning",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/scenes/plan", await_task: true },
        ],
      }),
      step({
        stage: "episode_1_props_planning",
        episode: 1,
        actions: [
          { method: "POST", path: "/api/v1/projects/proj-a/episodes/1/props/plan", await_task: true },
        ],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    const labels: string[] = [];
    const res = await runPipelineDriven({
      project,
      mode: "auto",
      episode: 1,
      ceiling: "script",
      planEpisodeAssets: true,
      onStep: (_ep, label) => labels.push(label),
    });
    // 规划阶段与身份规划同属脚本前前置（rank 1），不该被 script 上限挡下。
    expect(res.kind).toBe("completed");
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(labels).toEqual(["scenePlan", "propPlan"]);
    expect(awaitTaskCompletion).toHaveBeenCalledTimes(2);
  });

  it("sends plan_episode_assets only when the caller opts in", async () => {
    queueSteps([step({ stage: "done", status: "done" })]);
    await runPipelineDriven({ project, mode: "auto", episode: 1, planEpisodeAssets: true });
    expect(planPipeline.mock.calls[0][1].plan_episode_assets).toBe(true);
    // 「自动化草图」按口径不补场景/道具规划：字段不下发，服务端也就不会排这两步。
    await runPipelineDriven({ project, mode: "auto", episode: 1, ceiling: "sketch" });
    expect("plan_episode_assets" in planPipeline.mock.calls[1][1]).toBe(false);
  });

  it("runs the story_version_pending stage instead of tripping the ceiling", async () => {
    queueSteps([
      step({
        stage: "episode_2_story_version_pending",
        episode: 2,
        actions: [{ kind: "story_version", episode: 2 }],
      }),
      step({ stage: "done", status: "done" }),
    ]);
    const res = await runPipelineDriven({
      project,
      mode: "auto",
      episode: 2,
      ceiling: "sketch",
      includeStoryVersion: true,
      storyVersion: {
        basePrompt: "P {user_requirement}",
        categoryLabel: "SV",
        requirementFor: () => "req",
      },
    });
    // 回归：后端新增的阶段名必须登记进 EPISODE_STAGE_RANK，漏登记会算成 rank 99
    // 被上限直接挡停，故事版这一步就永远跑不到。
    expect(res.kind).toBe("completed");
    expect(submitFreezoneGen).toHaveBeenCalledTimes(1);
  });
});
