// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Beat } from "@/types/episode";
import type { Task } from "@/types/task";
import type { BeatStageState, BeatStates } from "@/types/beat-state";
import {
  EPISODE_STAGE_REGISTRY,
  type StageDef,
  type StageId,
} from "@/lib/episode-stage-registry";
import { SCOPED_TASK_TYPES } from "@/lib/task-types";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "pending", "starting", "running"]);

// Module-level constants — computed once, never per-call.
const STAGES: Array<Exclude<StageId, "compose">> = [
  "script",
  "sketch",
  "audio",
  "video",
];

const STAGE_DEFS: Record<Exclude<StageId, "compose">, StageDef> = {
  script: EPISODE_STAGE_REGISTRY.find((s) => s.id === "script")!,
  sketch: EPISODE_STAGE_REGISTRY.find((s) => s.id === "sketch")!,
  audio: EPISODE_STAGE_REGISTRY.find((s) => s.id === "audio")!,
  video: EPISODE_STAGE_REGISTRY.find((s) => s.id === "video")!,
};

/**
 * Pure derivation — exported for unit testing. `useBeatStates` is a thin
 * memoized wrapper on top that subscribes to TanStack Query caches.
 */
export function deriveBeatStates(beats: Beat[], tasks: Task[]): BeatStates {
  // Pre-index tasks by task_type — O(T) build, then O(1) per lookup.
  const taskIndex = new Map<string, Task[]>();
  for (const task of tasks) {
    let bucket = taskIndex.get(task.task_type);
    if (!bucket) {
      bucket = [];
      taskIndex.set(task.task_type, bucket);
    }
    bucket.push(task);
  }

  const result: BeatStates = {};

  for (const beat of beats) {
    const stateForBeat: Record<Exclude<StageId, "compose">, BeatStageState> = {
      script: "missing",
      sketch: "missing",
      audio: "missing",
      video: "missing",
    };

    for (const stage of STAGES) {
      stateForBeat[stage] = deriveSingle(stage, beat, taskIndex, STAGE_DEFS[stage]);
    }

    result[beat.beat_number] = stateForBeat;
  }

  return result;
}

function deriveSingle(
  stage: Exclude<StageId, "compose">,
  beat: Beat,
  taskIndex: Map<string, Task[]>,
  def: StageDef,
): BeatStageState {
  // 1. ready — evaluate first, never mask existing assets
  if (stage === "script") {
    // Script readiness is gauged by the visual description: every beat (incl.
    // silent / action shots that carry no spoken line) is "ready" once it has a
    // 画面描述. Spoken text (narration_segment) is optional and intentionally
    // not required here. See script-beat-preview's readiness display.
    if (beat.visual_description && beat.visual_description.trim().length > 0) {
      return "ready";
    }
  } else if (stage === "sketch" && beat.sketch_url) {
    return "ready";
  } else if (stage === "audio" && beat.audio_url) {
    return "ready";
  } else if (stage === "video" && beat.video_url) {
    return "ready";
  }

  // Collect tasks relevant to this stage from the pre-built index.
  const taskTypes = def.taskTypes as readonly string[];
  const relevant: Task[] = [];
  for (const tt of taskTypes) {
    const bucket = taskIndex.get(tt);
    if (bucket) {
      for (const t of bucket) relevant.push(t);
    }
  }

  // 2. generating — scoped task on this beat, OR batch task with this beat still missing
  const active = relevant.find(
    (t) =>
      ACTIVE_STATUSES.has(t.status) &&
      (isScopedTaskType(t.task_type) ? t.beat_num === beat.beat_number : true),
  );
  if (active) return "generating";

  // 3. failed — ONLY for scoped tasks matching this beat
  const failed = relevant.find(
    (t) =>
      t.status === "failed" &&
      isScopedTaskType(t.task_type) &&
      t.beat_num === beat.beat_number,
  );
  if (failed) return "failed";

  return "missing";
}

function isScopedTaskType(type: string): boolean {
  return SCOPED_TASK_TYPES.has(type as never);
}
