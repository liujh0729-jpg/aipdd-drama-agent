// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, CheckCircle2, Clock } from "lucide-react";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export type VerifyState = "not_run" | "running" | "passed" | "failed";

const VERIFY_STATE_KEYS: Record<VerifyState, string> = {
  not_run: "episode.workbench.verify.notRun",
  running: "episode.workbench.verify.running",
  passed: "episode.workbench.verify.passed",
  failed: "episode.workbench.verify.failed",
};

export interface ConsistencyReport {
  overall_passed?: boolean;
  characters?: Array<{
    name: string;
    passed?: boolean;
    face_score?: number;
    clothing_score?: number;
    lowest_beat?: number;
  }>;
}

export function useVerifyReport(
  project: string,
  episode: number,
  filename: string,
): { state: VerifyState; report: ConsistencyReport | null } {
  const epPad = String(episode).padStart(3, "0");
  const path = `verify_reports/ep${epPad}/${filename}`;

  const { data, isLoading, error } = useQuery({
    queryKey: ["verify-report", project, episode, filename],
    queryFn: async () => {
      const res = await api.get(
        `api/v1/projects/${project}/files/${path}`,
        { throwHttpErrors: false },
      );
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json<ConsistencyReport>();
    },
    retry: false,
    enabled: !!project && episode > 0,
  });

  if (isLoading) return { state: "running", report: null };
  if (error) return { state: "not_run", report: null };
  if (!data) return { state: "not_run", report: null };
  if (data.overall_passed === true) return { state: "passed", report: data };
  if (data.overall_passed === false) return { state: "failed", report: data };
  return { state: "running", report: data };
}

interface VerifyChipProps {
  label: string;
  drawerTitle: string;
  state: VerifyState;
  report: ConsistencyReport | null;
}

/**
 * Self-contained verify chip — renders a status pill and opens a detail drawer
 * on click. Used inside sub-tab panes (草图 / 视频) to surface the stage-specific
 * consistency report.
 */
export function VerifyChip({
  label,
  drawerTitle,
  state,
  report,
}: VerifyChipProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const style =
    state === "passed"
      ? "border-primary/40 bg-primary/10 text-primary"
      : state === "failed"
        ? "border-destructive/40 bg-destructive/10 text-destructive"
        : state === "running"
          ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "border-border bg-background/40 text-muted-foreground";
  const Icon =
    state === "passed"
      ? CheckCircle2
      : state === "failed"
        ? AlertCircle
        : Clock;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors hover:opacity-80",
          style,
        )}
      >
        <Icon className="size-3" />
        {label}
        <span className="text-[10px] opacity-70">· {t(VERIFY_STATE_KEYS[state])}</span>
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full sm:!max-w-[520px]">
          <SheetHeader className="border-b border-border pb-3">
            <SheetTitle>{drawerTitle}</SheetTitle>
            <SheetDescription>{t("episode.workbench.verify.status", { state: t(VERIFY_STATE_KEYS[state]) })}</SheetDescription>
          </SheetHeader>
          <div className="space-y-3 px-4 py-3">
            {state === "not_run" && (
              <p className="text-xs text-muted-foreground">
                {t("episode.workbench.verify.notGenerated")}
              </p>
            )}
            {report?.characters && (
              <div className="space-y-2">
                {report.characters.map((c) => (
                  <div
                    key={c.name}
                    className="rounded-md border border-border bg-background/40 p-2 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{c.name}</span>
                      <span
                        className={cn(
                          "text-xs",
                          c.passed ? "text-primary" : "text-amber-600 dark:text-amber-400",
                        )}
                      >
                        {c.passed ? t("episode.workbench.verify.charPassed") : t("episode.workbench.verify.charFailed")}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
                      {typeof c.face_score === "number" && (
                        <span>{t("episode.workbench.verify.faceScore", { score: c.face_score.toFixed(1) })}</span>
                      )}
                      {typeof c.clothing_score === "number" && (
                        <span>{t("episode.workbench.verify.clothingScore", { score: c.clothing_score.toFixed(1) })}</span>
                      )}
                      {typeof c.lowest_beat === "number" && (
                        <Button
                          variant="ghost"
                          size="xs"
                          className="ml-auto h-5 text-xs"
                        >
                          {t("episode.workbench.verify.jumpBeat", { n: c.lowest_beat })}
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
