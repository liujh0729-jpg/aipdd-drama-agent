// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { SlidingTabs } from "@/components/nav/sliding-tabs";
import { useBeatStates } from "@/hooks/use-beat-states";
import { TOP_TABS } from "@/lib/episode-nav";
import type { TopTabDef } from "@/lib/episode-nav";
import { cn } from "@/lib/utils";
import type { EpisodeCounts } from "@/types/beat-state";

interface EpisodeNavProps {
  project: string;
  episode: number;
}

export function HealthBar({ project, episode }: EpisodeNavProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const params = { project, episode: String(episode) };
  const activeTab =
    TOP_TABS.find(
      (tab) =>
        pathname.includes(tab.routeSegment) ||
        (tab.id === "beats" &&
          (pathname.endsWith("/sketches") ||
            pathname.endsWith("/audio") ||
            pathname.endsWith("/video"))),
    ) ??
    // 无法归属路径（/overview、/index）默认高亮脚本 tab —— 第一个 tab，
    // 与 index 重定向的默认口径保持一致。
    TOP_TABS[0];
  const items = TOP_TABS.map((tab) => ({
    value: tab.id,
    icon: tab.icon,
    label: t(tab.labelKey),
    testId: `episode-health-${tab.id}`,
  }));

  return (
    <nav className="border-b border-border/30 bg-background px-9 py-3">
      <SlidingTabs
        items={items}
        value={activeTab.id}
        aria-label={t("nav.episodes")}
        className="justify-center"
        onValueChange={(next) => {
          const tab = TOP_TABS.find((item) => item.id === next);
          if (!tab) return;
          void navigate({
            to: `/projects/$project/episodes/$episode${tab.routeSegment}`,
            params,
          });
        }}
      />
    </nav>
  );
}

export function EpisodeHealthSummary({
  project,
  episode,
  className,
}: EpisodeNavProps & { className?: string }) {
  const { t } = useTranslation();
  const { counts, loading } = useBeatStates(project, episode);
  const tabHealthItems = TOP_TABS.map((tab) => {
    const health = getTopTabHealth(tab, counts);
    return {
      tab,
      status:
        loading || health === null ? undefined : formatTabHealth(t, health),
    };
  });

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] leading-none text-muted-foreground",
        className,
      )}
    >
      {tabHealthItems.map(({ tab, status }) => {
        const Icon = tab.icon;
        return (
          <span
            key={tab.id}
            className="inline-flex items-center gap-1.5 whitespace-nowrap"
            data-testid={`episode-health-${tab.id}-status`}
          >
            <Icon className="size-3" />
            <span>{t(tab.labelKey)}</span>
            {status ? <span className="text-foreground/80">{status}</span> : null}
          </span>
        );
      })}
    </div>
  );
}

type TabHealth =
  | { kind: "stages"; ready: number; total: number; blocked: number }
  | { kind: "compose"; ready: boolean; blocked: number };

function formatTabHealth(
  t: (key: string, options?: Record<string, number>) => string,
  health: TabHealth,
) {
  if (health.kind === "compose") {
    return health.ready
      ? t("episode.health.composeReady")
      : t("episode.health.composeBlocked", { count: health.blocked });
  }

  return t("episode.health.readyRatio", {
    ready: health.ready,
    total: health.total,
  });
}

export function getTopTabHealth(
  tab: TopTabDef,
  counts: EpisodeCounts,
): TabHealth | null {
  if (tab.isCompose) {
    return {
      kind: "compose",
      ready: counts.compose.ready,
      blocked: counts.compose.missing.length,
    };
  }

  // 无 stage 的 tab 没有可统计的素材健康度。
  if (tab.stageIds.length === 0) return null;

  const totals = tab.stageIds.reduce(
    (acc, stageId) => {
      const stage = counts[stageId];
      acc.ready += stage.ready;
      acc.total += stage.total;
      return acc;
    },
    { ready: 0, total: 0 },
  );

  return {
    kind: "stages",
    ready: totals.ready,
    total: totals.total,
    blocked: Math.max(totals.total - totals.ready, 0),
  };
}
