// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] MCP 栏目 →「本地 Agent 工具」接入区块：检测本机安装的
// Workbuddy / Claude Code / Cursor / Codex（宿主 home 经 compose 挂载探测），
// 已安装的可多选；「连接」把内置 AIPDD Drama MCP 写进对应客户端配置（每工具一把
// 统一密钥，断开时自动吊销）；配置存在即显示「已连接」。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Bot,
  Check,
  Code2,
  Loader2,
  MousePointer2,
  Plug,
  RefreshCw,
  Sparkles,
  Unplug,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  useAgentLocalTools,
  useConnectAgentLocalTools,
  useDisconnectAgentLocalTools,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

/** 每工具一个通用图标（品牌图标不做内置；名称本身足够识别）。 */
const TOOL_ICONS: Record<string, LucideIcon> = {
  workbuddy: Bot,
  claude: Sparkles,
  cursor: MousePointer2,
  codex: Code2,
};

export function LocalAgentTools({ project }: { project?: string }) {
  const { t } = useTranslation();
  const query = useAgentLocalTools();
  const connect = useConnectAgentLocalTools();
  const disconnect = useDisconnectAgentLocalTools();
  const [picked, setPicked] = useState<string[]>([]);

  const tools = query.data?.data.tools ?? [];
  const hostHomeAvailable = query.data?.data.hostHomeAvailable ?? true;
  const busy = connect.isPending || disconnect.isPending;

  const pickedSet = useMemo(() => new Set(picked), [picked]);
  const connectTargets = tools
    .filter((tool) => pickedSet.has(tool.id) && tool.installed && !tool.connected)
    .map((tool) => tool.id);
  const disconnectTargets = tools
    .filter((tool) => pickedSet.has(tool.id) && tool.connected)
    .map((tool) => tool.id);

  const toggle = (id: string, checked: boolean) => {
    setPicked((prev) =>
      checked ? [...prev.filter((item) => item !== id), id] : prev.filter((item) => item !== id),
    );
  };

  const nameOf = (id: string) => tools.find((tool) => tool.id === id)?.name ?? id;

  const run = async (kind: "connect" | "disconnect", ids: string[]) => {
    if (!ids.length || busy) return;
    const mutation = kind === "connect" ? connect : disconnect;
    try {
      const data = await mutation.mutateAsync({ tools: ids, project: project ?? "" });
      const okNames = data.results.filter((item) => item.ok).map((item) => nameOf(item.id));
      const failed = data.results.filter((item) => !item.ok);
      if (okNames.length) {
        toast.success(
          t(
            kind === "connect"
              ? "mcp.localTools.connectSuccess"
              : "mcp.localTools.disconnectSuccess",
            { names: okNames.join("、") },
          ),
        );
      }
      if (failed.length) {
        toast.error(`${nameOf(failed[0].id)}: ${failed[0].error || t("mcp.localTools.actionFailed")}`);
      }
      setPicked([]);
    } catch (error) {
      toast.error(String((error as Error).message || t("mcp.localTools.actionFailed")));
    }
  };

  return (
    <section className="mb-4 rounded-[10px] border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-foreground">{t("mcp.localTools.title")}</h2>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
          className="h-7 gap-1.5 rounded-[7px] px-2 text-[11px] font-normal text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          <RefreshCw className={cn("size-3.5", query.isFetching && "animate-spin")} />
          {t("mcp.localTools.refresh")}
        </Button>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
        {t("mcp.localTools.hint")}
      </p>
      {/* [aipdd fork delta] 仅在存在 config 级（仅配置痕迹，可能卸载残留）工具时
          提示证据分级说明（req20）。 */}
      {tools.some(
        (tool) => (tool.evidence ?? (tool.installed ? "app" : "none")) === "config",
      ) ? (
        <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground/70">
          {t("mcp.localTools.configDetectedHint")}
        </p>
      ) : null}

      {!hostHomeAvailable ? (
        <p className="mb-3 rounded-[8px] border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-300">
          {t("mcp.localTools.hostHomeUnavailable")}
        </p>
      ) : null}

      {query.isLoading ? (
        <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {t("mcp.localTools.loading")}
        </div>
      ) : (
        <div className="overflow-hidden rounded-[10px] border border-white/[0.06]">
          {tools.map((tool) => {
            const Icon = TOOL_ICONS[tool.id] ?? Plug;
            const checkable = tool.installed && hostHomeAvailable && !busy;
            const fieldId = `local-tool-${tool.id}`;
            // 证据分级（req20）：app=应用本体命中；config=仅配置痕迹（可能为
            // 卸载残留）——不再冒充「已安装」，hover 说明并照常可勾选连接。
            const evidence = tool.evidence ?? (tool.installed ? "app" : "none");
            return (
              <div
                key={tool.id}
                className={cn(
                  "flex items-center gap-3 border-b border-white/[0.06] px-3 py-2.5 last:border-b-0",
                  checkable ? "hover:bg-white/[0.02]" : "opacity-60",
                )}
              >
                <Checkbox
                  id={fieldId}
                  checked={pickedSet.has(tool.id)}
                  disabled={!checkable}
                  onCheckedChange={(value) => toggle(tool.id, value === true)}
                />
                <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <label
                  htmlFor={fieldId}
                  className={cn("min-w-0 flex-1", checkable && "cursor-pointer")}
                >
                  <span className="block text-sm text-foreground">{tool.name}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground/70">
                    {tool.configPath}
                  </span>
                </label>
                <span className="flex shrink-0 items-center gap-1.5">
                  {evidence === "config" ? (
                    <span
                      title={t("mcp.localTools.configDetectedHint")}
                      className="rounded-md border border-amber-500/25 bg-amber-500/[0.08] px-2 py-0.5 text-[11px] text-amber-300/90"
                    >
                      {t("mcp.localTools.configDetected")}
                    </span>
                  ) : (
                    <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-muted-foreground">
                      {evidence === "app"
                        ? t("mcp.localTools.installed")
                        : t("mcp.localTools.notInstalled")}
                    </span>
                  )}
                  {tool.connected ? (
                    <span className="flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-400">
                      <Check className="size-3" />
                      {t("mcp.localTools.connected")}
                    </span>
                  ) : null}
                </span>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={busy || !connectTargets.length || !hostHomeAvailable}
          onClick={() => void run("connect", connectTargets)}
          className="h-7 shrink-0 gap-1.5 rounded-[7px] bg-primary px-2 text-[11px] font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
        >
          {connect.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Plug className="size-3.5" />
          )}
          {t("mcp.localTools.connect")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || !disconnectTargets.length}
          onClick={() => void run("disconnect", disconnectTargets)}
          className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-2 text-[11px] font-normal shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
        >
          {disconnect.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Unplug className="size-3.5" />
          )}
          {t("mcp.localTools.disconnect")}
        </Button>
      </div>
    </section>
  );
}