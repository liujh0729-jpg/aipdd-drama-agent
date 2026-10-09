// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 视频模型目录只有一个来源：/freezone/video/models 下发的真实列表。
//
// 以前 useFreezoneVideoModels 在 pending / 拉取失败时会顶上一份写死的 VIDEO_MODELS，
// 用户先看到假列表、再被真实列表闪一下，失败时更是永久停在一堆后台根本没配的模型上。
// 这份测试直接盯 module-level store（绕开 React），守住：加载中、拉取成功、拉取失败
// 三种状态下，读到的列表都不能出现任何写死模型。

import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchFreezoneVideoModels } from "@/api/ops";
import {
  prefetchFreezoneVideoModels,
  readFreezoneVideoModels,
  refreshFreezoneVideoModels,
} from "@/features/canvas/hooks/useFreezoneVideoModels";

// 只供 hook 运行期真正用到的取数函数。不用 importOriginal 把庞大的 @/api/ops 图整块
// 拉进 worker（会在 forks 环境里崩）；store 里的其他 import 都是 type-only（会被擦除）。
vi.mock("@/api/ops", () => ({
  fetchFreezoneVideoModels: vi.fn(),
}));

const fetchMock = vi.mocked(fetchFreezoneVideoModels);

// 每个用例用独立的 project 名，绕开 module-level store 的去重，用例互不污染。
let projectSeq = 0;
function freshProject(): string {
  projectSeq += 1;
  return `proj-video-${projectSeq}`;
}

/** 等待 prefetch/refresh 触发的异步 fetch 落地。 */
async function settleFetch(): Promise<void> {
  for (let i = 0; i < 50; i += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function expectNoHardcodedModels(
  models: readonly { id: string; apiModel?: string }[],
): void {
  // 历史上写死过、但后台未必真配的 seedance id 一律不许凭空出现。
  expect(
    models.some(
      (model) =>
        /seedance/i.test(model.id) || /seedance/i.test(model.apiModel ?? ""),
    ),
  ).toBe(false);
}

describe("useFreezoneVideoModels store 不再合成写死模型列表", () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });

  it("加载中读到空列表，而不是任何写死模型", async () => {
    let resolveFetch: (value: never[]) => void = () => {};
    fetchMock.mockReturnValue(
      new Promise<never[]>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const project = freshProject();
    prefetchFreezoneVideoModels(project);
    // 首帧：真实列表还没回来，读到的是空目录，而不是 seedance 假列表。
    const pending = readFreezoneVideoModels(project);
    expect(pending).toEqual([]);
    expectNoHardcodedModels(pending);
    // 收尾：兑现悬挂的 in-flight promise，别把 handle 漏给 worker。
    resolveFetch([]);
    await settleFetch();
  });

  it("拉取成功时如实反映目录模型", async () => {
    fetchMock.mockResolvedValue([
      {
        id: "ap-minimax-h3",
        providerId: "newapi",
        apiModel: "newapi_ap-minimax-h3",
        label: "MiniMax H3",
      },
    ] as never);
    const project = freshProject();
    prefetchFreezoneVideoModels(project);
    await settleFetch();
    expect(readFreezoneVideoModels(project).map((model) => model.id)).toEqual([
      "ap-minimax-h3",
    ]);
  });

  it("拉取失败时是诚实的空列表，绝不退回写死列表", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    const project = freshProject();
    prefetchFreezoneVideoModels(project);
    await settleFetch();
    const failed = readFreezoneVideoModels(project);
    expect(failed).toEqual([]);
    expectNoHardcodedModels(failed);
  });

  it("refresh 重拉时保留当前真实列表、原地替换，不闪回写死列表", async () => {
    fetchMock.mockResolvedValue([
      { id: "m1", providerId: "newapi", apiModel: "newapi_m1", label: "M1" },
    ] as never);
    const project = freshProject();
    prefetchFreezoneVideoModels(project);
    await settleFetch();
    expect(readFreezoneVideoModels(project).map((m) => m.id)).toEqual(["m1"]);

    // 第二次拉取换了内容；refresh 期间旧列表仍在（不是空、也不是写死列表）。
    fetchMock.mockResolvedValue([
      { id: "m2", providerId: "newapi", apiModel: "newapi_m2", label: "M2" },
    ] as never);
    refreshFreezoneVideoModels(project);
    expect(readFreezoneVideoModels(project).map((m) => m.id)).toEqual(["m1"]);
    expectNoHardcodedModels(readFreezoneVideoModels(project));
    await settleFetch();
    expect(readFreezoneVideoModels(project).map((m) => m.id)).toEqual(["m2"]);
  });
});
