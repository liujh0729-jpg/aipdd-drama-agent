// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cn } from "@/lib/utils";
import type { StageCount } from "@/types/beat-state";

interface PipelineSummaryProps {
  count: StageCount;
}

/** Compact inline progress bar for a single stage: ■■■□□ 3/5 */
export function PipelineSummary({ count }: PipelineSummaryProps) {
  const { ready, total, failed } = count;
  if (total === 0) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <div className="flex gap-px">
        {Array.from({ length: total }, (_, i) => {
          const state =
            i < ready
              ? "ready"
              : i < ready + failed
                ? "failed"
                : "missing";
          return (
            <div
              key={i}
              className={cn(
                "h-2.5 w-1.5 rounded-sm",
                state === "ready" && "bg-primary",
                state === "failed" && "bg-destructive",
                state === "missing" && "bg-muted-foreground/20",
              )}
            />
          );
        })}
      </div>
      <span className="tabular-nums">
        {ready}/{total}
      </span>
    </div>
  );
}
