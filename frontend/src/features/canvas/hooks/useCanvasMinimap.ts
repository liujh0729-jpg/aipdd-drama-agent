// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from 'react';

import { MINIMAP_HIDE_DELAY_MS } from '../canvasHelpers';

/**
 * 小地图（minimap）的状态机：固定 / 悬停 / 拖动三态决定弹层挂载；隐藏加短延迟，
 * 以稳定跨越「小地图 ↔ 书签数字行」的间隙点击；拖动期间钉住不卸载，并抑制
 * onMoveEnd 的逐帧视口提交（见 useSmoothMinimapPan）。自 Canvas 组件原样抽出
 * （行为不变）；视图区（MiniMap / 书签数字行 / 悬浮按钮）仍在 Canvas。
 */
export function useCanvasMinimap() {
  const [minimapPinned, setMinimapPinned] = useState(false);
  const [minimapHovered, setMinimapHovered] = useState(false);
  // 小地图拖动期间必须钉住不卸载：非固定模式下指针一拖出小地图就会触发
  // onMouseLeave → 180ms 后 minimapVisible 变 false → MiniMap 卸载 →
  // useSmoothMinimapPan 的清理函数摘掉 window 监听，拖动直接断在半路。
  const [minimapPanning, setMinimapPanning] = useState(false);
  const minimapVisible = minimapPinned || minimapHovered || minimapPanning;
  // 小地图弹层（含上方的书签数字行）靠 hover 显示。数字行是小地图上方、隔着间隙的
  // 独立 DOM 子树:鼠标从小地图移到数字按钮的途中会先离开小地图,若立即把
  // minimapHovered 置 false,整个 overlay 会在点到按钮前卸载,导致「点不了」。
  // 这里给「隐藏」加一个短延迟:离开时延迟 false,任意区域(小地图/触发按钮/数字行)
  // 重新 hover 会取消该延迟,从而能稳定跨越间隙去点击。
  const minimapHideTimerRef = useRef<number | null>(null);
  const setMinimapHover = useCallback((hovered: boolean) => {
    if (minimapHideTimerRef.current !== null) {
      window.clearTimeout(minimapHideTimerRef.current);
      minimapHideTimerRef.current = null;
    }
    if (hovered) {
      setMinimapHovered(true);
    } else {
      minimapHideTimerRef.current = window.setTimeout(() => {
        setMinimapHovered(false);
        minimapHideTimerRef.current = null;
      }, MINIMAP_HIDE_DELAY_MS);
    }
  }, []);
  // 小地图缓动期间，onMoveEnd 的逐帧视口提交要跳过 —— 见 handleMoveEnd 的注释。
  // 用 ref 而不是读 minimapPanning：handleMoveEnd 每帧都跑，不该跟着状态换身份。
  const minimapPanCommitSuppressedRef = useRef(false);
  const handleMinimapPanStart = useCallback(() => {
    minimapPanCommitSuppressedRef.current = true;
    setMinimapPanning(true);
  }, []);
  // 这里的「结束」是 hook 保证的「松手且缓动已收敛」，不是 pointerup 那一刻，
  // 所以可以直接解除挂载保护，不需要再赌一个固定延时（收敛耗时随剩余距离变化，
  // 180ms 盖不住，会把缓动掐断在半路）。
  const handleMinimapPanEnd = useCallback(
    (pointerInsideMinimap: boolean) => {
      minimapPanCommitSuppressedRef.current = false;
      setMinimapPanning(false);
      // 松手在小地图内 ⇒ 继续显示；在外 ⇒ 走 hover 的同一节奏收起。
      setMinimapHover(pointerInsideMinimap);
    },
    [setMinimapHover],
  );
  useEffect(
    () => () => {
      if (minimapHideTimerRef.current !== null) {
        window.clearTimeout(minimapHideTimerRef.current);
      }
    },
    [],
  );

  return {
    minimapPinned,
    setMinimapPinned,
    minimapVisible,
    setMinimapHover,
    handleMinimapPanStart,
    handleMinimapPanEnd,
    minimapPanCommitSuppressedRef,
  };
}
