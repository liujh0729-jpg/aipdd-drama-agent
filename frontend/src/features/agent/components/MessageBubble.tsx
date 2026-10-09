// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 消息气泡：正文 + 工具调用记录 + 产出媒体卡片（右键可存入资产库）。
import { useTranslation } from "react-i18next";
import { Check, Loader2, XCircle } from "lucide-react";

import type { AgentMessage, AgentToolEvent } from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { MediaCard } from "./MediaCard";

/** 工具调用记录行：进行中（无 summary）显示转圈，结束后按 ok 显示对勾/叉。 */
function ToolEventList({ events }: { events: AgentToolEvent[] }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1 rounded-lg border border-white/[0.06] bg-white/[0.018] px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {t("agent.chat.toolCalls")}
      </div>
      {events.map((event, index) => {
        const running = !event.summary;
        return (
          <div
            key={`${event.name}-${index}`}
            className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
          >
            {running ? (
              <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
            ) : event.ok ? (
              <Check className="size-3.5 shrink-0 text-emerald-400" />
            ) : (
              <XCircle className="size-3.5 shrink-0 text-red-400" />
            )}
            <code className="shrink-0 font-mono text-[11px] text-foreground">{event.name}</code>
            {event.summary ? (
              <span className="min-w-0 truncate" title={event.summary}>
                {event.summary}
              </span>
            ) : (
              <span>{t("agent.chat.toolRunning")}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function MessageBubble({
  message,
  project,
  pending,
}: {
  message: AgentMessage;
  project: string;
  /** 流式进行中的临时消息（未落库）：显示光标动画。 */
  pending?: boolean;
}) {
  const isUser = message.role === "user";
  const events = message.tool_events ?? [];
  const media = message.media ?? [];
  const hasBody = Boolean(message.content);

  // 工具事件 + 媒体都为空且正文为空：不渲染空气泡（例如纯工具报错的空回合）。
  if (!hasBody && events.length === 0 && media.length === 0) return null;

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] min-w-0 space-y-2 rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isUser
            ? "rounded-br-md bg-primary/15 text-foreground"
            : "rounded-bl-md border border-white/[0.06] bg-white/[0.025] text-foreground",
        )}
      >
        {hasBody ? (
          <div className="whitespace-pre-wrap break-words">
            {message.content}
            {pending ? (
              <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-primary align-text-bottom" />
            ) : null}
          </div>
        ) : null}
        {!isUser && events.length > 0 ? <ToolEventList events={events} /> : null}
        {media.length > 0 ? (
          <div className="space-y-2">
            {media.map((item, index) => (
              <MediaCard key={`${item.url}-${index}`} item={item} project={project} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}