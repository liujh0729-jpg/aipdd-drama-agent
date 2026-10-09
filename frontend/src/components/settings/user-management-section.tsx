// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 用户管理：超管添加账户、改角色、禁用/启用、重置口令。
// 只碰账户，不碰项目可见性：项目仍按账户完全隔离（含超管自己）。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import { KeyRound, Loader2, UserPlus, X } from "lucide-react";

import { confirmDialog } from "@/components/confirm-dialog-host";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAdminUsers,
  useCreateAdminUser,
  useResetAdminUserPassword,
  useUpdateAdminUser,
  type AdminUserRecord,
  type AdminUserRole,
} from "@/lib/queries/admin-users";
import { useAuthStore } from "@/stores/auth-store";

// 与后端 normalize_username / PRIVILEGED_MIN_PASSWORD_LENGTH 保持一致，
// 让错误在提交前就显示出来，而不是等后端回 400。
const USERNAME_RE = /^[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}$/;
const MIN_PASSWORD_LENGTH = 8;

const ROLE_OPTIONS: AdminUserRole[] = ["owner", "member"];

/**
 * [aipdd fork delta] 管理接口的失败文案统一走这里。后端 detail 是固定英文，直接
 * 透给中文团队很费解：认得出的先按词条翻，认不出的原样保留，既不吞真实错误、也
 * 不编造原因。 thrown Error 把 detail 放在 message，`{ok:false}` 响应放在 error。
 */
const BACKEND_ERROR_KEYS: ReadonlyArray<readonly [RegExp, string]> = [
  [/^multi-user mode is disabled/i, "settings.users.errors.multiUserDisabled"],
  [/^username must be/i, "settings.users.usernameInvalid"],
  [/^password must be at least/i, "settings.users.passwordTooShort"],
  [/username already exists/i, "settings.users.errors.usernameExists"],
  [/^user not found$/i, "settings.users.errors.userNotFound"],
  [/cannot change role or status of your own account/i, "settings.users.selfGuard"],
  [/the last active administrator/i, "settings.users.errors.lastAdmin"],
  [/a disabled account/i, "settings.users.errors.resetDisabledAccount"],
];

function adminErrorText(error: unknown, fallback: string, t: TFunction): string {
  const record = error as { message?: unknown; error?: unknown } | null;
  const raw =
    typeof record?.message === "string"
      ? record.message
      : typeof record?.error === "string"
        ? record.error
        : "";
  const detail = raw.trim();
  if (!detail) return fallback;
  for (const [pattern, key] of BACKEND_ERROR_KEYS) {
    if (pattern.test(detail)) {
      return t(key, { min: MIN_PASSWORD_LENGTH, defaultValue: detail });
    }
  }
  return detail;
}

function formatCreatedAt(value: string, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function RoleToggle({
  record,
  disabled,
  pending,
  onChange,
}: {
  record: AdminUserRecord;
  disabled: boolean;
  pending: boolean;
  onChange: (role: AdminUserRole) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t("settings.users.role")}
      className="inline-flex overflow-hidden rounded-md border border-border"
    >
      {ROLE_OPTIONS.map((role) => {
        const active = record.role === role;
        return (
          <button
            key={role}
            type="button"
            aria-pressed={active}
            disabled={disabled || pending}
            title={disabled ? t("settings.users.selfGuard") : undefined}
            onClick={() => onChange(role)}
            className={
              active
                ? "bg-white/[0.12] px-2.5 py-1 text-xs font-medium text-foreground disabled:opacity-60"
                : "px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:opacity-60"
            }
          >
            {t(`settings.users.roles.${role}`)}
          </button>
        );
      })}
    </div>
  );
}

function PasswordResetRow({
  record,
  onClose,
}: {
  record: AdminUserRecord;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const resetMutation = useResetAdminUserPassword();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("settings.users.passwordTooShort", { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    setError(null);
    try {
      const res = await resetMutation.mutateAsync({
        userId: record.id,
        password,
      });
      if (res.ok === false) {
        const message = adminErrorText(res, t("common.error"), t);
        setError(message);
        toast.error(message);
        return;
      }
      setPassword("");
      onClose();
      if (res.data.is_self) {
        // 改自己口令也会把当前会话吊销掉：与其让后面每个请求都 401，不如直接回登录页
        toast.success(t("settings.users.selfPasswordResetDone"));
        await useAuthStore.getState().logout();
        window.location.assign("/login");
        return;
      }
      toast.success(t("settings.users.resetPasswordDone", { username: record.username }));
    } catch (resetError) {
      const message = adminErrorText(
        resetError,
        t("settings.users.resetPasswordFailed"),
        t,
      );
      setError(message);
      toast.error(message);
    }
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-border bg-white/[0.03] px-3 py-2.5">
      <label className="text-xs text-muted-foreground" htmlFor={`reset-${record.id}`}>
        {t("settings.users.newPassword")}
      </label>
      <Input
        id={`reset-${record.id}`}
        type="text"
        value={password}
        autoComplete="new-password"
        onChange={(event) => setPassword(event.target.value)}
        placeholder={t("settings.users.newPasswordPlaceholder", {
          min: MIN_PASSWORD_LENGTH,
        })}
        className={`h-8 w-56 text-xs ${error ? "border-destructive" : ""}`}
      />
      <Button
        type="button"
        size="sm"
        onClick={() => void submit()}
        disabled={resetMutation.isPending}
      >
        {resetMutation.isPending ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <KeyRound className="size-3.5" aria-hidden />
        )}
        {t("settings.users.confirmReset")}
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onClose}>
        <X className="size-3.5" aria-hidden />
        {t("settings.users.cancel")}
      </Button>
      <p className="w-full text-[11px] leading-relaxed text-muted-foreground">
        {error ?? t("settings.users.resetPasswordHint", { username: record.username })}
      </p>
    </div>
  );
}

function UserRow({ record }: { record: AdminUserRecord }) {
  const { t, i18n } = useTranslation();
  const updateMutation = useUpdateAdminUser();
  const [resetting, setResetting] = useState(false);
  const pending = updateMutation.isPending;
  // 后端对禁用账户的重置口令直接回 400。与其让管理员点一下吃一条英文报错，不如
  // 提前把入口锁住并说明原因（先启用、再重置）。
  const suspended = record.status !== "active";

  const changeRole = async (role: AdminUserRole) => {
    if (role === record.role) return;
    try {
      const res = await updateMutation.mutateAsync({ userId: record.id, role });
      if (res.ok === false) {
        toast.error(adminErrorText(res, t("settings.users.updateFailed"), t));
        return;
      }
      toast.success(
        t("settings.users.roleChanged", {
          username: record.username,
          role: t(`settings.users.roles.${res.data.role}`),
        }),
      );
    } catch (error) {
      toast.error(adminErrorText(error, t("settings.users.updateFailed"), t));
    }
  };

  const toggleStatus = async () => {
    const suspending = record.status === "active";
    if (suspending) {
      const confirmed = await confirmDialog({
        title: t("settings.users.disable"),
        description: t("settings.users.disableConfirm", { username: record.username }),
        confirmText: t("settings.users.disable"),
        confirmVariant: "destructive",
      });
      if (!confirmed) return;
    }
    try {
      const res = await updateMutation.mutateAsync({
        userId: record.id,
        status: suspending ? "suspended" : "active",
      });
      if (res.ok === false) {
        toast.error(adminErrorText(res, t("settings.users.updateFailed"), t));
        return;
      }
      toast.success(
        suspending
          ? t("settings.users.disabledDone", { username: record.username })
          : t("settings.users.enabledDone", { username: record.username }),
      );
    } catch (error) {
      toast.error(adminErrorText(error, t("settings.users.updateFailed"), t));
    }
  };

  return (
    <div
      data-admin-user-row={record.username}
      className="rounded-lg border border-border bg-white/[0.02] px-3.5 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">
              {record.username}
            </span>
            {record.is_self ? (
              <Badge variant="outline" className="shrink-0 text-[10px]">
                {t("settings.users.self")}
              </Badge>
            ) : null}
            {record.status !== "active" ? (
              <Badge variant="destructive" className="shrink-0 text-[10px]">
                {t("settings.users.statuses.suspended")}
              </Badge>
            ) : null}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t("settings.users.createdAt", {
              date: formatCreatedAt(record.created_at, i18n.language),
            })}
          </p>
        </div>

        <RoleToggle
          record={record}
          disabled={record.is_self}
          pending={pending}
          onChange={(role) => void changeRole(role)}
        />

        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={suspended}
            title={suspended ? t("settings.users.resetPasswordDisabledHint") : undefined}
            onClick={() => setResetting((open) => !open)}
          >
            <KeyRound className="size-3.5" aria-hidden />
            {t("settings.users.resetPassword")}
          </Button>
          {record.is_self ? null : (
            <Button
              type="button"
              size="sm"
              variant={record.status === "active" ? "ghost" : "outline"}
              disabled={pending}
              onClick={() => void toggleStatus()}
            >
              {record.status === "active"
                ? t("settings.users.disable")
                : t("settings.users.enable")}
            </Button>
          )}
        </div>
      </div>

      {resetting && !suspended ? (
        <PasswordResetRow record={record} onClose={() => setResetting(false)} />
      ) : null}
    </div>
  );
}

function NewUserForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const createMutation = useCreateAdminUser();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<AdminUserRole>("member");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!USERNAME_RE.test(username.trim())) {
      setError(t("settings.users.usernameInvalid"));
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("settings.users.passwordTooShort", { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    setError(null);
    try {
      const res = await createMutation.mutateAsync({
        username: username.trim(),
        password,
        role,
      });
      if (res.ok === false) {
        const message = adminErrorText(res, t("settings.users.createFailed"), t);
        setError(message);
        toast.error(message);
        return;
      }
      onDone();
      toast.success(t("settings.users.created", { username: res.data.username }));
    } catch (createError) {
      const message = adminErrorText(
        createError,
        t("settings.users.createFailed"),
        t,
      );
      setError(message);
      toast.error(message);
    }
  };

  return (
    <div
      data-admin-user-create-form="1"
      className="mt-3 space-y-2.5 rounded-lg border border-border bg-white/[0.03] px-3.5 py-3"
    >
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="min-w-[180px] flex-1">
          <label className="text-xs text-muted-foreground" htmlFor="admin-new-username">
            {t("settings.users.username")}
          </label>
          <Input
            id="admin-new-username"
            value={username}
            autoComplete="off"
            onChange={(event) => setUsername(event.target.value)}
            placeholder={t("settings.users.usernamePlaceholder")}
            className="mt-1 h-8 text-sm"
          />
        </div>
        <div className="min-w-[180px] flex-1">
          <label className="text-xs text-muted-foreground" htmlFor="admin-new-password">
            {t("settings.users.password")}
          </label>
          <Input
            id="admin-new-password"
            type="text"
            value={password}
            autoComplete="new-password"
            onChange={(event) => setPassword(event.target.value)}
            placeholder={t("settings.users.passwordPlaceholder", {
              min: MIN_PASSWORD_LENGTH,
            })}
            className="mt-1 h-8 text-sm"
          />
        </div>
        <div>
          <span className="text-xs text-muted-foreground">{t("settings.users.role")}</span>
          <div className="mt-1 flex items-center gap-1.5">
            {ROLE_OPTIONS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={role === option}
                onClick={() => setRole(option)}
                className={
                  role === option
                    ? "rounded-md border border-border bg-white/[0.12] px-2.5 py-1.5 text-xs font-medium text-foreground"
                    : "rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
                }
              >
                {t(`settings.users.roles.${option}`)}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Button type="button" size="sm" onClick={() => void submit()} disabled={createMutation.isPending}>
            {createMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <UserPlus className="size-3.5" aria-hidden />
            )}
            {t("settings.users.create")}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onDone}>
            {t("settings.users.cancel")}
          </Button>
        </div>
      </div>
      <p className={`text-[11px] leading-relaxed ${error ? "text-destructive" : "text-muted-foreground"}`}>
        {error ?? t("settings.users.createHint")}
      </p>
    </div>
  );
}

export function UserManagementSection() {
  const { t } = useTranslation();
  const usersQuery = useAdminUsers();
  const [creating, setCreating] = useState(false);
  const users = usersQuery.data?.data ?? [];
  const ownerCount = users.filter((user) => user.role === "owner").length;

  return (
    <div className="px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">
            {t("settings.users.title")}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {t("settings.users.description")}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t("settings.users.summary", { total: users.length, owners: ownerCount })}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={creating}
          onClick={() => setCreating(true)}
        >
          <UserPlus className="size-3.5" aria-hidden />
          {t("settings.users.newUser")}
        </Button>
      </div>

      {creating ? <NewUserForm onDone={() => setCreating(false)} /> : null}

      {usersQuery.isLoading ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.users.loading")}
        </div>
      ) : usersQuery.isError || !usersQuery.data ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{t("settings.users.loadFailed")}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void usersQuery.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : (
        <div className="mt-4 space-y-2.5">
          {users.map((record) => (
            <UserRow key={record.id} record={record} />
          ))}
        </div>
      )}
    </div>
  );
}
