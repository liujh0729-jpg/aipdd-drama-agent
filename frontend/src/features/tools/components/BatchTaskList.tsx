// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具页批量任务列表：复用 useTasks 的项目级轮询/任务中心 SSE，
// 按当前工具产生的 task_type 过滤展示。
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";

import { useTasks } from "@/lib/queries/tasks";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { taskTypeLabel } from "@/task-center/derivations";
import { firstMediaUrlInResult, wasArchivedToLibrary } from "@/features/tools/core";
import type { Task, TaskState, TaskStatus } from "@/types/task";

const ACTIVE: TaskStatus[] = ["submitting", "queued", "pending", "starting", "running"];

const STATUS_LABEL: Record<TaskStatus, string> = {
  submitting: "tasks.status.submitting",
  queued: "tasks.status.queued",
  pending: "tasks.status.pending",
  starting: "tasks.status.starting",
  running: "tasks.status.running",
  completed: "tasks.status.completed",
  failed: "tasks.status.failed",
  cancelled: "tasks.status.cancelled",
};

export interface BatchTaskListProps {
  project: string;
  /**
   * [aipdd fork delta] 工具 kind。只显示后端写进 metadata.source_tool
   * 等于本 kind 的任务——多个功能共用同一 task_type（lip-sync / 换人 /
   * 换产品 / 视频去水印都是 freezone_video_gen）、以及画布等其它
   * 入口同类型的任务都不会混进来。
   */
  kind: string;
}

function sortKey(task: Task): number {
  const ts = task.created_at ? Date.parse(task.created_at) : 0;
  return Number.isNaN(ts) ? 0 : ts;
}

export function BatchTaskList({ project, kind }: BatchTaskListProps) {
  const { t } = useTranslation();
  const { data } = useTasks(project ? { project } : undefined);

  const tasks = useMemo(() => {
    // 严格归属：仅当后端浮现的 metadata.source_tool 命中本功能才展示。
    // 不回落 task_type（同 task_type 的画布任务不带该标签，回落会重现
    // 「一个页面看到所有任务」的问题）。
    const list = (data?.data ?? []).filter(
      (task) => task.metadata?.source_tool === kind,
    );
    return [...list].sort((a, b) => sortKey(b) - sortKey(a)).slice(0, 30);
  }, [data, kind]);

  if (tasks.length === 0) {
    return <div className="text-xs leading-relaxed text-muted-foreground">{t("tools.common.noTasks")}</div>;
  }

  return (
    <ul className="space-y-2">
      {tasks.map((task, index) => {
        const active = ACTIVE.includes(task.status);
        const url = firstMediaUrlInResult(task.result);
        // 工具页产物完成后会自动写进资产库「待分类资产」；标记由后端在
        // 归档成功时写入 result，同 task_type 的画布任务不会带该标记。
        const archived = wasArchivedToLibrary(task.result);
        return (
          <li
            key={task.task_id ?? `${task.task_type}-${index}`}
            className="rounded-lg border border-border bg-white/[0.02] px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm text-foreground">
                {/* 同一 task_type 被多个功能共用（对口型 / 换人 / 换产品都是
                    freezone_video_gen），后端那串中文只说得出大类；这里按
                    metadata.source_tool 显本功能的名字。列表已按 kind 过滤，行里就
                    不再拼 scope（omni 任务的 scope 是 job_id，对用户是噪声）。 */}
                {taskTypeLabel(task as TaskState, t)}
              </span>
              <span
                className={cn(
                  "shrink-0 text-xs",
                  task.status === "failed"
                    ? "text-red-400"
                    : task.status === "completed"
                      ? "text-emerald-400"
                      : "text-muted-foreground",
                )}
              >
                {t(STATUS_LABEL[task.status])}
              </span>
            </div>
            {task.current_task ? (
              <div className="mt-1 truncate text-xs leading-relaxed text-muted-foreground">{task.current_task}</div>
            ) : null}
            {active ? (
              <Progress value={Math.max(0, Math.min(100, (task.progress ?? 0) * 100))} className="mt-2" />
            ) : null}
            {task.error ? <div className="mt-1 text-xs leading-relaxed text-red-400">{task.error}</div> : null}
            {url || archived ? (
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <ExternalLink className="size-3.5" />
                    {t("tools.common.openResult")}
                  </a>
                ) : null}
                {archived ? (
                  <span className="text-xs leading-relaxed text-emerald-400/90">
                    {t("tools.common.savedToLibrary")}
                  </span>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
