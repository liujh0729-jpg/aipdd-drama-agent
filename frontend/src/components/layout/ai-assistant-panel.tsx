// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/use-media-query";
import {
  AI_ASSISTANT_WIDTH_DEFAULT,
  AI_ASSISTANT_WIDTH_MAX,
  AI_ASSISTANT_WIDTH_MIN,
  useAppStore,
} from "@/stores/app-store";
import { SuperChatPanel } from "@/features/superchat/superchat-panel";

export function AiAssistantPanel() {
  const { t } = useTranslation();
  const open = useAppStore((s) => s.aiAssistantOpen);
  const setOpen = useAppStore((s) => s.setAiAssistantOpen);
  const width = useAppStore((s) => s.aiAssistantWidth);
  const setWidth = useAppStore((s) => s.setAiAssistantWidth);
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const [resizing, setResizing] = useState(false);
  const startResize = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const target = event.currentTarget;
      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startWidth = width || AI_ASSISTANT_WIDTH_DEFAULT;
      let cleaned = false;

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      setResizing(true);

      const handlePointerMove = (moveEvent: PointerEvent) => {
        if (moveEvent.pointerId !== pointerId) return;
        const nextWidth = startWidth + startX - moveEvent.clientX;
        setWidth(nextWidth);
      };

      const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", cleanup);
        window.removeEventListener("pointercancel", cleanup);
        window.removeEventListener("blur", cleanup);
        target.removeEventListener("lostpointercapture", cleanup);
        try {
          if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
        } catch {
          // Pointer capture can already be released by the browser.
        }
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        setResizing(false);
      };

      try {
        target.setPointerCapture(pointerId);
      } catch {
        // Some browsers may reject capture for non-primary pointers.
      }

      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", cleanup);
      window.addEventListener("pointercancel", cleanup);
      window.addEventListener("blur", cleanup);
      target.addEventListener("lostpointercapture", cleanup);
    },
    [setWidth, width],
  );

  return (
    <>
      {open && isDesktop && (
        <>
          <div
            className="group hidden h-full w-2 shrink-0 cursor-col-resize touch-none items-stretch justify-center bg-border/25 transition-colors hover:bg-primary/20 lg:flex"
            role="separator"
            aria-orientation="vertical"
            aria-label={t("aiAssistant.resize")}
            onPointerDown={startResize}
            title={t("aiAssistant.resize")}
          >
            <span className="h-full w-px bg-border/80 transition-colors group-hover:bg-primary group-focus-visible:bg-primary" />
          </div>
          {resizing && (
            <div
              className="fixed inset-0 z-50 cursor-col-resize"
              aria-hidden="true"
            />
          )}
          <aside
            className="hidden h-full shrink-0 flex-col overflow-hidden border-l border-border/70 bg-background lg:flex"
            style={{
              width: Math.min(AI_ASSISTANT_WIDTH_MAX, Math.max(AI_ASSISTANT_WIDTH_MIN, width)),
              minWidth: AI_ASSISTANT_WIDTH_MIN,
              maxWidth: AI_ASSISTANT_WIDTH_MAX,
            }}
          >
            <SuperChatPanel />
          </aside>
        </>
      )}

      {open && !isDesktop && (
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="right" className="flex w-full flex-col p-0 sm:!max-w-[560px]">
            <SheetHeader className="sr-only">
              <SheetTitle>{t("aiAssistant.title")}</SheetTitle>
              <SheetDescription>{t("aiAssistant.description")}</SheetDescription>
            </SheetHeader>
            <SuperChatPanel />
          </SheetContent>
        </Sheet>
      )}
    </>
  );
}
