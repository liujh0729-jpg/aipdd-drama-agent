// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装」：本产品**部署环境**里真实依赖的运行时组件清单。
// 本 fork 走 Docker 源码部署，桌面客户端那套本机安装入口与本产品不兼容，已彻底移除。
// 现在如实回显服务端容器的探测结果（ffmpeg / node / npm / uv / 中文字体 / Chrome CDP，
// 见 @/lib/queries/env），缺失项给出补齐指引；「3D 世界（3DGS / SHARP）」的运行时按需
// 安装不再是独立菜单，而是融入本页作为可选特性区块（复用 @/components/settings/world-section
// 的 WorldSection）；本地 Agent 工具行继续复用 MCP 页的宿主 home 扫描
// （见 @/lib/queries/agent 的 useAgentLocalTools）。
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bot,
  Check,
  Clapperboard,
  Code2,
  ExternalLink,
  Globe,
  Hexagon,
  Loader2,
  MousePointer2,
  Package,
  PenTool,
  Sparkles,
  Type,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { WorldSection } from "@/components/settings/world-section";
import { useAgentLocalTools } from "@/lib/queries/agent";
import { useEnvStatus, type EnvDependency } from "@/lib/queries/env";
import { cn } from "@/lib/utils";

/** 各运行依赖的图标与文案键前缀（后端只回事实，名称/用途/修复指引在这里按 id 取）。 */
const DEPENDENCY_META: Record<string, { Icon: LucideIcon }> = {
  ffmpeg: { Icon: Clapperboard },
  node: { Icon: Hexagon },
  npm: { Icon: Package },
  uv: { Icon: Zap },
  fonts: { Icon: Type },
  chrome: { Icon: Globe },
  opencv: { Icon: PenTool },
};

/**
 * [aipdd fork delta] 不是运行依赖、但值得如实回显的项：Chrome（调试端口）。
 * 抖音文案提取的主链路是纯 HTTP + Cookie，不开浏览器；CDP 只是 Cookie 失效/风控时的
 * 兜底，而且连的是**宿主机**自己的 Chrome——没开调试端口是常态，不该在「运行
 * 依赖」里挂一条黄色「缺失」让部署看起来坏了。因此归到「可选特性」，状态用「未启动」。
 */
const OPTIONAL_DEPENDENCY_IDS: ReadonlySet<string> = new Set(["chrome"]);

/** 兜底图标：后端将来新增依赖时不至于没有图标。 */
const FALLBACK_ICON: LucideIcon = Package;

/** 本地 Agent 工具的官方入口；null = 无公开下载地址（用渠道提示兜底）。 */
const LOCAL_TOOL_LINKS: Record<string, string | null> = {
  workbuddy: null,
  claude: "https://claude.ai/code",
  cursor: "https://www.cursor.com/downloads",
  codex: "https://github.com/openai/codex",
};

/** 与 MCP 页「本地 Agent 工具」区块同一套图标（品牌图标不做内置）。 */
const TOOL_ICONS: Record<string, LucideIcon> = {
  workbuddy: Bot,
  claude: Sparkles,
  cursor: MousePointer2,
  codex: Code2,
};

export function EnvSetupSection() {
  const { t } = useTranslation();
  const envQuery = useEnvStatus();
  const toolsQuery = useAgentLocalTools();

  const items = envQuery.data?.data?.items ?? [];
  // 运行依赖与按需项分开渲染：按需项（Chrome CDP）属于下面的「可选特性」区块。
  const runtimeItems = items.filter((item) => !OPTIONAL_DEPENDENCY_IDS.has(item.id));
  const optionalItems = items.filter((item) => OPTIONAL_DEPENDENCY_IDS.has(item.id));
  const tools = toolsQuery.data?.data.tools ?? [];
  const hostHomeAvailable = toolsQuery.data?.data.hostHomeAvailable ?? true;

  return (
    <div>
      {/* 运行依赖：服务端容器的真实运行时清单。 */}
      <section className="px-5 py-5">
        <div className="flex items-center gap-2">
          <span className="size-1.5 rounded-full bg-emerald-400" />
          <h3 className="font-heading text-sm font-medium text-foreground">
            {t("settings.envSetup.title")}
          </h3>
        </div>

        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {t("settings.envSetup.description")}
        </p>

        <h4 className="mt-5 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          {t("settings.envSetup.groups.runtime")}
        </h4>

        {envQuery.isLoading ? (
          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            {t("settings.envSetup.loading")}
          </div>
        ) : envQuery.error ? (
          <div className="mt-2 flex items-center gap-3 text-sm text-muted-foreground">
            <span>{t("settings.envSetup.loadFailed")}</span>
            <button
              type="button"
              onClick={() => void envQuery.refetch()}
              className={cn(buttonVariants({ size: "sm", variant: "outline" }))}
            >
              {t("common.retry")}
            </button>
          </div>
        ) : (
          <div className="mt-2 overflow-hidden rounded-lg border border-border bg-white/[0.02]">
            {runtimeItems.map((item, index) => (
              <DependencyRow
                key={item.id}
                item={item}
                last={index === runtimeItems.length - 1}
              />
            ))}
          </div>
        )}
      </section>

      {/* 可选特性：3D 世界（3DGS / SHARP）运行时按需安装——不再是独立菜单，融入本页。 */}
      <div className="border-t border-border">
        <div className="px-5 pt-4 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          {t("settings.envSetup.groups.features")}
        </div>
        {/* [aipdd fork delta] 宿主机 Chrome（CDP）等按需项：与 3D 世界同排在本区块。 */}
        {!envQuery.isLoading && !envQuery.error && optionalItems.length > 0 ? (
          <div className="px-5 pt-3">
            <div className="overflow-hidden rounded-lg border border-border bg-white/[0.02]">
              {optionalItems.map((item, index) => (
                <DependencyRow
                  key={item.id}
                  item={item}
                  optional
                  last={index === optionalItems.length - 1}
                />
              ))}
            </div>
          </div>
        ) : null}
        <WorldSection />
      </div>

      {/* 本地 Agent 工具：宿主 home 实时扫描。 */}
      <section className="border-t border-border px-5 py-5">
        <h4 className="text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          {t("settings.envSetup.groups.localTools")}
        </h4>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {t("settings.envSetup.localTools.description")}
        </p>

        {!hostHomeAvailable && !toolsQuery.isLoading ? (
          <p className="mt-2.5 rounded-[8px] border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-300">
            {t("mcp.localTools.hostHomeUnavailable")}
          </p>
        ) : null}

        <div className="mt-2 overflow-hidden rounded-lg border border-border bg-white/[0.02]">
          {toolsQuery.isLoading ? (
            <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              {t("mcp.localTools.loading")}
            </div>
          ) : (
            tools.map((tool) => (
              <LocalToolRow
                key={tool.id}
                id={tool.id}
                name={tool.name}
                evidence={tool.evidence ?? (tool.installed ? "app" : "none")}
                hostHomeAvailable={hostHomeAvailable}
              />
            ))
          )}
        </div>

        <p className="mt-2.5 text-[11px] leading-relaxed text-muted-foreground">
          {t("settings.envSetup.localTools.connectHint")}
        </p>
      </section>
    </div>
  );
}

function DependencyRow({
  item,
  last,
  optional = false,
}: {
  item: EnvDependency;
  last: boolean;
  /** 按需项：未就绪不是故障，用中性「未启动」而不是黄色「缺失」告警。 */
  optional?: boolean;
}) {
  const { t } = useTranslation();
  const meta = DEPENDENCY_META[item.id];
  const Icon = meta?.Icon ?? FALLBACK_ICON;

  return (
    <div
      className={cn(
        "flex items-start gap-3 px-3 py-2.5",
        !last && "border-b border-white/[0.06]",
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {t(`settings.envSetup.dependencies.${item.id}.name`)}
          </span>
          {item.version ? (
            <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              v{item.version}
            </span>
          ) : null}
          {item.installed ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-400">
              <Check className="size-3" aria-hidden />
              {t("settings.envSetup.status.ready")}
            </span>
          ) : (
            <span
              className={cn(
                "inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-0.5 text-[11px]",
                optional
                  ? "border-white/10 bg-white/[0.03] text-muted-foreground"
                  : "border-amber-500/30 bg-amber-500/10 text-amber-300",
              )}
            >
              <X className="size-3" aria-hidden />
              {t(
                optional
                  ? "settings.envSetup.status.notStarted"
                  : "settings.envSetup.status.missing",
              )}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t(`settings.envSetup.dependencies.${item.id}.usage`)}
        </p>
        {!item.installed ? (
          <p
            className={cn(
              "mt-1 flex items-start gap-1.5 text-[11px] leading-relaxed",
              optional ? "text-muted-foreground" : "text-amber-300/90",
            )}
          >
            <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            <span>{t(`settings.envSetup.dependencies.${item.id}.fix`)}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

function LocalToolRow({
  id,
  name,
  evidence,
  hostHomeAvailable,
}: {
  id: string;
  name: string;
  evidence: "app" | "config" | "none";
  hostHomeAvailable: boolean;
}) {
  const { t } = useTranslation();
  const Icon = TOOL_ICONS[id] ?? Bot;
  const link = LOCAL_TOOL_LINKS[id] ?? null;
  // 证据分级（req20）：app=应用本体命中（真安装）；config=仅配置痕迹，
  // 可能为卸载残留——不再冒充「已安装」，hover 说明，仍可去官方入口。
  const installed = evidence === "app";

  return (
    <div className="flex items-center gap-3 border-b border-white/[0.06] px-3 py-2.5 last:border-b-0">
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1 truncate text-sm text-foreground">{name}</span>
      {hostHomeAvailable ? (
        evidence === "config" ? (
          <span
            title={t("mcp.localTools.configDetectedHint")}
            className="shrink-0 rounded-md border border-amber-500/25 bg-amber-500/[0.08] px-2 py-0.5 text-[11px] text-amber-300/90"
          >
            {t("mcp.localTools.configDetected")}
          </span>
        ) : (
          <span
            className={cn(
              "shrink-0 rounded-md border px-2 py-0.5 text-[11px]",
              installed
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : "border-white/10 bg-white/[0.03] text-muted-foreground",
            )}
          >
            {installed ? t("mcp.localTools.installed") : t("mcp.localTools.notInstalled")}
          </span>
        )
      ) : (
        <span className="shrink-0 rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-muted-foreground">
          {t("settings.envSetup.notDetectable")}
        </span>
      )}
      {!installed && link ? (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            buttonVariants({ size: "xs", variant: "outline" }),
            "h-6 shrink-0 gap-1 px-2 text-[11px]",
          )}
        >
          <ExternalLink className="size-3" aria-hidden />
          {t("settings.envSetup.localTools.openSite")}
        </a>
      ) : null}
      {!installed && !link ? (
        <span className="shrink-0 text-[11px] text-muted-foreground/70">
          {t("settings.envSetup.localTools.workbuddyHint")}
        </span>
      ) : null}
    </div>
  );
}
