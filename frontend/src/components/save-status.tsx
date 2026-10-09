// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/format-relative-time";
import { useSaveState } from "@/stores/save-status-store";

type Variant = "header" | "inline";

interface SaveStatusProps {
  scope: string;
  variant?: Variant;
  /** When provided, an error state renders a Retry button that invokes this. */
  onRetry?: () => void;
  className?: string;
}

/** How long a fresh save is rendered as "Saved" before switching to relative. */
const FRESH_SAVED_MS = 10_000;
/** How often to re-evaluate the relative-time label after the freshness window. */
const RELATIVE_TICK_MS = 30_000;

export function SaveStatus({
  scope,
  variant = "header",
  onRetry,
  className,
}: SaveStatusProps) {
  const { t } = useTranslation();
  const state = useSaveState(scope);
  const [now, setNow] = useState(() => Date.now());

  // Keep `now` fresh while in the saved state so the label updates from
  // "Saved" to "Saved 10s ago" exactly when the freshness window expires,
  // then tick coarsely.
  useEffect(() => {
    if (state.status !== "saved" || state.lastSavedAt === null) return;
    const age = Date.now() - state.lastSavedAt;
    let interval: number | undefined;
    const scheduleInterval = () => {
      interval = window.setInterval(() => setNow(Date.now()), RELATIVE_TICK_MS);
    };
    if (age < FRESH_SAVED_MS) {
      const timeout = window.setTimeout(() => {
        setNow(Date.now());
        scheduleInterval();
      }, FRESH_SAVED_MS - age);
      return () => {
        window.clearTimeout(timeout);
        if (interval !== undefined) window.clearInterval(interval);
      };
    }
    scheduleInterval();
    return () => {
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [state.status, state.lastSavedAt]);

  // Spec: only the header variant is a live announcer — inline chips
  // duplicate the header announcement and create screen-reader chatter.
  const isHeader = variant === "header";
  const sizing = isHeader
    ? "text-xs gap-1.5 px-2 py-1"
    : "text-[11px] gap-1 px-1.5 py-0.5";
  const iconSize = isHeader ? "size-3.5" : "size-3";

  if (state.status === "idle") return null;

  if (state.status === "saving") {
    return (
      <span
        role={isHeader ? "status" : undefined}
        aria-live={isHeader ? "polite" : undefined}
        aria-atomic={isHeader ? "true" : undefined}
        className={cn(
          "inline-flex items-center rounded-md text-muted-foreground tabular-nums",
          sizing,
          className,
        )}
      >
        <Loader2 className={cn(iconSize, "animate-spin")} aria-hidden="true" />
        {t("common.saveStatus.saving")}
      </span>
    );
  }

  if (state.status === "error") {
    return (
      <span
        role={isHeader ? "alert" : undefined}
        aria-live={isHeader ? "assertive" : undefined}
        className={cn(
          "inline-flex items-center rounded-md bg-destructive/10 text-destructive",
          sizing,
          className,
        )}
      >
        <AlertCircle className={iconSize} aria-hidden="true" />
        <span>{t("common.saveStatus.error")}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-1 rounded underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {t("common.saveStatus.retry")}
          </button>
        )}
      </span>
    );
  }

  // saved
  const ageMs = state.lastSavedAt ? now - state.lastSavedAt : 0;
  const isFresh = ageMs < FRESH_SAVED_MS;
  const relative =
    state.lastSavedAt && !isFresh
      ? formatRelativeTime(new Date(state.lastSavedAt).toISOString(), now)
      : null;
  const label =
    isFresh || !relative
      ? t("common.saveStatus.saved")
      : t("common.saveStatus.savedAgo", { t: relative });

  return (
    <span
      role={isHeader ? "status" : undefined}
      aria-live={isHeader ? "polite" : undefined}
      aria-atomic={isHeader ? "true" : undefined}
      className={cn(
        "inline-flex items-center rounded-md tabular-nums",
        isFresh ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground",
        sizing,
        className,
      )}
    >
      <Check className={iconSize} aria-hidden="true" />
      {label}
    </span>
  );
}
