// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { StageId } from "@/lib/episode-stage-registry";

/**
 * Per-beat per-stage derived state.
 * - missing: asset absent; no active task attributable to this beat/stage
 * - generating: active task matches this beat (scoped) OR is a batch and beat is still missing
 * - ready: asset present (URL non-null)
 * - failed: task terminal=failed AND task.beat_num === n AND asset still absent
 *
 * Note: `skipped` was considered (for 1.5-model audio bundling) but dropped —
 * compose currently requires an audio file for every beat regardless of video
 * model. See `docs/superpowers/specs/2026-04-14-episode-workbench-design.md`
 * Part 1 for derivation rules.
 */
export type BeatStageState = "missing" | "generating" | "ready" | "failed";

export type BeatStates = Record<
  number,
  Record<Exclude<StageId, "compose">, BeatStageState>
>;

export interface StageCount {
  /** Beats in `ready` state. */
  ready: number;
  /** Total beats considered — excludes a beat if state derivation had no inputs. */
  total: number;
  /** Beats with any non-ready state other than `missing` (generating + failed). */
  active: number;
  /** Beats in `failed` state. */
  failed: number;
}

export interface EpisodeCounts {
  script: StageCount;
  sketch: StageCount;
  audio: StageCount;
  video: StageCount;
  compose: {
    ready: boolean;
    missing: Array<{ beatNum: number; stages: Array<Exclude<StageId, "compose">> }>;
  };
}
