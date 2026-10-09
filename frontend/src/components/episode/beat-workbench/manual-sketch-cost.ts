// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { SketchAspectRatio } from "@/lib/queries/sketch-settings";
import {
  SKETCH_REGEN_MODES,
  bestFitMode,
  overflowBatchCount,
} from "@/lib/regen-modes";
import type { Beat } from "@/types/episode";

function missingManualSketchSceneId(beat: Beat): string {
  return beat.scene_ref?.scene_id?.trim() || beat.location?.trim() || "";
}

export function missingManualSketchSegments(beats: readonly Beat[]): number[][] {
  const ordered = [...beats].sort((a, b) => a.beat_number - b.beat_number);
  const segments: number[][] = [];
  let current: number[] = [];
  let currentSceneId = "";

  for (const beat of ordered) {
    const beatNumber = Number(beat.beat_number || 0);
    const sceneId = missingManualSketchSceneId(beat);
    const missingManual = beatNumber > 0 && !!beat.is_manual_shot && !beat.sketch_url;

    if (missingManual) {
      if (current.length > 0 && sceneId !== currentSceneId) {
        segments.push(current);
        current = [];
      }
      current.push(beatNumber);
      currentSceneId = sceneId;
      continue;
    }

    if (current.length > 0) {
      segments.push(current);
      current = [];
      currentSceneId = "";
    }
  }

  if (current.length > 0) segments.push(current);
  return segments;
}

function sketchModesForCost(aspectRatio: SketchAspectRatio) {
  const aspectToken = aspectRatio.replace(":", "-");
  const matching = SKETCH_REGEN_MODES.filter(
    (mode) => mode.key.endsWith("_sketch") && mode.key.includes(`_${aspectToken}`),
  );
  if (matching.length > 0) return matching;
  return SKETCH_REGEN_MODES.filter(
    (mode) => mode.key.endsWith("_sketch") && mode.key.includes("_2-3"),
  );
}

export function missingManualSketchModelCallCount(
  beats: readonly Beat[],
  aspectRatio: SketchAspectRatio,
): number {
  const modes = sketchModesForCost(aspectRatio);
  if (modes.length === 0) return 0;
  return missingManualSketchSegments(beats).reduce((sum, segment) => {
    const mode = bestFitMode(modes, segment.length);
    return sum + overflowBatchCount(mode, segment.length);
  }, 0);
}
