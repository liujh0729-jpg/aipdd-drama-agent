// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「Agent」栏目主页：三栏创作代理（会话与资产树 / 聊天 / 待办计划与技能、记忆）。
//
// 低耦合设计：所有模型调用走后端 /api/v1/agent/*（公共网关凭据在服务端解析），
// 与 DramaClaw 主线的数据、任务系统完全隔离；右侧面板管理待办计划、技能与记忆，
// 产出的媒体支持右键「存入资产库」（见 MediaCard / SaveToLibraryDialog）。
// 左栏在「会话」与「资产」间切换：资产树把本项目资产库渲染成文件树，
// 素材可拖拽/点击进入输入区作为参考素材（见 AssetTreePanel 与 Composer）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Bot, Plug } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SUBTLE_HEADER_ACTION_BUTTON_CLASS } from "@/components/ui/header-action-styles";
import { taskResponseToastMessage } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import {
  streamAgentTurn,
  useAgentConversations,
  useAgentMessages,
  useAgentStatus,
  useCancelAgentTurn,
  useCreateAgentConversation,
  useDeleteAgentConversation,
  type AgentMediaItem,
  type AgentMessage,
  type AgentToolEvent,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { AgentConnectorsDialog } from "./components/AgentConnectorsDialog";
import { AssetTreePanel } from "./components/AssetTreePanel";
import { ChatPanel } from "./components/ChatPanel";
import { type ComposerHandle, type ComposerSendPayload } from "./components/Composer";
import { ConversationList } from "./components/ConversationList";
import { MemoryPanel } from "./components/MemoryPanel";
import { PlanPanel } from "./components/PlanPanel";
import { SkillsPanel } from "./components/SkillsPanel";
import { AGENT_STREAM_MESSAGE_ID, agentErrorMessage } from "./core";

/** 流式回合的临时状态（done 后清空，落库消息由查询刷新接管）。 */
interface StreamTurnState {
  text: string;
  toolEvents: AgentToolEvent[];
  media: AgentMediaItem[];
  notices: string[];
}

export function AgentPage({ project }: { project: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [leftTab, setLeftTab] = useState<"conversations" | "assets">("conversations");
  const [rightTab, setRightTab] = useState<"plan" | "skills" | "memory">("skills");
  const [connectorsOpen, setConnectorsOpen] = useState(false);
  const [pendingContent, setPendingContent] = useState<string | null>(null);
  const [streamTurn, setStreamTurn] = useState<StreamTurnState | null>(null);
  const [streamError, setStreamError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  // 资产树「点击即引用」直达输入区参考 chips 的句柄（拖拽路径不经过它）。
  const composerRef = useRef<ComposerHandle>(null);

  const statusQuery = useAgentStatus();
  const conversationsQuery = useAgentConversations(project);
  const messagesQuery = useAgentMessages(activeId);
  const createConversation = useCreateAgentConversation();
  const deleteConversation = useDeleteAgentConversation();
  const cancelTurn = useCancelAgentTurn();

  const conversations = useMemo(
    () => conversationsQuery.data?.data ?? [],
    [conversationsQuery.data],
  );
  const streaming = streamTurn !== null;

  // 默认选中最近的会话；当前会话被删除时回落到第一个。
  useEffect(() => {
    if (activeId && conversations.some((item) => item.id === activeId)) return;
    setActiveId(conversations[0]?.id ?? null);
  }, [conversations, activeId]);

  // 打开页面时若没有任何会话，自动创建一条，让输入框立即可用；
  // 失败时复位标记并提示，保留手动「新建」兜底。
  const autoCreateRef = useRef(false);
  useEffect(() => {
    if (!conversationsQuery.isSuccess) return;
    if (conversations.length > 0) {
      autoCreateRef.current = false;
      return;
    }
    if (autoCreateRef.current) return;
    autoCreateRef.current = true;
    createConversation
      .mutateAsync({ project })
      .then((conversation) => setActiveId(conversation.id))
      .catch(async (error) => {
        autoCreateRef.current = false;
        toast.error(await agentErrorMessage(error));
      });
  }, [conversationsQuery.isSuccess, conversations.length, createConversation, project]);

  const handleNew = async () => {
    try {
      const conversation = await createConversation.mutateAsync({ project });
      setActiveId(conversation.id);
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  const handleDelete = (id: string) => {
    deleteConversation.mutate(id, {
      onError: (error) => {
        void agentErrorMessage(error).then((message) => toast.error(message));
      },
    });
  };

  const handleSend = useCallback(
    async (payload: ComposerSendPayload) => {
      if (!activeId || streaming) return;
      setStreamError("");
      setPendingContent(payload.content);
      setStreamTurn({ text: "", toolEvents: [], media: [], notices: [] });
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamAgentTurn(
          activeId,
          {
            content: payload.content,
            project,
            mode: payload.mode,
            image_size: payload.imageSize,
            video_resolution: payload.videoResolution,
            video_duration: payload.videoDuration,
            video_upscale_resolution: payload.videoHdResolution,
            references: payload.references.length > 0 ? payload.references : undefined,
          },
          {
            signal: controller.signal,
            onEvent: (event) => {
              if (event.type === "delta") {
                setStreamTurn((prev) =>
                  prev ? { ...prev, text: prev.text + (event.text ?? "") } : prev,
                );
              } else if (event.type === "notice") {
                setStreamTurn((prev) =>
                  prev ? { ...prev, notices: [...prev.notices, event.text ?? ""] } : prev,
                );
              } else if (event.type === "media" && event.media) {
                const media = event.media;
                setStreamTurn((prev) =>
                  prev ? { ...prev, media: [...prev.media, media] } : prev,
                );
              } else if (event.type === "tool_start") {
                const name = event.name ?? "";
                const args = event.arguments;
                setStreamTurn((prev) =>
                  prev
                    ? { ...prev, toolEvents: [...prev.toolEvents, { name, ok: true, arguments: args }] }
                    : prev,
                );
              } else if (event.type === "tool_end") {
                const name = event.name ?? "";
                const summary = event.summary ?? "";
                const ok = event.ok ?? true;
                setStreamTurn((prev) => {
                  if (!prev) return prev;
                  const events = [...prev.toolEvents];
                  // 从后往前找最近一个还没收尾的同名工具调用。
                  const offset = [...events]
                    .reverse()
                    .findIndex((item) => item.name === name && !item.summary);
                  if (offset >= 0) {
                    const index = events.length - 1 - offset;
                    events[index] = { ...events[index], ok, summary };
                  } else {
                    events.push({ name, ok, summary });
                  }
                  return { ...prev, toolEvents: events };
                });
              } else if (event.type === "error") {
                // 与排队 toast 同约定：带 message_code 时按词条本地化，否则回显中文兜底。
                const text =
                  typeof event.message === "string" ? event.message : "";
                setStreamError(
                  taskResponseToastMessage(
                    {
                      message: text,
                      message_code: event.message_code,
                      message_params: event.message_params,
                    },
                    t,
                  ) || t("agent.error.generic"),
                );
              }
              // done：不在这里处理，流结束后统一刷新落库消息。
            },
          },
        );
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setStreamError(await agentErrorMessage(error));
        }
      } finally {
        abortRef.current = null;
        setStreamTurn(null);
        // 等落库消息刷新完成再摘掉乐观气泡，避免消息闪断。
        await queryClient.invalidateQueries({ queryKey: queryKeys.agentMessages(activeId) });
        void queryClient.invalidateQueries({ queryKey: ["agent", "conversations"] });
        // AI 回合里可能用 agent_update_plan 改过计划：流结束后刷新面板进度。
        void queryClient.invalidateQueries({ queryKey: queryKeys.agentPlan(project) });
        setPendingContent(null);
      }
    },
    [activeId, streaming, project, queryClient, t],
  );

  const handleStop = useCallback(() => {
    if (!activeId) return;
    abortRef.current?.abort();
    cancelTurn.mutate(activeId);
  }, [activeId, cancelTurn]);

  // 渲染列表 = 已落库消息 + 乐观用户消息 + 流式助手消息。
  const renderedMessages = useMemo<AgentMessage[]>(() => {
    const stored = messagesQuery.data?.data ?? [];
    if (!pendingContent && !streamTurn) return stored;
    const now = new Date().toISOString();
    const pending: AgentMessage[] = [];
    if (pendingContent) {
      pending.push({
        id: "local-user",
        conversation_id: activeId ?? "",
        role: "user",
        content: pendingContent,
        tool_events: [],
        media: [],
        created_at: now,
      });
    }
    if (streamTurn) {
      pending.push({
        id: AGENT_STREAM_MESSAGE_ID,
        conversation_id: activeId ?? "",
        role: "assistant",
        content: streamTurn.text,
        tool_events: streamTurn.toolEvents,
        media: streamTurn.media,
        created_at: now,
      });
    }
    return [...stored, ...pending];
  }, [messagesQuery.data, pendingContent, streamTurn, activeId]);

  const summary = statusQuery.data?.data;

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] min-h-0 flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 flex-col gap-3 border-b border-border/30 bg-background px-9 py-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Bot className="size-[18px]" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate py-1 text-2xl font-semibold tracking-tight text-foreground">
              {t("agent.page.title")}
            </h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              {t("agent.page.subtitle")}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {summary ? (
            <span
              className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[10px]",
                summary.gateway_configured
                  ? "bg-emerald-500/15 text-emerald-400"
                  : "bg-red-500/15 text-red-400",
              )}
            >
              {summary.gateway_configured
                ? t("agent.status.gatewayOk")
                : t("agent.status.gatewayError")}
            </span>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={SUBTLE_HEADER_ACTION_BUTTON_CLASS}
            onClick={() => setConnectorsOpen(true)}
          >
            <Plug className="size-3.5" />
            {t("agent.connectors.title")}
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-60 shrink-0 flex-col border-r border-border/30 bg-background">
          <div className="border-b border-border/30 p-2">
            <div className="inline-flex h-8 w-full items-center rounded-full border border-border bg-background/40 p-1 text-xs">
              {(["conversations", "assets"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setLeftTab(tab)}
                  className={cn(
                    "inline-flex h-6 flex-1 items-center justify-center rounded-full px-2 font-normal transition-colors",
                    leftTab === tab
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`agent.leftTabs.${tab}`)}
                </button>
              ))}
            </div>
          </div>
          {leftTab === "conversations" ? (
            <ConversationList
              conversations={conversations}
              activeId={activeId}
              creating={createConversation.isPending}
              onSelect={setActiveId}
              onNew={() => void handleNew()}
              onDelete={handleDelete}
            />
          ) : (
            <AssetTreePanel
              project={project}
              onAddReference={(reference) => composerRef.current?.addReference(reference)}
            />
          )}
        </aside>

        <ChatPanel
          project={project}
          messages={renderedMessages}
          streaming={streaming}
          streamError={streamError}
          streamNotices={streamTurn?.notices ?? []}
          hasConversation={Boolean(activeId)}
          onSend={(payload) => void handleSend(payload)}
          onStop={handleStop}
          composerRef={composerRef}
        />

        <aside className="flex w-72 shrink-0 flex-col border-l border-border/30 bg-background">
          <div className="border-b border-border/30 p-2">
            <div className="inline-flex h-8 w-full items-center rounded-full border border-border bg-background/40 p-1 text-xs">
              {(["plan", "skills", "memory"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setRightTab(tab)}
                  className={cn(
                    "inline-flex h-6 flex-1 items-center justify-center rounded-full px-2 font-normal transition-colors",
                    rightTab === tab
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`agent.panel.${tab}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {rightTab === "plan" ? (
              <PlanPanel project={project} />
            ) : rightTab === "skills" ? (
              <SkillsPanel />
            ) : (
              <MemoryPanel />
            )}
          </div>
        </aside>
      </div>

      <AgentConnectorsDialog
        open={connectorsOpen}
        onClose={() => setConnectorsOpen(false)}
        project={project}
      />
    </div>
  );
}