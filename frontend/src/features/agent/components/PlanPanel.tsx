// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 待办计划面板：AI 在对话中自动把制作需求拆解成任务写进计划
//（agent_update_plan 工具），用户也可以手动添加 / 编辑 / 勾选 / 删除；
// 清单每超过 30 项，后端自动清理已完成的任务（从最早完成的开始，未完成的保留）。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Check, Loader2, Pencil, Play, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAddAgentPlanItem,
  useAgentPlan,
  useDeleteAgentPlanItem,
  useRunAgentPlan,
  useUpdateAgentPlanItem,
  type AgentPlanItem,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { agentErrorMessage } from "../core";

/** 服务端 plans.PLAN_STAGE_ORDER 的前端镜像；order 即先决顺序。 */
const STAGE_OPTIONS = [
  "story",
  "screenplay",
  "ingest",
  "characters",
  "episodes",
  "identities",
  "script",
  "scenes",
  "props",
  "sketches",
  "frames",
] as const;

const PER_EPISODE_STAGES = new Set(["identities", "script", "scenes", "props", "sketches", "frames"]);

const SELECT_CLASS =
  "h-7 w-full rounded-lg border border-white/10 bg-white/[0.025] px-2 text-xs text-foreground outline-none focus:border-primary/50";

export function PlanPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const plan = useAgentPlan(project);
  const addItem = useAddAgentPlanItem();
  const updateItem = useUpdateAgentPlanItem();
  const deleteItem = useDeleteAgentPlanItem();
  const runPlan = useRunAgentPlan();
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState("");
  const [episode, setEpisode] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const items = plan.data?.data.items ?? [];
  const pending = items.filter((item) => !item.done).length;
  const executablePending = items.filter((item) => !item.done && item.stage).length;

  const handleAdd = async () => {
    const title = draft.trim();
    if (!title || addItem.isPending) return;
    const parsedEpisode = Number.parseInt(episode, 10);
    try {
      await addItem.mutateAsync({
        project,
        title,
        ...(stage ? { stage } : {}),
        ...(stage && PER_EPISODE_STAGES.has(stage) && parsedEpisode > 0
          ? { episode: parsedEpisode }
          : {}),
      });
      setDraft("");
      setEpisode("");
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  const handleRun = async () => {
    if (runPlan.isPending || executablePending === 0) return;
    try {
      const result = await runPlan.mutateAsync({ project, mode: "auto", budget_s: 90 });
      if (result.status === "failed") {
        toast.error(t("agent.plan.runFailed", { message: result.message || "" }));
      } else if (result.status === "busy") {
        toast.info(t("agent.plan.runBusy"));
      } else if (result.status === "done") {
        toast.success(t("agent.plan.runDone"));
      } else {
        toast.success(t("agent.plan.runProgress", { count: result.advanced?.length ?? 0 }));
      }
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  const commitEdit = async () => {
    if (!editingId) return;
    const itemId = editingId;
    const title = editingTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await updateItem.mutateAsync({ project, itemId, title });
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <p className="text-xs leading-relaxed text-muted-foreground">{t("agent.plan.description")}</p>

      <div className="flex items-center gap-1.5">
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void handleAdd();
            }
          }}
          placeholder={t("agent.plan.placeholder")}
          className="h-7 text-xs"
        />
        <Button
          type="button"
          size="sm"
          disabled={!draft.trim() || addItem.isPending}
          onClick={() => void handleAdd()}
          className="h-7 shrink-0 gap-1 rounded-[7px] bg-primary px-2 text-[11px] font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
        >
          {addItem.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Plus className="size-3.5" />
          )}
          {t("agent.plan.add")}
        </Button>
      </div>

      <div className="flex items-center gap-1.5">
        <select
          aria-label={t("agent.plan.stageLabel")}
          title={t("agent.plan.stageHint")}
          value={stage}
          onChange={(event) => setStage(event.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">{t("agent.plan.manualStage")}</option>
          {STAGE_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {t(`agent.plan.stages.${option}`)}
            </option>
          ))}
        </select>
        {PER_EPISODE_STAGES.has(stage) ? (
          <Input
            type="number"
            min={1}
            value={episode}
            onChange={(event) => setEpisode(event.target.value)}
            placeholder={t("agent.plan.episodePlaceholder")}
            className="h-7 w-20 shrink-0 text-xs"
          />
        ) : null}
      </div>

      {plan.isLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-3">
          <p className="text-xs text-muted-foreground">{t("agent.plan.empty")}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("agent.plan.emptyHint")}
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              {t("agent.plan.count", { total: items.length, pending })}
            </span>
            <Button
              type="button"
              size="sm"
              disabled={executablePending === 0 || runPlan.isPending}
              title={
                executablePending === 0
                  ? t("agent.plan.runNeedsStage")
                  : t("agent.plan.runHint")
              }
              onClick={() => void handleRun()}
              className="h-6 shrink-0 gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 text-[10px] font-normal text-primary shadow-none hover:bg-primary/20 disabled:opacity-40"
            >
              {runPlan.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Play className="size-3" />
              )}
              {runPlan.isPending ? t("agent.plan.running") : t("agent.plan.run")}
              {executablePending > 0 ? ` · ${executablePending}` : ""}
            </Button>
          </div>
          <div className="space-y-1.5">
            {items.map((item) => (
              <PlanRow
                key={item.id}
                item={item}
                editing={editingId === item.id}
                editingTitle={editingTitle}
                onEditingTitleChange={setEditingTitle}
                onStartEdit={() => {
                  setEditingId(item.id);
                  setEditingTitle(item.title);
                }}
                onCommitEdit={() => void commitEdit()}
                onCancelEdit={() => setEditingId(null)}
                onToggle={() =>
                  updateItem.mutate({ project, itemId: item.id, done: !item.done })
                }
                onRemove={() => deleteItem.mutate({ project, itemId: item.id })}
              />
            ))}
          </div>
        </>
      )}

      <p className="text-[11px] leading-relaxed text-muted-foreground">{t("agent.plan.limitHint")}</p>
    </div>
  );
}

interface PlanRowProps {
  item: AgentPlanItem;
  editing: boolean;
  editingTitle: string;
  onEditingTitleChange: (value: string) => void;
  onStartEdit: () => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onToggle: () => void;
  onRemove: () => void;
}

function PlanRow({
  item,
  editing,
  editingTitle,
  onEditingTitleChange,
  onStartEdit,
  onCommitEdit,
  onCancelEdit,
  onToggle,
  onRemove,
}: PlanRowProps) {
  const { t } = useTranslation();
  return (
    <div className="flex items-start gap-1.5 rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-2.5 py-1.5">
      <button
        type="button"
        role="checkbox"
        aria-checked={item.done}
        title={item.done ? t("agent.plan.markPending") : t("agent.plan.markDone")}
        onClick={onToggle}
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border transition-colors",
          item.done
            ? "border-primary bg-primary text-primary-foreground"
            : "border-white/15 hover:border-primary",
        )}
      >
        {item.done ? <Check className="size-3" /> : null}
      </button>

      {editing ? (
        <Input
          autoFocus
          value={editingTitle}
          onChange={(event) => onEditingTitleChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onCommitEdit();
            }
            if (event.key === "Escape") onCancelEdit();
          }}
          onBlur={onCommitEdit}
          className="h-6 flex-1 text-xs"
        />
      ) : (
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block break-words text-xs leading-5",
              item.done ? "text-muted-foreground line-through" : "text-foreground",
            )}
          >
            {item.title}
          </span>
          {item.stage || item.episode ? (
            <span className="mt-0.5 flex flex-wrap items-center gap-1">
              {item.stage ? (
                <span className="rounded bg-primary/10 px-1 py-px text-[10px] leading-4 text-primary/90">
                  {t(`agent.plan.stages.${item.stage}`, { defaultValue: item.stage })}
                </span>
              ) : null}
              {item.episode ? (
                <span className="rounded bg-white/[0.06] px-1 py-px text-[10px] leading-4 text-muted-foreground">
                  {t("agent.plan.episode", { n: item.episode })}
                </span>
              ) : null}
              {item.stage_inferred ? (
                <span className="text-[10px] leading-4 text-muted-foreground/70">
                  {t("agent.plan.inferred")}
                </span>
              ) : null}
            </span>
          ) : null}
          {item.last_error && !item.done ? (
            <span className="mt-0.5 block break-words text-[10px] leading-4 text-red-400/90">
              {item.last_error}
            </span>
          ) : null}
        </span>
      )}

      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          title={t("agent.plan.edit")}
          onClick={onStartEdit}
          className="rounded-sm p-0.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <Pencil className="size-3" />
        </button>
        <button
          type="button"
          title={t("agent.plan.delete")}
          onClick={onRemove}
          className="rounded-sm p-0.5 text-muted-foreground transition-colors hover:text-red-400"
        >
          <Trash2 className="size-3" />
        </button>
      </div>
    </div>
  );
}