// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bolt,
  Bot,
  Camera,
  Check,
  ChevronRight,
  Languages,
  LogOut,
  KeyRound,
  Library,
  Plug,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { AvatarUploadDialog } from "@/components/account/avatar-upload-dialog";
import { PasswordChangeDialog } from "@/components/account/password-change-dialog";
import { PhoneBindingDialog } from "@/components/account/phone-binding-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { BrandLockup } from "@/components/layout/brand-lockup";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import {
  PetGalleryDialog,
  type CompanionSelection,
} from "@/features/companion/petdex/PetGalleryDialog";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { useAppStore } from "@/stores/app-store";
import { authRequired, isCeRuntime, phoneOtpEntryVisible } from "@/lib/runtime-config";
import { resetUserSessionState } from "@/lib/reset-region-state";
import { useModelGatewayConfig } from "@/lib/queries/model-gateway";
import { useOrgBranding } from "@/lib/queries/org-branding";
import { useAccountSecurity } from "@/lib/queries/auth";
import { LANGUAGE_LABEL_KEYS, normalize, SUPPORTED, type Supported } from "@/i18n/languages";
// [aipdd fork delta] 公告中心整体下线：账号面板里的「公告中心」行、NotificationDrawer
// 抽屉、OSS 公告数据层与头像未读红点一并移除。版本更新提醒仍由 routes/_app.tsx 挂载的
// VersionUpdateDialog 独立弹出，不经这里。
import {
  ProjectHeaderNavigation,
  ProjectSwitcher,
  ProjectXiajiMenu,
} from "@/components/layout/project-header-navigation";
import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import { toast } from "sonner";
const ACCOUNT_PANEL_TRANSITION_MS = 350;

export function Header({ ambientBackground = false }: { ambientBackground?: boolean }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const params = useParams({ strict: false }) as { project?: string };
  const [companionOpen, setCompanionOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [avatarDialogOpen, setAvatarDialogOpen] = useState(false);
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [phoneBindingOpen, setPhoneBindingOpen] = useState(false);
  const [accountPanelOpen, setAccountPanelOpen] = useState(false);
  const [accountPanelVisible, setAccountPanelVisible] = useState(false);
  const [settingsWarningBubbleDismissed, setSettingsWarningBubbleDismissed] = useState(false);
  const [accountPanelPosition, setAccountPanelPosition] = useState<{ top: number; right: number }>({
    top: 56,
    right: 16,
  });
  const accountCloseTimerRef = useRef<number | null>(null);
  const accountUnmountTimerRef = useRef<number | null>(null);
  const accountOpenFrameRef = useRef<number | null>(null);
  const accountAnchorRef = useRef<HTMLDivElement | null>(null);
  const accountTriggerRef = useRef<HTMLButtonElement | null>(null);
  const accountPanelRef = useRef<HTMLDivElement | null>(null);
  // 面板由点击/键盘打开时「钉住」：此时鼠标移开不再收走它。悬停打开的面板不钉。
  const accountPanelPinnedRef = useRef(false);
  const settingsAnchorRef = useRef<HTMLDivElement | null>(null);
  const { username, displayName: storedDisplayName, logout } = useAuthStore();
  const queryClient = useQueryClient();
  // 退出登录是 SPA 内部跳转（不刷新页面），必须一并清掉 React Query 缓存和
  // 用户级 zustand/localStorage 状态，否则换账号登录后 projectSummaries 等
  // 查询还在 staleTime 内，新账号会直接看到上一个账号的项目列表。
  const handleLogout = async () => {
    await logout();
    resetUserSessionState({ queryClient });
  };
  const avatarUrl = useAuthStore((s) => s.avatarUrl);
  const companionKind = useAppStore((s) => s.companionKind);
  const companionPet = useAppStore((s) => s.companionPet);
  const pikoAccessory = useAppStore((s) => s.pikoAccessory);
  const setCompanion = useAppStore((s) => s.setCompanion);
  const setPikoAccessory = useAppStore((s) => s.setPikoAccessory);
  const setLanguage = useAppStore((s) => s.setLanguage);
  const showLogout = authRequired();
  const ceRuntime = isCeRuntime();
  const orgBranding = useOrgBranding(!ceRuntime && Boolean(username));
  const brandName = orgBranding.data?.branding
    ? orgBranding.data.organization?.name ?? null
    : null;
  const homeLinkLabel = brandName
    ? `${t("app.logoHomeTooltip")} — ${brandName}`
    : t("app.logoHomeTooltip");
  const accountSecurity = useAccountSecurity(!ceRuntime && showLogout && Boolean(username));
  const passwordConfigured = accountSecurity.data?.password_configured ?? true;
  const canBindPhone = !ceRuntime && showLogout && phoneOtpEntryVisible()
    && accountSecurity.data?.phone === null && accountSecurity.data?.password_configured === true;
  const displayName = accountSecurity.data?.phone_masked ?? storedDisplayName ?? username ?? "User";
  const avatarInitial = displayName.slice(0, 1).toUpperCase();
  const activeLanguage = normalize(i18n.resolvedLanguage ?? i18n.language);
  const modelGatewayConfig = useModelGatewayConfig(ceRuntime);
  const gatewayConfig = modelGatewayConfig.data?.data;
  const hasSettingsWarning = Boolean(
    ceRuntime &&
      gatewayConfig &&
      (gatewayConfig.effective.configured === false ||
        gatewayConfig.mediaRelay?.configured === false),
  );
  const settingsWarningBubble = useFloatingBubblePosition(
    settingsAnchorRef,
    hasSettingsWarning && !settingsOpen && !settingsWarningBubbleDismissed,
  );
  const project = params.project ?? null;

  useEffect(() => {
    return () => {
      clearAccountCloseTimer();
      clearAccountUnmountTimer();
      clearAccountOpenFrame();
    };
  }, []);

  useEffect(() => {
    if (!hasSettingsWarning) {
      setSettingsWarningBubbleDismissed(false);
    }
  }, [hasSettingsWarning]);

  const handleCompanionConfirm = (
    selection: CompanionSelection,
    accessory: typeof pikoAccessory,
  ) => {
    setCompanion(selection.kind, selection.pet);
    setPikoAccessory(accessory);
    window.dispatchEvent(new Event("mybuddy-companion-reset"));
  };

  const clearAccountCloseTimer = () => {
    if (accountCloseTimerRef.current === null) return;
    window.clearTimeout(accountCloseTimerRef.current);
    accountCloseTimerRef.current = null;
  };

  const clearAccountUnmountTimer = () => {
    if (accountUnmountTimerRef.current === null) return;
    window.clearTimeout(accountUnmountTimerRef.current);
    accountUnmountTimerRef.current = null;
  };

  const clearAccountOpenFrame = () => {
    if (accountOpenFrameRef.current === null) return;
    window.cancelAnimationFrame(accountOpenFrameRef.current);
    accountOpenFrameRef.current = null;
  };

  const closeAccountPanelNow = () => {
    accountPanelPinnedRef.current = false;
    clearAccountCloseTimer();
    clearAccountOpenFrame();
    clearAccountUnmountTimer();
    setAccountPanelVisible(false);
    setAccountPanelOpen(false);
  };

  const openAccountPanel = () => {
    clearAccountCloseTimer();
    clearAccountUnmountTimer();
    clearAccountOpenFrame();
    const rect = accountAnchorRef.current?.getBoundingClientRect();
    if (rect) {
      setAccountPanelPosition({
        top: Math.round(rect.bottom + 8),
        right: Math.round(window.innerWidth - rect.right),
      });
    }
    setAccountPanelOpen(true);
    accountOpenFrameRef.current = window.requestAnimationFrame(() => {
      setAccountPanelVisible(true);
      accountOpenFrameRef.current = null;
    });
  };

  const scheduleCloseAccountPanel = () => {
    // 钉住的面板只由 Escape、面板外交互或选中某一项关闭 —— 鼠标掠过就收走的话,
    // 触屏和键盘用户点开后根本读不完里面的内容(公告中心现在只在这儿)。
    if (accountPanelPinnedRef.current) return;
    clearAccountCloseTimer();
    accountCloseTimerRef.current = window.setTimeout(() => {
      setAccountPanelVisible(false);
      clearAccountUnmountTimer();
      accountUnmountTimerRef.current = window.setTimeout(() => {
        setAccountPanelOpen(false);
        accountUnmountTimerRef.current = null;
      }, ACCOUNT_PANEL_TRANSITION_MS);
      accountCloseTimerRef.current = null;
    }, 120);
  };

  const toggleAccountPanel = () => {
    if (accountPanelOpen && accountPanelPinnedRef.current) {
      closeAccountPanelNow();
      return;
    }
    accountPanelPinnedRef.current = true;
    openAccountPanel();
  };

  // 账号面板必须在指针、触屏和键盘下都能开关。面板 portal 到 body,不在触发器的
  // DOM 子树里,所以「点到外面」「焦点移到外面」只能在 document 上判定。
  useEffect(() => {
    if (!accountPanelOpen) return;
    const isInsideAccountUi = (target: EventTarget | null) =>
      target instanceof Node
      && (Boolean(accountAnchorRef.current?.contains(target))
        || Boolean(accountPanelRef.current?.contains(target)));
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      closeAccountPanelNow();
      accountTriggerRef.current?.focus();
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (isInsideAccountUi(event.target)) return;
      closeAccountPanelNow();
    };
    const handleFocusIn = (event: FocusEvent) => {
      if (isInsideAccountUi(event.target)) return;
      closeAccountPanelNow();
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountPanelOpen]);

  // 面板挂在 body 末尾,Tab 不会从触发器走进去。点击/键盘打开时把焦点送进第一项,
  // 否则键盘用户能打开面板却够不到里面的菜单项。悬停打开的面板不抢焦点。
  useEffect(() => {
    if (!accountPanelOpen || !accountPanelPinnedRef.current) return;
    accountPanelRef.current
      ?.querySelector<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')
      ?.focus();
  }, [accountPanelOpen]);

  const switchLanguage = (lang: Supported) => {
    void i18n.changeLanguage(lang);
    setLanguage(lang);
  };

  const openAvatarDialog = () => {
    closeAccountPanelNow();
    setAvatarDialogOpen(true);
  };

  const openPasswordDialog = () => {
    closeAccountPanelNow();
    setPasswordDialogOpen(true);
  };

  const handlePasswordChanged = () => {
    resetUserSessionState({ queryClient });
    void navigate({ to: "/login", replace: true });
  };

  return (
    <div
      className={`relative z-20 shrink-0 text-sidebar-foreground ${
        ambientBackground ? "bg-transparent" : "bg-background/58 backdrop-blur-xl"
      }`}
    >
      <header className="relative flex h-[48px] items-center justify-between gap-3 px-4">
        <div className="flex min-w-0 flex-1 items-center">
          <TooltipProvider delay={80}>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Link
                    to="/"
                    aria-label={homeLinkLabel}
                    className="flex min-w-0 shrink-0 items-center"
                  />
                }
              >
                <BrandLockup value={orgBranding.data} />
              </TooltipTrigger>
              <TooltipContent
                side="bottom"
                sideOffset={10}
                showArrow={false}
                className="border border-white/10 bg-background/95 text-foreground shadow-none"
              >
                {t("app.logoHomeTooltip")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <div className="ml-[22px] flex min-w-0 items-center gap-6">
            {project ? <ProjectSwitcher current={project} /> : null}
          </div>
        </div>

        {project ? <ProjectHeaderNavigation project={project} /> : null}

        {/* Actions */}
        <div className="flex min-w-0 flex-1 shrink-0 items-center justify-end gap-1">
          {/* 设置仅在 CE 版显示,EE 版隐藏 */}
          {ceRuntime ? (
            <div ref={settingsAnchorRef} className="relative">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="relative size-[32px] text-sidebar-foreground/82 transition-colors duration-150 ease-[var(--ease-out-quint)] hover:bg-white/[0.05] hover:text-white aria-expanded:bg-white/[0.05] aria-expanded:text-white"
                aria-label={
                  hasSettingsWarning ? t("header.settingsWithWarning") : t("header.settings")
                }
                aria-expanded={settingsOpen}
                onClick={() => setSettingsOpen(true)}
              >
                <Bolt className="size-[17px]" />
                {hasSettingsWarning ? (
                  <span
                    className="absolute right-[5px] top-[5px] flex size-[11px] items-center justify-center rounded-full bg-amber-400 text-black shadow-[0_0_7px_rgba(251,191,36,0.68)]"
                    aria-hidden="true"
                  >
                    <AlertTriangle className="size-[8px]" strokeWidth={3} />
                  </span>
                ) : null}
              </Button>
            </div>
          ) : null}
          {/* [aipdd fork delta] 「模型广场」「MCP」快捷入口：始终显示。有项目上下文时进
              项目内页（保持顶栏栏目导航高亮），无项目（仪表盘）时进全局页
              ——两个页面本身不依赖项目，项目只决定 MCP 配置里预填的编号。 */}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="size-[32px] text-sidebar-foreground/82 transition-colors duration-150 ease-[var(--ease-out-quint)] hover:bg-white/[0.05] hover:text-white"
            aria-label={t("nav.models")}
            title={t("nav.models")}
            onClick={() => {
              if (project) {
                void navigate({ to: PROJECT_SECTION_ROUTES.models, params: { project } });
                return;
              }
              void navigate({ to: "/models" });
            }}
          >
            <Library className="size-[17px]" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="size-[32px] text-sidebar-foreground/82 transition-colors duration-150 ease-[var(--ease-out-quint)] hover:bg-white/[0.05] hover:text-white"
            aria-label={t("nav.mcp")}
            title={t("nav.mcp")}
            onClick={() => {
              if (project) {
                void navigate({ to: PROJECT_SECTION_ROUTES.mcp, params: { project } });
                return;
              }
              void navigate({ to: "/mcp" });
            }}
          >
            <Plug className="size-[17px]" />
          </Button>
          {/*
            [aipdd fork delta] 「Agent」快捷入口：与「模型广场」「MCP」并排。
            Agent 是项目内栏目（只有 /projects/{项目}/agent，没有全局页），所以在
            仪表盘（未选项目）上点了不跳转、只提示先选项目；因此按钮保持可点
            （置灰 disable 就发不出这条指引），用低透明度表达当前不可用。
          */}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className={`size-[32px] text-sidebar-foreground/82 transition-colors duration-150 ease-[var(--ease-out-quint)] hover:bg-white/[0.05] hover:text-white ${
              project ? "" : "opacity-50"
            }`}
            aria-label={t("nav.agent")}
            title={project ? t("nav.agent") : t("header.agentNeedsProject")}
            onClick={() => {
              if (project) {
                void navigate({
                  to: PROJECT_SECTION_ROUTES.agent,
                  params: { project },
                });
                return;
              }
              toast.info(t("header.agentNeedsProject"));
            }}
          >
            <Bot className="size-[17px]" />
          </Button>
          <Button
            id="mybuddy-companion-entry"
            type="button"
            variant="ghost"
            size="icon-sm"
            className="companion-capsule-entry -ml-0.5 -mr-0.5 size-[32px]"
            onClick={() => setCompanionOpen(true)}
            aria-label={t("myBuddy.companion.entry")}
          >
            <img
              src="/piko/entry/companion-capsule.png"
              alt=""
              aria-hidden="true"
              className="companion-capsule-entry__icon size-[22px] object-contain [image-rendering:pixelated]"
            />
          </Button>
          <div
            id="superchat-header-controls"
            className="flex min-w-0 shrink items-center gap-2 empty:hidden"
          />
          <div
            ref={accountAnchorRef}
            className="relative ml-1 flex items-center"
            onMouseEnter={openAccountPanel}
            onMouseLeave={scheduleCloseAccountPanel}
          >
            <Button
              ref={accountTriggerRef}
              type="button"
              variant="ghost"
              size="icon-sm"
              className="relative size-[28px] rounded-full p-0 hover:bg-transparent"
              aria-label={t("header.account.open")}
              aria-haspopup="true"
              aria-expanded={accountPanelOpen}
              aria-controls={accountPanelOpen ? "header-account-panel" : undefined}
              onClick={toggleAccountPanel}
            >
              <span className="flex size-[26px] items-center justify-center overflow-hidden rounded-full border border-white/[0.10] bg-white/[0.07] text-[11px] font-normal text-white/72">
                {avatarUrl ? (
                  <img src={avatarUrl} alt="" className="size-full object-cover" />
                ) : (
                  avatarInitial
                )}
              </span>
            </Button>
          </div>
        </div>
      </header>
      {project ? <ProjectXiajiMenu project={project} /> : null}
      {accountPanelOpen
        ? createPortal(
            <AccountPanel
              activeLanguage={activeLanguage}
              avatarInitial={avatarInitial}
              avatarUrl={avatarUrl}
              displayName={displayName}
              onChangeAvatar={openAvatarDialog}
              onChangePassword={showLogout ? openPasswordDialog : undefined}
              onBindPhone={canBindPhone ? () => {
                closeAccountPanelNow();
                setPhoneBindingOpen(true);
              } : undefined}
              onLanguageChange={switchLanguage}
              onClose={scheduleCloseAccountPanel}
              onEnter={openAccountPanel}
              onLogout={showLogout ? () => void handleLogout() : undefined}
              panelRef={accountPanelRef}
              passwordConfigured={passwordConfigured}
              position={accountPanelPosition}
              visible={accountPanelVisible}
              t={t}
            />,
            document.body,
          )
        : null}
      <PetGalleryDialog
        open={companionOpen}
        onOpenChange={setCompanionOpen}
        currentKind={companionKind}
        currentPet={companionPet}
        currentAccessory={pikoAccessory}
        onConfirm={handleCompanionConfirm}
      />
      <AvatarUploadDialog
        avatarInitial={avatarInitial}
        displayName={displayName}
        open={avatarDialogOpen}
        onOpenChange={setAvatarDialogOpen}
      />
      <PasswordChangeDialog
        open={passwordDialogOpen}
        onOpenChange={setPasswordDialogOpen}
        onPasswordChanged={handlePasswordChanged}
        passwordConfigured={passwordConfigured}
      />
      {phoneBindingOpen && canBindPhone ? <PhoneBindingDialog
        key={username}
        onClose={() => setPhoneBindingOpen(false)}
        onBound={() => { void accountSecurity.refetch(); }}
      /> : null}
      {ceRuntime ? <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} /> : null}
      {settingsWarningBubble
        ? createPortal(
            <div
              className="fixed z-[9999] w-[112px] rounded-md border border-amber-400/45 bg-amber-400 py-1 pl-2 pr-6 text-[11px] font-medium leading-none text-black shadow-[0_8px_22px_rgba(0,0,0,0.36),0_0_12px_rgba(251,191,36,0.28)]"
              style={{ left: settingsWarningBubble.left, top: settingsWarningBubble.top }}
              role="status"
            >
              <span
                className="absolute -top-[4px] size-2 rotate-45 border-l border-t border-amber-400/45 bg-amber-400"
                style={{ left: settingsWarningBubble.arrowLeft }}
                aria-hidden="true"
              />
              <span className="block truncate">{t("header.settingsWarningBubble")}</span>
              <button
                type="button"
                className="absolute right-1 top-1/2 flex size-4 -translate-y-1/2 items-center justify-center rounded-full text-black/70 transition-colors hover:bg-black/10 hover:text-black"
                aria-label={t("header.dismissSettingsWarningBubble")}
                onClick={() => setSettingsWarningBubbleDismissed(true)}
              >
                <X className="size-3" strokeWidth={3} />
              </button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function useFloatingBubblePosition(
  anchorRef: RefObject<HTMLElement | null>,
  enabled: boolean,
): { left: number; top: number; arrowLeft: number } | null {
  const [position, setPosition] = useState<{ left: number; top: number; arrowLeft: number } | null>(
    null,
  );

  useEffect(() => {
    if (!enabled) {
      setPosition(null);
      return;
    }

    const update = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) {
        setPosition(null);
        return;
      }
      const bubbleWidth = 112;
      const viewportPadding = 8;
      const idealLeft = rect.left + rect.width / 2 - bubbleWidth / 2;
      const left = Math.min(
        Math.max(viewportPadding, idealLeft),
        window.innerWidth - bubbleWidth - viewportPadding,
      );
      setPosition({
        left,
        top: rect.bottom + 7,
        arrowLeft: rect.left + rect.width / 2 - left - 4,
      });
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [anchorRef, enabled]);

  return position;
}

// [aipdd fork delta] `LANGUAGE_LABEL_KEYS` now lives in `@/i18n/languages` so the
// account menu and the public `LanguageSwitcher` share one source; the account
// menu maps over `SUPPORTED`, so adding a locale touches only that list.

function AccountPanel({
  activeLanguage,
  avatarInitial,
  avatarUrl,
  displayName,
  onChangeAvatar,
  onChangePassword,
  onBindPhone,
  onLanguageChange,
  onClose,
  onEnter,
  onLogout,
  panelRef,
  passwordConfigured,
  position,
  visible,
  t,
}: {
  activeLanguage: Supported;
  avatarInitial: string;
  avatarUrl: string | null;
  displayName: string;
  onChangeAvatar: () => void;
  onChangePassword?: () => void;
  onBindPhone?: () => void;
  onLanguageChange: (lang: Supported) => void;
  onClose: () => void;
  onEnter: () => void;
  onLogout?: () => void;
  panelRef: RefObject<HTMLDivElement | null>;
  passwordConfigured: boolean;
  position: { top: number; right: number };
  visible: boolean;
  t: (key: string) => string;
}) {
  const [languageOpen, setLanguageOpen] = useState(false);
  const activeLanguageLabel = t(LANGUAGE_LABEL_KEYS[activeLanguage]);

  return (
    <div
      ref={panelRef}
      id="header-account-panel"
      aria-label={t("header.account.open")}
      className={`fixed z-[80] w-[216px] transition-opacity duration-[350ms] ease-[var(--ease-out-quint)] ${
        visible ? "opacity-100" : "opacity-0"
      }`}
      style={{ top: position.top, right: position.right }}
      onMouseEnter={onEnter}
      onMouseLeave={onClose}
    >
      <div className="rounded-[14px] border border-white/[0.08] bg-[#202020]/78 p-2.5 text-slate-100 shadow-[0_18px_50px_rgba(0,0,0,0.36)] backdrop-blur-xl">
        <div className="mb-2.5 flex h-[50px] items-center gap-2.5 rounded-[10px] bg-white/[0.07] px-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full border border-white/[0.10] bg-white/[0.07] text-xs font-normal text-white/72">
            {avatarUrl ? (
              <img src={avatarUrl} alt="" className="size-full object-cover" />
            ) : (
              avatarInitial
            )}
          </span>
          <span className="min-w-0 truncate text-[15px] font-medium text-white">
            {displayName}
          </span>
        </div>
        <div className="space-y-0.5">
          {onBindPhone ? <AccountMenuRow
            icon={<KeyRound className="size-3.5" />}
            label={t("header.account.phoneBinding.title")}
            onClick={onBindPhone}
          /> : null}
          <AccountMenuRow
            icon={<Camera className="size-3.5" />}
            label={t("header.account.changeAvatar")}
            onClick={onChangeAvatar}
          />
          {onChangePassword ? (
            <AccountMenuRow
              icon={<KeyRound className="size-3.5" />}
              label={t(
                passwordConfigured
                  ? "header.account.changePassword"
                  : "header.account.setPassword",
              )}
              onClick={onChangePassword}
            />
          ) : null}
          <AccountMenuRow
            active={languageOpen}
            icon={<Languages className="size-3.5" />}
            label={t("header.account.selectLanguage")}
            meta={activeLanguageLabel}
            onClick={() => setLanguageOpen((open) => !open)}
          />
          {languageOpen ? (
            <div className="ml-[30px] mr-1 space-y-0.5 pb-1">
              {SUPPORTED.map((language) => (
                <PreferenceOption
                  key={language}
                  active={activeLanguage === language}
                  label={t(LANGUAGE_LABEL_KEYS[language])}
                  onClick={() => onLanguageChange(language)}
                />
              ))}
            </div>
          ) : null}
          {onLogout ? (
            <AccountMenuRow
              icon={<LogOut className="size-3.5" />}
              label={t("auth.logout")}
              onClick={onLogout}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function AccountMenuRow({
  active = false,
  icon,
  label,
  meta,
  onClick,
}: {
  active?: boolean;
  icon: ReactNode;
  label: string;
  meta?: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="ml-1 flex size-3.5 shrink-0 items-center justify-center text-slate-100/58" aria-hidden="true">
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {meta ? (
        <span className="max-w-16 truncate text-[11px] text-slate-400">{meta}</span>
      ) : null}
      <ChevronRight
        className={`mr-1 size-3.5 shrink-0 text-slate-100/88 transition-transform duration-150 ${
          active ? "rotate-90" : ""
        }`}
      />
    </>
  );
  const className =
    "flex h-9 w-full items-center gap-2 rounded-[8px] px-1.5 text-left text-[13px] font-normal text-slate-100 transition-colors duration-150 hover:bg-white/[0.05]";
  return (
    <button type="button" className={className} onClick={onClick}>
      {content}
    </button>
  );
}

function PreferenceOption({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="flex h-7 w-full items-center justify-between rounded-[7px] px-2 text-left text-[11px] text-slate-100/78 transition-colors duration-150 hover:bg-white/[0.05] hover:text-white"
      onClick={onClick}
    >
      <span>{label}</span>
      {active ? <Check className="size-3.5 text-cyan-300" /> : null}
    </button>
  );
}
