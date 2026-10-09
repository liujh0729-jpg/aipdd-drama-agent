// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ─── react-i18next: return the key so assertions can read stable text ──────
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string | number>) => {
      if (!vars) return key;
      return Object.entries(vars).reduce(
        (acc, [k, v]) => acc.replace(`{{${k}}}`, String(v)),
        key,
      );
    },
  }),
}));

// ─── sonner: capture toast fires ───────────────────────────────────────────
const { toast } = vi.hoisted(() => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast }));

// ─── /lib/queries/render-plan: manual mutations with per-test handlers ─────
let planHandler: (params: unknown) => Promise<unknown> = async () => ({
  ok: true,
  data: null,
});
let executeHandler: (params: unknown) => Promise<unknown> = async () => ({
  ok: true,
  data: null,
});

vi.mock("@/lib/queries/render-plan", () => {
  const mockUse = (handler: () => (params: unknown) => Promise<unknown>) => {
    return () => {
      // useMutation-shaped facade (only the fields the dialog actually reads)
      return {
        mutate: (
          params: unknown,
          options?: {
            onSuccess?: (res: unknown) => void;
            onError?: (err: unknown) => void;
          },
        ) => {
          handler()(params)
            .then((res) => options?.onSuccess?.(res))
            .catch((err) => options?.onError?.(err));
        },
        mutateAsync: async (params: unknown) => handler()(params),
        isPending: false,
      };
    };
  };
  return {
    useRenderPlan: mockUse(() => planHandler),
    useRenderExecute: mockUse(() => executeHandler),
  };
});

import { RenderPlanDialog } from "@/components/episode/beat-workbench/render-plan-dialog";

function Wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function makeMultiGridPlan() {
  return {
    ok: true,
    data: {
      plan: [
        {
          mode_key: "2x3_1-1",
          rows: 2,
          cols: 3,
          beat_numbers: [1, 2, 3, 4, 5],
          location: "闹市街头",
          padding_count: 1,
          reasons: [],
          warnings: [],
        },
        {
          mode_key: "2x3_1-1",
          rows: 2,
          cols: 3,
          beat_numbers: [6, 7, 8],
          location: "公园",
          padding_count: 0,
          reasons: [],
          warnings: [],
        },
      ],
      plan_hash: "abc123def4567890",
      input_fingerprint: "xyz789abc1234567",
      strategy: "location" as const,
      total_beats: 8,
      total_grids: 2,
    },
  };
}

beforeEach(() => {
  toast.error.mockClear();
  toast.success.mockClear();
  planHandler = async () => makeMultiGridPlan();
  executeHandler = async () => ({
    ok: true,
    data: {
      task_type: "render_plan",
      message: "started",
      scope: "location__abc123def4567890",
      resolved_grids: makeMultiGridPlan().data.plan,
    },
  });
});

afterEach(() => cleanup());

describe("RenderPlanDialog — /plan logical errors", () => {
  it("surfaces ok:false plan responses and does not leave a confirmable empty plan", async () => {
    planHandler = async () => ({
      ok: false,
      error: "未检测到颜色分配，请先调用 assign-colors 接口",
    });

    const onOpenChange = vi.fn();
    const onDispatched = vi.fn();
    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={onOpenChange}
          project="demo"
          episode={1}
          beatIndices={[1, 2, 3]}
          aspectMode="9:16"
          onDispatched={onDispatched}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "未检测到颜色分配，请先调用 assign-colors 接口",
      );
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onDispatched).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "episode.renderPlan.unavailable" }),
    ).toBeDisabled();
  });

  it("surfaces invalid_beats as a toast and closes", async () => {
    const error: { response: { status: number; json: () => Promise<unknown> } } =
      {
        response: {
          status: 400,
          json: async () => ({
            ok: false,
            error: "invalid_beats",
            data: { invalid: [99] },
          }),
        },
      };
    planHandler = async () => {
      throw error;
    };

    const onOpenChange = vi.fn();
    const onDispatched = vi.fn();
    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={onOpenChange}
          project="demo"
          episode={1}
          beatIndices={[99]}
          aspectMode="9:16"
          onDispatched={onDispatched}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "episode.renderPlan.errors.invalidBeats",
      );
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onDispatched).not.toHaveBeenCalled();
  });

  it("surfaces no_beats as a distinct toast", async () => {
    const error: { response: { status: number; json: () => Promise<unknown> } } =
      {
        response: {
          status: 400,
          json: async () => ({
            ok: false,
            error: "no_beats",
            data: { episode: 7 },
          }),
        },
      };
    planHandler = async () => {
      throw error;
    };

    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={7}
          beatIndices={[1]}
          aspectMode="9:16"
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "episode.renderPlan.errors.noBeats",
      );
    });
  });

  it("falls back to common.error for unknown status", async () => {
    const error: { response: { status: number } } = { response: { status: 500 } };
    planHandler = async () => {
      throw error;
    };

    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          beatIndices={[1]}
          aspectMode="9:16"
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("common.error");
    });
  });

  it("surfaces unknown 400 backend messages directly", async () => {
    const backendMessage =
      "Render 前请先到「草图」点击「AI 检测」识别出场身份，或在「更多 > 出场身份」手工标注。";
    const error: { response: { status: number; json: () => Promise<unknown> } } =
      {
        response: {
          status: 400,
          json: async () => ({
            ok: false,
            error: backendMessage,
          }),
        },
      };
    planHandler = async () => {
      throw error;
    };

    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          beatIndices={[1]}
          aspectMode="9:16"
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(backendMessage);
    });
  });
});

describe("RenderPlanDialog — /plan happy path", () => {
  it("fetches the plan and renders one card per grid entry", async () => {
    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          beatIndices={[1, 2, 3, 4, 5, 6, 7, 8]}
          aspectMode="9:16"
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    // Two plan entries → two location labels ("闹市街头", "公园") visible.
    await waitFor(() => {
      expect(screen.getByTitle("闹市街头")).toBeInTheDocument();
      expect(screen.getByTitle("公园")).toBeInTheDocument();
    });
  });

  it("keeps render planning fixed to location strategy", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const planCalls: unknown[] = [];
    const executeCalls: unknown[] = [];
    planHandler = async (params) => {
      planCalls.push(params);
      return makeMultiGridPlan();
    };
    executeHandler = async (params) => {
      executeCalls.push(params);
      return {
        ok: true,
        data: {
          task_type: "render_plan",
          message: "started",
          scope: "location__hash",
          resolved_grids: [],
        },
      };
    };

    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          beatIndices={[1, 2, 3, 4, 5, 6, 7, 8]}
          aspectMode="9:16"
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    await screen.findByTitle("闹市街头");
    expect(
      screen.queryByRole("combobox", { name: "episode.renderPlan.strategy" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("episode.renderPlan.strategyNaive")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "episode.renderPlan.split" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "episode.renderPlan.mergeNext" }),
    ).not.toBeInTheDocument();
    expect(planCalls).toContainEqual(expect.objectContaining({ strategy: "location" }));

    await user.click(screen.getByRole("button", { name: "episode.renderPlan.confirm" }));
    await waitFor(() => {
      expect(executeCalls).toContainEqual(
        expect.objectContaining({ strategy: "location" }),
      );
    });
    expect(executeCalls[0]).not.toMatchObject({ custom_plan: true });
  });

});

function makeSingleBeat1x1Plan() {
  return {
    ok: true,
    data: {
      plan: [
        {
          mode_key: "1x1_2-3",
          rows: 1,
          cols: 1,
          beat_numbers: [1],
          location: "A",
          padding_count: 0,
          reasons: [],
          warnings: [],
        },
      ],
      plan_hash: "plan_hash_1",
      input_fingerprint: "fp_1",
      strategy: "location" as const,
      total_beats: 1,
      total_grids: 1,
    },
  };
}

describe("RenderPlanDialog — single beat confirmation", () => {
  it("opens directly in forced 1x1 mode when requested by the caller", async () => {
    const user = userEvent.setup();
    const planCalls: unknown[] = [];
    const executeCalls: unknown[] = [];
    planHandler = async (params) => {
      planCalls.push(params);
      return makeMultiGridPlan();
    };
    executeHandler = async (params) => {
      executeCalls.push(params);
      return {
        ok: true,
        data: {
          task_type: "render_plan",
          message: "started",
          scope: "location__forced",
          resolved_grids: [],
        },
      };
    };

    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          episode={1}
          beatIndices={[1, 2]}
          aspectMode="16:9"
          defaultForceOneByOne
          onDispatched={vi.fn()}
        />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(planCalls).toContainEqual(
        expect.objectContaining({
          aspect_mode: "16:9",
          force_one_by_one: true,
        }),
      );
    });
    expect(screen.queryByText("episode.renderPlan.forceOneByOne")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "episode.renderPlan.confirm" }));
    await waitFor(() => {
      expect(executeCalls).toContainEqual(
        expect.objectContaining({
          force_one_by_one: true,
        }),
      );
    });
  });

  it("requires a manual confirm for a fresh single-beat 1x1 plan", async () => {
    const user = userEvent.setup();
    planHandler = async () => makeSingleBeat1x1Plan();
    const executeCalls: unknown[] = [];
    executeHandler = async (params) => {
      executeCalls.push(params);
      return {
        ok: true,
        data: {
          task_type: "render_plan",
          message: "started",
          scope: "location__plan_hash_1",
          resolved_grids: makeSingleBeat1x1Plan().data.plan,
          task_ids: ["task_render_1"],
        },
      };
    };

    const onDispatched = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <Wrapper>
        <RenderPlanDialog
          open
          onOpenChange={onOpenChange}
          project="demo"
          episode={1}
          beatIndices={[1]}
          aspectMode="9:16"
          onDispatched={onDispatched}
        />
      </Wrapper>,
    );

    expect(executeCalls).toHaveLength(0);
    await screen.findByRole("button", { name: "episode.renderPlan.confirm" });
    await user.click(screen.getByRole("button", { name: "episode.renderPlan.confirm" }));
    await waitFor(() => expect(executeCalls).toHaveLength(1));
    await waitFor(() =>
      expect(onDispatched).toHaveBeenCalledWith(["task_render_1"]),
    );
  });
});
