// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from "react";

/**
 * Close an overlay/lightbox/dialog with the Escape key while it's open.
 *
 * Attaches a `keydown` listener to `document` only while `open` is true,
 * scoped so `Escape` fires `onClose` and nothing else reacts. Keeps a11y
 * parity with native `<dialog>` / shadcn dialogs (which handle this
 * automatically) for hand-rolled overlays like image previews.
 */
export function useEscapeToClose(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);
}
