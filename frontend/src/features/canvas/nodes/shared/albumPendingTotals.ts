// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from 'react';

/**
 * 叠卡画册「应到张数」的模块级登记表（nodeId → 本次批量生成的总数）。
 *
 * 为什么不放组件 state：画布开了 onlyRenderVisibleElements，批量生成期间把
 * 节点平移出视口会卸载组件，回来时 useState 归零、骨架占位消失。放模块级
 * Map 可跨卸载/重挂存活；刻意不持久化——刷新后未完成任务不续传（每节点只
 * 保留第 1 个任务句柄），持久化只会留下永远转圈的占位。
 */
const totals = new Map<string, number>();
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setAlbumPendingTotal(nodeId: string, total: number): void {
  if (total <= 0) {
    if (!totals.delete(nodeId)) return;
  } else {
    if (totals.get(nodeId) === total) return;
    totals.set(nodeId, total);
  }
  emit();
}

export function useAlbumPendingTotal(nodeId: string): number {
  return useSyncExternalStore(subscribe, () => totals.get(nodeId) ?? 0);
}
