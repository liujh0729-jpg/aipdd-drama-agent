// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 聊天面板：消息滚动区（自动贴底）+ 错误横幅 + 输入区。
import { useEffect, useRef, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Bot } from "lucide-react";

import type { AgentMessage } from "@/lib/queries/agent";

import { AGENT_STREAM_MESSAGE_ID } from "../core";
import { Composer, type ComposerHandle, type ComposerSendPayload } from "./Composer";
import { MessageBubble } from "./MessageBubble";

export function ChatPanel({
  project,
  messages,
  streaming,
  streamError,
  streamNotices,
  hasConversation,
  onSend,
  onStop,
  composerRef,
}: {
  project: string;
  messages: AgentMessage[];
  streaming: boolean;
  streamError: string;
  /** 流式过程中的提示信息（如 MCP 服务器连接失败）。 */
  streamNotices: string[];
  hasConversation: boolean;
  onSend: (payload: ComposerSendPayload) => void;
  onStop: () => void;
  /** 左栏资产树「点击即引用」的句柄入口（拖拽不走这里）。 */
  composerRef?: Ref<ComposerHandle>;
}) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息 / 流式增量时贴底滚动。
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, streaming]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Bot className="size-8 text-muted-foreground" />
            <div className="text-sm font-medium text-foreground">{t("agent.chat.emptyTitle")}</div>
            <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
              {t("agent.chat.emptyBody")}
            </p>
          </div>
        ) : (
          messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              project={project}
              pending={message.id === AGENT_STREAM_MESSAGE_ID}
            />
          ))
        )}
      </div>
      {streamNotices.length > 0 ? (
        <div className="mx-4 mb-2 space-y-1">
          {streamNotices.map((notice, index) => (
            <div
              key={`${notice}-${index}`}
              className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300"
            >
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
              <span className="min-w-0 break-words">{notice}</span>
            </div>
          ))}
        </div>
      ) : null}
      {streamError ? (
        <div className="mx-4 mb-2 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
          <span className="min-w-0 break-words">{streamError}</span>
        </div>
      ) : null}
      <Composer
        ref={composerRef}
        project={project}
        disabled={!hasConversation}
        streaming={streaming}
        onSend={onSend}
        onStop={onStop}
      />
    </div>
  );
}