// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

interface StageProgressPanelProps {
  title: string;
  currentTask: string;
  progress: number;
  logs: string[];
  onStop: () => void;
  stopping?: boolean;
}

/**
 * Shared progress + log panel for stage SSE tasks.
 * - `onStop` is expected to cancel the task server-side and close the SSE.
 * - Auto-scrolls the log panel as new lines arrive.
 * - The status row is a polite live region so assistive tech announces
 *   progress changes without hijacking the user's reading flow. Log lines
 *   are intentionally NOT announced — they'd flood the SR buffer.
 */
export function StageProgressPanel({
  title,
  currentTask,
  progress,
  logs,
  onStop,
  stopping = false,
}: StageProgressPanelProps) {
  const { t } = useTranslation();
  const reducedMotion = useReducedMotion();
  const logEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const scrollIntoView = logEndRef.current?.scrollIntoView;
    if (!scrollIntoView) return;
    scrollIntoView.call(logEndRef.current, {
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }, [logs, reducedMotion]);

  const percent = Math.round(progress * 100);

  return (
    <div className="border-b border-border bg-card/30">
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="flex items-center gap-3 px-4 py-2 text-xs"
      >
        <span className="shrink-0 font-medium text-muted-foreground">{title}</span>
        <span className="flex-1 truncate text-foreground">
          {currentTask || t("common.preparing")}
        </span>
        <span className="font-mono tabular-nums text-muted-foreground">
          {percent}%
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onStop}
          disabled={stopping}
          className="h-6 gap-1 px-2 text-xs"
        >
          {stopping ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Square className="size-3" />
          )}
          {t("common.stop")}
        </Button>
      </div>
      <Progress
        value={percent}
        aria-label={title}
        className="rounded-none"
      />
      {logs.length > 0 && (
        <div
          aria-hidden="true"
          className="max-h-32 overflow-y-auto border-t border-border bg-background/40 p-2"
        >
          {logs.map((log, i) => (
            <p
              key={i}
              className="font-mono text-xs leading-tight text-muted-foreground"
            >
              <span className="mr-2 text-muted-foreground/50">
                [{String(i + 1).padStart(2, "0")}]
              </span>
              {log}
            </p>
          ))}
          <div ref={logEndRef} />
        </div>
      )}
    </div>
  );
}
