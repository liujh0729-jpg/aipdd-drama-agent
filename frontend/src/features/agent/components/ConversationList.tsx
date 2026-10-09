// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 会话列表：新建 / 选择 / 删除（二次点击确认，避免误删）。
// 外层容器（宽度/边框/左栏 Tab）由 AgentPage 统一提供，这里只负责会话内容。
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AgentConversation } from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { formatAgentTime } from "../core";

export function ConversationList({
  conversations,
  activeId,
  creating,
  onSelect,
  onNew,
  onDelete,
}: {
  conversations: AgentConversation[];
  activeId: string | null;
  creating: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  // 二次点击确认的 3 秒窗口。
  useEffect(() => {
    if (!pendingDelete) return;
    const timer = window.setTimeout(() => setPendingDelete(null), 3000);
    return () => window.clearTimeout(timer);
  }, [pendingDelete]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-border/30 px-3 py-2">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={creating}
          onClick={onNew}
          className="h-7 w-full justify-start gap-1.5 rounded-[7px] px-2 text-xs font-normal text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          {creating ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
          {t("agent.conversations.new")}
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {conversations.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t("agent.conversations.empty")}</p>
        ) : (
          conversations.map((conversation) => {
            const active = conversation.id === activeId;
            return (
              <div
                key={conversation.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(conversation.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(conversation.id);
                  }
                }}
                className={cn(
                  "group flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 transition-colors",
                  active ? "bg-primary/15" : "hover:bg-white/[0.05]",
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-foreground">
                    {conversation.title || t("agent.conversations.untitled")}
                  </div>
                  <div className="mt-0.5 text-[10px] text-muted-foreground">
                    {formatAgentTime(conversation.updated_at)}
                  </div>
                </div>
                <button
                  type="button"
                  title={
                    pendingDelete === conversation.id
                      ? t("agent.conversations.deleteConfirm")
                      : t("agent.conversations.delete")
                  }
                  onClick={(event) => {
                    event.stopPropagation();
                    if (pendingDelete === conversation.id) {
                      setPendingDelete(null);
                      onDelete(conversation.id);
                    } else {
                      setPendingDelete(conversation.id);
                    }
                  }}
                  className={cn(
                    "rounded-sm p-1 transition-all",
                    pendingDelete === conversation.id
                      ? "bg-red-500/15 text-red-400"
                      : "text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100",
                  )}
                >
                  {pendingDelete === conversation.id ? (
                    <Check className="size-3.5" />
                  ) : (
                    <Trash2 className="size-3.5" />
                  )}
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}