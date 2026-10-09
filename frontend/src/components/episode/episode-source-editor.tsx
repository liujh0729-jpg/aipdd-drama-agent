// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from "react";
import { FileText } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { splitLiteralSourceText } from "@/lib/literal-source-text";
import { cn } from "@/lib/utils";

interface EpisodeSourceEditorLabels {
  rawLabel: string;
  rawActionLabel: string;
  noRawText: string;
  sourceLabel: string;
  sourceMeta: (count: number) => string;
  sourcePlaceholder: string;
  linePreviewLabel: string;
  lineCount: (count: number) => string;
  noLines: string;
}

interface EpisodeSourceEditorProps {
  rawContent: string;
  sourceText: string;
  labels: EpisodeSourceEditorLabels;
  onSave: (next: string) => void | Promise<void>;
  saving?: boolean;
  className?: string;
}

const SCRIPT_PANEL_CONTROL_CLASS =
  "!h-6 gap-1 !rounded-[6px] border-white/[0.12] bg-white/[0.04] px-2 text-[11px] font-normal text-foreground/78 shadow-none transition-colors hover:border-white/[0.2] hover:bg-white/[0.05] hover:text-foreground focus-visible:border-white/24 focus-visible:ring-0 focus-visible:outline-none [&_svg]:!size-3";

export function EpisodeSourceEditor({
  rawContent,
  sourceText,
  labels,
  onSave,
  saving = false,
  className,
}: EpisodeSourceEditorProps) {
  const [draftLines, setDraftLines] = useState<string[]>(() =>
    splitLiteralSourceText(sourceText),
  );
  const lineCount = draftLines.filter((line) => line.trim()).length;

  useEffect(() => {
    setDraftLines(splitLiteralSourceText(sourceText));
  }, [sourceText]);

  const serializeDraft = (lines: string[]) =>
    lines
      .map((line) => line.trim())
      .filter(Boolean)
      .join("\n");

  const commit = () => {
    const next = serializeDraft(draftLines);
    if (saving || next === sourceText) return;
    void onSave(next);
  };

  const handleLineChange = (index: number, value: string) => {
    const parts = value.split(/\r?\n/);
    setDraftLines((current) => {
      const next = current.length > 0 ? [...current] : [""];
      next.splice(index, 1, ...parts);
      return next;
    });
  };
  const visibleLines = draftLines.length > 0 ? draftLines : [""];

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col",
        className,
      )}
    >
      <div className="flex min-h-0 flex-col">
        <div className="mb-2 flex h-6 shrink-0 items-center justify-between gap-3">
          <Label
            className="flex min-w-0 items-baseline gap-2 text-muted-foreground"
          >
            <span className="truncate text-sm font-semibold tracking-tight text-foreground">
              {labels.sourceLabel}
            </span>
            <span className="shrink-0 text-xs">
              ({labels.sourceMeta(lineCount)})
            </span>
          </Label>
          <Dialog>
            <DialogTrigger
              className={cn(
                "inline-flex w-[112px] shrink-0 items-center justify-center border",
                SCRIPT_PANEL_CONTROL_CLASS,
              )}
            >
              <FileText className="size-3" />
              {labels.rawActionLabel}
            </DialogTrigger>
            <DialogContent className="gap-4 overflow-hidden rounded-2xl border border-white/8 bg-background/68 p-7 shadow-none backdrop-blur-3xl sm:max-w-3xl">
              <DialogHeader className="gap-2">
                <DialogTitle className="text-lg font-medium tracking-tight">
                  {labels.rawLabel}
                </DialogTitle>
              </DialogHeader>
              <div className="max-h-[68vh] overflow-y-auto px-1 py-1 font-mono text-sm leading-relaxed">
                {rawContent ? (
                  <p className="whitespace-pre-wrap text-foreground/85">
                    {rawContent}
                  </p>
                ) : (
                  <p className="italic text-muted-foreground/60">
                    {labels.noRawText}
                  </p>
                )}
              </div>
            </DialogContent>
          </Dialog>
        </div>
        <div className="rounded-[10px] border border-white/[0.06] px-2 py-4">
          <ol className="space-y-3">
            {visibleLines.map((line, index) => (
              <li
                key={index}
                className="relative min-h-9 rounded-[7px] border border-white/[0.08] bg-white/[0.025] transition-colors focus-within:border-white/[0.2] focus-within:bg-white/[0.04] focus-within:ring-2 focus-within:ring-white/[0.08]"
              >
                <span className="pointer-events-none absolute left-3 top-2 font-mono text-xs leading-relaxed tabular-nums text-muted-foreground">
                  {index + 1}.
                </span>
                <Textarea
                  aria-label={`${labels.sourceLabel} ${index + 1}`}
                  value={line}
                  onChange={(e) => handleLineChange(index, e.target.value)}
                  onBlur={commit}
                  disabled={saving}
                  placeholder={index === 0 ? labels.sourcePlaceholder : undefined}
                  className="min-h-9 resize-none rounded-none border-0 bg-transparent py-1.5 pl-12 pr-3 font-mono text-sm leading-relaxed shadow-none focus-visible:border-transparent focus-visible:ring-0"
                />
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
