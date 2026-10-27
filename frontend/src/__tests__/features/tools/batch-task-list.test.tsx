// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const tasksMock = vi.hoisted(() => ({
  current: { data: { ok: true, data: [] as Array<Record<string, unknown>> } },
}));

vi.mock("@/lib/queries/tasks", () => ({
  useTasks: () => tasksMock.current,
}));

import { BatchTaskList } from "@/features/tools/components/BatchTaskList";

function renderList(kind = "lip-sync") {
  return render(<BatchTaskList project="demo" kind={kind} />);
}

describe("BatchTaskList", () => {
  beforeEach(() => {
    tasksMock.current = { data: { ok: true, data: [] } };
  });

  it("shows the open-result link and the archive hint for archived tool tasks", () => {
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_video_gen",
            task_id: "t1",
            status: "completed",
            display_name: "视频生成",
            metadata: { source_tool: "lip-sync" },
            result: {
              output_url: "/static/out.mp4",
              archived_to_library: true,
              library_item_id: "tool-job-1",
            },
          },
        ],
      },
    };

    renderList();

    // 任务记录可点击打开生成资源。
    const link = screen.getByRole("link", { name: /打开结果/ });
    expect(link).toHaveAttribute("href", "/static/out.mp4");
    // 归档成功提示：产物已存入资产库「待分类资产」。
    expect(screen.getByText("已存入资产库「待分类资产」")).toBeInTheDocument();
  });

  it("keeps canvas-style results without the archive hint", () => {
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_video_gen",
            task_id: "t2",
            status: "completed",
            display_name: "视频生成",
            metadata: { source_tool: "lip-sync" },
            result: { output_url: "/static/canvas.mp4" },
          },
        ],
      },
    };

    renderList();

    expect(screen.getByRole("link", { name: /打开结果/ })).toHaveAttribute(
      "href",
      "/static/canvas.mp4",
    );
    expect(screen.queryByText("已存入资产库「待分类资产」")).not.toBeInTheDocument();
  });

  it("shows the archive hint even without an extractable media url", () => {
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_video_gen",
            task_id: "t3",
            status: "completed",
            display_name: "视频生成",
            metadata: { source_tool: "lip-sync" },
            result: { archived_to_library: true, library_item_id: "tool-job-3" },
          },
        ],
      },
    };

    renderList();

    expect(screen.getByText("已存入资产库「待分类资产」")).toBeInTheDocument();
  });

  it("only shows tasks tagged with the current tool's source_tool", () => {
    // 同 task_type（freezone_video_gen）的其它功能 / 画布任务不带标签，
    // 或带不同 kind，都不应混进本页——这就是「工具任务归属」要修的点。
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_video_gen",
            task_id: "mine",
            status: "completed",
            display_name: "对口型",
            metadata: { source_tool: "lip-sync" },
            result: { output_url: "/static/mine.mp4" },
          },
          {
            task_type: "freezone_video_gen",
            task_id: "other",
            status: "completed",
            display_name: "换人",
            metadata: { source_tool: "replace-person" },
            result: { output_url: "/static/other.mp4" },
          },
          {
            task_type: "freezone_video_gen",
            task_id: "canvas",
            status: "completed",
            display_name: "画布视频",
            metadata: {},
            result: { output_url: "/static/canvas.mp4" },
          },
        ],
      },
    };

    renderList("lip-sync");

    const links = screen.getAllByRole("link", { name: /打开结果/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/static/mine.mp4");
    expect(screen.queryByText("换人")).not.toBeInTheDocument();
    expect(screen.queryByText("画布视频")).not.toBeInTheDocument();
  });

  it("labels rows with the tool's own name, not the shared task type", () => {
    // 后端兜底名是任务类型级的（对口型 / 换人 / 换产品共用一个 task_type），
    // 直接把 display_name 显出来会说不出自己点的是哪个功能。
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_video_gen",
            task_id: "t9",
            status: "running",
            progress: 0.2,
            display_name: "自由区视频",
            scope: "01KZ5R8ZZZY9M8T9F01H159RP7",
            metadata: { source_tool: "replace-product" },
          },
        ],
      },
    };

    renderList("replace-product");

    expect(screen.getByText("替换视频产品")).toBeInTheDocument();
    expect(screen.queryByText("自由区视频")).not.toBeInTheDocument();
    // job_id（scope）不拼进标题行。
    expect(screen.queryByText(/01KZ5R8/)).not.toBeInTheDocument();
  });

  it("marks narration-draft rows so a finished draft is not read as a finished video", () => {
    // 草稿与成片共用同一个工具 kind（同一张面板），名字一样：靠徽标分。
    tasksMock.current = {
      data: {
        ok: true,
        data: [
          {
            task_type: "freezone_explainer_script",
            task_id: "draft",
            status: "completed",
            metadata: { source_tool: "explainer-video" },
            result: { draft: { kind: "explainer", shots: [] } },
          },
          {
            task_type: "freezone_explainer_video",
            task_id: "video",
            status: "completed",
            metadata: { source_tool: "explainer-video" },
            result: { output_url: "/static/out.mp4" },
          },
        ],
      },
    };

    renderList("explainer-video");

    expect(screen.getByText("台词草稿")).toBeInTheDocument();
    // 只有草稿那一行带徽标，成片行不带（带的就是误标）。
    expect(screen.getAllByText("台词草稿")).toHaveLength(1);
  });
});
