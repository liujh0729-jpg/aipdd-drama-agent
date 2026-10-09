// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req45] 设置 → 抖音 Cookie 的「从 Chrome 抓取」行为契约。
//
// 钉三件容易退化的事：
// 1. 抓不到时后端把成因写在 400 detail 里（端口没开 / 那个 Chrome 没登录抖音），
//    必须**原样贴出**——换成一句「抓取失败」就等于把用户支去瞎试；
// 2. 抓取与手工粘贴写的是同一份凭据，进行中要互锁，否则会互相覆盖；
// 3. 自动记录这条主路要在页面上讲清楚（用户不必每次手贴）。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DouyinCookieSection } from "@/components/settings/douyin-cookie-section";

const saveMutate = vi.hoisted(() => vi.fn());
const captureMutate = vi.hoisted(() => vi.fn());
const cookieState = vi.hoisted(() => ({
  current: { configured: false, cookieCount: 0, cookieNames: [], length: 0 },
}));
const capturePending = vi.hoisted(() => ({ current: false }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: () => ({
    data: { data: { douyinCookie: cookieState.current } },
    error: null,
  }),
  useSaveDouyinCookie: () => ({ mutate: saveMutate, isPending: false }),
  useCaptureDouyinCookie: () => ({
    mutate: captureMutate,
    isPending: capturePending.current,
  }),
}));

vi.mock("sonner", () => ({ toast }));

beforeEach(() => {
  vi.clearAllMocks();
  cookieState.current = { configured: false, cookieCount: 0, cookieNames: [], length: 0 };
  capturePending.current = false;
});

describe("DouyinCookieSection 从 Chrome 抓取", () => {
  it("提供抓取入口，并说明提取成功会自动记录", () => {
    render(<DouyinCookieSection />);
    expect(
      screen.getByRole("button", { name: "从 Chrome 抓取" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/^自动记录：/)).toBeInTheDocument();
  });

  it("抓取成功按后端回的数量提示，不出现 Cookie 原文", async () => {
    captureMutate.mockImplementation((_v, opts) =>
      opts?.onSuccess?.({ data: { configured: true, cookieCount: 3, cookieNames: ["sessionid"], length: 128 } }),
    );
    render(<DouyinCookieSection />);
    fireEvent.click(screen.getByRole("button", { name: "从 Chrome 抓取" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("已从 Chrome 记录抖音登录态"),
    );
    expect(toast.success.mock.calls[0]?.[0]).toContain("3");
    expect(JSON.stringify(toast.success.mock.calls)).not.toContain("sessionid=");
  });

  it("抓不到时把后端成因原样贴出，而不是笼统一句失败", async () => {
    const reason =
      "宿主 Chrome 里有抖音的 Cookie，但没有登录态字段（sessionid / sid_tt 等）——请在弹出的那个 Chrome 里登录抖音后重试。";
    captureMutate.mockImplementation((_v, opts) =>
      opts?.onError?.(new Error(reason)),
    );
    render(<DouyinCookieSection />);
    fireEvent.click(screen.getByRole("button", { name: "从 Chrome 抓取" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0]?.[0]).toBe(reason);
  });

  it("后端没给可读信息时才回落到兜底文案", async () => {
    captureMutate.mockImplementation((_v, opts) => opts?.onError?.(new Error("")));
    render(<DouyinCookieSection />);
    fireEvent.click(screen.getByRole("button", { name: "从 Chrome 抓取" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0]?.[0]).toBe("抓取抖音 Cookie 失败");
  });

  it("抓取进行中同时锁住保存，两个写动作不会互相覆盖", () => {
    capturePending.current = true;
    render(<DouyinCookieSection />);

    expect(screen.getByRole("button", { name: /抓取中/ })).toBeDisabled();
    const textarea = document.getElementById("douyin-cookie-input") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "sessionid=x" } });
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("手工粘贴保存路径不受影响：提交后清空本地输入框", async () => {
    saveMutate.mockImplementation((_input, opts) => opts?.onSuccess?.());
    render(<DouyinCookieSection />);
    const textarea = document.getElementById("douyin-cookie-input") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: " sessionid=x " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(saveMutate).toHaveBeenCalled());
    expect(saveMutate.mock.calls[0]?.[0]).toEqual({ cookie: "sessionid=x" });
    expect(textarea.value).toBe("");
  });
});
