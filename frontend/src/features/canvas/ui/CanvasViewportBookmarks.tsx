// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CanvasBookmarkContextMenu } from "./CanvasBookmarkContextMenu";
import {
  type ViewportBookmarks,
  bookmarkIndexToDigit,
  createEmptyBookmarks,
} from "@/features/canvas/domain/viewportBookmarks";

interface CanvasViewportBookmarksProps {
  bookmarks: ViewportBookmarks;
  /** Slot index the camera is currently parked on, or -1. Rendered as selected. */
  activeIndex?: number;
  onJump: (index: number) => void;
  onSetCurrent: (index: number) => void;
  onDelete: (index: number) => void;
  onClearAll: () => void;
}

const SLOT_INDICES = createEmptyBookmarks().map((_, index) => index);

export function CanvasViewportBookmarks({
  bookmarks,
  activeIndex = -1,
  onJump,
  onSetCurrent,
  onDelete,
  onClearAll,
}: CanvasViewportBookmarksProps) {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<{ index: number; x: number; y: number } | null>(null);

  // NOTE: keep this row free of backdrop-blur/filter/transform. Any of those would
  // make this box the containing block for the context menu's `position: fixed`,
  // throwing its viewport-coordinate (clientX/clientY) placement off-screen.
  return (
    <div className="pointer-events-auto flex items-center gap-0.5 rounded-sm border border-border-dark bg-surface-dark px-2 py-1 shadow-lg">
      {SLOT_INDICES.map((index) => {
        const digit = bookmarkIndexToDigit(index) ?? "";
        const filled = Boolean(bookmarks[index]);
        const active = index === activeIndex;
        return (
          <button
            key={index}
            type="button"
            title={t(filled ? "canvas.bookmarks.jumpTooltip" : "canvas.bookmarks.emptyTooltip", { digit })}
            onClick={() => {
              if (filled) {
                onJump(index);
              } else {
                onSetCurrent(index);
              }
            }}
            onContextMenu={(event) => {
              event.preventDefault();
              setMenu({ index, x: event.clientX, y: event.clientY });
            }}
            className={
              "flex h-6 w-6 items-center justify-center rounded-sm text-xs font-medium underline-offset-[3px] transition-colors " +
              (active
                ? "bg-white font-semibold text-black"
                : filled
                  ? "text-white underline decoration-white decoration-2 hover:bg-white/15"
                  : "text-white/35 hover:bg-white/15 hover:text-white/80")
            }
          >
            {digit}
          </button>
        );
      })}

      {menu ? (
        <CanvasBookmarkContextMenu
          index={menu.index}
          filled={Boolean(bookmarks[menu.index])}
          position={{ x: menu.x, y: menu.y }}
          onSetCurrent={() => onSetCurrent(menu.index)}
          onDelete={() => onDelete(menu.index)}
          onClearAll={onClearAll}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </div>
  );
}
