// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 待办计划面板：AI 在对话中自动把制作需求拆解成任务写进计划
//（agent_update_plan 工具），用户也可以手动添加 / 编辑 / 勾选 / 删除；
// 清单每超过 30 项，后端自动清理已完成的任务（从最早完成的开始，未完成的保留）。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Check, Loader2, Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAddAgentPlanItem,
  useAgentPlan,
  useDeleteAgentPlanItem,
  useUpdateAgentPlanItem,
  type AgentPlanItem,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { agentErrorMessage } from "../core";

export function PlanPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const plan = useAgentPlan(project);
  const addItem = useAddAgentPlanItem();
  const updateItem = useUpdateAgentPlanItem();
  const deleteItem = useDeleteAgentPlanItem();
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const items = plan.data?.data.items ?? [];
  const pending = items.filter((item) => !item.done).length;

  const handleAdd = async () => {
    const title = draft.trim();
    if (!title || addItem.isPending) return;
    try {
      await addItem.mutateAsync({ project, title });
      setDraft("");
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
          <span className="text-xs text-muted-foreground">
            {t("agent.plan.count", { total: items.length, pending })}
          </span>
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
        <span
          className={cn(
            "min-w-0 flex-1 break-words text-xs leading-5",
            item.done ? "text-muted-foreground line-through" : "text-foreground",
          )}
        >
          {item.title}
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