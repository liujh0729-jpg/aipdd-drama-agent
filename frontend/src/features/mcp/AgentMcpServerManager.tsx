// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent MCP 服务器管理（自 AgentMcpSection 抽取）。
//
// MCP 列表：每个服务一张卡，展开可看「概览」（描述 / 连接信息 / 变量与请求头键名）
// 与「工具」（内置 AIPDD Drama 读取工具目录；其余服务经「测试连接」拉取）。支持新增/
// 编辑/启停/删除/恢复默认；内置服务器不可删除、可禁用。添加时按接入类型区分输入：
// SSE 与流式传输 HTTP 填服务地址（+请求头），自定义代码填启动命令、参数与环境变量。
// 身份边界：新增/编辑/测试连接只对 owner 渲染（后端同为 owner 依赖）；member 保留
// 清单、启停与清理（删除 / 恢复默认）。
// 两种宿主：
// - MCP 栏目页：showHeading 缺省（true），自带区块标题与描述（AgentMcpSection 壳）；
// - Agent「连接器MCP」弹窗：showHeading=false，标题由对话框提供，只渲染操作行与列表。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Check,
  ChevronRight,
  Globe,
  Loader2,
  Pencil,
  Plug,
  Plus,
  Radio,
  RotateCcw,
  Terminal,
  Trash2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  useAgentMcpServers,
  useDeleteAgentMcpServer,
  useProbeAgentMcpServer,
  useResetAgentMcpServers,
  useSetAgentMcpServerEnabled,
  useUpsertAgentMcpServer,
  type AgentMcpProbeResult,
  type AgentMcpServer,
  type AgentMcpServerInput,
  type AgentMcpTransport,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";

import { agentErrorMessage } from "@/features/agent/core";

import { McpToolCatalog } from "./McpToolCatalog";
import { McpToolList } from "./McpToolList";

interface ServerFormState {
  id: string;
  name: string;
  description: string;
  transport: AgentMcpTransport;
  command: string;
  /** 每行一个参数。 */
  argsText: string;
  /** 每行一个 KEY=VALUE。 */
  envText: string;
  url: string;
  /** 每行一个 KEY=VALUE。 */
  headersText: string;
  enabled: boolean;
}

const EMPTY_FORM: ServerFormState = {
  id: "",
  name: "",
  description: "",
  transport: "stdio",
  command: "",
  argsText: "",
  envText: "",
  url: "",
  headersText: "",
  enabled: true,
};

/** 接入类型选项（顺序与产品口径一致：SSE → 流式传输 HTTP → 自定义代码）。 */
const TYPE_OPTIONS: ReadonlyArray<{
  value: AgentMcpTransport;
  icon: LucideIcon;
  labelKey: string;
  hintKey: string;
}> = [
  {
    value: "sse",
    icon: Radio,
    labelKey: "mcp.agentServers.type.sse",
    hintKey: "mcp.agentServers.type.sseHint",
  },
  {
    value: "http",
    icon: Globe,
    labelKey: "mcp.agentServers.type.http",
    hintKey: "mcp.agentServers.type.httpHint",
  },
  {
    value: "stdio",
    icon: Terminal,
    labelKey: "mcp.agentServers.type.custom",
    hintKey: "mcp.agentServers.type.customHint",
  },
];

/** 内置 AIPDD Drama 服务器 id：「工具」页只读目录只对它渲染（Chrome MCP 等
 * 其它内置服务器与自添加服务器一样走「测试连接」探测）。 */
const AIPDD_DRAMA_SERVER_ID = "aipdd-drama";

/** 列表徽标文案：内置显示「内置」，自添加服务器显示接入类型。 */
function badgeKey(server: AgentMcpServer): string {
  if (server.builtin) return "mcp.agentServers.builtin";
  if (server.transport === "sse") return "mcp.agentServers.type.sse";
  if (server.transport === "http") return "mcp.agentServers.type.http";
  return "mcp.agentServers.type.custom";
}

function parseKeyValueLines(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    if (!key) continue;
    result[key] = trimmed.slice(index + 1).trim();
  }
  return result;
}

function formatKeyValueLines(record: Record<string, string>): string {
  return Object.entries(record)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function toFormState(server: AgentMcpServer): ServerFormState {
  return {
    id: server.id,
    name: server.name,
    description: server.description,
    transport: server.transport,
    command: server.command,
    argsText: (server.args ?? []).join("\n"),
    envText: formatKeyValueLines(server.env ?? {}),
    url: server.url,
    headersText: formatKeyValueLines(server.headers ?? {}),
    enabled: server.enabled,
  };
}

export function AgentMcpServerManager({
  showHeading = true,
  className,
}: {
  /** 区块自带头部（标题 + 描述 + 操作按钮）；弹窗内嵌时设 false 只留操作行。 */
  showHeading?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  // 新增/编辑/测试连接在后端是 owner 能力（会注册或真实执行本机命令），
  // 入口不渲染给 member；清单、启停、清理（删除/恢复默认）继续可用。
  const role = useAuthStore((state) => state.role);
  const isOwner = role === "owner";
  const serversQuery = useAgentMcpServers();
  const upsert = useUpsertAgentMcpServer();
  const setEnabled = useSetAgentMcpServerEnabled();
  const remove = useDeleteAgentMcpServer();
  const probe = useProbeAgentMcpServer();
  const reset = useResetAgentMcpServers();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState<ServerFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<{ serverId: string; result: AgentMcpProbeResult } | null>(
    null,
  );
  // [aipdd fork delta] 展开态：每个服务可查看「概览」与「工具」两个分页。
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<"overview" | "tools">("overview");

  const servers = useMemo(() => serversQuery.data?.data ?? [], [serversQuery.data]);

  const toggleExpanded = (serverId: string) => {
    if (expandedId === serverId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(serverId);
    setDetailTab("overview");
  };

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError("");
    setDialogOpen(true);
  };

  const openEdit = (server: AgentMcpServer) => {
    setForm(toFormState(server));
    setFormError("");
    setDialogOpen(true);
  };

  const submit = async () => {
    const name = form.name.trim();
    if (!name) {
      setFormError(t("mcp.agentServers.errorName"));
      return;
    }
    if (form.transport === "stdio" && !form.command.trim()) {
      setFormError(t("mcp.agentServers.errorCommand"));
      return;
    }
    if (form.transport !== "stdio" && !form.url.trim()) {
      setFormError(t("mcp.agentServers.errorUrl"));
      return;
    }
    const payload: AgentMcpServerInput = {
      id: form.id || undefined,
      name,
      description: form.description.trim(),
      transport: form.transport,
      command: form.transport === "stdio" ? form.command.trim() : "",
      args:
        form.transport === "stdio"
          ? form.argsText
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean)
          : [],
      env: form.transport === "stdio" ? parseKeyValueLines(form.envText) : {},
      url: form.transport === "stdio" ? "" : form.url.trim(),
      headers: form.transport === "stdio" ? {} : parseKeyValueLines(form.headersText),
      enabled: form.enabled,
    };
    try {
      await upsert.mutateAsync(payload);
      toast.success(t("mcp.agentServers.saved"));
      setDialogOpen(false);
    } catch (error) {
      setFormError(await agentErrorMessage(error));
    }
  };

  const handleProbe = async (server: AgentMcpServer) => {
    setProbeResult(null);
    try {
      const result = await probe.mutateAsync(server.id);
      setProbeResult({ serverId: server.id, result });
    } catch (error) {
      setProbeResult({
        serverId: server.id,
        result: { ok: false, error: await agentErrorMessage(error) },
      });
    }
    // 测试连接的结果落在「工具」分页：自动展开该服务并切过去。
    setExpandedId(server.id);
    setDetailTab("tools");
  };

  const handleReset = async () => {
    try {
      await reset.mutateAsync();
      toast.success(t("mcp.agentServers.resetDone"));
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  const actions = (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => void handleReset()}
        disabled={reset.isPending}
        className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-2 text-xs font-normal text-foreground shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
      >
        {reset.isPending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <RotateCcw className="size-3.5" />
        )}
        {t("mcp.agentServers.reset")}
      </Button>
      {isOwner ? (
        <Button type="button" size="sm" onClick={openCreate} className="h-7 gap-1.5 rounded-[7px] bg-primary px-2 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75">
          <Plus className="size-3.5" />
          {t("mcp.agentServers.add")}
        </Button>
      ) : null}
    </>
  );

  return (
    <section
      className={cn(
        "rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4",
        showHeading ? "mb-4" : "border-0 bg-transparent p-0",
        className,
      )}
    >
      {showHeading ? (
        <div className="mb-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              <Plug className="size-3.5 text-primary" />
              {t("mcp.agentServers.title")}
            </h2>
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              {t("mcp.agentServers.description")}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">{actions}</div>
        </div>
      ) : (
        <div className="mb-3 flex items-center justify-end gap-1.5">{actions}</div>
      )}

      {serversQuery.isLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : servers.length === 0 ? (
        <p className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-3 text-xs text-muted-foreground">
          {t("mcp.agentServers.empty")}
        </p>
      ) : (
        <div className="space-y-2">
          {servers.map((server) => {
            const open = expandedId === server.id;
            const probing = probe.isPending && probe.variables === server.id;
            const result = probeResult?.serverId === server.id ? probeResult.result : null;
            const unavailable = server.available === false;
            const unavailableReason = server.unavailable_reason || t("mcp.agentServers.unavailableHint");
            const args = server.args ?? [];
            const envKeys = Object.keys(server.env ?? {});
            const headerKeys = Object.keys(server.headers ?? {});
            // 「工具」分页要么是只读目录（内置 aipdd-drama），要么经「测试连接」
            // 拉取（owner 能力）；member 只见「概览」，分页控件整个不渲染。
            const canSeeTools = isOwner || (server.builtin && server.id === AIPDD_DRAMA_SERVER_ID);
            return (
              <div
                key={server.id}
                className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-2.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    aria-expanded={open}
                    title={t("mcp.agentServers.detailToggle")}
                    onClick={() => toggleExpanded(server.id)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    <ChevronRight
                      className={cn(
                        "size-3.5 shrink-0 text-muted-foreground transition-transform",
                        open && "rotate-90",
                      )}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 truncate text-xs font-medium text-foreground">
                      {server.name}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded-sm px-1.5 py-0.5 text-[10px]",
                        server.builtin ? "bg-primary/15 text-primary" : "bg-white/[0.05] text-muted-foreground",
                      )}
                    >
                      {t(badgeKey(server))}
                    </span>
                    {unavailable ? (
                      <span
                        title={unavailableReason}
                        className="shrink-0 rounded-sm border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300"
                      >
                        {t("mcp.agentServers.unavailable")}
                      </span>
                    ) : null}
                  </button>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={server.enabled}
                      title={
                        unavailable
                          ? unavailableReason
                          : server.enabled
                            ? t("mcp.agentServers.disable")
                            : t("mcp.agentServers.enable")
                      }
                      disabled={setEnabled.isPending || unavailable}
                      onClick={() => setEnabled.mutate({ serverId: server.id, enabled: !server.enabled })}
                      className={cn(
                        "relative h-4 w-7 rounded-full transition-colors",
                        server.enabled ? "bg-primary" : "bg-white/15",
                      )}
                    >
                      <span
                        className={cn(
                          "absolute top-0.5 size-3 rounded-full bg-white transition-all",
                          server.enabled ? "left-3.5" : "left-0.5",
                        )}
                      />
                    </button>
                    {isOwner ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={probing || unavailable}
                        title={unavailable ? unavailableReason : undefined}
                        onClick={() => void handleProbe(server)}
                        className="h-6 gap-1 px-1.5 text-[11px]"
                      >
                        {probing ? <Loader2 className="size-3 animate-spin" /> : null}
                        {t("mcp.agentServers.test")}
                      </Button>
                    ) : null}
                    {isOwner ? (
                      <button
                        type="button"
                        title={
                          server.builtin
                            ? t("mcp.agentServers.builtinLocked")
                            : t("mcp.agentServers.edit")
                        }
                        disabled={server.builtin}
                        onClick={() => openEdit(server)}
                        className={cn(
                          "rounded-sm p-1 transition-colors",
                          server.builtin
                            ? "cursor-not-allowed text-muted-foreground/40"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        <Pencil className="size-3.5" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      title={
                        server.builtin
                          ? t("mcp.agentServers.builtinLocked")
                          : pendingDelete === server.id
                            ? t("mcp.agentServers.deleteConfirm")
                            : t("mcp.agentServers.delete")
                      }
                      disabled={server.builtin}
                      onClick={() => {
                        if (pendingDelete === server.id) {
                          setPendingDelete(null);
                          remove.mutate(server.id, {
                            onError: (error) => {
                              void agentErrorMessage(error).then((message) =>
                                toast.error(message),
                              );
                            },
                          });
                        } else {
                          setPendingDelete(server.id);
                          window.setTimeout(() => setPendingDelete(null), 3000);
                        }
                      }}
                      className={cn(
                        "rounded-sm p-1 transition-colors",
                        server.builtin
                          ? "cursor-not-allowed text-muted-foreground/40"
                          : pendingDelete === server.id
                            ? "bg-red-500/15 text-red-400"
                            : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {pendingDelete === server.id ? (
                        <Check className="size-3.5" />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* [aipdd fork delta] 展开态：概览（描述 / 连接信息 / 键名）与工具（目录或探测结果）。 */}
                {open ? (
                  <div className="mt-2 border-t border-white/[0.06] pt-2">
                    {canSeeTools ? (
                      <div className="flex w-fit items-center gap-0.5 rounded-full border border-border bg-background/40 p-0.5">
                        {(["overview", "tools"] as const).map((item) => (
                          <button
                            key={item}
                            type="button"
                            aria-pressed={detailTab === item}
                            onClick={() => setDetailTab(item)}
                            className={cn(
                              "rounded-sm px-2 py-0.5 text-[11px] transition-colors",
                              detailTab === item
                                ? "bg-primary text-primary-foreground"
                                : "text-muted-foreground hover:text-foreground",
                            )}
                          >
                            {t(`mcp.agentServers.detail.${item}`)}
                          </button>
                        ))}
                      </div>
                    ) : null}

                    {detailTab === "overview" || !canSeeTools ? (
                      <div className="mt-2 space-y-1.5 text-[11px] leading-relaxed">
                        <p className="text-muted-foreground">
                          {server.description || t("mcp.agentServers.noDescription")}
                        </p>
                        {unavailable ? (
                          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-300">
                            {unavailableReason}
                          </p>
                        ) : null}
                        {server.transport === "stdio" ? (
                          <div className="flex items-start gap-1.5">
                            <span className="shrink-0 text-muted-foreground">
                              {t("mcp.agentServers.command")}
                            </span>
                            <code className="min-w-0 break-all font-mono text-foreground">
                              {[server.command, ...args].filter(Boolean).join(" ")}
                            </code>
                          </div>
                        ) : server.url ? (
                          <div className="flex items-start gap-1.5">
                            <span className="shrink-0 text-muted-foreground">
                              {t("mcp.agentServers.url")}
                            </span>
                            <code className="min-w-0 break-all font-mono text-foreground">
                              {server.url}
                            </code>
                          </div>
                        ) : null}
                        {envKeys.length > 0 ? (
                          <div className="flex items-start gap-1.5">
                            <span className="shrink-0 text-muted-foreground">
                              {t("mcp.agentServers.env")}
                            </span>
                            <code className="min-w-0 break-all font-mono text-foreground">
                              {envKeys.join(", ")}
                            </code>
                          </div>
                        ) : null}
                        {headerKeys.length > 0 ? (
                          <div className="flex items-start gap-1.5">
                            <span className="shrink-0 text-muted-foreground">
                              {t("mcp.agentServers.headers")}
                            </span>
                            <code className="min-w-0 break-all font-mono text-foreground">
                              {headerKeys.join(", ")}
                            </code>
                          </div>
                        ) : null}
                      </div>
                    ) : server.builtin && server.id === AIPDD_DRAMA_SERVER_ID ? (
                      <div className="mt-2">
                        <McpToolCatalog />
                      </div>
                    ) : result ? (
                      result.ok ? (
                        <div className="mt-2">
                          <p className="text-[11px] text-emerald-400">
                            {t("mcp.agentServers.testOk", { n: result.tools?.length ?? 0 })}
                          </p>
                          {(result.tools?.length ?? 0) > 0 ? (
                            <div className="mt-1.5">
                              <McpToolList
                                tools={(result.tools ?? []).map((tool) => ({
                                  name: tool.name,
                                  description: tool.description,
                                  inputSchema: tool.input_schema,
                                }))}
                              />
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <p className="mt-2 rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-[11px] text-red-300">
                          {t("mcp.agentServers.testFailed")}
                          {result.error ? `：${result.error}` : ""}
                        </p>
                      )
                    ) : (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <p className="text-[11px] text-muted-foreground">
                          {t("mcp.agentServers.toolsHint")}
                        </p>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={probing}
                          onClick={() => void handleProbe(server)}
                          className="h-6 gap-1 px-1.5 text-[11px]"
                        >
                          {probing ? <Loader2 className="size-3 animate-spin" /> : null}
                          {t("mcp.agentServers.fetchTools")}
                        </Button>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(next) => {
          if (!next) setDialogOpen(false);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {form.id ? t("mcp.agentServers.editTitle") : t("mcp.agentServers.addTitle")}
            </DialogTitle>
            <DialogDescription>{t("mcp.agentServers.formHint")}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            <div className="space-y-1.5">
              <Label htmlFor="mcp-server-name">{t("mcp.agentServers.name")}</Label>
              <Input
                id="mcp-server-name"
                value={form.name}
                maxLength={64}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mcp-server-description">{t("mcp.agentServers.descriptionLabel")}</Label>
              <Input
                id="mcp-server-description"
                value={form.description}
                maxLength={300}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, description: event.target.value }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("mcp.agentServers.transport")}</Label>
              <div className="grid grid-cols-3 gap-2">
                {TYPE_OPTIONS.map((option) => {
                  const Icon = option.icon;
                  const active = form.transport === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setForm((prev) => ({ ...prev, transport: option.value }))}
                      className={cn(
                        "flex flex-col items-start gap-1 rounded-lg border px-2.5 py-2 text-left transition-colors",
                        active
                          ? "border-primary/60 bg-primary/10"
                          : "border-white/[0.06] bg-white/[0.018] hover:border-primary/30",
                      )}
                    >
                      <Icon
                        className={cn("size-3.5", active ? "text-primary" : "text-muted-foreground")}
                        aria-hidden="true"
                      />
                      <span className="text-xs font-medium text-foreground">{t(option.labelKey)}</span>
                      <span className="text-[10px] leading-snug text-muted-foreground">
                        {t(option.hintKey)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
            {form.transport === "stdio" ? (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-server-command">{t("mcp.agentServers.command")}</Label>
                  <Input
                    id="mcp-server-command"
                    value={form.command}
                    placeholder="npx / uv / python"
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, command: event.target.value }))
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-server-args">{t("mcp.agentServers.args")}</Label>
                  <Textarea
                    id="mcp-server-args"
                    value={form.argsText}
                    rows={2}
                    placeholder={"-y\n@modelcontextprotocol/server-filesystem"}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, argsText: event.target.value }))
                    }
                    className="font-mono text-xs"
                  />
                  <p className="text-[11px] text-muted-foreground">{t("mcp.agentServers.argsHint")}</p>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-server-env">{t("mcp.agentServers.env")}</Label>
                  <Textarea
                    id="mcp-server-env"
                    value={form.envText}
                    rows={2}
                    placeholder="KEY=value"
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, envText: event.target.value }))
                    }
                    className="font-mono text-xs"
                  />
                  <p className="text-[11px] text-muted-foreground">{t("mcp.agentServers.envHint")}</p>
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-server-url">{t("mcp.agentServers.url")}</Label>
                  <Input
                    id="mcp-server-url"
                    value={form.url}
                    placeholder={
                      form.transport === "sse" ? "https://example.com/sse" : "https://example.com/mcp"
                    }
                    onChange={(event) => setForm((prev) => ({ ...prev, url: event.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-server-headers">{t("mcp.agentServers.headers")}</Label>
                  <Textarea
                    id="mcp-server-headers"
                    value={form.headersText}
                    rows={2}
                    placeholder="Authorization=Bearer xxx"
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, headersText: event.target.value }))
                    }
                    className="font-mono text-xs"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {t("mcp.agentServers.headersHint")}
                  </p>
                </div>
              </>
            )}
            {formError ? (
              <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-300">
                {formError}
              </p>
            ) : null}
          </div>
          <DialogFooter className="p-0 pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDialogOpen(false)}
              className="h-8 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-3 text-xs font-normal text-foreground shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
            >
              {t("mcp.agentServers.cancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={upsert.isPending}
              onClick={() => void submit()}
              className="h-8 gap-1.5 rounded-[7px] bg-primary px-3 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
            >
              {upsert.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {t("mcp.agentServers.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}