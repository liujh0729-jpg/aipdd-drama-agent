// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { ImageIcon, ScrollText, UserRound, Volume2, VolumeX } from "lucide-react";

import { EpisodeEmptyState } from "@/components/episode/episode-empty-state";
import { cn } from "@/lib/utils";
import type { Beat } from "@/types/episode";

export interface ScriptBeatPreviewLabels {
  title: string;
  count: (count: number) => string;
  loading: string;
  emptyTitle: string;
  empty: string;
  audioType: (type: string) => string;
  speaker: string;
  noSpeaker: string;
  dialogueLine: string;
  narrationLine: string;
  noNarration: string;
  visualDescription: string;
  noVisualDescription: string;
}

interface ScriptBeatPreviewProps {
  beats: Beat[];
  labels: ScriptBeatPreviewLabels;
  loading?: boolean;
  className?: string;
}

export function ScriptBeatPreview({
  beats,
  labels,
  loading = false,
  className,
}: ScriptBeatPreviewProps) {
  return (
    <section className={cn("px-5 pb-5", className)}>
      <div className="mb-4 flex min-w-0 items-baseline gap-2">
        <h2 className="truncate text-sm font-semibold tracking-tight text-foreground">
          {labels.title}
        </h2>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          ({labels.count(beats.length)})
        </span>
      </div>

      <div className="pr-1">
        {loading ? (
          <p className="rounded-[8px] border border-white/[0.06] px-3 py-4 text-sm text-muted-foreground">
            {labels.loading}
          </p>
        ) : beats.length === 0 ? (
          <div className="grid min-h-[420px] place-items-center rounded-[10px] border border-white/[0.06]">
            <EpisodeEmptyState
              icon={ScrollText}
              title={labels.emptyTitle}
              description={labels.empty}
              className="h-auto p-0"
            />
          </div>
        ) : (
          <ol className="space-y-3">
            {beats.map((beat) => (
              <ScriptBeatPreviewRow
                key={beat.beat_number}
                beat={beat}
                labels={labels}
              />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function ScriptBeatPreviewRow({
  beat,
  labels,
}: {
  beat: Beat;
  labels: ScriptBeatPreviewLabels;
}) {
  const audioType = (beat.audio_type ?? "narration").trim() || "narration";
  const speaker = (beat.speaker ?? "").trim() || labels.noSpeaker;
  const narration = (beat.narration_segment ?? "").trim() || labels.noNarration;
  const visual =
    (beat.visual_description ?? "").trim() || labels.noVisualDescription;
  const isSilent = ["silence", "action"].includes(audioType);
  const showNarration = !beat.is_manual_shot && !isSilent;
  // Dialogue beats carry an actor's spoken line; everything else is narration.
  const lineLabel =
    audioType === "dialogue" ? labels.dialogueLine : labels.narrationLine;

  return (
    <li className="rounded-[8px] border border-white/[0.06] bg-white/[0.02] px-4 pb-3 pt-4">
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <span className="inline-flex h-5 items-center rounded-[5px] bg-accent/70 px-1.5 font-mono text-[11px] tabular-nums text-foreground/80">
          #{beat.beat_number}
        </span>
        <span
          className={cn(
            "inline-flex h-5 items-center gap-1 rounded-[5px] px-1.5 text-[11px]",
            isSilent
              ? "bg-white/[0.04] text-muted-foreground"
              : "bg-primary/10 text-primary/90",
          )}
        >
          {isSilent ? (
            <VolumeX className="size-3 shrink-0" />
          ) : (
            <Volume2 className="size-3 shrink-0" />
          )}
          <span className="truncate">{labels.audioType(audioType)}</span>
        </span>
        {showNarration && (
          <span className="inline-flex h-5 items-center gap-1 rounded-[5px] bg-white/[0.04] px-1.5 text-[11px] text-muted-foreground">
            <UserRound className="size-3 shrink-0" />
            <span className="truncate">{speaker}</span>
          </span>
        )}
      </div>

      <div
        className={cn(
          "grid gap-4",
          showNarration && "xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]",
        )}
      >
        {showNarration && (
          <PreviewText icon={ScrollText} label={lineLabel} value={narration} />
        )}
        <PreviewText
          icon={ImageIcon}
          label={labels.visualDescription}
          value={visual}
          tone="visual"
        />
      </div>
    </li>
  );
}

function PreviewText({
  icon: Icon,
  label,
  value,
  tone = "line",
}: {
  icon: typeof ScrollText;
  label: string;
  value: string;
  tone?: "line" | "visual";
}) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-[8px] border px-3 py-2.5",
        tone === "line"
          ? "border-primary/15 bg-primary/[0.035]"
          : "border-white/[0.09] bg-white/[0.026]",
      )}
    >
      <div
        className={cn(
          "mb-2 flex items-center gap-1.5 text-xs font-medium",
          tone === "line" ? "text-primary/85" : "text-muted-foreground",
        )}
      >
        <Icon className="size-3.5 shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/88">
        {value}
      </p>
    </section>
  );
}
