// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 技能面板：列表错误态 / 下载并发与错误详情 / 控件可访问性。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { SkillsPanel } from "@/features/agent/components/SkillsPanel";

const hoisted = vi.hoisted(() => ({
  list: {
    data: { data: [] as Array<Record<string, unknown>> },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  },
  download: vi.fn(),
  toggle: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/queries/agent", () => ({
  useAgentSkills: () => hoisted.list,
  useAgentSkillDetail: () => ({ data: undefined, isLoading: false, isError: false }),
  downloadAgentSkill: hoisted.download,
  useInstallAgentSkill: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetAgentSkillEnabled: () => ({ mutate: hoisted.toggle, isPending: false }),
  useDeleteAgentSkill: () => ({ mutate: hoisted.remove }),
}));

function skill(name: string) {
  return {
    name,
    title: name,
    description: "",
    enabled: true,
    source: "user",
    files: [],
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SkillsPanel />
    </QueryClientProvider>,
  );
}

describe("SkillsPanel", () => {
  beforeEach(() => {
    hoisted.list = {
      data: { data: [] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    };
    hoisted.download.mockReset();
    vi.mocked(toast.error).mockReset();
  });

  it("shows a fetch error instead of the empty state and retries", async () => {
    hoisted.list.isError = true;
    const user = userEvent.setup();
    renderPanel();

    expect(screen.getByText("agent.skills.loadFailed")).toBeInTheDocument();
    expect(screen.queryByText("agent.skills.empty")).toBeNull();

    await user.click(screen.getByRole("button", { name: "agent.skills.retry" }));
    expect(hoisted.list.refetch).toHaveBeenCalledTimes(1);
  });

  it("tracks downloads per skill and surfaces backend error details", async () => {
    hoisted.list.data = { data: [skill("one"), skill("two")] };
    let finishOne: () => void = () => undefined;
    hoisted.download.mockImplementation((name: string) =>
      name === "one"
        ? new Promise<void>((resolve) => {
            finishOne = resolve;
          })
        : Promise.reject(new Error("技能包体积过大")),
    );

    const user = userEvent.setup();
    renderPanel();
    const buttons = screen.getAllByRole("button", { name: "agent.skills.download" });
    expect(buttons).toHaveLength(2);

    await user.click(buttons[0]);
    await user.click(buttons[1]);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("技能包体积过大"));
    await waitFor(() => expect(buttons[1]).not.toBeDisabled());
    // 另一个下载仍在进行，不能被先完成/失败的请求清掉 loading。
    expect(buttons[0]).toBeDisabled();

    finishOne();
    await waitFor(() => expect(buttons[0]).not.toBeDisabled());
  });

  it("labels the enable switch with the action it performs", () => {
    hoisted.list.data = { data: [skill("one")] };
    renderPanel();
    expect(screen.getByRole("switch", { name: "agent.skills.disable" })).toBeInTheDocument();
  });
});
