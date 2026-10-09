// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

export interface CanvasContextMenuItem {
  key: string;
  label: string;
  shortcut?: string;
  disabled?: boolean;
  onSelect: () => void;
}

interface CanvasContextMenuProps {
  /** Position relative to the canvas wrapper (its `offsetParent`). */
  position: { x: number; y: number };
  /** Item groups; a divider is drawn between groups. */
  sections: CanvasContextMenuItem[][];
  onClose: () => void;
}

const MENU_VIEWPORT_MARGIN = 12;

/**
 * Lightweight right-click context menu for the canvas pane (上传 / 添加节点 /
 * 撤销 / 重做 / 粘贴 …). Positioned absolutely inside the canvas wrapper, clamped
 * to the viewport, and dismissed on outside-click or Escape — mirrors how
 * {@link NodeSelectionMenu} anchors and closes.
 */
export function CanvasContextMenu({ position, sections, onClose }: CanvasContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [panelPosition, setPanelPosition] = useState(position);
  const [isVisible, setIsVisible] = useState(false);
  const [isPositioned, setIsPositioned] = useState(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setIsVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  useLayoutEffect(() => {
    const element = menuRef.current;
    const viewport = element?.offsetParent as HTMLElement | null;
    if (!element || !viewport) {
      return;
    }
    const width = element.offsetWidth;
    const height = element.offsetHeight;
    const maxX = Math.max(MENU_VIEWPORT_MARGIN, viewport.clientWidth - width - MENU_VIEWPORT_MARGIN);
    const maxY = Math.max(
      MENU_VIEWPORT_MARGIN,
      viewport.clientHeight - height - MENU_VIEWPORT_MARGIN,
    );
    const nextX = Math.min(Math.max(position.x, MENU_VIEWPORT_MARGIN), maxX);
    const nextY = Math.min(Math.max(position.y, MENU_VIEWPORT_MARGIN), maxY);
    setPanelPosition((current) =>
      current.x === nextX && current.y === nextY ? current : { x: nextX, y: nextY },
    );
    setIsPositioned(true);
  }, [position.x, position.y]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current?.contains(event.target as Node)) {
        return;
      }
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('mousedown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
      className={`absolute z-50 min-w-[212px] overflow-hidden rounded-[14px] border border-white/[0.10] bg-[#101217]/80 py-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.45)] backdrop-blur-2xl transition-opacity duration-150 ${
        isVisible && isPositioned ? 'opacity-100' : 'opacity-0'
      }`}
      style={{ left: panelPosition.x, top: panelPosition.y }}
    >
      {sections.map((section, sectionIndex) => (
        <div key={sectionIndex}>
          {sectionIndex > 0 && <div className="my-1.5 h-px bg-white/[0.08]" />}
          {section.map((item) => (
            <button
              key={item.key}
              type="button"
              disabled={item.disabled}
              onClick={() => {
                if (item.disabled) {
                  return;
                }
                item.onSelect();
                onClose();
              }}
              className={`flex w-full items-center justify-between gap-8 px-4 py-1.5 text-left text-[13px] transition-colors ${
                item.disabled
                  ? 'cursor-not-allowed text-white/28'
                  : 'text-white/85 hover:bg-white/[0.07]'
              }`}
            >
              <span>{item.label}</span>
              {item.shortcut ? (
                <span className={item.disabled ? 'text-white/20' : 'text-white/35'}>
                  {item.shortcut}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
