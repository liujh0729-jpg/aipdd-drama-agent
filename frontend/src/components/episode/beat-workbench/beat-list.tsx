// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import { FileText, Mic2, Pencil, Video } from "lucide-react";

import { cn } from "@/lib/utils";
import type { BeatStageState, BeatStates } from "@/types/beat-state";
import type { Beat } from "@/types/episode";

interface BeatListProps {
  beats: Beat[];
  states: BeatStates;
  selected: number | null;
  onSelect: (beatNum: number) => void;
}

/** Left-column beat list. Master-detail navigator for 镜头 tab. */
export function BeatList({ beats, states, selected, onSelect }: BeatListProps) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Beat rows */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {beats.length === 0 ? (
          <div className="p-6 text-center text-xs text-muted-foreground">
            {t("episode.beat.noMatching")}
          </div>
        ) : (
          <ul className="py-1">
            {beats.map((beat) => (
              <BeatRow
                key={beat.beat_number}
                beat={beat}
                stageSet={states[beat.beat_number]}
                isSelected={selected === beat.beat_number}
                onSelect={() => onSelect(beat.beat_number)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function BeatRow({
  beat,
  stageSet,
  isSelected,
  onSelect,
}: {
  beat: Beat;
  stageSet:
    | { script: BeatStageState; sketch: BeatStageState; audio: BeatStageState; video: BeatStageState }
    | undefined;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const preview = (beat.narration_segment ?? "").slice(0, 42);
  const speaker = beat.speaker ?? "";
  return (
    <li className="min-w-0">
      <div
        className={cn(
          "group flex min-w-0 items-start gap-2 overflow-hidden border-l-2 px-3 py-2 transition-colors",
          isSelected
            ? "border-l-primary bg-primary/5"
            : "border-l-transparent hover:bg-accent/40",
        )}
      >
        <button
          type="button"
          onClick={onSelect}
          className="min-w-0 flex-1 space-y-1 text-left"
        >
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="rounded bg-accent px-1.5 py-0.5 font-mono tabular-nums">
              #{beat.beat_number}
            </span>
            {speaker && <span className="truncate">{speaker}</span>}
            {beat.audio_type && (
              <span className="rounded bg-accent px-1 py-0.5 text-[10px]">
                {t("audioType." + beat.audio_type)}
              </span>
            )}
          </div>
          <p className="line-clamp-1 text-xs text-foreground">
            {preview || (
              <span className="italic text-muted-foreground/60">
                {t("episode.beat.noNarration")}
              </span>
            )}
          </p>
          <div className="flex items-center gap-1.5">
            <StageDot state={stageSet?.script ?? "missing"} icon={FileText} />
            <StageDot state={stageSet?.sketch ?? "missing"} icon={Pencil} />
            <StageDot state={stageSet?.audio ?? "missing"} icon={Mic2} />
            <StageDot state={stageSet?.video ?? "missing"} icon={Video} />
          </div>
        </button>
      </div>
    </li>
  );
}

function StageDot({
  state,
  icon: Icon,
}: {
  state: BeatStageState;
  icon: React.ElementType;
}) {
  const color =
    state === "ready"
      ? "text-primary"
      : state === "generating"
        ? "text-amber-600 dark:text-amber-300"
        : state === "failed"
          ? "text-destructive"
          : "text-muted-foreground/40";
  return <Icon className={cn("size-3", color)} />;
}
