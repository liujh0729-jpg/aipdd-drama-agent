// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { useTaskCenterStore, selectRunningTasks } from "@/task-center/store";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function HeaderEntry({ className }: { className?: string }) {
  const { t } = useTranslation();
  const open = useAppStore((s) => s.taskPanelOpen);
  const setOpen = useAppStore((s) => s.setTaskPanelOpen);
  const runningCount = useTaskCenterStore((s) => selectRunningTasks(s).length);

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={() => setOpen(!open)}
      aria-label={t("nav.taskCenter")}
      aria-pressed={open}
      className={cn("relative", className)}
    >
      <Activity className={cn("size-4", open && "text-primary")} />
      {runningCount > 0 && (
        <span
          className="absolute right-0 top-0 flex size-3.5 items-center justify-center rounded-full border border-primary/35 bg-primary/18 text-[9px] font-semibold leading-none text-primary"
          aria-hidden="true"
        >
          {runningCount > 9 ? "9+" : runningCount}
        </span>
      )}
    </Button>
  );
}
