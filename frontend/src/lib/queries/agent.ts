// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 模块数据层：状态 / 设置 / 会话 / 回合（SSE）/ 记忆 / 技能 / MCP 服务器 / 入库。
// 全部走独立命名空间 /api/v1/agent/*，与主线的 query key 体系零耦合；
// MCP 服务器管理接口同时供 MCP 栏目页面使用（按产品要求 MCP 配置统一归 MCP 栏目）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, uploadApi } from "@/lib/api";
import { downloadBlobAsFile } from "@/lib/browserDownload";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

// ---------------------------------------------------------------------------
// 类型（与 src/novelvideo/agent 后端结构一一对应）
// ---------------------------------------------------------------------------

export interface AgentStatus {
  gateway_configured: boolean;
  gateway_error: string;
  default_text_model: string;
  default_image_model: string;
  default_video_model: string;
  text_model: string;
  image_model: string;
  video_model: string;
  skills_total: number;
  skills_enabled: number;
  mcp_servers_total: number;
  mcp_servers_enabled: number;
}

export interface AgentSettings {
  text_model: string;
  image_model: string;
  video_model: string;
  default_text_model: string;
  default_image_model: string;
  default_video_model: string;
}

export interface AgentConversation {
  id: string;
  title: string;
  project: string;
  created_at: string;
  updated_at: string;
}

/** 落库的工具调用记录（engine.py 的 tool_events 结构）。 */
export interface AgentToolEvent {
  name: string;
  ok: boolean;
  arguments?: Record<string, unknown>;
  summary?: string;
}

/** 回合产出的媒体（tools.py 的 media 结构）：kind + url + name + origin。 */
export interface AgentMediaItem {
  kind: "image" | "video" | "audio";
  url: string;
  name: string;
  local_path?: string;
  origin?: "project" | "agent";
  provider_url?: string;
}

export interface AgentMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  content: string;
  tool_events: AgentToolEvent[];
  media: AgentMediaItem[];
  created_at: string;
}

export interface AgentSkill {
  name: string;
  title: string;
  description: string;
  path?: string;
  enabled: boolean;
  body?: string;
  truncated?: boolean;
  /** 内置技能来自仓库 .hermes/skills，只读（可停用、不可删除）。 */
  source?: "builtin" | "user";
  files?: string[];
}

export type AgentMcpTransport = "stdio" | "http" | "sse";

export interface AgentMcpServer {
  id: string;
  name: string;
  description: string;
  transport: AgentMcpTransport;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  enabled: boolean;
  builtin: boolean;
  /** 内置服务器的外部命令是否就绪（自添加服务器缺省视为可用）。 */
  available?: boolean;
  /** 不可用原因（直接展示给用户，含安装指引）。 */
  unavailable_reason?: string;
  created_at: string;
  updated_at: string;
}

/** 新增/更新 MCP 服务器的提交体（id 省略时后端生成）。 */
export interface AgentMcpServerInput {
  id?: string;
  name: string;
  description?: string;
  transport: AgentMcpTransport;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface AgentMcpProbeTool {
  name: string;
  qualified_name: string;
  description: string;
  input_schema?: Record<string, unknown>;
}

export interface AgentMcpProbeResult {
  ok: boolean;
  error?: string;
  tools?: AgentMcpProbeTool[];
}

/** 内置 DramaClaw 工具目录条目（「工具」页：清单 + 字段 schema 与必填）。 */
export interface AgentMcpCatalogTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

/** 「存入资产库」的提交体（folder/category 均可省略：省略时存入默认位置）。 */
export interface AgentLibrarySaveInput {
  project: string;
  url: string;
  name: string;
  media: "image" | "video" | "audio";
  folder?: string;
  category?: string;
}

// ---------------------------------------------------------------------------
// 状态 / 设置
// ---------------------------------------------------------------------------

export function useAgentStatus() {
  return useQuery({
    queryKey: queryKeys.agentStatus(),
    queryFn: ({ signal }) =>
      api.get("api/v1/agent/status", { signal, retry: 0 }).json<OkResponse<AgentStatus>>(),
    staleTime: 15_000,
    retry: false,
  });
}

export function useAgentSettings() {
  return useQuery({
    queryKey: queryKeys.agentSettings(),
    queryFn: ({ signal }) =>
      api.get("api/v1/agent/settings", { signal, retry: 0 }).json<OkResponse<AgentSettings>>(),
    retry: false,
  });
}

export function useUpdateAgentSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { text_model?: string; image_model?: string; video_model?: string }) =>
      (
        await api
          .put("api/v1/agent/settings", { json: payload, retry: 0 })
          .json<OkResponse<AgentSettings>>()
      ).data,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agentSettings(), { ok: true, data });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

// ---------------------------------------------------------------------------
// 会话
// ---------------------------------------------------------------------------

export function useAgentConversations(project?: string) {
  return useQuery({
    queryKey: queryKeys.agentConversations(project),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/conversations", {
          signal,
          retry: 0,
          searchParams: project ? { project } : {},
        })
        .json<OkResponse<AgentConversation[]>>(),
    retry: false,
  });
}

export function useCreateAgentConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { title?: string; project?: string }) =>
      (
        await api
          .post("api/v1/agent/conversations", { json: payload, retry: 0 })
          .json<OkResponse<AgentConversation>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent", "conversations"] });
    },
  });
}

export function useUpdateAgentConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      conversationId,
      title,
    }: {
      conversationId: string;
      title: string;
    }) =>
      (
        await api
          .patch(`api/v1/agent/conversations/${encodeURIComponent(conversationId)}`, {
            json: { title },
            retry: 0,
          })
          .json<OkResponse<AgentConversation>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent", "conversations"] });
    },
  });
}

export function useDeleteAgentConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (conversationId: string) =>
      (
        await api
          .delete(`api/v1/agent/conversations/${encodeURIComponent(conversationId)}`, {
            retry: 0,
          })
          .json<OkResponse<{ deleted: boolean }>>()
      ).data,
    onSuccess: (_data, conversationId) => {
      queryClient.removeQueries({ queryKey: queryKeys.agentMessages(conversationId) });
      void queryClient.invalidateQueries({ queryKey: ["agent", "conversations"] });
    },
  });
}

// ---------------------------------------------------------------------------
// 消息
// ---------------------------------------------------------------------------

export function useAgentMessages(conversationId: string | null) {
  return useQuery({
    queryKey: queryKeys.agentMessages(conversationId ?? ""),
    enabled: Boolean(conversationId),
    queryFn: ({ signal }) =>
      api
        .get(`api/v1/agent/conversations/${encodeURIComponent(conversationId ?? "")}/messages`, {
          signal,
          retry: 0,
        })
        .json<OkResponse<AgentMessage[]>>(),
    retry: false,
  });
}

export function useClearAgentMessages() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (conversationId: string) =>
      (
        await api
          .delete(`api/v1/agent/conversations/${encodeURIComponent(conversationId)}/messages`, {
            retry: 0,
          })
          .json<OkResponse<{ removed: number }>>()
      ).data,
    onSuccess: (_data, conversationId) => {
      queryClient.setQueryData(queryKeys.agentMessages(conversationId), {
        ok: true,
        data: [],
      });
    },
  });
}

/** 中断当前回合（SSE 回合进行中时由「停止生成」按钮调用）。 */
export function useCancelAgentTurn() {
  return useMutation({
    mutationFn: async (conversationId: string) =>
      (
        await api
          .post(`api/v1/agent/conversations/${encodeURIComponent(conversationId)}/cancel`, {
            retry: 0,
          })
          .json<OkResponse<{ cancelled: boolean }>>()
      ).data,
  });
}

// ---------------------------------------------------------------------------
// 记忆
// ---------------------------------------------------------------------------

export function useAgentMemory() {
  return useQuery({
    queryKey: queryKeys.agentMemory(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/memory", { signal, retry: 0 })
        .json<OkResponse<{ content: string }>>(),
    retry: false,
  });
}

export function useUpdateAgentMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (content: string) =>
      (
        await api
          .put("api/v1/agent/memory", { json: { content }, retry: 0 })
          .json<OkResponse<{ content: string }>>()
      ).data,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agentMemory(), { ok: true, data });
    },
  });
}

// ---------------------------------------------------------------------------
// 待办计划（项目级；AI 用 agent_update_plan 工具维护，面板提供手动增删改）
// ---------------------------------------------------------------------------

export interface AgentPlanItem {
  id: string;
  title: string;
  done: boolean;
  source: "agent" | "user" | string;
  created_at: string;
  updated_at: string;
  done_at: string | null;
  /** 流水线里程碑；为空表示手工待办，执行器不会碰。 */
  stage?: string | null;
  /** 逐集阶段的集号；空表示全部集。 */
  episode?: number | null;
  stage_inferred?: boolean;
  last_error?: string;
  last_run_at?: string | null;
}

export interface AgentPlanPayload {
  items: AgentPlanItem[];
  max_items: number;
}

/** /agent/plan/run 的摘要：status=done/running/failed/busy/... */
export interface AgentPlanRunSummary {
  status: string;
  message?: string;
  advanced?: string[];
  completed?: string[];
  inferred?: string[];
  remaining?: number;
  next_action?: string;
}

export function useAgentPlan(project: string) {
  return useQuery({
    queryKey: queryKeys.agentPlan(project),
    enabled: Boolean(project),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/plan", { signal, retry: 0, searchParams: { project } })
        .json<OkResponse<AgentPlanPayload>>(),
    retry: false,
  });
}

export function useAddAgentPlanItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      project: string;
      title: string;
      stage?: string;
      episode?: number;
    }) =>
      (
        await api
          .post("api/v1/agent/plan/items", { json: payload, retry: 0 })
          .json<OkResponse<AgentPlanPayload & { item: AgentPlanItem }>>()
      ).data,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPlan(variables.project) });
    },
  });
}

export function useUpdateAgentPlanItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      project: string;
      itemId: string;
      title?: string;
      done?: boolean;
      stage?: string | null;
      episode?: number | null;
    }) =>
      (
        await api
          .patch(`api/v1/agent/plan/items/${encodeURIComponent(payload.itemId)}`, {
            json: {
              project: payload.project,
              ...("title" in payload ? { title: payload.title } : {}),
              ...("done" in payload ? { done: payload.done } : {}),
              ...("stage" in payload ? { stage: payload.stage } : {}),
              ...("episode" in payload ? { episode: payload.episode } : {}),
            },
            retry: 0,
          })
          .json<OkResponse<AgentPlanPayload & { item: AgentPlanItem }>>()
      ).data,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPlan(variables.project) });
    },
  });
}

export function useDeleteAgentPlanItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { project: string; itemId: string }) =>
      (
        await api
          .delete(`api/v1/agent/plan/items/${encodeURIComponent(payload.itemId)}`, {
            searchParams: { project: payload.project },
            retry: 0,
          })
          .json<OkResponse<AgentPlanPayload>>()
      ).data,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPlan(variables.project) });
    },
  });
}

/** 按计划执行：服务端 plan_runner 委托权威流水线计划器，推进到镜头首帧。 */
export function useRunAgentPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      project: string;
      mode?: "auto" | "manual" | "checkpoint";
      approve?: boolean;
      budget_s?: number;
    }) =>
      (
        await api
          .post("api/v1/agent/plan/run", {
            json: { mode: "auto", ...payload },
            retry: 0,
          })
          .json<OkResponse<AgentPlanRunSummary>>()
      ).data,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPlan(variables.project) });
    },
  });
}

// ---------------------------------------------------------------------------
// 技能
// ---------------------------------------------------------------------------

export function useAgentSkills() {
  return useQuery({
    queryKey: queryKeys.agentSkills(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/skills", { signal, retry: 0 })
        .json<OkResponse<AgentSkill[]>>(),
    retry: false,
  });
}

/** 技能详情（含 SKILL.md 正文）：name 为空时不发请求。 */
export function useAgentSkillDetail(name: string | null) {
  return useQuery({
    queryKey: [...queryKeys.agentSkills(), "detail", name],
    queryFn: ({ signal }) =>
      api
        .get(`api/v1/agent/skills/${encodeURIComponent(name ?? "")}`, { signal, retry: 0 })
        .json<OkResponse<AgentSkill>>(),
    enabled: Boolean(name),
    retry: false,
  });
}

/** 下载技能 zip 包（后端 Content-Disposition 带文件名，结构与安装器一致）。 */
export async function downloadAgentSkill(name: string): Promise<void> {
  const response = await api.get(
    `api/v1/agent/skills/${encodeURIComponent(name)}/download`,
    { timeout: 120_000 },
  );
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/i.exec(disposition);
  downloadBlobAsFile(blob, match?.[1] ?? `${name}.zip`);
}

export function useInstallAgentSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (
        await uploadApi
          .post("api/v1/agent/skills/install", { body: form })
          .json<OkResponse<{ skill: AgentSkill; updated: boolean }>>()
      ).data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentSkills() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

export function useSetAgentSkillEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, enabled }: { name: string; enabled: boolean }) =>
      (
        await api
          .patch(`api/v1/agent/skills/${encodeURIComponent(name)}`, {
            json: { enabled },
            retry: 0,
          })
          .json<OkResponse<AgentSkill>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentSkills() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

export function useDeleteAgentSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) =>
      (
        await api
          .delete(`api/v1/agent/skills/${encodeURIComponent(name)}`, { retry: 0 })
          .json<OkResponse<{ deleted: boolean }>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentSkills() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

// ---------------------------------------------------------------------------
// MCP 服务器（管理接口同时服务 MCP 栏目页面）
// ---------------------------------------------------------------------------

export function useAgentMcpServers() {
  return useQuery({
    queryKey: queryKeys.agentMcpServers(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/mcp/servers", { signal, retry: 0 })
        .json<OkResponse<AgentMcpServer[]>>(),
    retry: false,
  });
}

export function useUpsertAgentMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AgentMcpServerInput) =>
      (
        await api
          .post("api/v1/agent/mcp/servers", { json: payload, retry: 0 })
          .json<OkResponse<AgentMcpServer>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentMcpServers() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

export function useSetAgentMcpServerEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ serverId, enabled }: { serverId: string; enabled: boolean }) =>
      (
        await api
          .patch(`api/v1/agent/mcp/servers/${encodeURIComponent(serverId)}`, {
            json: { enabled },
            retry: 0,
          })
          .json<OkResponse<AgentMcpServer>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentMcpServers() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

export function useDeleteAgentMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (serverId: string) =>
      (
        await api
          .delete(`api/v1/agent/mcp/servers/${encodeURIComponent(serverId)}`, { retry: 0 })
          .json<OkResponse<{ deleted: boolean }>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentMcpServers() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

export function useProbeAgentMcpServer() {
  return useMutation({
    mutationFn: async (serverId: string) =>
      (
        await api
          .post(`api/v1/agent/mcp/servers/${encodeURIComponent(serverId)}/probe`, { retry: 0 })
          .json<OkResponse<AgentMcpProbeResult>>()
      ).data,
  });
}

export function useResetAgentMcpServers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (
        await api
          .post("api/v1/agent/mcp/servers/reset", { retry: 0 })
          .json<OkResponse<AgentMcpServer[]>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentMcpServers() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentStatus() });
    },
  });
}

/** 内置 DramaClaw 工具目录（不启动子进程；「工具」页数据源）。 */
export function useAgentBuiltinMcpTools() {
  return useQuery({
    queryKey: queryKeys.agentBuiltinMcpTools(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/mcp/builtin-tools", { signal, retry: 0 })
        .json<OkResponse<{ server_id: string; tools: AgentMcpCatalogTool[] }>>(),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// 本地 Agent 工具接入（Workbuddy / Claude Code / Cursor / Codex）
// ---------------------------------------------------------------------------

export interface AgentLocalTool {
  id: string;
  name: string;
  /** 本机安装证据（宿主 home / 应用目录检测）。 */
  installed: boolean;
  /** 证据级别：app=应用本体命中（真安装）；config=仅配置痕迹（可能为卸载残留）；none=无。 */
  evidence: "app" | "config" | "none";
  /** 目标工具配置里已存在 dramaclaw 条目。 */
  connected: boolean;
  /** 宿主视角的配置路径（~/.workbuddy/mcp.json 等）。 */
  configPath: string;
}

/** 后端 MCP 运行形态（手动配置片段按它生成启动命令）。 */
export interface AgentMcpRuntime {
  mode: "docker" | "native";
  /** 本机 Python 可执行文件（native 形态直接用它启动 MCP server）。 */
  python: string;
  pythonPath: string;
  /** docker exec 目标容器名（docker 形态）。 */
  containerName: string;
}

export interface AgentLocalToolsPayload {
  tools: AgentLocalTool[];
  /** 宿主 home 是否可用（未挂载/不可读时检测不可用）。 */
  hostHomeAvailable: boolean;
  hostHomePath: string;
  mcpRuntime: AgentMcpRuntime;
}

export interface AgentLocalToolsActionResult {
  tools: AgentLocalTool[];
  results: { id: string; ok: boolean; error?: string }[];
}

/** 本机 Agent 工具安装 / 连接状态（MCP 栏目「本地 Agent 工具」区块）。 */
export function useAgentLocalTools() {
  return useQuery({
    queryKey: queryKeys.agentLocalTools(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/agent/local-tools", { signal, retry: 0 })
        .json<OkResponse<AgentLocalToolsPayload>>(),
    retry: false,
  });
}

function useLocalToolsAction(path: "connect" | "disconnect") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { tools: string[]; project?: string }) =>
      (
        await api
          .post(`api/v1/agent/local-tools/${path}`, { json: payload, retry: 0 })
          .json<OkResponse<AgentLocalToolsActionResult>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentLocalTools() });
    },
  });
}

/** 把内置 DramaClaw MCP 写进勾选的本地工具配置（每工具一把统一密钥）。 */
export function useConnectAgentLocalTools() {
  return useLocalToolsAction("connect");
}

/** 移除勾选工具配置里的 dramaclaw 条目并吊销密钥。 */
export function useDisconnectAgentLocalTools() {
  return useLocalToolsAction("disconnect");
}

// ---------------------------------------------------------------------------
// 存入资产库
// ---------------------------------------------------------------------------

export function useSaveAgentMediaToLibrary() {
  return useMutation({
    mutationFn: async (payload: AgentLibrarySaveInput) =>
      (
        await api
          .post("api/v1/agent/library/save", { json: payload, retry: 0, timeout: 120_000 })
          .json<OkResponse<Record<string, unknown>>>()
      ).data,
  });
}

// ---------------------------------------------------------------------------
// 回合事件流（POST + ReadableStream 解析 SSE）
// ---------------------------------------------------------------------------

export type AgentTurnEventType =
  | "delta"
  | "tool_start"
  | "tool_end"
  | "media"
  | "notice"
  | "done"
  | "error";

/**
 * 回合事件（与 routes.py 的 SSE 协议一一对应）：
 * - delta      {"text"}                    正文增量
 * - tool_start {"name","server","arguments"} 工具开始
 * - tool_end   {"name","server","ok","summary"} 工具结束
 * - media      {"media":{...}}             产出媒体
 * - notice     {"text"}                    提示信息
 * - done       {"message":{...}}           回合完成（落库消息）
 * - error      {"message","message_code"?,"message_params"?} 失败（后两者见 i18n_message 约定）
 */
export interface AgentTurnEvent {
  type: AgentTurnEventType;
  text?: string;
  name?: string;
  server?: string;
  arguments?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  media?: AgentMediaItem;
  message?: AgentMessage | string;
  /** 失败事件的结构化本地化字段：带 code 时按词条翻译，缺失则回显 message。 */
  message_code?: string;
  message_params?: Record<string, unknown>;
}

/** 对话类型：text（默认，LLM 对话）/ image / video——决定本回合生成工具优先使用的模型。 */
export type AgentDialogMode = "text" | "image" | "video";

/** 参考素材媒介（与资产库条目一致）。 */
export type AgentTurnReferenceMedia = "image" | "video" | "audio";

/** 回合参考素材（用户从资产库选择的条目，随消息附带一次）。 */
export interface AgentTurnReference {
  name: string;
  url: string;
  media: AgentTurnReferenceMedia;
}

export interface AgentTurnRequestBody {
  content: string;
  project?: string;
  model?: string;
  image_model?: string;
  video_model?: string;
  mode?: AgentDialogMode;
  /** [aipdd fork delta] 输入区显式设定的回合媒体参数（未设定则不携带）。 */
  image_size?: string;
  video_resolution?: string;
  video_duration?: number;
  /** [aipdd fork delta] 视频超清目标档位（req22：1080p/2k/4k；未开启不携带）。 */
  video_upscale_resolution?: string;
  references?: AgentTurnReference[];
}

/** 单帧解析：`: ping` 心跳注释行 / 空行直接跳过，坏帧忽略而不是终止整个回合。 */
function emitAgentFrame(frame: string, onEvent: (event: AgentTurnEvent) => void): void {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue;
    const raw = line.slice(5).trim();
    if (!raw) continue;
    try {
      onEvent(JSON.parse(raw) as AgentTurnEvent);
    } catch {
      // 忽略坏帧：SSE 流本身会以 done/error 收尾，不会卡住 UI。
    }
  }
}

/**
 * 发送消息并消费 SSE 回合事件流。
 *
 * 会话回合是 POST + body，浏览器原生 EventSource 不支持，所以这里用
 * fetch + ReadableStream 手工逐帧解析。调用方通过 onEvent 收到增量事件；
 * 函数返回即流结束（done/error 之后）。终止请求请传 AbortSignal。
 */
export async function streamAgentTurn(
  conversationId: string,
  body: AgentTurnRequestBody,
  handlers: { onEvent: (event: AgentTurnEvent) => void; signal?: AbortSignal },
): Promise<void> {
  const response = await fetch(
    `/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: handlers.signal,
    },
  );
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown; error?: unknown };
      const raw = payload?.detail ?? payload?.error;
      if (typeof raw === "string" && raw) detail = raw;
    } catch {
      // 非 JSON 错误体：保留状态码。
    }
    handlers.onEvent({ type: "error", message: detail });
    return;
  }
  if (!response.body) {
    handlers.onEvent({ type: "error", message: `HTTP ${response.status}` });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        emitAgentFrame(frame, handlers.onEvent);
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) emitAgentFrame(buffer, handlers.onEvent);
  } finally {
    reader.releaseLock();
  }
}