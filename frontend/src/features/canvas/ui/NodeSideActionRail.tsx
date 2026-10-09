// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { NodeToolbar as ReactFlowNodeToolbar, Position, useStore } from '@xyflow/react';
import { useState, type ReactNode } from 'react';

import { useCanvasStore } from '@/stores/canvasStore';
import { ZoomScaledToolbar } from '@/features/canvas/ui/ZoomScaledToolbar';

interface NodeSideActionRailProps {
  nodeId: string;
  position?: Position.Left | Position.Right;
  children: ReactNode;
  /**
   * 仅在节点被 hover 或选中时显示这条按钮栏（默认 false = 恒显示，保持
   * 上传/音频节点把上传当主操作的原有行为）。视频/图片节点的上传按钮接入它，
   * 避免在画布上一直显眼。
   */
  autoHide?: boolean;
  /** 节点是否被选中（autoHide 时用于「选中也显示」）。 */
  selected?: boolean;
}

// NodeToolbar 默认恒定屏幕尺寸（不随缩放变化），于是整理画布缩小后节点变成缩略图、
// 这条上传/替换按钮栏却仍是原大小，显得格外突兀。改用 ZoomScaledToolbar 跟随画布
// zoom：不再夹上限（之前夹 1 导致放大态不跟着变大、和其余 UI 脱节），放大时与顶部
// 操作工具条一致地一起变大；仅保留 min=0.6 下限，避免缩到 minZoom(0.1) 时点不准。

export const NODE_SIDE_ACTION_BUTTON_CLASS =
  'nodrag inline-flex h-8 items-center gap-1.5 rounded-[12px] border border-white/10 bg-[#242426]/95 px-3 text-xs font-medium text-text-dark backdrop-blur-xl transition-colors hover:border-white/18 hover:bg-[#29292b]/95 hover:text-white disabled:cursor-not-allowed disabled:opacity-50';

export const NODE_SIDE_ACTION_ICON_CLASS = 'h-3.5 w-3.5 text-text-muted/90';

export function NodeSideActionRail({
  nodeId,
  position = Position.Right,
  children,
  autoHide = false,
  selected = false,
}: NodeSideActionRailProps) {
  const isLeft = position === Position.Left;
  // Canvas 维护的节点 hover（离开带 400ms 延迟，桥接「从节点移到上方按钮」的
  // 空隙）；railHovered 进一步保证鼠标停在按钮栏上时不被那个延迟清掉而隐藏。
  const nodeHovered = useCanvasStore((state) => state.hoveredNodeId === nodeId);
  const [railHovered, setRailHovered] = useState(false);
  const isVisible = !autoHide || selected || nodeHovered || railHovered;
  // 把这条按钮栏抬到同节点的 spawn「+」(NodeSpawnPlusOverlay) 之上。两者都是
  // 同一节点的 NodeToolbar，xyflow 给它们同一个 zIndex(node.internals.z + 1);
  // 「+」在 Canvas 层后渲染，平局时 DOM 顺序更靠后而盖在上面，它那块 80px 的隐形
  // 磁吸命中区会压住「上传」按钮、把点击吃掉(磁吸还会把「+」吸到按钮上)。这里给
  // 按钮栏 +2,确保按钮永远在「+」之上接收 hover/点击(光标落到按钮上时「+」自动退回)。
  const nodeZ = useStore((state) => state.nodeLookup.get(nodeId)?.internals.z ?? 0);
  return (
    <ReactFlowNodeToolbar
      nodeId={nodeId}
      isVisible={isVisible}
      position={position}
      align="start"
      offset={18}
      className="pointer-events-auto"
      style={{ zIndex: nodeZ + 2 }}
    >
      {/*
        Right rail (upload): lift it just above the node's top-right corner. The
        spawn "+" (NodeSpawnPlusOverlay) lives on the same right edge but
        vertically centered, so a top-aligned rail collides with it on short
        nodes (audio) — worse now that the "+" scales with zoom. Anchoring above
        the top edge keeps it clear of the centered "+" at any zoom/height.

        Left rail (替换素材): do NOT lift above the top edge. This rail AND the
        centered top action toolbar both now scale with zoom, so a lifted left
        rail grows up into the toolbar's band and overlaps it at high zoom (the
        toolbar's left end reaches the rail's column). Anchoring at the node's
        top-left corner and growing downward keeps the rail below the toolbar —
        which sits entirely above the node's top edge — at any zoom.
      */}
      <div
        style={isLeft ? undefined : { transform: "translateY(calc(-100% - 2px))" }}
        onMouseEnter={() => setRailHovered(true)}
        onMouseLeave={() => setRailHovered(false)}
      >
        {/* 跟随画布缩放，与顶部操作工具条同一套逻辑。锚点贴住靠节点的那个角
            （右栏 bottom-left 抬起后朝上、左栏 top-right 朝下），缩放时朝远离
            节点方向展开，始终贴在节点角上。 */}
        <ZoomScaledToolbar origin={isLeft ? 'top right' : 'bottom left'} min={0.6}>
          <div className={`flex flex-col gap-2 ${isLeft ? 'items-end' : 'items-start'}`}>
            {children}
          </div>
        </ZoomScaledToolbar>
      </div>
    </ReactFlowNodeToolbar>
  );
}
