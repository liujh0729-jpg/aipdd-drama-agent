// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 首次初始化表单：门槛与后端 normalize_username /
// PRIVILEGED_MIN_PASSWORD_LENGTH 对齐，提交成功后直接以超管身份进场。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SetupCard } from "@/components/login/setup-card";

const navigate = vi.hoisted(() => vi.fn());
const setupAdministrator = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { setupAdministrator: typeof setupAdministrator }) => unknown) =>
    selector({ setupAdministrator }),
}));

function fill(username: string, password: string, confirm = password) {
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("登录口令"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("确认口令"), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: /创建并进入/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SetupCard", () => {
  // zodResolver 是异步跑校验的，提交后要等一轮渲染才能看到字段错误。
  it("用户名非法时不给建超管", async () => {
    render(<SetupCard />);
    fill("a", "Aipdd@2026");

    expect(
      await screen.findByText("用户名需为 2-32 位字母、数字、-、_ 或中文"),
    ).toBeInTheDocument();
    expect(setupAdministrator).not.toHaveBeenCalled();
  });

  it("口令不足 8 位被拦下（超管口令走严格门槛）", async () => {
    render(<SetupCard />);
    fill("admin", "abc123");

    expect(await screen.findByText("口令至少 8 位")).toBeInTheDocument();
    expect(setupAdministrator).not.toHaveBeenCalled();
  });

  it("两次口令不一致时提示确认框", async () => {
    render(<SetupCard />);
    fill("admin", "Aipdd@2026", "Aipdd@2027");

    expect(await screen.findByText("两次输入的口令不一致")).toBeInTheDocument();
    expect(setupAdministrator).not.toHaveBeenCalled();
  });

  it("中文用户名放行，提交前去掉首尾空格", async () => {
    setupAdministrator.mockResolvedValueOnce(undefined);
    render(<SetupCard />);
    fill("  小王  ", "Aipdd@2026");

    await waitFor(() => expect(setupAdministrator).toHaveBeenCalledWith("小王", "Aipdd@2026"));
    expect(navigate).toHaveBeenCalledWith({ to: "/", replace: true });
    expect(toast.success).toHaveBeenCalledWith("初始化完成，已登录为管理员");
  });

  it("后端拒绝时把原因显示出来，不静默失败", async () => {
    setupAdministrator.mockRejectedValueOnce(new Error("system already initialized"));
    render(<SetupCard />);
    fill("admin", "Aipdd@2026");

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("system already initialized"));
    expect(screen.getByText("system already initialized")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
