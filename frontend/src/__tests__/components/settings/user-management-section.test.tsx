// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 用户管理面板的行为契约。
import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UserManagementSection } from "@/components/settings/user-management-section";

const listState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const createUser = vi.hoisted(() => vi.fn());
const updateUser = vi.hoisted(() => vi.fn());
const resetPassword = vi.hoisted(() => vi.fn());
const refetch = vi.hoisted(() => vi.fn());
const logout = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
const confirmDialog = vi.hoisted(() => vi.fn());

vi.mock("@/lib/queries/admin-users", () => ({
  useAdminUsers: () => listState.current,
  useCreateAdminUser: () => ({ mutateAsync: createUser, isPending: false }),
  useUpdateAdminUser: () => ({ mutateAsync: updateUser, isPending: false }),
  useResetAdminUserPassword: () => ({ mutateAsync: resetPassword, isPending: false }),
}));

vi.mock("sonner", () => ({ toast }));
vi.mock("@/components/confirm-dialog-host", () => ({ confirmDialog }));
vi.mock("@/stores/auth-store", () => ({
  useAuthStore: Object.assign(
    (selector: (state: { role: string | null }) => unknown) => selector({ role: "owner" }),
    { getState: () => ({ logout }) },
  ),
}));

const SELF = {
  id: "local",
  username: "local",
  role: "owner",
  status: "active",
  created_at: "2026-09-01T00:00:00+00:00",
  updated_at: "2026-09-01T00:00:00+00:00",
  is_self: true,
};

const MEMBER = {
  id: "u-2",
  username: "小王",
  role: "member",
  status: "active",
  created_at: "2026-09-02T00:00:00+00:00",
  updated_at: "2026-09-02T00:00:00+00:00",
  is_self: false,
};

const SUSPENDED = {
  id: "u-3",
  username: "tom",
  role: "member",
  status: "suspended",
  created_at: "2026-09-03T00:00:00+00:00",
  updated_at: "2026-09-03T00:00:00+00:00",
  is_self: false,
};

function ready(users: Record<string, unknown>[]) {
  listState.current = {
    isLoading: false,
    isError: false,
    data: { data: users },
    refetch,
  };
}

function rowOf(username: string): HTMLElement {
  const node = document.querySelector(`[data-admin-user-row="${username}"]`);
  if (!node) throw new Error(`missing row for ${username}`);
  return node as HTMLElement;
}

function readLocale(language: "zh" | "en" | "vi"): Record<string, any> {
  return JSON.parse(
    readFileSync(`public/locales/${language}/translation.json`, "utf8"),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  ready([SELF, MEMBER, SUSPENDED]);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UserManagementSection", () => {
  it("渲染账户列表：角色选中态、当前账户与已禁用标记", () => {
    render(<UserManagementSection />);

    expect(screen.getByText("用户管理")).toBeInTheDocument();
    expect(screen.getByText("共 3 个账户，其中 1 个管理员")).toBeInTheDocument();

    const selfRow = rowOf("local");
    expect(within(selfRow).getByText("当前账户")).toBeInTheDocument();
    expect(within(selfRow).getByRole("button", { name: "管理员" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const memberRow = rowOf("小王");
    expect(within(memberRow).getByRole("button", { name: "普通成员" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(memberRow).getByText(/建立于/)).toBeInTheDocument();

    const deadRow = rowOf("tom");
    expect(within(deadRow).getByText("已禁用")).toBeInTheDocument();
    // 已禁用的账户给的是「启用」，不是「禁用」
    expect(within(deadRow).getByRole("button", { name: "启用" })).toBeInTheDocument();
    expect(within(deadRow).queryByRole("button", { name: "禁用" })).toBeNull();
  });

  it("当前账户这一行锁死角色与启停用，避免自己把自己关在门外", () => {
    render(<UserManagementSection />);
    const selfRow = rowOf("local");

    for (const button of within(selfRow).getAllByRole("button", { name: /管理员|普通成员/ })) {
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "不能修改自己的角色或状态，请让另一位管理员操作");
    }
    expect(within(selfRow).queryByRole("button", { name: "禁用" })).toBeNull();
    // 重置口令仍然开放：改自己口令是正常操作
    expect(within(selfRow).getByRole("button", { name: /重置口令/ })).toBeInTheDocument();
  });

  it("新建账户：用户名/口令门槛在前端就拦住，通过后才提交", async () => {
    render(<UserManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: /新建用户/ }));
    const form = document.querySelector("[data-admin-user-create-form]") as HTMLElement;
    const box = within(form);

    // 默认落在「普通成员」，避免手滑把每个新账户都提成管理员
    expect(box.getByRole("button", { name: "普通成员" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.change(box.getByLabelText("用户名"), { target: { value: "has space" } });
    fireEvent.change(box.getByLabelText("初始口令"), { target: { value: "longenough" } });
    fireEvent.click(box.getByRole("button", { name: "创建账户" }));
    expect(createUser).not.toHaveBeenCalled();
    expect(box.getByText("用户名需为 2-32 位字母、数字、-、_ 或中文")).toBeInTheDocument();

    fireEvent.change(box.getByLabelText("用户名"), { target: { value: "小王二" } });
    fireEvent.change(box.getByLabelText("初始口令"), { target: { value: "abc" } });
    fireEvent.click(box.getByRole("button", { name: "创建账户" }));
    expect(createUser).not.toHaveBeenCalled();
    expect(box.getByText("口令至少 8 位")).toBeInTheDocument();

    createUser.mockResolvedValueOnce({ ok: true, data: { ...MEMBER, username: "小王二" } });
    fireEvent.change(box.getByLabelText("初始口令"), { target: { value: "Aipdd@2026" } });
    fireEvent.click(box.getByRole("button", { name: "创建账户" }));
    await waitFor(() =>
      expect(createUser).toHaveBeenCalledWith({
        username: "小王二",
        password: "Aipdd@2026",
        role: "member",
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("已创建账户 小王二");
  });

  it("改角色走 PATCH，后端回的角色才写进提示文案", async () => {
    updateUser.mockResolvedValueOnce({ ok: true, data: { ...MEMBER, role: "owner" } });
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "管理员" }));
    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({ userId: "u-2", role: "owner" }),
    );
    expect(toast.success).toHaveBeenCalledWith("小王 已改为管理员");
  });

  it("禁用要二次确认，取消就不发请求；启用直接生效", async () => {
    confirmDialog.mockResolvedValueOnce(false);
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "禁用" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(updateUser).not.toHaveBeenCalled();

    confirmDialog.mockResolvedValueOnce(true);
    updateUser.mockResolvedValueOnce({ ok: true, data: { ...MEMBER, status: "suspended" } });
    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "禁用" }));
    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({ userId: "u-2", status: "suspended" }),
    );
    expect(toast.success).toHaveBeenCalledWith("已禁用 小王");
    expect(confirmDialog.mock.calls[0][0].description).toBe(
      "禁用后 小王 会立即下线且无法再登录，确定吗？",
    );
  });

  it("已禁用账户锁掉重置口令入口，不必点了才发现后端不让改", async () => {
    render(<UserManagementSection />);
    const deadRow = rowOf("tom");

    const button = within(deadRow).getByRole("button", { name: /重置口令/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      "title",
      "该账户已禁用，请先启用后再重置口令",
    );

    // 就算状态在列表刷新前被改掉了，重置表单也不会展开，更不会去打后端
    fireEvent.click(button);
    expect(screen.queryByLabelText("新口令")).toBeNull();
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("后端英文 detail 翻成中文，认不出的原样透出来", async () => {
    updateUser.mockRejectedValueOnce(new Error("cannot demote the last active administrator"));
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "管理员" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "至少要保留一位可用的管理员，否则将没人能管理账户",
      ),
    );

    updateUser.mockRejectedValueOnce(new Error("upstream exploded"));
    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "管理员" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("upstream exploded"));
  });

  it("用户名重复时回中文提示，不再把 username already exists 甩给用户", async () => {
    render(<UserManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: /新建用户/ }));
    const form = document.querySelector("[data-admin-user-create-form]") as HTMLElement;
    const box = within(form);

    createUser.mockRejectedValueOnce(
      Object.assign(new Error("username already exists"), { status: 409 }),
    );
    fireEvent.change(box.getByLabelText("用户名"), { target: { value: "xiaowang" } });
    fireEvent.change(box.getByLabelText("初始口令"), { target: { value: "Aipdd@2026" } });
    fireEvent.click(box.getByRole("button", { name: "创建账户" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("该用户名已被占用"));
    expect(box.getByText("该用户名已被占用")).toBeInTheDocument();
  });

  it("重置口令：先本地校验，成功后按后端 is_self 决定是否重新登录", async () => {
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: /重置口令/ }));
    const input = screen.getByLabelText("新口令");
    const submit = screen.getByRole("button", { name: "确认重置" });

    fireEvent.change(input, { target: { value: "short" } });
    fireEvent.click(submit);
    expect(resetPassword).not.toHaveBeenCalled();
    expect(screen.getByText("口令至少 8 位")).toBeInTheDocument();

    resetPassword.mockResolvedValueOnce({
      ok: true,
      data: { revoked_sessions: 2, is_self: false },
    });
    fireEvent.change(input, { target: { value: "Aipdd@2027" } });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(resetPassword).toHaveBeenCalledWith({ userId: "u-2", password: "Aipdd@2027" }),
    );
    expect(toast.success).toHaveBeenCalledWith("已重置 小王 的口令");
    expect(logout).not.toHaveBeenCalled();
  });

  it("重置自己的口令后被踢回登录页，不留半死会话", async () => {
    const assign = vi.fn();
    const browserWindow = window;
    vi.stubGlobal(
      "window",
      new Proxy(browserWindow, {
        get(target, key) {
          return key === "location" ? { assign } : Reflect.get(target, key);
        },
      }),
    );
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("local")).getByRole("button", { name: /重置口令/ }));
    resetPassword.mockResolvedValueOnce({
      ok: true,
      data: { revoked_sessions: 1, is_self: true },
    });
    fireEvent.change(screen.getByLabelText("新口令"), { target: { value: "Aipdd@2027" } });
    fireEvent.click(screen.getByRole("button", { name: "确认重置" }));

    await waitFor(() => expect(logout).toHaveBeenCalled());
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("后端返回错误时把 detail 文案弹出来，不静默失败", async () => {
    updateUser.mockResolvedValueOnce({ ok: false, error: "不能降级最后一个可用超管" });
    render(<UserManagementSection />);

    fireEvent.click(within(rowOf("小王")).getByRole("button", { name: "管理员" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("不能降级最后一个可用超管"));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("加载失败给出重试入口，而不是空白页", () => {
    listState.current = { isLoading: false, isError: true, data: undefined, refetch };
    render(<UserManagementSection />);

    expect(screen.getByText("账户列表加载失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("加载中显示骨架文案", () => {
    listState.current = { isLoading: true, isError: false, data: undefined, refetch };
    render(<UserManagementSection />);

    expect(screen.getByText("正在加载账户列表…")).toBeInTheDocument();
    expect(document.querySelector("[data-admin-user-row]")).toBeNull();
  });

  it("三语词条覆盖面板与首次初始化表单的全部文案", () => {
    const usersKeys = [
      "title",
      "description",
      "summary",
      "newUser",
      "create",
      "cancel",
      "username",
      "password",
      "role",
      "createHint",
      "usernameInvalid",
      "passwordTooShort",
      "created",
      "createFailed",
      "loading",
      "loadFailed",
      "self",
      "createdAt",
      "resetPassword",
      "newPassword",
      "confirmReset",
      "resetPasswordHint",
      "resetPasswordDone",
      "resetPasswordFailed",
      "selfPasswordResetDone",
      "disable",
      "enable",
      "disableConfirm",
      "disabledDone",
      "enabledDone",
      "updateFailed",
      "roleChanged",
      "selfGuard",
      "resetPasswordDisabledHint",
    ];
    const setupKeys = [
      "title",
      "description",
      "username",
      "password",
      "confirmPassword",
      "submit",
      "submitting",
      "done",
      "failed",
      "invalid",
    ];

    for (const language of ["zh", "en", "vi"] as const) {
      const bundle = readLocale(language);
      expect(bundle.settings.pages.users, `${language}.settings.pages.users`).toBeTruthy();
      for (const key of usersKeys) {
        expect(bundle.settings.users[key], `${language}.settings.users.${key}`).toBeTruthy();
      }
      for (const key of ["owner", "member"]) {
        expect(bundle.settings.users.roles[key], `${language}.roles.${key}`).toBeTruthy();
      }
      expect(bundle.settings.users.statuses.suspended).toBeTruthy();
      for (const key of [
        "multiUserDisabled",
        "usernameExists",
        "userNotFound",
        "lastAdmin",
        "resetDisabledAccount",
      ]) {
        expect(bundle.settings.users.errors[key], `${language}.setup.errors.${key}`).toBeTruthy();
      }
      for (const key of setupKeys) {
        expect(bundle.auth.setup[key], `${language}.auth.setup.${key}`).toBeTruthy();
      }
      for (const key of ["username", "password", "confirm"]) {
        expect(bundle.auth.setup.errors[key], `${language}.setup.errors.${key}`).toBeTruthy();
      }
    }
  });

  it("「用户管理」页只对开了多用户模式的超管出现，查询层指向 admin 端点", () => {
    const dialog = readFileSync("src/components/settings/settings-dialog.tsx", "utf8");
    expect(dialog).toContain('const multiUser = setupStatusQuery.data?.data?.multi_user === true;');
    expect(dialog).toContain('const showUsersPage = multiUser && role === "owner";');
    expect(dialog).toContain("{showUsersPage ? (");
    expect(dialog).toContain("<UserManagementSection />");

    const queries = readFileSync("src/lib/queries/admin-users.ts", "utf8");
    expect(queries).toContain('api.get("api/v1/auth/setup-status"');
    expect(queries).toContain('api.get("api/v1/admin/users"');
    expect(queries).toContain('api.post("api/v1/admin/users"');
    expect(queries).toContain("api/v1/admin/users/${encodeURIComponent(userId)}/password");
    expect(readFileSync("src/lib/query-keys.ts", "utf8")).toContain(
      'adminUsers: () => ["admin", "users"] as const',
    );
  });
});
