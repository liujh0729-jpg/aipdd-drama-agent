// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 项目「MCP」栏目页：说明当前项目作为 MCP server（aipdd-drama）
// 如何被 AI 客户端接入，让用户用 AI 直接读取并处理本项目。页面分两个 Tab：
// 「接入指南」＝接入配置与调用说明（含画布基础用法、一键把内置服务器接入 Agent）；
// 「MCP 列表」＝Agent 可调用的 MCP 服务器列表（内置 aipdd-drama 不可删除 + 用户自添加，
// 每个服务器可展开查看概览与工具；自装服务器的工具经「测试连接」探测）。
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowRight,
  BookOpen,
  Bot,
  Check,
  Copy,
  Loader2,
  Plug,
  Plus,
  Server,
} from "lucide-react";

import { SlidingTabs } from "@/components/nav/sliding-tabs";
import { Button } from "@/components/ui/button";
import {
  useAgentLocalTools,
  useAgentMcpServers,
  useSetAgentMcpServerEnabled,
} from "@/lib/queries/agent";
import { useAllProjectSummaries } from "@/lib/queries/projects";
import { cn } from "@/lib/utils";

import { agentErrorMessage } from "@/features/agent/core";

import { AgentMcpSection } from "./AgentMcpSection";
import { LocalAgentTools } from "./LocalAgentTools";

/** 单段可复制内容（配置 / 命令 / 环境变量）。 */
function CopyBlock({
  code,
  label,
  className,
}: {
  code: string;
  label?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      toast.success(t("mcp.common.copied"));
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error(t("mcp.common.copyFailed"));
    }
  }, [code, t]);

  return (
    <div className={cn("relative overflow-hidden rounded-[10px] border border-white/[0.06] bg-black/20", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-3 py-1.5">
        {label ? <span className="text-xs text-muted-foreground">{label}</span> : <span />}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onCopy}
          className="h-7 gap-1.5 rounded-[7px] px-2 text-[11px] font-normal text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          {copied ? <Check className="size-3.5 text-primary" /> : <Copy className="size-3.5" />}
          {copied ? t("mcp.common.copied") : t("mcp.common.copy")}
        </Button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 text-[12.5px] leading-relaxed text-foreground">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function StepBadge({ n }: { n: number }) {
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
      {n}
    </span>
  );
}

// [aipdd fork delta] 「添加到 Agent」卡片：一键启用官方内置 AIPDD Drama MCP 服务器，
// 启用后 Agent 回合即可调用下列全部工具；停用/增删服务器在「工具」页管理。
function AgentAddCard() {
  const { t } = useTranslation();
  const serversQuery = useAgentMcpServers();
  const setEnabled = useSetAgentMcpServerEnabled();
  const builtin = useMemo(
    // [aipdd fork delta] 该卡片只服务内置 AIPDD Drama 工具集（Chrome MCP 等
    // 其它内置服务器在「工具」页列表里自行启停）。
    () => (serversQuery.data?.data ?? []).find((server) => server.id === "aipdd-drama"),
    [serversQuery.data],
  );
  const enabled = builtin?.enabled ?? false;

  const enable = async () => {
    if (!builtin) return;
    try {
      await setEnabled.mutateAsync({ serverId: builtin.id, enabled: true });
      toast.success(t("mcp.agentAdd.enableDone"));
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  return (
    <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            <Bot className="size-3.5 text-primary" />
            {t("mcp.agentAdd.title")}
          </h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t("mcp.agentAdd.body")}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground/70">
            {t("mcp.agentAdd.hint")}
          </p>
        </div>
        {serversQuery.isLoading ? (
          <Loader2 className="mt-1 size-4 shrink-0 animate-spin text-muted-foreground" />
        ) : enabled ? (
          <span className="flex shrink-0 items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-400">
            <Check className="size-3.5" />
            {t("mcp.agentAdd.enabled")}
          </span>
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={!builtin || setEnabled.isPending}
            onClick={() => void enable()}
            className="h-7 shrink-0 gap-1.5 rounded-[7px] bg-primary px-2 text-[11px] font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
          >
            {setEnabled.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plus className="size-3.5" />
            )}
            {t("mcp.agentAdd.enable")}
          </Button>
        )}
      </div>
    </section>
  );
}

/** 容器内接入：docker exec 进 api 容器后，服务端要访问容器自身的 8780。 */
const DOCKER_API_URL = "http://127.0.0.1:8780";

/** [aipdd fork delta] 未进入项目时（全局 /mcp 页）配置里的项目编号占位符。 */
const PROJECT_ID_PLACEHOLDER = "<your-project-id>";

export function McpPage({ project }: { project?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: projects } = useAllProjectSummaries();
  // [aipdd fork delta] 后端运行形态：桌面/裸机起本机 Python，容器走 docker exec。
  const localToolsQuery = useAgentLocalTools();
  const runtime = localToolsQuery.data?.data.mcpRuntime;
  // [aipdd fork delta] 顶部分段切换（接入指南 / MCP 列表），与剧集页 tab 同一套视觉。
  const [tab, setTab] = useState<"overview" | "tools">("overview");

  // [aipdd fork delta] 无项目上下文时项目编号走占位符，并给出进入项目的快捷入口
  // ——项目内页面复制出来的才是含项目编号的开箱即用配置。
  const projectId = project ?? PROJECT_ID_PLACEHOLDER;
  const recentProjects = useMemo(
    () =>
      (projects ?? [])
        .filter((item) => item.status === "active")
        .sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""))
        .slice(0, 3),
    [projects],
  );

  // API 地址取当前站点来源；项目 id 用当前项目。用户在别处接入时可自行替换 host。
  const apiBase = useMemo(() => {
    if (typeof window !== "undefined" && window.location?.origin) {
      return window.location.origin;
    }
    return "http://localhost:8780";
  }, []);

  // [aipdd fork delta] 手动配置按运行形态生成：桌面版用随包 Python 直接启动
  // （没有 uv 也没有仓库 checkout），源码运行才用 uv run。
  const configJson = useMemo(() => {
    const native = runtime?.mode === "native";
    const env: Record<string, string> = {
      AIPDD_DRAMA_API_URL: apiBase,
      // 统一密钥：在「设置 → API 密钥」生成，与模型广场调用共用同一把。
      AIPDD_DRAMA_AGENT_TOKEN: "<your-api-key>",
      AIPDD_DRAMA_PROJECT_ID: projectId,
    };
    if (native && runtime?.pythonPath) env.PYTHONPATH = runtime.pythonPath;
    return JSON.stringify(
      {
        mcpServers: {
          "aipdd-drama": {
            command: native ? runtime?.python || "python" : "uv",
            args: native
              ? ["-m", "novelvideo.chat.aipdd_drama_mcp"]
              : ["run", "python", "-m", "novelvideo.chat.aipdd_drama_mcp"],
            env,
          },
        },
      },
      null,
      2,
    );
  }, [apiBase, projectId, runtime]);

  // 容器化部署时：让 MCP 客户端通过 docker exec 进入已运行的 api 容器内启动服务器。
  // api 容器没有内置接入用 env，这里显式带入容器内 API 地址、统一密钥占位与项目 id。
  // 容器名取后端上报值（compose 可用 ST_CONTAINER_NAME 自定义，见 docker-compose.release.yml）。
  const dockerConfig = useMemo(
    () =>
      JSON.stringify(
        {
          mcpServers: {
            "aipdd-drama": {
              command: "docker",
              args: [
                "exec",
                "-i",
                "-e",
                `AIPDD_DRAMA_API_URL=${DOCKER_API_URL}`,
                "-e",
                "AIPDD_DRAMA_AGENT_TOKEN=<your-api-key>",
                "-e",
                `AIPDD_DRAMA_PROJECT_ID=${projectId}`,
                runtime?.containerName || "aipdd-drama-api",
                "python",
                "-m",
                "novelvideo.chat.aipdd_drama_mcp",
              ],
            },
          },
        },
        null,
        2,
      ),
    [projectId, runtime?.containerName],
  );

  // [aipdd fork delta] 画布（freezone）基础用法：按「用途 → 工具 → 参数」三要素列出，
  // 替代此前不易理解的裸 API 路径；参数即 aipdd_drama_get/post 的调用字段。
  const canvasBasics = useMemo(
    () => [
      {
        title: t("mcp.basics.case1.title"),
        tool: "aipdd_drama_get",
        params: [`path=/projects/${projectId}/freezone/canvases`],
      },
      {
        title: t("mcp.basics.case2.title"),
        tool: "aipdd_drama_get",
        params: [`path=/projects/${projectId}/freezone/canvases/<canvas_id>`],
      },
      {
        title: t("mcp.basics.case3.title"),
        tool: "aipdd_drama_post",
        params: [`path=/projects/${projectId}/freezone/gen`, 'body={"prompt": "..."}'],
      },
    ],
    [projectId, t],
  );

  const envRows = [
    { key: "AIPDD_DRAMA_API_URL", value: apiBase, desc: t("mcp.env.apiUrl") },
    { key: "AIPDD_DRAMA_AGENT_TOKEN", value: "<your-api-key>", desc: t("mcp.env.agentToken") },
    { key: "AIPDD_DRAMA_PROJECT_ID", value: projectId, desc: t("mcp.env.projectId") },
  ];

  const examples = [t("mcp.example.1"), t("mcp.example.2"), t("mcp.example.3")];

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] min-h-0 flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 flex-col gap-3 border-b border-border/30 bg-background px-9 py-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Plug className="size-[18px]" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate py-1 text-2xl font-semibold tracking-tight text-foreground">
              {t("mcp.page.title")}
            </h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              {t("mcp.page.description")}
            </p>
          </div>
        </div>
      </header>

      <div className="flex shrink-0 justify-center border-b border-border/30 bg-background px-9 py-3">
        <SlidingTabs
          items={[
            { value: "overview", label: t("mcp.tabs.overview"), icon: BookOpen },
            { value: "tools", label: t("mcp.tabs.tools"), icon: Server },
          ]}
          value={tab}
          onValueChange={setTab}
          aria-label={t("mcp.page.title")}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-9 py-6">
        <div className="mx-auto max-w-4xl">

          {/* [aipdd fork delta] 全局页（无项目上下文）：编号为占位符，给进入项目的快捷入口。 */}
          {!project ? (
            <section className="mb-4 rounded-[10px] border border-primary/30 bg-primary/10 p-4">
              <h2 className="text-sm font-medium text-foreground">{t("mcp.noProject.title")}</h2>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {t("mcp.noProject.body")}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {recentProjects.length > 0 ? (
                  <span className="text-xs text-muted-foreground">
                    {t("mcp.noProject.recentLabel")}
                  </span>
                ) : null}
                {recentProjects.map((item) => (
                  <Button
                    key={item.id}
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-2 text-[11px] font-normal shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
                    onClick={() =>
                      void navigate({
                        to: "/projects/$project/mcp",
                        params: { project: item.id },
                      })
                    }
                  >
                    {item.name}
                    <ArrowRight className="size-3.5" aria-hidden="true" />
                  </Button>
                ))}
                {projects && recentProjects.length === 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-2 text-[11px] font-normal shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
                    onClick={() => void navigate({ to: "/" })}
                  >
                    {t("mcp.noProject.create")}
                    <ArrowRight className="size-3.5" aria-hidden="true" />
                  </Button>
                ) : null}
              </div>
            </section>
          ) : null}

          {tab === "overview" ? (
            <>
              {/* 简介 */}
              <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <p className="text-sm leading-relaxed text-muted-foreground">{t("mcp.intro.body")}</p>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t("mcp.intro.note")}</p>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t("mcp.intro.apiKeyHint")}</p>
              </section>

              {/* [aipdd fork delta] 一键把官方内置 AIPDD Drama MCP 接入 Agent。 */}
              <AgentAddCard />

              {/* [aipdd fork delta] 本地 Agent 工具：检测本机安装 + 多选连接。 */}
              <LocalAgentTools project={project} />

              {/* Step 1: 接入配置 */}
              <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <div className="mb-3 flex items-center gap-2">
                  <StepBadge n={1} />
                  <h2 className="text-sm font-medium text-foreground">{t("mcp.step.configTitle")}</h2>
                </div>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("mcp.step.configHint")}</p>
                <CopyBlock code={configJson} label="mcp.json" />
                {runtime?.mode === "native" ? (
                  <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                    {t("mcp.step.runtimeNative")}
                  </p>
                ) : null}
              </section>

              {/* Step 2: 环境变量 */}
              <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <div className="mb-3 flex items-center gap-2">
                  <StepBadge n={2} />
                  <h2 className="text-sm font-medium text-foreground">{t("mcp.step.envTitle")}</h2>
                </div>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("mcp.step.envHint")}</p>
                <div className="overflow-hidden rounded-[10px] border border-white/[0.06]">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-white/[0.018] text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 font-medium">{t("mcp.env.colKey")}</th>
                        <th className="px-3 py-2 font-medium">{t("mcp.env.colValue")}</th>
                        <th className="px-3 py-2 font-medium">{t("mcp.env.colDesc")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {envRows.map((row) => (
                        <tr key={row.key} className="border-t border-white/[0.06]">
                          <td className="px-3 py-2 font-mono text-foreground">{row.key}</td>
                          <td className="px-3 py-2 font-mono text-muted-foreground">{row.value}</td>
                          <td className="px-3 py-2 text-muted-foreground">{row.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* Step 3: 容器内接入（可选） */}
              <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <div className="mb-3 flex items-center gap-2">
                  <StepBadge n={3} />
                  <h2 className="text-sm font-medium text-foreground">{t("mcp.step.dockerTitle")}</h2>
                </div>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("mcp.step.dockerHint")}</p>
                <CopyBlock code={dockerConfig} label="mcp.json (docker)" />
              </section>

              {/* 画布（freezone）基础用法 */}
              <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <h2 className="mb-1 text-sm font-medium text-foreground">{t("mcp.basics.title")}</h2>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("mcp.basics.hint")}</p>
                <div className="grid gap-2 sm:grid-cols-3">
                  {canvasBasics.map((item) => (
                    <div
                      key={item.title}
                      className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-2.5"
                    >
                      <h3 className="text-xs font-medium text-foreground">{item.title}</h3>
                      <div className="mt-1.5">
                        <p className="text-[11px] text-muted-foreground">{t("mcp.basics.toolLabel")}</p>
                        <code className="text-[12.5px] font-medium text-primary">{item.tool}</code>
                      </div>
                      <div className="mt-1.5">
                        <p className="text-[11px] text-muted-foreground">{t("mcp.basics.paramLabel")}</p>
                        {item.params.map((param) => (
                          <code
                            key={param}
                            className="mt-0.5 block break-all font-mono text-[11px] leading-relaxed text-foreground"
                          >
                            {param}
                          </code>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              {/* 使用示例 */}
              <section className="rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
                <h2 className="mb-1 text-sm font-medium text-foreground">{t("mcp.examplesTitle")}</h2>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("mcp.examplesHint")}</p>
                <ul className="space-y-1.5">
                  {examples.map((ex, i) => (
                    <li
                      key={i}
                      className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-2 text-sm text-foreground"
                    >
                      {ex}
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : (
            /* [aipdd fork delta] MCP 服务器列表：内置 aipdd-drama 默认写入且不可删除，
               用户可添加 SSE / 流式 HTTP / 自定义代码（stdio）三类服务器；
               每个服务器可展开查看概览与工具（内置目录 / 测试连接探测）。 */
            <AgentMcpSection />
          )}
        </div>
      </div>
    </div>
  );
}
