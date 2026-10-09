// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from 'zustand';

// 触控板平移：笔记本用户没有鼠标中键（画布默认靠中键拖动平移），开启后用触控板
// 两指滑动平移画布（ReactFlow panOnScroll），捏合仍可缩放。状态独立成一个轻量
// store，和对齐吸附开关同构，订阅它的按钮 / Canvas 不会因 canvas 节点变动而重渲染。

const STORAGE_KEY = 'canvas.trackpadPan.enabled';

function readPersistedEnabled(): boolean {
  // 默认开启：笔记本用户没有鼠标中键，开箱即可用触控板两指滑动平移画布。
  // 仅当用户显式关过（持久化为 '0'）才保持关闭，尊重其选择。
  if (typeof window === 'undefined') return true;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === null) return true;
    return stored === '1';
  } catch {
    return true;
  }
}

function persistEnabled(value: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
  } catch {
    // localStorage 写不进去就算了，下次进来从默认值开始。
  }
}

interface TrackpadPanState {
  enabled: boolean;
  toggle: () => void;
}

export const useTrackpadPanStore = create<TrackpadPanState>((set, get) => ({
  enabled: readPersistedEnabled(),
  toggle: () => {
    const next = !get().enabled;
    persistEnabled(next);
    set({ enabled: next });
  },
}));
