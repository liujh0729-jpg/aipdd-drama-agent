// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Palette } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  buildContextPromptPaletteForNode,
  type ContextPromptPalette,
  type ContextPromptPaletteEntry,
} from "@/features/canvas/nodes/contextPromptPalette";
import {
  NODE_CONTEXT_CONTROL_TRIGGER_CLASS,
  NODE_FLOATING_PANEL_SURFACE_CLASS,
} from "@/features/canvas/ui/nodeControlStyles";
import { useCanvasStore } from "@/stores/canvasStore";

interface ContextPromptPaletteProps {
  palette: ContextPromptPalette;
  onInsert: (entry: ContextPromptPaletteEntry) => void;
}

export function ContextPromptPaletteButton({
  palette,
  onInsert,
}: ContextPromptPaletteProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  // 弹层用 portal 渲染到 body（position: fixed），避免被外层工具栏的 overflow-x-auto
  // 等裁剪上下文截断（视频节点工具栏会横向滚动，overflow-y 随之被算成 auto）。
  const [popoverPos, setPopoverPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      setPopoverPos(null);
      return;
    }
    // rAF 循环跟随触发按钮：React Flow 的平移/缩放用 CSS transform，不触发
    // window 的 scroll/resize 事件，单靠事件监听会让 fixed 弹层与按钮错位。rAF
    // 每帧读 rect、仅在坐标变化时 setState，关闭时停止——只在面板打开期间运行。
    let raf = 0;
    let last = "";
    const tick = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (rect) {
        const left = Math.round(rect.left);
        const top = Math.round(rect.bottom + 6);
        const key = `${left}:${top}`;
        if (key !== last) {
          last = key;
          setPopoverPos({ left, top });
        }
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [open]);

  if (!palette.hasEntries) return null;

  const insertAndClose = (entry: ContextPromptPaletteEntry) => {
    onInsert(entry);
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        className={NODE_CONTEXT_CONTROL_TRIGGER_CLASS}
        title={t("node.imageGen.contextPalette.button")}
        aria-label={t("node.imageGen.contextPalette.button")}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        <Palette className="h-3 w-3" />
        <span>{t("node.imageGen.contextPalette.button")}</span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 text-text-muted/90 transition-transform ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        />
      </button>
      {open &&
        popoverPos &&
        createPortal(
          <div
            ref={popoverRef}
            className={`fixed z-[200] w-[248px] p-3 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`}
            style={{ left: popoverPos.left, top: popoverPos.top }}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={(event) => event.stopPropagation()}
          >
            <PaletteSection
              title={t("node.imageGen.contextPalette.actors")}
              entries={palette.actorEntries}
              onInsert={insertAndClose}
            />
            <PaletteSection
              title={t("node.imageGen.contextPalette.props")}
              entries={palette.propEntries}
              onInsert={insertAndClose}
            />
          </div>,
          document.body,
        )}
    </div>
  );
}

/**
 * 节点用的调色盘按钮：把「订阅全量 nodes/edges 构建 palette」的逻辑下沉到这个叶子
 * 组件，宿主节点（VideoNode / ImageGenNode 等重组件）就不必为了调色盘订阅整图、
 * 从而避免任意节点拖动都重渲染整个宿主节点——只让这个小按钮重渲染。
 */
export function NodeContextPromptPaletteButton({
  nodeId,
  onInsert,
}: {
  nodeId: string;
  onInsert: (entry: ContextPromptPaletteEntry) => void;
}) {
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const palette = useMemo(
    () => buildContextPromptPaletteForNode(nodes, edges, nodeId),
    [nodes, edges, nodeId],
  );
  return <ContextPromptPaletteButton palette={palette} onInsert={onInsert} />;
}

function PaletteSection({
  title,
  entries,
  onInsert,
}: {
  title: string;
  entries: ContextPromptPaletteEntry[];
  onInsert: (entry: ContextPromptPaletteEntry) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <section className="py-1 first:pt-0 last:pb-0">
      <div className="mb-1.5 px-0.5 text-[11px] font-semibold text-text-dark/72">
        {title}
      </div>
      <div className="flex flex-wrap gap-2">
        {entries.map((entry) => (
          <button
            key={`${entry.kind}:${entry.id}`}
            type="button"
            className={entry.named
              ? "inline-flex max-w-full items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[11px] text-text-dark/84 transition-colors hover:border-white/22 hover:bg-white/[0.09] hover:text-text-dark"
              : "inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-text-dark/84 transition-colors hover:border-white/22 hover:bg-white/[0.09] hover:text-text-dark"
            }
            title={entry.named ? `${entry.label} · ${entry.color}` : entry.color}
            aria-label={entry.named ? `${entry.label} · ${entry.color}` : entry.color}
            onClick={() => onInsert(entry)}
          >
            <span
              className={entry.named
                ? "h-3.5 w-3.5 shrink-0 rounded-full border border-white/45 shadow-[0_0_0_1px_rgba(0,0,0,0.45)]"
                : "h-4 w-4 shrink-0 rounded-full border border-white/45 shadow-[0_0_0_1px_rgba(0,0,0,0.45)]"
              }
              style={{ backgroundColor: entry.color }}
              aria-hidden="true"
            />
            {entry.named && <span className="min-w-0 truncate">{entry.label}</span>}
          </button>
        ))}
      </div>
    </section>
  );
}
