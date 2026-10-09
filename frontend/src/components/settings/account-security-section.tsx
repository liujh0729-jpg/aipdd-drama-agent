// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 账户安全：本人自助改密（口令存 auth.db，改后 DB 权威、
// 保留当前会话）+ operator 操作口令（导出提示词等，存设置数据库，不再只靠环境变量）。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useChangePassword } from "@/lib/queries/admin-users";
import {
  useModelGatewayConfig,
  useSaveOperatorSecurityConfig,
} from "@/lib/queries/model-gateway";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";

// 与后端 PRIVILEGED_MIN_PASSWORD_LENGTH 对齐：owner 自助改密的严格门槛。
const MIN_PASSWORD_LENGTH = 8;

function ChangePasswordCard() {
  const { t } = useTranslation();
  const changePassword = useChangePassword();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const dirty = Boolean(oldPassword || newPassword || confirmPassword);

  const submit = () => {
    setError(null);
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(
        t("settings.accountSecurity.passwordTooShort", { min: MIN_PASSWORD_LENGTH }),
      );
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t("settings.accountSecurity.passwordMismatch"));
      return;
    }
    changePassword.mutate(
      { oldPassword, newPassword },
      {
        onSuccess: () => {
          setOldPassword("");
          setNewPassword("");
          setConfirmPassword("");
          toast.success(t("settings.accountSecurity.passwordChanged"));
        },
        onError: (err) => {
          const message = backendErrorToastMessage(err, t);
          setError(message);
          toast.error(message);
        },
      },
    );
  };

  return (
    <section className="rounded-lg border border-border bg-white/[0.02] px-5 py-4">
      <div className="flex items-center gap-2">
        <KeyRound className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.accountSecurity.changeTitle")}
        </h3>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.accountSecurity.changeDescription")}
      </p>

      <div className="mt-4 space-y-3">
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label
            className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase"
            htmlFor="change-old-password"
          >
            {t("settings.accountSecurity.oldPassword")}
          </Label>
          <Input
            id="change-old-password"
            type="password"
            value={oldPassword}
            autoComplete="current-password"
            onChange={(event) => setOldPassword(event.target.value)}
            className="h-8 text-sm"
          />
        </div>
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label
            className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase"
            htmlFor="change-new-password"
          >
            {t("settings.accountSecurity.newPassword")}
          </Label>
          <Input
            id="change-new-password"
            type="password"
            value={newPassword}
            autoComplete="new-password"
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder={t("settings.accountSecurity.newPasswordPlaceholder", {
              min: MIN_PASSWORD_LENGTH,
            })}
            className="h-8 text-sm"
          />
        </div>
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label
            className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase"
            htmlFor="change-confirm-password"
          >
            {t("settings.accountSecurity.confirmPassword")}
          </Label>
          <Input
            id="change-confirm-password"
            type="password"
            value={confirmPassword}
            autoComplete="new-password"
            onChange={(event) => setConfirmPassword(event.target.value)}
            className="h-8 text-sm"
          />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!dirty || changePassword.isPending}
          onClick={submit}
        >
          {changePassword.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : null}
          {t("settings.accountSecurity.changeSubmit")}
        </Button>
        <p
          className={cn(
            "text-[11px] leading-relaxed",
            error ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {error ?? t("settings.accountSecurity.changeHint")}
        </p>
      </div>
    </section>
  );
}

function OperatorSecurityCard() {
  const { t } = useTranslation();
  const statusQuery = useModelGatewayConfig();
  const saveOperator = useSaveOperatorSecurityConfig();
  const status = statusQuery.data?.data?.operatorSecurity;
  const [password, setPassword] = useState("");
  const seeded = useRef(false);

  // 仅在首次拿到状态时用后端预览回填提示；口令本身不回显明文，占位提示走预览。
  useEffect(() => {
    if (status && !seeded.current) {
      seeded.current = true;
    }
  }, [status]);

  const configured = status?.promptExportPasswordConfigured ?? false;
  const preview = status?.promptExportPasswordPreview ?? "";
  const source = status?.source ?? "unset";

  const handleSave = () => {
    saveOperator.mutate(
      { promptExportPassword: password.trim() },
      {
        onSuccess: () => {
          seeded.current = false;
          setPassword("");
          toast.success(t("settings.accountSecurity.operatorSaved"));
        },
        onError: (err) =>
          toast.error(
            err instanceof Error
              ? err.message
              : t("settings.accountSecurity.operatorSaveFailed"),
          ),
      },
    );
  };

  return (
    <section className="rounded-lg border border-border bg-white/[0.02] px-5 py-4">
      <div className="flex items-center gap-2">
        <ShieldCheck
          className={cn(
            "size-4",
            configured ? "text-emerald-400" : "text-amber-400",
          )}
          aria-hidden
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.accountSecurity.operatorTitle")}
        </h3>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.accountSecurity.operatorDescription")}
      </p>

      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>
          {t("settings.accountSecurity.operatorStatusLabel")}
          {": "}
        </span>
        {configured ? (
          <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
            {preview || t("settings.accountSecurity.operatorConfigured")}
          </span>
        ) : (
          <span className="text-amber-300">
            {t("settings.accountSecurity.operatorUnset")}
          </span>
        )}
        <span className="text-[11px] text-muted-foreground/70">
          {t(`settings.accountSecurity.operatorSources.${source}`)}
        </span>
      </p>

      <div className="mt-4 space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
        <div className="grid grid-cols-[120px_1fr] items-start gap-3">
          <Label
            className="mt-1.5 justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase"
            htmlFor="operator-export-password"
          >
            {t("settings.accountSecurity.operatorFieldLabel")}
          </Label>
          <div className="space-y-1">
            <Input
              id="operator-export-password"
              type="password"
              value={password}
              autoComplete="new-password"
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("settings.accountSecurity.operatorPlaceholder")}
            />
            <p className="text-[11px] text-muted-foreground/80">
              {t("settings.accountSecurity.operatorFieldHint")}
            </p>
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          disabled={!password.trim() || saveOperator.isPending}
          onClick={handleSave}
        >
          {saveOperator.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : null}
          {t("settings.accountSecurity.operatorSave")}
        </Button>
      </div>
    </section>
  );
}

export function AccountSecuritySection() {
  const role = useAuthStore((state) => state.role);
  const isOwner = role === "owner";
  return (
    <div className="space-y-4 px-5 py-5">
      <ChangePasswordCard />
      {isOwner ? <OperatorSecurityCard /> : null}
    </div>
  );
}
