// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";
import { toast } from "sonner";

import { openPresetProjectionInMyCanvas } from "@/features/freezone/openPresetProjection";
import { useResponsiveColumns } from "@/hooks/use-responsive-columns";
import { useDeleteManualShot } from "@/lib/queries/episodes";
import { useGridsByBeat, type PoolImage } from "@/lib/queries/sketches";
import type { Beat } from "@/types/episode";
import type { ViewToggleId } from "@/hooks/use-view-toggles";
import type { SelectionState } from "@/hooks/use-selection";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { BeatCard } from "./beat-card";
import { InsertManualShotDialog } from "./insert-manual-shot-dialog";

const EMPTY_IMAGES: PoolImage[] = [];

interface BeatCardGridProps {
  beats: Beat[];
  toggles: Set<ViewToggleId>;
  selection: SelectionState;
  aspectRatio: "portrait" | "landscape";
  project: string;
  episode: number;
  spineTemplate?: "drama" | "narrated";
  onCardClick: (beatNum: number) => void;
  onCheckboxClick: (beatNum: number) => void;
}

/**
 * Beat card grid — CSS grid layout with responsive columns.
 * No virtualization needed for typical 18-50 beat episodes.
 * Cards self-size based on content (images + text), all equal height
 * within a row via CSS grid's implicit row stretching.
 */
export function BeatCardGrid({
  beats,
  toggles,
  selection,
  aspectRatio,
  project,
  episode,
  spineTemplate = "drama",
  onCardClick,
  onCheckboxClick,
}: BeatCardGridProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const columns = useResponsiveColumns(scrollRef);
  const { byBeat, assignments } = useGridsByBeat(project, episode);
  const deleteManual = useDeleteManualShot(project, episode);

  const showSketch = toggles.has("sketch");
  const showRender = toggles.has("render");
  const selectedBeat = selection.mode === "single" ? selection.beatNum : null;
  const checkedSet = selection.mode === "multi" ? selection.checked : null;

  // Bring the selected beat card into view when the selection changes (e.g.
  // arriving from the compose tab via ?focusBeat, or any single-select). Uses
  // "nearest" so an already-visible card doesn't jump.
  useEffect(() => {
    if (selectedBeat === null) return;
    const container = scrollRef.current;
    if (!container) return;
    const card = container.querySelector<HTMLElement>(
      `[data-beat-number="${selectedBeat}"]`,
    );
    card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedBeat]);

  // Per-card insert controls share one dialog. "Before Beat #1" maps to
  // afterBeatNumber=null; "Before Beat #N" maps to after Beat #(N-1).
  const [insertAfterBeat, setInsertAfterBeat] = useState<number | null>(null);
  const [insertOpen, setInsertOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    beatNumber: number;
    displayNumber: number;
  } | null>(null);
  const [freezonePendingBeat, setFreezonePendingBeat] = useState<number | null>(null);
  const handleInsertBefore = useCallback((beatNum: number) => {
    const currentIndex = beats.findIndex((beat) => beat.beat_number === beatNum);
    if (currentIndex <= 0) {
      setInsertAfterBeat(null);
    } else {
      setInsertAfterBeat(beats[currentIndex - 1]?.beat_number ?? null);
    }
    setInsertOpen(true);
  }, [beats]);
  const handleInsertAfter = useCallback((beatNum: number) => {
    setInsertAfterBeat(beatNum);
    setInsertOpen(true);
  }, []);
  const handleOpenFreezone = useCallback(
    async (beatNum: number, primarySlot: "sketch" | "frame") => {
      if (freezonePendingBeat !== null) return;
      setFreezonePendingBeat(beatNum);
      try {
        await openPresetProjectionInMyCanvas(project, {
          scope: "beat",
          episode,
          beat: beatNum,
          primary_slot: primarySlot,
        });
      } catch {
        toast.error(t("episode.beat.openFreezoneFailed"));
        setFreezonePendingBeat(null);
      }
    },
    [episode, freezonePendingBeat, project, t],
  );
  const handleDeleteManual = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      const res = await deleteManual.mutateAsync(deleteTarget.beatNumber);
      if (res.ok === false) {
        toast.error(res.error);
        return;
      }
      toast.success(
        t("episode.beat.deleteManualShotSuccess", {
          n: deleteTarget.displayNumber,
        }),
      );
      setDeleteTarget(null);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("episode.beat.deleteManualShotFailed"),
      );
    }
  }, [deleteManual, deleteTarget, t]);

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 pt-2 pb-4">
      <div
        className="grid gap-3.5"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {beats.map((beat, index) => (
          <BeatCard
            key={beat.beat_number}
            beat={beat}
            displayNumber={index + 1}
            showSketch={showSketch}
            showRender={showRender}
            images={byBeat.get(beat.beat_number) ?? EMPTY_IMAGES}
            assignments={assignments}
            aspectRatio={aspectRatio}
            isSelected={selectedBeat === beat.beat_number}
            isChecked={checkedSet?.has(beat.beat_number) ?? false}
            onCardClick={onCardClick}
            onCheckboxClick={onCheckboxClick}
            onInsertBefore={handleInsertBefore}
            onInsertAfter={handleInsertAfter}
            onOpenFreezone={handleOpenFreezone}
            onDeleteManual={(beatNumber, displayNumber) =>
              setDeleteTarget({ beatNumber, displayNumber })
            }
            isOpeningFreezone={freezonePendingBeat === beat.beat_number}
            isDeletingManual={
              deleteManual.isPending &&
              deleteTarget?.beatNumber === beat.beat_number
            }
          />
        ))}
      </div>

      <InsertManualShotDialog
        open={insertOpen}
        onOpenChange={(v) => {
          setInsertOpen(v);
          if (!v) setInsertAfterBeat(null);
        }}
        project={project}
        episode={episode}
        spineTemplate={spineTemplate}
        afterBeatNumber={insertAfterBeat}
      />

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleteManual.isPending) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("episode.beat.deleteManualShotTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("episode.beat.deleteManualShotDesc", {
                n: deleteTarget?.displayNumber ?? "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteManual.isPending}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleDeleteManual();
              }}
              disabled={deleteManual.isPending}
              className="border border-destructive/35 bg-destructive/[0.08] text-destructive hover:border-destructive/50 hover:bg-destructive/[0.14] hover:text-destructive"
            >
              {t("episode.beat.deleteManualShot")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {beats.length === 0 && (
        <div className="p-6 text-center text-xs text-muted-foreground">
          {t("episode.workbench.view.noMatchingBeats")}
        </div>
      )}

      {beats.length > 0 && (
        <div
          className="mt-6 mb-2 flex items-center gap-3 px-6 text-muted-foreground/50"
          aria-hidden
        >
          <span className="h-px flex-1 bg-gradient-to-r from-transparent to-white/[0.08]" />
          <span className="flex items-center gap-1.5 text-[11px] font-medium tracking-wider uppercase">
            <Clapperboard className="size-3.5" />
            {t("episode.workbench.endOfBeatsCount", { count: beats.length })}
          </span>
          <span className="h-px flex-1 bg-gradient-to-l from-transparent to-white/[0.08]" />
        </div>
      )}
    </div>
  );
}
