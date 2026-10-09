// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { cloneElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "@/components/layout/header";

const runtimeState = vi.hoisted(() => ({ authRequired: true, isCe: false, phoneVisible: false }));
const authState = vi.hoisted(() => ({ username: "local", logout: vi.fn() }));
const paramsState = vi.hoisted(() => ({ value: {} as Record<string, string> }));
const navigateMock = vi.hoisted(() => vi.fn());
const toastMock = vi.hoisted(() => ({
  info: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));
const resetUserSessionStateMock = vi.hoisted(() => vi.fn());
const securityState = vi.hoisted(() => ({
  data: undefined as
    | undefined
    | { password_configured: boolean; phone: string | null; phone_masked: string | null },
}));
const brandingState = vi.hoisted(() => ({
  enabled: null as boolean | null,
  data: undefined as undefined | {
    schema_version: 1;
    organization: { org_id: string; name: string };
    branding: { logo_url: string; updated_at: string };
  },
}));

vi.mock("@/lib/reset-region-state", () => ({
  resetUserSessionState: resetUserSessionStateMock,
}));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("@/lib/runtime-config", () => ({
  authRequired: () => runtimeState.authRequired,
  isCeRuntime: () => runtimeState.isCe,
  phoneOtpEntryVisible: () => runtimeState.phoneVisible,
}));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: () => ({ data: undefined }),
}));

vi.mock("@/lib/queries/auth", () => ({
  useAccountSecurity: () => securityState,
}));

vi.mock("@/lib/queries/org-branding", () => ({
  useOrgBranding: (enabled: boolean) => {
    brandingState.enabled = enabled;
    return { data: brandingState.data };
  },
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    ...props
  }: React.ComponentProps<"a"> & { to?: string }) => (
    <a href={to} {...props}>{children}</a>
  ),
  useNavigate: () => navigateMock,
  useParams: () => paramsState.value,
  useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => string }) =>
    select({ location: { pathname: "/" } }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "app.logoHomeTooltip": "Home",
        "header.account.open": "Open account",
        "header.account.changeAvatar": "Change avatar",
        "header.account.changePassword": "Change password",
        "header.account.setPassword": "Set password",
        "header.account.phoneBinding.title": "Bind phone",
        "header.account.selectLanguage": "Select language",
        "header.account.languageChinese": "Chinese",
        "header.account.languageEnglish": "English",
        "auth.logout": "Log out",
        "nav.mcp": "MCP",
        "nav.models": "Model Plaza",
        "nav.agent": "Agent",
        "header.agentNeedsProject": "Pick a project first",
      })[key] ?? key,
    i18n: {
      language: "en",
      resolvedLanguage: "en",
      changeLanguage: vi.fn(),
    },
  }),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: () => authState,
}));

vi.mock("@/stores/app-store", () => ({
  useAppStore: () => vi.fn(),
}));

vi.mock("@/components/task-center/header-entry", () => ({
  HeaderEntry: () => <button type="button">Tasks</button>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: React.ComponentProps<"button">) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/tooltip", () => ({
  TooltipProvider: ({ children }: React.PropsWithChildren) => <>{children}</>,
  Tooltip: ({ children }: React.PropsWithChildren) => <>{children}</>,
  TooltipTrigger: ({
    children,
    render,
  }: React.PropsWithChildren<{ render?: React.ReactElement }>) =>
    render ? cloneElement(render, {}, children) : <>{children}</>,
  TooltipContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuGroup: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuLabel: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuItem: ({ children, ...props }: React.ComponentProps<"button">) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

function renderHeader() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Header />
    </QueryClientProvider>,
  );
}

describe("Header runtime gating", () => {
  beforeEach(() => {
    runtimeState.authRequired = true;
    runtimeState.isCe = false;
    runtimeState.phoneVisible = false;
    authState.username = "local";
    authState.logout.mockReset();
    resetUserSessionStateMock.mockReset();
    brandingState.enabled = null;
    brandingState.data = undefined;
    securityState.data = undefined;
    paramsState.value = {};
    navigateMock.mockReset();
    toastMock.info.mockReset();
  });

  it("reads branding only for an authenticated EE session and renders it in the home link", () => {
    brandingState.data = {
      schema_version: 1,
      organization: { org_id: "org-1", name: "Claymore" },
      branding: {
        logo_url: "/assets/org-brand/org-1/logo",
        updated_at: "2026-08-21T10:00:00Z",
      },
    };

    renderHeader();

    expect(brandingState.enabled).toBe(true);
    expect(screen.getByRole("link", { name: "Home — Claymore" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Claymore" })).not.toBeInTheDocument();

    fireEvent.error(screen.getByTestId("organization-brand").querySelector("img")!);
    expect(screen.queryByTestId("organization-brand")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Home — Claymore" })).toBeInTheDocument();
  });

  it("disables branding in CE", () => {
    runtimeState.isCe = true;
    renderHeader();
    expect(brandingState.enabled).toBe(false);
  });

  it("renders logout in the account panel when runtime requires auth", async () => {
    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    expect(await screen.findByText("Log out")).toBeInTheDocument();
    expect(screen.getByText("Change password")).toBeInTheDocument();
  });

  it("shows a masked phone and first-password action for a passwordless account", async () => {
    securityState.data = {
      password_configured: false,
      phone: "+8613800138000",
      phone_masked: "138****8000",
    };
    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    expect(await screen.findByText("138****8000")).toBeInTheDocument();
    expect(screen.getByText("Set password")).toBeInTheDocument();
  });

  it.each([false, true])("gates first phone binding on phone entry visibility (%s)", async (visible) => {
    runtimeState.phoneVisible = visible;
    securityState.data = { password_configured: true, phone: null, phone_masked: null };
    renderHeader();
    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);
    await screen.findByText("Log out");
    expect(screen.queryByText("Bind phone") !== null).toBe(visible);
  });

  it("does not offer replacing an existing phone", async () => {
    runtimeState.phoneVisible = true;
    securityState.data = { password_configured: true, phone: "+8613800138000", phone_masked: "138****8000" };
    renderHeader();
    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);
    await screen.findByText("Log out");
    expect(screen.queryByText("Bind phone")).not.toBeInTheDocument();
  });

  // [aipdd fork delta] 公告中心整体下线 —— 头像上不再有未读红点，header 与
  // 账号面板里都不该再出现「公告中心」入口（版本更新提醒走 VersionUpdateDialog）。
  // 探针用未映射的 key 名：t() mock 不再认识 header.notifications，一旦入口被
  // 加回来就会把原始 key 直接渲染到屏幕上。
  it("keeps the announcement center entry out of the header and the account panel", async () => {
    renderHeader();

    expect(screen.queryByText("header.notifications")).not.toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    expect(await screen.findByRole("button", { name: "Change avatar" })).toBeInTheDocument();
    expect(screen.queryByText("header.notifications")).not.toBeInTheDocument();
  });

  // 回归用例：账号面板只给头像留了一个入口。它当时只绑了 onMouseEnter ——
  // 触屏没有 hover、键盘也激活不了，面板等于对这两类用户消失。
  it("opens the account panel on click so the menu survives without hover", async () => {
    const user = userEvent.setup();
    renderHeader();

    const trigger = screen.getByLabelText("Open account");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("button", { name: "Change avatar" }),
    ).toBeInTheDocument();
  });

  it("opens with the keyboard, lands focus inside the panel and closes on Escape", async () => {
    const user = userEvent.setup();
    renderHeader();

    const trigger = screen.getByLabelText("Open account");
    trigger.focus();
    await user.keyboard("{Enter}");

    // 面板 portal 到 body 末尾，Tab 走不进去，所以打开时必须主动把焦点送进第一项。
    expect(await screen.findByRole("button", { name: "Change avatar" })).toHaveFocus();

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Change avatar" }),
      ).not.toBeInTheDocument();
    });
    expect(trigger).toHaveFocus();
  });

  it("closes the pinned panel on a second click and on an outside pointer press", async () => {
    const user = userEvent.setup();
    renderHeader();

    const trigger = screen.getByLabelText("Open account");

    await user.click(trigger);
    await screen.findByRole("button", { name: "Change avatar" });
    await user.click(trigger);
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Change avatar" }),
      ).not.toBeInTheDocument();
    });

    await user.click(trigger);
    await screen.findByRole("button", { name: "Change avatar" });
    fireEvent.pointerDown(document.body);
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Change avatar" }),
      ).not.toBeInTheDocument();
    });
  });

  it("keeps a click-opened panel open when the pointer leaves it", async () => {
    const user = userEvent.setup();
    renderHeader();

    const trigger = screen.getByLabelText("Open account");
    await user.click(trigger);
    await screen.findByRole("button", { name: "Change avatar" });

    // 悬停打开的面板移开就收；点击「钉住」的不该被 mouseleave 收走，
    // 否则触屏点开后手指一抬内容就没了。
    fireEvent.mouseLeave(trigger.parentElement!);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Change avatar" })).toBeInTheDocument();
    });
  });

  it("hides logout when runtime does not require auth while keeping the local identity", async () => {
    runtimeState.authRequired = false;

    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    await waitFor(() => {
      expect(screen.getByText("local")).toBeInTheDocument();
    });
    expect(screen.queryByText("Log out")).not.toBeInTheDocument();
    expect(screen.queryByText("Change password")).not.toBeInTheDocument();
  });

  it("purges user-scoped caches after logout so the next account can't see stale data", async () => {
    // 回归用例：手动退出是 SPA 内部跳转，不清 QueryClient 的话换账号登录后
    // projectSummaries 还在 staleTime 内，新账号会看到上一个账号的项目列表。
    authState.logout.mockResolvedValue(undefined);

    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);
    fireEvent.click(await screen.findByText("Log out"));

    await waitFor(() => {
      expect(resetUserSessionStateMock).toHaveBeenCalled();
    });
    expect(authState.logout).toHaveBeenCalled();
  });

  // [aipdd fork delta] 顶部「设置」右侧的「模型广场」「MCP」快捷入口：有项目上下文时
  // 导航到对应 section 路由；无项目（仪表盘）时导航到全局页（见下一条用例）。
  it("shows model plaza and MCP shortcuts beside settings when a project is active", () => {
    runtimeState.isCe = true;
    paramsState.value = { project: "test" };

    renderHeader();

    const plaza = screen.getByRole("button", { name: "Model Plaza" });
    const mcp = screen.getByRole("button", { name: "MCP" });

    fireEvent.click(plaza);
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/projects/$project/models",
      params: { project: "test" },
    });
    fireEvent.click(mcp);
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/projects/$project/mcp",
      params: { project: "test" },
    });
  });

  // [aipdd fork delta] 无项目（仪表盘）时快捷入口仍显示，指向全局页 /models、/mcp；
  // 两个页面本身不依赖项目，项目只决定 MCP 配置里预填的编号。
  it("routes the shortcuts to the global pages without a project context", () => {
    runtimeState.isCe = true;
    renderHeader();

    fireEvent.click(screen.getByRole("button", { name: "Model Plaza" }));
    expect(navigateMock).toHaveBeenCalledWith({ to: "/models" });

    fireEvent.click(screen.getByRole("button", { name: "MCP" }));
    expect(navigateMock).toHaveBeenCalledWith({ to: "/mcp" });
  });

  // [aipdd fork delta] 「Agent」快捷入口：与模型广场/MCP 并排，但它只有项目内页
  // （/projects/{项目}/agent）没有全局页。有项目时导航到该项目 Agent；无项目时
  // 不跳转、只提示先选项目（按钮保持可点才能发出这条指引）。
  it("routes the Agent shortcut to the project's agent column when a project is active", () => {
    runtimeState.isCe = true;
    paramsState.value = { project: "test" };

    renderHeader();

    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/projects/$project/agent",
      params: { project: "test" },
    });
    expect(toastMock.info).not.toHaveBeenCalled();
  });

  it("does not route the Agent shortcut without a project and prompts to pick one", () => {
    runtimeState.isCe = true;

    renderHeader();

    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(navigateMock).not.toHaveBeenCalled();
    expect(toastMock.info).toHaveBeenCalledWith("Pick a project first");
  });

});
