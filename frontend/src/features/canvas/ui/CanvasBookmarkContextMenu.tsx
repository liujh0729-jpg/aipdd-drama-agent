// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { MOD_KEY_LABEL as CMD } from "@/lib/platform";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { bookmarkIndexToDigit } from "@/features/canvas/domain/viewportBookmarks";

interface CanvasBookmarkContextMenuProps {
  index: number;
  filled: boolean;
  position: { x: number; y: number };
  onSetCurrent: () => void;
  onDelete: () => void;
  onClearAll: () => void;
  onClose: () => void;
}

export function CanvasBookmarkContextMenu({
  index,
  filled,
  position,
  onSetCurrent,
  onDelete,
  onClearAll,
  onClose,
}: CanvasBookmarkContextMenuProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const digit = bookmarkIndexToDigit(index) ?? "";
  // The minimap lives in a screen corner, so a menu opened at the cursor would
  // otherwise overflow the viewport edge and look like "nothing happened".
  // Clamp it inside the viewport in a layout effect (runs before paint → no flash).
  const [coords, setCoords] = useState(position);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const margin = 8;
    let left = position.x;
    let top = position.y;
    if (left + rect.width > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - margin - rect.width);
    }
    if (top + rect.height > window.innerHeight - margin) {
      top = Math.max(margin, window.innerHeight - margin - rect.height);
    }
    setCoords({ x: left, y: top });
  }, [position.x, position.y]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [onClose]);

  const run = (action: () => void) => {
    action();
    onClose();
  };

  return (
    <div
      ref={ref}
      className="fixed z-[10010] min-w-[220px] rounded-lg border border-border-dark bg-surface-dark py-1 text-sm text-white shadow-xl"
      style={{ left: coords.x, top: coords.y }}
      onContextMenu={(event) => event.preventDefault()}
    >
      <MenuRow
        label={t(filled ? "canvas.bookmarks.setCurrent" : "canvas.bookmarks.setNew")}
        shortcut={`${CMD} ${digit}`}
        onClick={() => run(onSetCurrent)}
      />
      {filled ? (
        <MenuRow label={t("canvas.bookmarks.deleteCurrent")} onClick={() => run(onDelete)} />
      ) : null}
      <MenuRow label={t("canvas.bookmarks.clearAll")} shortcut={`${CMD} ⇧ E`} onClick={() => run(onClearAll)} />
    </div>
  );
}

function MenuRow({
  label,
  shortcut,
  disabled,
  onClick,
}: {
  label: string;
  shortcut?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center justify-between gap-6 px-3 py-1.5 text-left hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
    >
      <span>{label}</span>
      {shortcut ? <span className="text-xs text-white/50">{shortcut}</span> : null}
    </button>
  );
}
