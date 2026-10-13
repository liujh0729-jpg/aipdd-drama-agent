// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：自定义网关面板（原 CustomGatewayPanel）。

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  useEnableHybrid,
  useInitCustomNewApi,
  type ModelGatewayConfig,
} from "@/lib/queries/model-gateway";
import { getRequestErrorMessage, getResponseErrorMessage } from "./shared";
import { FieldRow } from "./field-row";

function CustomGatewayPanel({
  config,
  loading,
  baseUrl,
  activateHybrid = false,
}: {
  config: ModelGatewayConfig | undefined;
  loading: boolean;
  baseUrl: string;
  activateHybrid?: boolean;
}) {
  const { t } = useTranslation();
  const initCustom = useInitCustomNewApi();
  const enableHybrid = useEnableHybrid();
  const [setupUsername, setSetupUsername] = useState("root");
  const [setupPassword, setSetupPassword] = useState("");
  const [setupConfirmPassword, setSetupConfirmPassword] = useState("");
  const [initError, setInitError] = useState("");
  const [initNotice, setInitNotice] = useState("");

  const showInitError = (message: string) => {
    setInitError(message);
    setInitNotice("");
    toast.error(message);
  };

  const showInitResponseError = (message: string) => {
    const displayMessage =
      message.includes("setupUsername") ||
      message.includes("NewAPI is not initialized")
        ? t("settings.modelConfig.custom.setupRequired")
        : message;
    showInitError(displayMessage);
  };

  const handleInit = async () => {
    setInitError("");
    setInitNotice("");
    const trimmedSetupUsername = setupUsername.trim();
    const trimmedSetupPassword = setupPassword.trim();
    const trimmedSetupConfirmPassword = setupConfirmPassword.trim();
    const hasSetupPassword = Boolean(
      trimmedSetupPassword || trimmedSetupConfirmPassword,
    );
    if (
      hasSetupPassword &&
      (!trimmedSetupPassword || !trimmedSetupConfirmPassword)
    ) {
      showInitError(t("settings.modelConfig.custom.setupPasswordIncomplete"));
      return;
    }
    if (hasSetupPassword && trimmedSetupPassword.length < 8) {
      showInitError(t("settings.modelConfig.custom.setupPasswordTooShort"));
      return;
    }
    if (
      hasSetupPassword &&
      trimmedSetupPassword !== trimmedSetupConfirmPassword
    ) {
      showInitError(t("settings.modelConfig.custom.setupPasswordMismatch"));
      return;
    }

    try {
      const response = await initCustom.mutateAsync({
        ...(baseUrl.trim() ? { newApiBaseUrl: baseUrl.trim() } : {}),
        ...(hasSetupPassword
          ? {
              setupUsername: trimmedSetupUsername || "root",
              setupPassword: trimmedSetupPassword,
              setupConfirmPassword: trimmedSetupConfirmPassword,
            }
          : {}),
      });
      if (response.ok !== true) {
        showInitResponseError(
          getResponseErrorMessage(
            response,
            t("settings.modelConfig.requestFailed"),
          ),
        );
        return;
      }
      const passwordIgnored =
        hasSetupPassword &&
        response.data.newApiSetup?.alreadyInitialized === true;
      setSetupPassword("");
      setSetupConfirmPassword("");
      if (passwordIgnored) {
        setInitNotice(
          t(
            "settings.modelConfig.custom.setupAlreadyInitializedPasswordIgnored",
          ),
        );
      }
      if (activateHybrid && config?.official?.configured) {
        const hybridResponse = await enableHybrid.mutateAsync();
        if (!hybridResponse.ok) {
          showInitResponseError(
            getResponseErrorMessage(
              hybridResponse,
              t("settings.modelConfig.requestFailed"),
            ),
          );
          return;
        }
      }
      toast.success(t("settings.modelConfig.custom.initialized"));
    } catch (error) {
      const message = await getRequestErrorMessage(
        error,
        t("settings.modelConfig.requestFailed"),
      );
      showInitResponseError(message);
    }
  };

  const databaseStatus = config?.provisioner?.database;
  const databaseReady = Boolean(databaseStatus?.available);
  const customConfigured = Boolean(config?.custom?.configured);

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.custom.description")}
      </p>

      <div className="rounded-md border border-border/70 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.custom.localStatusTitle")}
          </p>
          <span
            className={cn(
              "text-[11px]",
              customConfigured ? "text-emerald-400" : "text-amber-300",
            )}
          >
            {customConfigured
              ? t("settings.modelConfig.custom.localReady")
              : t("settings.modelConfig.custom.localNeedsInit")}
          </span>
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          {databaseReady
            ? t("settings.modelConfig.custom.sqliteReady")
            : t("settings.modelConfig.custom.sqliteWaiting")}
        </p>
      </div>

      {!customConfigured ? (
        <div className="rounded-md border border-border/70 p-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.custom.setupAdminTitle")}
          </p>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.custom.setupAdminDescription")}
          </p>
          <div className="mt-3 space-y-2.5">
            <FieldRow
              name="newapi-setup-username"
              autoComplete="username"
              label={t("settings.modelConfig.custom.setupUsername")}
              value={setupUsername}
              onChange={setSetupUsername}
              placeholder={t(
                "settings.modelConfig.custom.setupUsernamePlaceholder",
              )}
            />
            <FieldRow
              secret
              name="newapi-setup-password"
              autoComplete="new-password"
              label={t("settings.modelConfig.custom.setupPassword")}
              value={setupPassword}
              onChange={setSetupPassword}
              placeholder={t(
                "settings.modelConfig.custom.setupPasswordPlaceholder",
              )}
            />
            <FieldRow
              secret
              name="newapi-setup-password-confirmation"
              autoComplete="new-password"
              label={t("settings.modelConfig.custom.setupConfirmPassword")}
              value={setupConfirmPassword}
              onChange={setSetupConfirmPassword}
              placeholder={t(
                "settings.modelConfig.custom.setupConfirmPasswordPlaceholder",
              )}
            />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.custom.setupPasswordOnlyOnce")}
          </p>
        </div>
      ) : null}

      {initError ? (
        <p
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive"
        >
          {initError}
        </p>
      ) : null}
      {!initError && initNotice ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-400/35 bg-amber-400/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200"
        >
          {initNotice}
        </p>
      ) : null}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          onClick={handleInit}
          disabled={loading || initCustom.isPending}
        >
          {initCustom.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}
          {t(
            customConfigured
              ? "settings.modelConfig.custom.repair"
              : "settings.modelConfig.custom.init",
          )}
        </Button>
      </div>
    </div>
  );
}

export {
  CustomGatewayPanel,
};
