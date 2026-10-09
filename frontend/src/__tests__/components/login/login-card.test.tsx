// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginCard } from "@/components/login/login-card";

const navigate = vi.hoisted(() => vi.fn());
const login = vi.hoisted(() => vi.fn());
const loginWithOtp = vi.hoisted(() => vi.fn());
const setupAdministrator = vi.hoisted(() => vi.fn());
const requestOtp = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
const otpEntryVisible = vi.hoisted(() => vi.fn(() => true));
// [aipdd fork delta] 登录页进场要探一次 setup-status；这里用可切换的状态源，
// 既能钉住「空库换初始化表单」，也不影响原有登录用例。
const setupStatus = vi.hoisted(() => ({
  current: { data: { data: { needs_setup: false, multi_user: true } } } as Record<string, unknown>,
}));

vi.mock("@/lib/queries/admin-users", () => ({ useSetupStatus: () => setupStatus.current }));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/components/login/region-selector", () => ({ RegionSelector: () => null }));
vi.mock("@/lib/cluster-config", () => ({
  clusterConfig: { mode: "none", regions: [] },
}));
vi.mock("@/lib/runtime-config", () => ({ phoneOtpEntryVisible: otpEntryVisible }));
vi.mock("@/stores/region-store", () => ({
  useRegionStore: (selector: (state: { selectedRegionId: null }) => unknown) =>
    selector({ selectedRegionId: null }),
}));
vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { login: typeof login; loginWithOtp: typeof loginWithOtp; setupAdministrator: typeof setupAdministrator }) => unknown) =>
    selector({ login, loginWithOtp, setupAdministrator }),
}));
vi.mock("@/lib/auth-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-api")>("@/lib/auth-api");
  return {
    ...actual,
    newAuthIdempotencyKey: () => "web:test-key-0001",
    requestOtp,
  };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

describe("LoginCard", () => {
  afterEach(() => vi.unstubAllGlobals());
  beforeEach(() => {
    sessionStorage.clear();
    navigate.mockReset();
    login.mockReset();
    loginWithOtp.mockReset();
    requestOtp.mockReset();
    toast.success.mockReset();
    toast.error.mockReset();
    setupAdministrator.mockReset();
    otpEntryVisible.mockReturnValue(true);
    setupStatus.current = { data: { data: { needs_setup: false, multi_user: true } } };
  });

  it("空库时整张登录卡换成首次初始化表单", () => {
    setupStatus.current = { data: { data: { needs_setup: true, multi_user: true } } };
    render(<LoginCard />);

    expect(document.querySelector("[data-login-setup-card]")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("button", { name: "auth.loginButton" })).toBeNull();
  });

  it("探测失败时照常渲染登录表单，不把已部署的实例锁在门外", () => {
    setupStatus.current = { isError: true, data: undefined };
    render(<LoginCard />);

    expect(document.querySelector("[data-login-setup-card]")).toBeNull();
    expect(screen.getByRole("tablist")).toBeTruthy();
  });

  it("uses OTP as the default sign-in path and completes auto-registration", async () => {
    requestOtp.mockResolvedValue({
      verification_id: "A".repeat(26),
      phone_masked: "138****8000",
      expires_in_seconds: 300,
    });
    loginWithOtp.mockResolvedValue({
      username: `u_${"B".repeat(26)}`,
      phone_masked: "138****8000",
      role: "worker",
      created_user: true,
      password_configured: false,
    });
    render(<LoginCard />);

    fireEvent.change(screen.getByLabelText("auth.otp.phone"), {
      target: { value: "13800138000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "auth.otp.send" }));

    await waitFor(() => expect(requestOtp).toHaveBeenCalledWith("13800138000", "web:test-key-0001"));
    fireEvent.change(screen.getByLabelText("auth.otp.code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "auth.otp.loginButton" }));

    await waitFor(() =>
      expect(loginWithOtp).toHaveBeenCalledWith(
        "13800138000",
        "A".repeat(26),
        "123456",
        "web:test-key-0001",
      ),
    );
    expect(toast.success).toHaveBeenCalledWith("auth.otp.accountCreated");
    expect(navigate).toHaveBeenCalledWith({ to: "/", replace: true });
  });

  it("keeps account/password login available as the second mode", async () => {
    login.mockResolvedValue(undefined);
    render(<LoginCard />);

    fireEvent.click(screen.getByRole("tab", { name: "auth.passwordTab" }));
    fireEvent.change(screen.getByLabelText("auth.accountOrPhone"), {
      target: { value: "13800138000" },
    });
    fireEvent.change(screen.getByLabelText("auth.password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "auth.loginButton" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("13800138000", "password123"));
    expect(navigate).toHaveBeenCalledWith({ to: "/", replace: true });
  });

  // [aipdd fork delta] 带意图登录：登录页顶栏 icon（模型广场 / MCP）传入 onAuthenticated
  // 后由父层接管去向，卡片自身不再强制跳仪表盘。
  it("hands the destination to onAuthenticated instead of always landing on the dashboard", async () => {
    const onAuthenticated = vi.fn();
    login.mockResolvedValue(undefined);
    render(<LoginCard onAuthenticated={onAuthenticated} />);

    fireEvent.click(screen.getByRole("tab", { name: "auth.passwordTab" }));
    fireEvent.change(screen.getByLabelText("auth.accountOrPhone"), {
      target: { value: "13800138000" },
    });
    fireEvent.change(screen.getByLabelText("auth.password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "auth.loginButton" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("13800138000", "password123"));
    expect(onAuthenticated).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows only account/password login while the OTP entry is hidden", async () => {
    otpEntryVisible.mockReturnValue(false);
    login.mockResolvedValue(undefined);
    render(<LoginCard />);

    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByLabelText("auth.otp.phone")).toBeNull();
    expect(screen.queryByLabelText("auth.accountOrPhone")).toBeNull();
    expect(screen.getByLabelText("auth.account")).toBeTruthy();
    expect(screen.getByPlaceholderText("auth.accountPlaceholder")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("auth.account"), {
      target: { value: "legacy-account" },
    });
    fireEvent.change(screen.getByLabelText("auth.password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "auth.loginButton" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("legacy-account", "password123"));
    expect(requestOtp).not.toHaveBeenCalled();
  });
});