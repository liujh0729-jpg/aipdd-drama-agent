// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 账户安全面板的行为契约：自助改密 + operator 口令入库。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountSecuritySection } from "@/components/settings/account-security-section";

const changeMutate = vi.hoisted(() => vi.fn());
const saveOperatorMutate = vi.hoisted(() => vi.fn());
const operatorState = vi.hoisted(() => ({ current: undefined as unknown }));
const authRole = vi.hoisted(() => ({ current: "owner" as string }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/queries/admin-users", () => ({
  useChangePassword: () => ({ mutate: changeMutate, isPending: false }),
}));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: () => ({
    data: { data: { operatorSecurity: operatorState.current } },
    error: null,
  }),
  useSaveOperatorSecurityConfig: () => ({
    mutate: saveOperatorMutate,
    isPending: false,
  }),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { role: string }) => unknown) =>
    selector({ role: authRole.current }),
}));

vi.mock("@/lib/api-errors", () => ({
  backendErrorToastMessage: (error: unknown) =>
    error instanceof Error ? error.message : "操作失败",
}));

vi.mock("sonner", () => ({ toast }));

function fill(field: "change-old-password" | "change-new-password" | "change-confirm-password", value: string) {
  fireEvent.change(document.getElementById(field) as HTMLElement, {
    target: { value },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  authRole.current = "owner";
  operatorState.current = {
    promptExportPasswordConfigured: false,
    promptExportPasswordPreview: "",
    source: "unset",
  };
});

describe("AccountSecuritySection", () => {
  it("渲染改密表单：新旧密码 + 确认字段", () => {
    render(<AccountSecuritySection />);
    expect(screen.getByText("修改登录密码")).toBeInTheDocument();
    expect(document.getElementById("change-old-password")).toBeInTheDocument();
    expect(document.getElementById("change-new-password")).toBeInTheDocument();
    expect(document.getElementById("change-confirm-password")).toBeInTheDocument();
  });

  it("新密码不足门槛时前端拦截，不发请求", () => {
    render(<AccountSecuritySection />);
    fill("change-old-password", "old-secret-1");
    fill("change-new-password", "short1");
    fill("change-confirm-password", "short1");
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

    expect(changeMutate).not.toHaveBeenCalled();
    expect(screen.getByText("新密码至少需要 8 位。")).toBeInTheDocument();
  });

  it("两次新密码不一致时拦截，不发请求", () => {
    render(<AccountSecuritySection />);
    fill("change-old-password", "old-secret-1");
    fill("change-new-password", "brand-new-9");
    fill("change-confirm-password", "brand-new-8");
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

    expect(changeMutate).not.toHaveBeenCalled();
    expect(screen.getByText("两次输入的新密码不一致。")).toBeInTheDocument();
  });

  it("校验通过后以 snake_case 提交并在成功时提示", async () => {
    changeMutate.mockImplementation((_input, opts) => opts?.onSuccess?.());
    render(<AccountSecuritySection />);
    fill("change-old-password", "old-secret-1");
    fill("change-new-password", "brand-new-9");
    fill("change-confirm-password", "brand-new-9");
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

    await waitFor(() =>
      expect(changeMutate).toHaveBeenCalledWith(
        { oldPassword: "old-secret-1", newPassword: "brand-new-9" },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      ),
    );
    expect(toast.success).toHaveBeenCalledWith("密码已修改。");
  });

  it("后端报错经 backendErrorToastMessage 落界面", async () => {
    changeMutate.mockImplementation((_input, opts) =>
      opts?.onError?.(new Error("当前密码错误")),
    );
    render(<AccountSecuritySection />);
    fill("change-old-password", "wrong");
    fill("change-new-password", "brand-new-9");
    fill("change-confirm-password", "brand-new-9");
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("当前密码错误"));
    expect(screen.getByText("当前密码错误")).toBeInTheDocument();
  });

  it("operator 口令区仅超管可见并可保存", async () => {
    saveOperatorMutate.mockImplementation((_input, opts) => opts?.onSuccess?.());
    render(<AccountSecuritySection />);
    expect(screen.getByText("操作口令（导出提示词等）")).toBeInTheDocument();

    fireEvent.change(document.getElementById("operator-export-password") as HTMLElement, {
      target: { value: "op-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(saveOperatorMutate).toHaveBeenCalledWith(
        { promptExportPassword: "op-secret" },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      ),
    );
    expect(toast.success).toHaveBeenCalledWith("操作口令已保存。");
  });

  it("非超管不渲染 operator 口令区", () => {
    authRole.current = "member";
    render(<AccountSecuritySection />);
    expect(
      screen.queryByText("操作口令（导出提示词等）"),
    ).not.toBeInTheDocument();
    // 改密区对任何登录用户都开放
    expect(screen.getByText("修改登录密码")).toBeInTheDocument();
  });
});
