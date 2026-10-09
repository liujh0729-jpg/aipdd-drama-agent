// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";
import type { TaskState } from "@/task-center/types";
import { displayLabel } from "@/task-center/derivations";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

function shortTimestamp(task: TaskState): string {
  // Match the list sort: prefer updated_at, fall back to created_at.
  const raw = task.updated_at || task.created_at;
  if (!raw) return "";
  const d = dayjs(raw);
  if (!d.isValid()) return "";
  return d.isSame(dayjs(), "day") ? d.format("HH:mm") : d.format("MM-DD");
}

const STATUS_ICON: Record<TaskState["status"], string> = {
  submitting: "·",
  queued: "·",
  pending: "·",
  starting: "·",
  running: "⚡",
  completed: "✓",
  failed: "✗",
  cancelled: "×",
};

const STATUS_COLOR: Record<TaskState["status"], string> = {
  submitting: "text-muted-foreground",
  queued: "text-muted-foreground",
  pending: "text-muted-foreground",
  starting: "text-muted-foreground",
  running: "text-primary",
  completed: "text-success",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
};

export function TaskRow({
  task,
  selected,
  onClick,
}: {
  task: TaskState;
  selected: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const label = displayLabel(task, t);
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 border-b border-border/50 px-3 py-2 text-left text-xs hover:bg-muted/50",
        selected && "bg-muted",
      )}
    >
      <span className={cn("w-3 shrink-0 text-center", STATUS_COLOR[task.status])}>
        {STATUS_ICON[task.status]}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {task.status === "running" && (
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="w-16">
            <Progress value={Math.round(task.progress * 100)} />
          </div>
          <span className="w-8 text-right font-mono text-xs tabular-nums text-muted-foreground">
            {Math.round(task.progress * 100)}%
          </span>
        </div>
      )}
      <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground/70">
        {shortTimestamp(task)}
      </span>
    </button>
  );
}
