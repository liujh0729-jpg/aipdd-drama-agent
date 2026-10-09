// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { QueryClient } from "@tanstack/react-query";
import { useAspectRatioStore } from "@/stores/aspect-ratio-store";
import { useAuthStore } from "@/stores/auth-store";
import { useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { useSaveStatusStore } from "@/stores/save-status-store";
import { useSeenPoolStore } from "@/stores/seen-pool-store";
import { useTaskCenterStore } from "@/task-center/store";
import { useRewardEventsStore } from "@/features/rewards/reward-events-store";

// UX chrome keys that must survive a region switch.
const PRESERVE_KEYS = new Set<string>([
  "supertale-app",
  "i18nextLng",
]);

// 退出登录（换账号）时额外保留：区域选择是设备/部署级偏好而非用户态，换账号
// 不应强迫用户重选区域。区域切换流程仍然要清它（切完会立刻写入新值）。
const LOGOUT_PRESERVE_KEYS = new Set<string>([
  ...PRESERVE_KEYS,
  "supertale-region",
]);

// Prefix sweep is self-maintaining: any future region-scoped key matching
// these prefixes is covered without updating this list.
const SWEEP_PREFIXES = [
  "supertale-",
  "st.episode.",
  "st.beats.toggles",
  "st.beats.action-panel.sections",
];

function resetSessionScopedState(
  deps: { queryClient: QueryClient },
  preserveKeys: ReadonlySet<string>,
): void {
  useAuthStore.getState().reset();
  useSaveStatusStore.getState().reset();
  useSeenPoolStore.getState().reset();
  useEpisodeWorkbenchStore.getState().reset();
  useTaskCenterStore.getState().reset();
  useAspectRatioStore.getState().reset();
  useRewardEventsStore.getState().reset();

  deps.queryClient.clear();

  const toRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (preserveKeys.has(key)) continue;
    if (SWEEP_PREFIXES.some((p) => key.startsWith(p))) {
      toRemove.push(key);
    }
  }
  toRemove.forEach((k) => localStorage.removeItem(k));
}

export function resetRegionState(deps: { queryClient: QueryClient }): void {
  resetSessionScopedState(deps, PRESERVE_KEYS);
}

/**
 * 退出登录 / 换账号时的用户态清理：与区域切换同一套（zustand 各用户级 store、
 * React Query 缓存、localStorage 前缀清扫），但保留区域选择。
 *
 * 为什么必须清 queryClient：手动退出是 SPA 内部跳转（不刷新页面），QueryClient
 * 常驻内存；projectSummaries 等查询的 key 不含用户名且有 staleTime，换账号登录
 * 后缓存仍在新鲜期内，React Query 连请求都不发 —— B 会一直看到 A 的项目列表，
 * 直到手动刷新。
 */
export function resetUserSessionState(deps: { queryClient: QueryClient }): void {
  resetSessionScopedState(deps, LOGOUT_PRESERVE_KEYS);
}
