// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CSSProperties, ReactNode } from 'react';

interface ZoomScaledToolbarProps {
  children: ReactNode;
  /**
   * 缩放锚点。工具条浮在节点上方时，锚点取贴着节点那条边（底边），缩放时
   * 工具条朝节点收缩、不会飘走。align center → 'bottom center'，align start →
   * 'bottom left'。
   */
  origin?: CSSProperties['transformOrigin'];
  /**
   * 缩放下限。默认无下限（跟随 zoom 一路缩小，与顶部操作工具条一致）。侧边
   * 按钮栏（NodeSideActionRail）传一个下限，避免画布缩到 minZoom(0.1) 时按钮
   * 太小到点不准。
   */
  min?: number;
  /**
   * 缩放模式：
   * - `follow`（默认）：scale = zoom，工具条跟着画布同向缩放（缩小时一起变小）。
   * - `counter`：scale = clamp(counterMin, 1/zoom, counterMax)，反向缩放——画布
   *   放大时工具条变小、画布缩小时工具条变大，屏幕上尺寸基本恒定并封顶，避免
   *   缩到极小时菜单/操作区跟着缩到看不清、或撑到盖住旁边节点。
   */
  mode?: 'follow' | 'counter';
  /** counter 模式下的缩放下限（画布放得很大时不再继续缩小）。 */
  counterMin?: number;
  /** counter 模式下的缩放上限（画布缩得很小时不再继续放大，即「封顶」）。 */
  counterMax?: number;
}

/**
 * 把浮动工具条按画布缩放比例同步缩放。
 *
 * React Flow 的 `<NodeToolbar>` 默认固定屏幕尺寸、不随缩放变——画布缩小时
 * 节点变小但工具条不变，显得又大又突兀。这里读当前 viewport.zoom，对工具条
 * 内容套一层 `scale(zoom)`，让按钮/文字跟着画布一起放大缩小。
 *
 * 注意 transform 不改变布局盒尺寸，所以 NodeToolbar 仍按原始尺寸定位；配合
 * 贴边的 transform-origin，缩放后的工具条视觉上仍停在节点对应边上。
 *
 * 缩放比例来自根元素的 `--st-canvas-zoom` CSS 变量(由 Canvas 单一写入器维护),
 * 纯 CSS 跟随,不再 useStore 订阅 zoom —— 缩放时本组件不会因 zoom 变化而重渲染。
 */
export function ZoomScaledToolbar({
  children,
  origin = 'bottom center',
  min,
  mode = 'follow',
  counterMin = 0.7,
  counterMax = 1.6,
}: ZoomScaledToolbarProps) {
  const scale =
    mode === 'counter'
      ? `clamp(${counterMin}, calc(1 / var(--st-canvas-zoom, 1)), ${counterMax})`
      : min !== undefined
        ? `max(${min}, var(--st-canvas-zoom, 1))`
        : 'var(--st-canvas-zoom, 1)';
  return (
    <div style={{ transform: `scale(${scale})`, transformOrigin: origin }}>
      {children}
    </div>
  );
}
