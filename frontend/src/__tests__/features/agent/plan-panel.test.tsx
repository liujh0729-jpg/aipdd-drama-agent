// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 待办计划面板定向测试：清单渲染 / 勾选 / 内联编辑 / 删除 / 新增。
// 数据层整体桩掉——断言聚焦面板交互后调用到的数据层载荷，不触网。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlanPanel } from "@/features/agent/components/PlanPanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

interface PlanItemFixture {
  id: string;
  title: string;
  done: boolean;
  source: string;
  created_at: string;
  updated_at: string;
  done_at: string | null;
  stage?: string | null;
  episode?: number | null;
}

const hoisted = vi.hoisted(() => ({
  items: [] as PlanItemFixture[],
  addCalls: [] as Array<{ project: string; title: string; stage?: string; episode?: number }>,
  updateCalls: [] as Array<Record<string, unknown>>,
  deleteCalls: [] as Array<{ project: string; itemId: string }>,
  runCalls: [] as Array<Record<string, unknown>>,
  runResult: { status: "done" } as Record<string, unknown>,
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentPlan: () => ({
    data: { data: { items: hoisted.items, max_items: 30 } },
    isLoading: false,
  }),
  useAddAgentPlanItem: () => ({
    mutateAsync: vi.fn(
      async (payload: { project: string; title: string; stage?: string; episode?: number }) => {
        hoisted.addCalls.push(payload);
      },
    ),
    isPending: false,
  }),
  useUpdateAgentPlanItem: () => ({
    mutate: (payload: Record<string, unknown>) => {
      hoisted.updateCalls.push(payload);
    },
    mutateAsync: vi.fn(async (payload: Record<string, unknown>) => {
      hoisted.updateCalls.push(payload);
    }),
  }),
  useDeleteAgentPlanItem: () => ({
    mutate: (payload: { project: string; itemId: string }) => {
      hoisted.deleteCalls.push(payload);
    },
  }),
  useRunAgentPlan: () => ({
    mutateAsync: vi.fn(async (payload: Record<string, unknown>) => {
      hoisted.runCalls.push(payload);
      return hoisted.runResult;
    }),
    isPending: false,
  }),
}));

function fixtureItems(): PlanItemFixture[] {
  return [
    {
      id: "plan_a",
      title: "写剧本",
      done: false,
      source: "agent",
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      done_at: null,
      stage: "script",
      episode: 1,
    },
    {
      id: "plan_b",
      title: "画分镜",
      done: true,
      source: "agent",
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T01:00:00Z",
      done_at: "2026-10-01T01:00:00Z",
    },
  ];
}

describe("PlanPanel", () => {
  beforeEach(() => {
    hoisted.items = fixtureItems();
    hoisted.addCalls = [];
    hoisted.updateCalls = [];
    hoisted.deleteCalls = [];
    hoisted.runCalls = [];
    hoisted.runResult = { status: "done" };
  });

  it("renders stored items with the pending count", () => {
    render(<PlanPanel project="alpha" />);

    expect(screen.getByText("写剧本")).toBeInTheDocument();
    expect(screen.getByText("画分镜")).toBeInTheDocument();
    expect(
      screen.getByText('agent.plan.count:{"total":2,"pending":1}'),
    ).toBeInTheDocument();
    expect(screen.getByText("agent.plan.limitHint")).toBeInTheDocument();
  });

  it("toggles an item through its checkbox", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0].getAttribute("aria-checked")).toBe("false");
    expect(boxes[1].getAttribute("aria-checked")).toBe("true");

    await user.click(boxes[0]);
    await user.click(boxes[1]);

    expect(hoisted.updateCalls).toEqual([
      { project: "alpha", itemId: "plan_a", done: true },
      { project: "alpha", itemId: "plan_b", done: false },
    ]);
  });

  it("adds a task from the input", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    await user.type(screen.getByPlaceholderText("agent.plan.placeholder"), "拆解分镜脚本");
    await user.click(screen.getByRole("button", { name: "agent.plan.add" }));

    await waitFor(() =>
      expect(hoisted.addCalls).toEqual([{ project: "alpha", title: "拆解分镜脚本" }]),
    );
  });

  it("edits a task title inline", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    await user.click(screen.getAllByTitle("agent.plan.edit")[0]);
    const input = screen.getByDisplayValue("写剧本");
    await user.clear(input);
    await user.type(input, "写剧本初稿{Enter}");

    await waitFor(() =>
      expect(hoisted.updateCalls).toEqual([
        { project: "alpha", itemId: "plan_a", title: "写剧本初稿" },
      ]),
    );
  });

  it("removes a task", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    await user.click(screen.getAllByTitle("agent.plan.delete")[1]);

    expect(hoisted.deleteCalls).toEqual([{ project: "alpha", itemId: "plan_b" }]);
  });

  it("shows the empty state without items", () => {
    hoisted.items = [];
    render(<PlanPanel project="alpha" />);

    expect(screen.getByText("agent.plan.empty")).toBeInTheDocument();
    expect(screen.getByText("agent.plan.emptyHint")).toBeInTheDocument();
  });

  it("shows stage badges and runs the plan", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    // 阶段名同时出现在新增下拉的 option 里；这里只认任务行上的徽标。
    const stageBadges = screen.getAllByText(/agent\.plan\.stages\.script/);
    expect(stageBadges.some((node) => node.tagName === "SPAN")).toBe(true);
    expect(screen.getByText('agent.plan.episode:{"n":1}')).toBeInTheDocument();

    const runButton = screen.getByRole("button", { name: /agent\.plan\.run/ });
    expect(runButton).not.toBeDisabled();
    await user.click(runButton);

    await waitFor(() =>
      expect(hoisted.runCalls).toEqual([{ project: "alpha", mode: "auto", budget_s: 90 }]),
    );
  });

  it("adds a staged task with an episode", async () => {
    const user = userEvent.setup();
    render(<PlanPanel project="alpha" />);

    await user.type(screen.getByPlaceholderText("agent.plan.placeholder"), "生成第 2 集草图");
    await user.selectOptions(screen.getByLabelText("agent.plan.stageLabel"), "sketches");
    await user.type(screen.getByPlaceholderText("agent.plan.episodePlaceholder"), "2");
    await user.click(screen.getByRole("button", { name: "agent.plan.add" }));

    await waitFor(() =>
      expect(hoisted.addCalls).toEqual([
        { project: "alpha", title: "生成第 2 集草图", stage: "sketches", episode: 2 },
      ]),
    );
  });

  it("disables run when no pending task has a stage", () => {
    hoisted.items = hoisted.items.map((item) => ({ ...item, stage: null }));
    render(<PlanPanel project="alpha" />);

    expect(screen.getByRole("button", { name: /agent\.plan\.run/ })).toBeDisabled();
  });
});